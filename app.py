"""Project-local application server for smartdisplay.com.

SQLite is the durable application source of truth and SSE broadcasts committed
changes. Project-local Google authentication and Pillow validate identity/media.
"""
from __future__ import annotations

import argparse
import json
import logging
import queue
import sqlite3
import threading
import time
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import security
DB_PATH = ROOT / ".data" / "smartdisplay.db"
LOG_PATH = ROOT / ".logs" / "application-server.log"
SOURCE_FILE = ROOT / "index.html"
DAYS = ("Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
PRIORITIES = ("Normal", "Important", "Urgent")
STATUSES = ("pending", "completed")

DEFAULT_TASKS = {day: [] for day in DAYS}
DEFAULT_SETTINGS = {
    "directives": [],
    "supervisors": [],
    "tickerText": "",
    "customLogoUrl": None,
    "selectedDay": "Monday",
}

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def read_site_config() -> dict:
    path = ROOT / ".data" / "site-config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialise_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                title TEXT NOT NULL,
                dept TEXT NOT NULL,
                priority TEXT NOT NULL,
                task_time TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count == 0 and not conn.execute("SELECT 1 FROM metadata WHERE key='state_version'").fetchone():
            stamp = now()
            rows = []
            for day, tasks in DEFAULT_TASKS.items():
                for task in tasks:
                    rows.append((task["id"], day, task["title"], task["dept"], task["priority"], task["time"], task["desc"], task["imageUrl"], task["status"], stamp, stamp, 1))
            conn.executemany("INSERT INTO tasks (id,day,title,dept,priority,task_time,description,image_url,status,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key,value_json,updated_at,version) VALUES (?,?,?,1)", (key, json.dumps(value), now()))
        conn.execute("INSERT OR IGNORE INTO metadata (key,value) VALUES ('state_version','1')")
    security.migrate(sys.modules[__name__])
    security.cleanup_media()
    logging.info("database ready: %s", DB_PATH)


def task_record(row: sqlite3.Row, conn=None) -> dict:
    """Return the single public representation of a committed task row."""
    own = conn is None
    if own:
        conn = db()
    media = security.media_record(row['id'], conn)
    if own:
        conn.close()
    return {
        "id": row["id"],
        "day": row["day"],
        "title": row["title"],
        "dept": row["dept"],
        "priority": row["priority"],
        "time": row["task_time"],
        "desc": row["description"],
        "imageUrl": row["image_url"],
        "status": row["status"],
        "updatedAt": row["updated_at"],
        "version": row["version"],
        "deadlineUtc": row['deadline_utc'],
        "visibleFromUtc": row['visible_from_utc'],
        "visibleUntilUtc": row['visible_until_utc'],
        "channel": row['channel'],
        "overdue": bool(row['deadline_utc'] and row['status'] != 'completed' and datetime.fromisoformat(row['deadline_utc']) < datetime.now(timezone.utc)),
        "media": media,
    }


def state(conn: sqlite3.Connection | None = None) -> dict:
    own = conn is None
    if own:
        conn = db()
    try:
        if own:
            conn.execute('BEGIN')
        tasks = {day: [] for day in DAYS}
        for row in conn.execute("SELECT * FROM tasks ORDER BY id"):
            tasks[row["day"]].append(task_record(row, conn))
        settings = {row["key"]: json.loads(row["value_json"]) for row in conn.execute("SELECT key,value_json FROM settings")}
        version = int(conn.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0])
        return {
            "tasks": tasks,
            "directives": settings["directives"],
            "supervisors": settings["supervisors"],
            "tickerText": settings["tickerText"],
            "customLogoUrl": settings["customLogoUrl"],
            "selectedDay": settings.get("selectedDay", DAYS[0]),
            "version": version,
            "siteName": read_site_config().get("site_name", "Smart Display System"),
            "timeZone": read_site_config().get("timezone", "UTC"),
            "serverTime": now(),
        }
    finally:
        if own:
            conn.close()


def clean_task(payload: dict, partial: bool = False) -> dict:
    allowed = ("day", "title", "dept", "priority", "time", "desc", "imageUrl", "status", "deadlineUtc", "visibleFromUtc", "visibleUntilUtc", "channel")
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    value = {key: payload[key] for key in allowed if key in payload}
    if not partial and set(("day", "title", "dept", "priority")) - set(value):
        raise ValueError("day, title, department and priority are required")
    if "day" in value and value["day"] not in DAYS:
        raise ValueError("invalid day")
    if "channel" in value:
        import re
        if not isinstance(value["channel"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", value["channel"]):
            raise ValueError("channel must be a lowercase slug such as lobby or general")
    if "priority" in value and value["priority"] not in PRIORITIES:
        raise ValueError("invalid priority")
    if "status" in value and value["status"] not in STATUSES:
        raise ValueError("invalid status")
    for key in ("title", "dept", "time", "desc", "imageUrl"):
        if key in value:
            if not isinstance(value[key], str) or len(value[key]) > (2_000_000 if key == "imageUrl" else 2000):
                raise ValueError(f"invalid {key}")
            value[key] = value[key].strip()
    if "title" in value and not value["title"]:
        raise ValueError("title is required")
    if value.get('imageUrl'):
        raise ValueError('Upload media using the file picker; arbitrary image URLs are not accepted')
    return value


class Events:
    def __init__(self) -> None:
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def add(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=20)
        with self.lock:
            self.clients.add(q)
        logging.info("sse open clients=%d", len(self.clients))
        return q

    def remove(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(q)
            logging.info("sse close clients=%d", len(self.clients))

    def publish(self, event: str, payload: dict) -> None:
        with self.lock:
            clients = tuple(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass
        logging.info("broadcast event=%s clients=%d version=%s", event, len(clients), payload.get("version"))


EVENTS = Events()
MUTATION_LOCK = threading.Lock()


def commit_mutation(operation: str, work) -> dict:
    # Serialize commit + publication so SSE event order always follows the
    # monotonic database revision, even when HTTP handlers run concurrently.
    with MUTATION_LOCK:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mutation = work(conn)
            if not isinstance(mutation, dict):
                mutation = {"result": mutation}
            include_snapshot = bool(mutation.pop("_snapshot", False))
            version = int(conn.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0]) + 1
            conn.execute("UPDATE metadata SET value=? WHERE key='state_version'", (str(version),))
            if include_snapshot:
                mutation["snapshot"] = state(conn)
            conn.execute("COMMIT")
        payload = {"operation": operation, "version": version, "at": now(), **mutation}
        EVENTS.publish("state", payload)
        logging.info("mutation committed operation=%s version=%d", operation, version)
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "smartdisplay-app/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin-allow-popups')
        if getattr(self, 'extra_cookie', None):
            self.send_header('Set-Cookie', self.extra_cookie)
            self.extra_cookie = None
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        logging.info("http %s - %s", self.address_string(), fmt % args)

    def send_json(self, data: dict, status_code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 3_000_000:
            raise ValueError("invalid request size")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                body = SOURCE_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/state":
                self.send_json(state())
            elif path == '/api/auth/session':
                self.send_json(security.auth_state(self))
            elif path.startswith('/api/media/'):
                security.serve_media(self, path.removeprefix('/api/media/'))
            elif path in ('/upgrade.js', '/upgrade.css'):
                body = (ROOT / path[1:]).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/health":
                self.send_json({"ok": True, "database": str(DB_PATH.name)})
            elif path == "/api/events":
                self.handle_events()
            else:
                self.send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except security.Problem as exc:
            self.send_json({'error': exc.error, 'message': exc.message}, exc.status)
        except Exception as exc:
            logging.exception("GET failed path=%s", path)
            self.send_json({"error": "server error"}, 500)

    def handle_events(self) -> None:
        q = EVENTS.add()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            self._event("ready", {"version": state()["version"]})
            while True:
                try:
                    event, payload = q.get(timeout=10)
                    self._event(event, payload)
                except queue.Empty:
                    # A named heartbeat is visible to EventSource clients, so
                    # they can replace a proxied stream that has gone silent.
                    self._event("heartbeat", {"at": now()})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            EVENTS.remove(q)

    def _event(self, event: str, payload: dict) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.wfile.write(b"event: " + event.encode() + b"\ndata: " + raw + b"\n\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        self.handle_mutation("POST")

    def do_PUT(self) -> None:
        self.handle_mutation("PUT")

    def do_PATCH(self) -> None:
        self.handle_mutation("PATCH")

    def do_DELETE(self) -> None:
        self.handle_mutation("DELETE")

    def handle_mutation(self, method: str) -> None:
        path = urlparse(self.path).path
        try:
            if method == 'POST' and path.startswith('/api/auth/'):
                security.auth_mutation(self, path)
                return
            actor = security.require_editor(self)
            if path.startswith('/api/tasks/') and path.endswith('/media') and method in ('POST', 'DELETE'):
                security.media_mutation(self, int(path.split('/')[3]), actor, method)
                return
            if method == "POST" and path == "/api/tasks":
                task = security.visibility(security.deadline(clean_task(self.read_json())))
                def add(conn):
                    stamp = now()
                    cur = conn.execute("INSERT INTO tasks (day,title,dept,priority,task_time,description,image_url,status,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,1)", (task["day"], task["title"], task["dept"], task["priority"], task.get("time", ""), task.get("desc", ""), task.get("imageUrl", ""), task.get("status", "pending"), stamp, stamp))
                    task_id = cur.lastrowid
                    conn.execute('UPDATE tasks SET deadline_utc=?,visible_from_utc=?,visible_until_utc=?,channel=?,created_by=?,updated_by=? WHERE id=?', (task.get('deadlineUtc'), task.get('visibleFromUtc'), task.get('visibleUntilUtc'), task.get('channel', 'general'), actor, actor, task_id))
                    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                    return {"result": task_id, "task": task_record(row, conn)}
                self.send_json(commit_mutation("created", add), 201)
            elif method == "PATCH" and path.startswith("/api/tasks/"):
                task_id = int(path.rsplit("/", 1)[1])
                changes = clean_task(self.read_json(), partial=True)
                if not changes:
                    raise ValueError("no task fields supplied")
                columns = {"day": "day", "title": "title", "dept": "dept", "priority": "priority", "time": "task_time", "desc": "description", "imageUrl": "image_url", "status": "status", "deadlineUtc": "deadline_utc", "visibleFromUtc": "visible_from_utc", "visibleUntilUtc": "visible_until_utc", "channel": "channel"}
                def update(conn):
                    old = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                    if not old:
                        raise security.Problem(404, 'task_not_found')
                    security.deadline(changes, old)
                    security.visibility(changes, old)
                    fields = [f"{columns[k]}=?" for k in changes]
                    args = list(changes.values()) + [actor, now(), task_id]
                    cur = conn.execute(f"UPDATE tasks SET {','.join(fields)},updated_by=?,updated_at=?,version=version+1 WHERE id=?", args)
                    if cur.rowcount != 1:
                        raise ValueError("task not found")
                    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                    return {"result": task_id, "task": task_record(row, conn)}
                self.send_json(commit_mutation("updated", update))
            elif method == "DELETE" and path.startswith("/api/tasks/"):
                task_id = int(path.rsplit("/", 1)[1])
                obsolete = []
                def delete(conn):
                    obsolete.extend(r[0] for r in conn.execute('SELECT storage_name FROM task_media WHERE task_id=?', (task_id,)))
                    cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                    if cur.rowcount != 1:
                        raise ValueError("task not found")
                    return {"result": task_id, "deletedId": task_id}
                self.send_json(commit_mutation("deleted", delete))
                logging.info('task deleted id=%s actor=%s', task_id, actor)
                security.cleanup_media(obsolete)
            elif method == "POST" and path == "/api/tasks/reset":
                raise security.Problem(410, "reset_unavailable", "The demo task reset is unavailable.")

            elif method == "POST" and path == "/api/tasks/import":
                raw = self.read_json()
                items = raw.get("tasks", []) if isinstance(raw, dict) else []
                if len(items) > 100:
                    raise ValueError('Import at most 100 tasks at a time')
                cleaned = [security.visibility(security.deadline(clean_task(item))) for item in items]
                def imported(conn):
                    stamp = now()
                    inserted = []
                    for item in cleaned:
                        cur = conn.execute("INSERT INTO tasks (day,title,dept,priority,task_time,description,image_url,status,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?,?,?,?,1)", (item["day"], item["title"], item["dept"], item["priority"], item.get("time", ""), item.get("desc", ""), item.get("imageUrl", ""), item.get("status", "pending"), stamp, stamp))
                        conn.execute('UPDATE tasks SET deadline_utc=?,visible_from_utc=?,visible_until_utc=?,channel=?,created_by=?,updated_by=? WHERE id=?', (item.get('deadlineUtc'), item.get('visibleFromUtc'), item.get('visibleUntilUtc'), item.get('channel', 'general'), actor, actor, cur.lastrowid))
                        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
                        inserted.append(task_record(row, conn))
                    return {"result": len(inserted), "tasks": inserted}
                self.send_json(commit_mutation("bulk_imported", imported), 201)
            elif method == "PUT" and path == "/api/settings":
                values = self.read_json()
                valid = {k: values[k] for k in ("directives", "supervisors", "tickerText", "customLogoUrl") if k in values}
                if not valid:
                    raise ValueError("no settings supplied")
                if "directives" in valid and (not isinstance(valid["directives"], list) or any(not isinstance(x, str) or len(x) > 2000 for x in valid["directives"])):
                    raise ValueError("invalid directives")
                if "supervisors" in valid and (not isinstance(valid["supervisors"], list) or any(not isinstance(x, dict) or not isinstance(x.get("title"), str) or not isinstance(x.get("name"), str) for x in valid["supervisors"])):
                    raise ValueError("invalid supervisors")
                if "tickerText" in valid and (not isinstance(valid["tickerText"], str) or len(valid["tickerText"]) > 2000):
                    raise ValueError("invalid ticker")
                if "customLogoUrl" in valid and valid["customLogoUrl"] is not None and (not isinstance(valid["customLogoUrl"], str) or len(valid["customLogoUrl"]) > 2_000_000):
                    raise ValueError("invalid logo")
                if valid.get('customLogoUrl'):
                    import base64
                    import re
                    match = re.fullmatch(r'data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)', valid['customLogoUrl'])
                    if not match:
                        raise ValueError('Logo must be a JPG, PNG, WebP or GIF image upload')
                    extension = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif'}[match[1]]
                    security.validate_media(base64.b64decode(match[2], validate=True), 'logo' + extension, match[1])
                def settings_update(conn):
                    stamp = now()
                    for key, value in valid.items():
                        conn.execute("UPDATE settings SET value_json=?,updated_at=?,version=version+1 WHERE key=?", (json.dumps(value), stamp, key))
                    return {"result": list(valid), "settings": valid}
                self.send_json(commit_mutation("settings_updated", settings_update))
            elif method == "PUT" and path == "/api/selected-day":
                values = self.read_json()
                selected_day = values.get("selectedDay") if isinstance(values, dict) else None
                if selected_day not in DAYS:
                    raise ValueError("invalid selected day")

                def selected_day_update(conn):
                    conn.execute(
                        """
                        INSERT INTO settings (key,value_json,updated_at,version)
                        VALUES ('selectedDay',?,?,1)
                        ON CONFLICT(key) DO UPDATE SET
                            value_json=excluded.value_json,
                            updated_at=excluded.updated_at,
                            version=settings.version+1
                        """,
                        (json.dumps(selected_day), now()),
                    )
                    return {"result": selected_day, "selectedDay": selected_day}

                self.send_json(commit_mutation("selected_day_changed", selected_day_update))
            else:
                self.send_json({"error": "not found"}, 404)
        except security.Problem as exc:
            self.send_json({'error': exc.error, 'message': exc.message}, exc.status)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except sqlite3.Error:
            logging.exception("database mutation failure path=%s", path)
            self.send_json({"error": "database failure"}, 500)
        except Exception:
            logging.exception("mutation failed path=%s", path)
            self.send_json({"error": "server error"}, 500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    initialise_database()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    logging.info("application server started address=127.0.0.1:%d", args.port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        logging.info("application server stopped")


if __name__ == "__main__":
    main()
