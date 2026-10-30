"""Isolated smoke coverage for first-run, HTTP security and scheduling data."""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]


class IsolatedInstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="smartdisplay-test-")
        cls.root = Path(cls.workspace.name)
        for name in ("app.py", "security.py", "index.html", "upgrade.js", "upgrade.css"):
            shutil.copy2(SOURCE / name, cls.root / name)
        (cls.root / "scripts").mkdir()
        shutil.copy2(SOURCE / "scripts/first_run.py", cls.root / "scripts/first_run.py")
        command = [sys.executable, str(cls.root / "scripts/first_run.py"),
                   "--site-name", "Test Room", "--timezone", "Europe/London"]
        setup = subprocess.run(command, cwd=cls.root, text=True, capture_output=True, timeout=25)
        if setup.returncode:
            cls.workspace.cleanup()
            raise RuntimeError("First-run failed: " + setup.stderr)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            cls.port = server.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.process = subprocess.Popen([sys.executable, str(cls.root / "app.py"),
                                        "--port", str(cls.port)], cwd=cls.root,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if cls.process.poll() is not None:
                raise RuntimeError("Application exited unexpectedly")
            try:
                with urllib.request.urlopen(cls.url + "/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(.15)
        else:
            cls.tearDownClass()
            raise RuntimeError("Application did not start")

    @classmethod
    def tearDownClass(cls):
        process = getattr(cls, "process", None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        workspace = getattr(cls, "workspace", None)
        if workspace is not None:
            workspace.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as response:
            return response.status, json.loads(response.read().decode())

    def test_fresh_install_is_empty_and_brand_neutral(self):
        status, data = self.get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["siteName"], "Test Room")
        self.assertEqual(data["timeZone"], "Europe/London")
        self.assertTrue(all(not items for items in data["tasks"].values()))
        self.assertFalse(data["supervisors"])
        with sqlite3.connect(self.root / ".data/smartdisplay.db") as database:
            self.assertEqual(database.execute("select count(*) from authorized_users").fetchone()[0], 0)
            self.assertEqual(database.execute("pragma integrity_check").fetchone()[0], "ok")

    def test_public_is_read_only_and_sensitive_path_is_not_served(self):
        status, data = self.get("/api/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(data["role"], "viewer")
        request = urllib.request.Request(self.url + "/api/tasks", method="POST",
                                         data=b'{}', headers={"Content-Type": "application/json",
                                                                "Origin": self.url})
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(failure.exception.code, 401)
        for path in ("/.data/smartdisplay.db", "/security.py", "/scripts/first_run.py"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(self.url + path, timeout=5)
                self.assertEqual(failure.exception.code, 404)

    def test_visibility_columns_and_snapshot(self):
        with sqlite3.connect(self.root / ".data/smartdisplay.db") as database:
            columns = {row[1] for row in database.execute("pragma table_info(tasks)")}
            self.assertIn("visible_from_utc", columns)
            self.assertIn("visible_until_utc", columns)
            self.assertIn("channel", columns)
            start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
            database.execute("""INSERT INTO tasks
                (day,title,dept,priority,task_time,description,image_url,status,created_at,updated_at,
                 version,visible_from_utc,visible_until_utc)
                 VALUES ('Monday','Future notice','General','Normal','','','','pending',?,?,1,?,?)""",
                 (start,start,start,end))
            database.commit()
        _, state = self.get("/api/state")
        notice = state["tasks"]["Monday"][0]
        self.assertEqual(notice["title"], "Future notice")
        self.assertEqual(notice["visibleFromUtc"], start)
        self.assertEqual(notice["visibleUntilUtc"], end)
        self.assertEqual(notice["channel"], "general")


class ChannelValidation(unittest.TestCase):
    def test_valid_and_invalid_slugs(self):
        import sys
        sys.path.insert(0, str(SOURCE))
        import app
        for name in ("general", "lobby", "staff-room-2"):
            with self.subTest(name=name):
                self.assertEqual(app.clean_task({"channel": name}, partial=True)["channel"], name)
        for name in ("Lobby", "../private", "has space", "", "x" * 33):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    app.clean_task({"channel": name}, partial=True)


class ScheduleValidation(unittest.TestCase):
    def test_visibility_range(self):
        sys.path.insert(0, str(SOURCE))
        import security
        start = "2030-01-01T12:00:00Z"
        end = "2030-01-01T13:00:00Z"
        good = security.visibility({"visibleFromUtc": start, "visibleUntilUtc": end})
        self.assertIsNotNone(good["visibleFromUtc"])
        self.assertIsNotNone(good["visibleUntilUtc"])
        for bad in ({"visibleFromUtc": end, "visibleUntilUtc": start},
                    {"visibleFromUtc": start, "visibleUntilUtc": start},
                    {"visibleFromUtc": "2030-01-01", "visibleUntilUtc": end}):
            with self.subTest(bad=bad):
                with self.assertRaises(security.Problem):
                    security.visibility(bad)


if __name__ == "__main__":
    unittest.main()
