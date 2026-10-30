"""SQLite authorization, Google identity, deadlines and validated local media."""
import base64
import hashlib
import io
import json
import re
import secrets
import time
import warnings
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = None
COOKIE = '__Host-smartdisplay_session'
SESSION_SECONDS = 7 * 86400
MYT = timezone(timedelta(hours=8), 'Asia/Kuala_Lumpur')
SEEDED_EDITORS = {}  # No organization accounts are shipped.
FORMATS = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif', '.mp4': 'video/mp4', '.webm': 'video/webm'}
MAX_MEDIA = 25 * 1024 * 1024
LOGIN_ATTEMPTS = deque()
LOGIN_LOCK = threading.Lock()

class Problem(Exception):
    def __init__(self, status, error, message=None):
        self.status, self.error, self.message = status, error, message or error

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def config():
    path = APP.ROOT / '.data' / 'auth-config.json'
    return json.loads(path.read_text('utf-8')) if path.exists() else {}

def migrate(app):
    global APP
    APP = app
    with app.db() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.execute('BEGIN IMMEDIATE')
        for name in ('deadline_utc', 'created_by', 'updated_by', 'visible_from_utc', 'visible_until_utc'):
            if name not in {r['name'] for r in conn.execute('PRAGMA table_info(tasks)')}:
                conn.execute(f'ALTER TABLE tasks ADD COLUMN {name} TEXT')
        statements = [
            '''CREATE TABLE IF NOT EXISTS authorized_users(email TEXT PRIMARY KEY, display_name TEXT NOT NULL, department TEXT NOT NULL, role TEXT NOT NULL CHECK(role='editor'), active INTEGER NOT NULL DEFAULT 1, google_sub TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, email TEXT NOT NULL REFERENCES authorized_users(email), csrf_token TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS login_challenges(token_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS task_media(id TEXT PRIMARY KEY, task_id INTEGER UNIQUE NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, storage_name TEXT UNIQUE NOT NULL, original_name TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        ]
        for sql in statements:
            conn.execute(sql)
        for email, (name, dept) in SEEDED_EDITORS.items():
            conn.execute('INSERT OR IGNORE INTO authorized_users(email,display_name,department,role,active,created_at,updated_at) VALUES (?,?,?,\'editor\',1,?,?)', (email, name, dept, app.now(), app.now()))
        conn.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('security_schema','1')")
        conn.execute('COMMIT')
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    (app.ROOT / '.data' / 'media').mkdir(exist_ok=True)

def cookie_value(handler, name=COOKIE):
    # Parse only our cryptographically generated token. Browsers can include
    # third-party cookies (notably Google GIS's g_state) whose value is rejected
    # by Python's strict SimpleCookie parser, which would otherwise hide every
    # valid cookie later in the same header.
    header = handler.headers.get('Cookie', '')
    match = re.search(r'(?:^|;\s*)' + re.escape(name) + r'=([A-Za-z0-9_-]{20,128})(?:;|$)', header)
    return match.group(1) if match else ''

def session(handler):
    token = cookie_value(handler)
    if not token:
        return None
    with APP.db() as conn:
        return conn.execute('SELECT s.*,u.role,u.active,u.display_name FROM sessions s JOIN authorized_users u ON u.email=s.email WHERE token_hash=? AND expires_at>?', (digest(token), int(time.time()))).fetchone()

def same_origin(handler):
    origin = handler.headers.get('Origin', '')
    allowed = {
        origin for origin in config().get("allowed_origins", [])
        if isinstance(origin, str) and re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{2,5})?", origin)
    }
    # Loopback is a local browser origin, never an authentication bypass.
    allowed.add(f'http://127.0.0.1:{handler.server.server_port}')
    if origin not in allowed or handler.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
        raise Problem(403, 'cross_origin_rejected', 'This action must originate from this display application.')

def require_editor(handler):
    user = session(handler)
    if not user:
        raise Problem(401, 'sign_in_required', 'Staff sign-in is required to make changes.')
    if not user['active'] or user['role'] != 'editor':
        raise Problem(403, 'editor_not_authorized', 'This account is not authorized to edit this display.')
    same_origin(handler)
    if not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), user['csrf_token']):
        raise Problem(403, 'csrf_rejected', 'Refresh your staff session and try again.')
    return user['email']

def auth_state(handler):
    user = session(handler)
    valid = user and user['active'] and user['role'] == 'editor'
    return {'role': 'editor' if valid else 'viewer', 'email': user['email'] if valid else None,
            'name': user['display_name'] if valid else None, 'csrfToken': user['csrf_token'] if valid else None,
            'clientId': config().get('google_client_id', ''), 'serverTime': APP.now()}

def verify_credential(credential, client_id):
    from google.oauth2 import id_token
    from google.auth.transport.requests import Request
    if not isinstance(credential, str) or len(credential) > 16000:
        raise Problem(401, 'invalid_google_credential')
    try:
        request = Request()
        claims = id_token.verify_oauth2_token(credential, lambda *a, **kw: request(*a, timeout=10, **kw), audience=client_id, clock_skew_in_seconds=0)
    except Exception:
        raise Problem(401, 'invalid_google_credential', 'Google sign-in could not be verified. Please sign in again.')
    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com') or claims.get('aud') != client_id or not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time() or claims.get('email_verified') is not True or not claims.get('sub'):
        raise Problem(401, 'invalid_google_credential')
    return claims

def auth_mutation(handler, path):
    same_origin(handler)
    if path != '/api/auth/logout':
        # A bounded global budget also limits forged-token certificate fetches.
        with LOGIN_LOCK:
            cutoff = time.time() - 60
            while LOGIN_ATTEMPTS and LOGIN_ATTEMPTS[0] < cutoff:
                LOGIN_ATTEMPTS.popleft()
            if len(LOGIN_ATTEMPTS) >= 120:
                raise Problem(429, 'sign_in_rate_limited', 'Too many sign-in attempts. Please wait one minute and try again.')
            LOGIN_ATTEMPTS.append(time.time())
    if path == '/api/auth/challenge':
        token, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with APP.db() as conn:
            conn.execute('DELETE FROM login_challenges WHERE expires_at<?', (time.time(),))
            conn.execute('INSERT INTO login_challenges VALUES (?,?,?)', (digest(token), nonce, int(time.time()) + 300))
        handler.extra_cookie = f'__Host-smartdisplay_login={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=300'
        handler.send_json({'nonce': nonce})
    elif path == '/api/auth/google':
        data = handler.read_json()
        client = config().get('google_client_id', '')
        if not client:
            raise Problem(503, 'google_setup_required', 'Staff sign-in is awaiting Google configuration. Public viewing is available.')
        token = cookie_value(handler, '__Host-smartdisplay_login')
        with APP.db() as conn:
            challenge = conn.execute('SELECT * FROM login_challenges WHERE token_hash=? AND expires_at>?', (digest(token), time.time())).fetchone()
        if not challenge:
            raise Problem(401, 'login_expired', 'Please start Google sign-in again.')
        claims = verify_credential(data.get('credential'), client)
        if not secrets.compare_digest(str(claims.get('nonce', '')), challenge['nonce']):
            raise Problem(401, 'invalid_login_nonce')
        email = str(claims.get('email', '')).strip().lower()
        with APP.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('DELETE FROM login_challenges WHERE token_hash=?', (digest(token),)).rowcount != 1:
                raise Problem(401, 'login_expired')
            user = conn.execute('SELECT * FROM authorized_users WHERE email=?', (email,)).fetchone()
            if not user or not user['active'] or user['role'] != 'editor':
                raise Problem(403, 'editor_not_authorized', 'This Google account is not approved for Editor access. You can continue viewing.')
            if user['google_sub'] and user['google_sub'] != claims['sub']:
                raise Problem(403, 'google_identity_changed', 'This identity does not match the approved staff account.')
            conn.execute('UPDATE authorized_users SET google_sub=?,updated_at=? WHERE email=?', (claims['sub'], APP.now(), email))
            raw, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
            conn.execute('DELETE FROM sessions WHERE expires_at<? OR token_hash=?', (time.time(), digest(cookie_value(handler))))
            conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (digest(raw), email, csrf, int(time.time()), int(time.time()) + SESSION_SECONDS))
            conn.execute('COMMIT')
        handler.extra_cookie = f'{COOKIE}={raw}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={SESSION_SECONDS}'
        handler.send_json({'role': 'editor', 'email': email, 'name': user['display_name'], 'csrfToken': csrf})
    elif path == '/api/auth/logout':
        user = session(handler)
        if user and not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), user['csrf_token']):
            raise Problem(403, 'csrf_rejected')
        with APP.db() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest(cookie_value(handler)),))
        handler.extra_cookie = f'{COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'
        handler.send_json({'role': 'viewer'})
    else:
        raise Problem(404, 'not_found')

def deadline(value, old=None):
    previous = old['deadline_utc'] if old else None
    status = value.get('status', old['status'] if old else 'pending')
    incoming = value.get('deadlineUtc', previous)
    normalized = None
    if incoming:
        try:
            dt = datetime.fromisoformat(incoming.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError()
            normalized = dt.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, AttributeError):
            raise Problem(400, 'invalid_deadline', 'Choose a valid deadline date and time.')
    changed = normalized != previous
    reopened = old and old['status'] == 'completed' and status == 'pending'
    if status != 'completed' and (not old or changed or reopened):
        if not normalized or datetime.fromisoformat(normalized) <= datetime.now(timezone.utc):
            raise Problem(400, 'deadline_in_past', 'Deadline must be in the future.')
    if 'deadlineUtc' in value or not old:
        value['deadlineUtc'] = normalized
    return value

def visibility(value, old=None):
    """Validate optional UTC visibility bounds, independent from a task deadline."""
    def normalize(raw):
        if raw in (None, ''):
            return None
        if not isinstance(raw, str) or len(raw) > 50:
            raise Problem(400, 'invalid_visibility', 'Use valid UTC visibility dates.')
        try:
            timestamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError()
            return timestamp.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            raise Problem(400, 'invalid_visibility', 'Use valid UTC visibility dates.')

    from_date = normalize(value.get('visibleFromUtc', old['visible_from_utc'] if old else None))
    until_date = normalize(value.get('visibleUntilUtc', old['visible_until_utc'] if old else None))
    if from_date and until_date and datetime.fromisoformat(from_date) >= datetime.fromisoformat(until_date):
        raise Problem(400, 'invalid_visibility', 'Visibility end must be after visibility start.')
    if 'visibleFromUtc' in value or not old:
        value['visibleFromUtc'] = from_date
    if 'visibleUntilUtc' in value or not old:
        value['visibleUntilUtc'] = until_date
    return value


def validate_media(data, filename, mime):
    if not filename or '/' in filename or '\\' in filename or '\x00' in filename or len(filename) > 180:
        raise Problem(400, 'invalid_filename')
    ext = Path(filename).suffix.lower()
    if ext not in FORMATS or mime != FORMATS[ext]:
        raise Problem(400, 'unsupported_media', 'Use JPG, PNG, WebP, GIF, MP4 or WebM with the correct file type.')
    if not data or len(data) > MAX_MEDIA:
        raise Problem(413, 'media_too_large', 'Media must be 25 MB or smaller.')
    if mime.startswith('image/'):
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as im:
                    if Image.MIME.get(im.format) != mime or im.width * im.height > 40_000_000:
                        raise ValueError()
                    im.verify()
        except Exception:
            raise Problem(400, 'invalid_media_content', 'The image contents do not match its file type or are damaged.')
    elif mime == 'video/mp4':
        # Require a bounded ISO-BMFF file with ftyp, media data, and movie metadata.
        offset, boxes = 0, set()
        while offset + 8 <= len(data):
            size = int.from_bytes(data[offset:offset+4], 'big')
            kind = data[offset+4:offset+8]
            if size == 1 and offset + 16 <= len(data):
                size = int.from_bytes(data[offset+8:offset+16], 'big')
            if size == 0:
                size = len(data) - offset
            if size < 8 or offset + size > len(data):
                break
            boxes.add(kind)
            offset += size
        if offset != len(data) or not {b'ftyp', b'mdat', b'moov'} <= boxes or data[4:8] != b'ftyp':
            raise Problem(400, 'invalid_media_content', 'Choose a valid MP4 video.')
    elif not (data.startswith(b'\x1aE\xdf\xa3') and b'webm' in data[:4096] and b'\x18\x53\x80\x67' in data[:8192] and b'\x16\x54\xae\x6b' in data and b'\x1f\x43\xb6\x75' in data):
        raise Problem(400, 'invalid_media_content', 'Choose a valid WebM video.')
    return ext

def media_record(task_id, conn):
    row = conn.execute('SELECT id,mime_type,size_bytes,original_name FROM task_media WHERE task_id=?', (task_id,)).fetchone()
    return {'id': row['id'], 'url': '/api/media/' + row['id'], 'mimeType': row['mime_type'], 'sizeBytes': row['size_bytes'], 'name': row['original_name']} if row else None

def media_mutation(handler, task_id, actor, method):
    stored = None
    obsolete = []
    if method == 'POST':
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 1 or length > MAX_MEDIA:
            raise Problem(413, 'media_too_large', 'Media must be 25 MB or smaller.')
        from urllib.parse import unquote
        filename = unquote(handler.headers.get('X-File-Name', ''))
        mime = handler.headers.get('Content-Type', '').split(';')[0]
        data = handler.rfile.read(length)
        if len(data) != length:
            raise Problem(400, 'incomplete_upload')
        ext = validate_media(data, filename, mime)
        media_id = secrets.token_hex(16)
        storage = media_id + ext
        stored = APP.ROOT / '.data' / 'media' / storage
        with stored.open('xb') as out:
            out.write(data)
    def work(conn):
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise Problem(404, 'task_not_found')
        obsolete.extend(r[0] for r in conn.execute('SELECT storage_name FROM task_media WHERE task_id=?', (task_id,)))
        conn.execute('DELETE FROM task_media WHERE task_id=?', (task_id,))
        if method == 'POST':
            conn.execute('INSERT INTO task_media VALUES (?,?,?,?,?,?,?,?,?)', (media_id, task_id, storage, filename, mime, len(data), hashlib.sha256(data).hexdigest(), actor, APP.now()))
        conn.execute("UPDATE tasks SET image_url='',updated_by=?,updated_at=?,version=version+1 WHERE id=?", (actor, APP.now(), task_id))
        return {'result': task_id, 'task': APP.task_record(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone(), conn)}
    try:
        result = APP.commit_mutation('updated', work)
    except Exception:
        if stored:
            stored.unlink(missing_ok=True)
        raise
    cleanup_media(obsolete)
    handler.send_json(result, 201 if method == 'POST' else 200)

def cleanup_media(obsolete=()):
    # Grace period prevents racing an upload before its DB transaction begins.
    with APP.MUTATION_LOCK, APP.db() as conn:
        names = {row[0] for row in conn.execute('SELECT storage_name FROM task_media')}
        for file in (APP.ROOT / '.data' / 'media').iterdir():
            if re.fullmatch(r'[0-9a-f]{32}\.(jpg|jpeg|png|webp|gif|mp4|webm)', file.name) and file.name not in names and (file.name in obsolete or file.stat().st_mtime < time.time() - 3600):
                file.unlink()

def serve_media(handler, media_id):
    if not re.fullmatch('[0-9a-f]{32}', media_id):
        raise Problem(404, 'not_found')
    with APP.db() as conn:
        row = conn.execute('SELECT * FROM task_media WHERE id=?', (media_id,)).fetchone()
    if not row:
        raise Problem(404, 'not_found')
    path = APP.ROOT / '.data' / 'media' / row['storage_name']
    if not path.is_file():
        raise Problem(404, 'media_missing')
    size = path.stat().st_size
    start, end, code = 0, size - 1, 200
    requested = handler.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
        if not match:
            raise Problem(416, 'invalid_range')
        start, end = int(match[1]), min(int(match[2]) if match[2] else end, end)
        if start > end:
            raise Problem(416, 'invalid_range')
        code = 206
    handler.send_response(code)
    handler.send_header('Content-Type', row['mime_type'])
    handler.send_header('Content-Length', str(end - start + 1))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Cache-Control', 'public, max-age=3600')
    handler.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
    if code == 206:
        handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    handler.end_headers()
    with path.open('rb') as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
