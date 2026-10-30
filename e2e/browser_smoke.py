"""Real Chromium smoke verification for brand-neutral TV, channels and visibility."""
from __future__ import annotations
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="smartdisplay-browser-") as temp:
        workspace = Path(temp)
        for filename in ("app.py", "security.py", "index.html", "upgrade.js", "upgrade.css"):
            shutil.copy2(ROOT / filename, workspace / filename)
        (workspace / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/first_run.py", workspace / "scripts/first_run.py")
        subprocess.run([sys.executable, str(workspace / "scripts/first_run.py"),
                        "--site-name", "Demo Display", "--timezone", "America/New_York"],
                       check=True, cwd=workspace, capture_output=True)
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()
        future = (now + timedelta(days=2)).isoformat()
        expired = (now - timedelta(days=2)).isoformat()
        records = [
            ("General active", "general", start, end),
            ("Lobby active", "lobby", start, end),
            ("Not started", "general", future, None),
            ("Already expired", "general", None, expired),
        ]
        with sqlite3.connect(workspace / ".data/smartdisplay.db") as conn:
            for title, channel, visible_from, visible_until in records:
                conn.execute("""INSERT INTO tasks
                    (day,title,dept,priority,task_time,description,image_url,status,
                     created_at,updated_at,version,deadline_utc,visible_from_utc,
                     visible_until_utc,channel)
                    VALUES ('Monday',?,'General','Normal','','','','pending',
                            ?,?,1,?,?,?,?)""",
                    (title, now.isoformat(), now.isoformat(), future,
                     visible_from, visible_until, channel))

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([sys.executable, str(workspace / "app.py"),
                                    "--port", str(port)], cwd=workspace,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError("Server crashed")
                try:
                    with urllib.request.urlopen(url + "/api/health", timeout=1):
                        break
                except OSError:
                    time.sleep(.15)
            else:
                raise RuntimeError("Server did not become ready")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    for channel, expected, absent in [
                        ("general", "General active", ["Lobby active", "Not started", "Already expired"]),
                        ("lobby", "Lobby active", ["General active", "Not started", "Already expired"]),
                    ]:
                        page = browser.new_page(viewport={"width": 1440, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(url + "/?channel=" + channel, wait_until="domcontentloaded")
                        try:
                            page.wait_for_function(
                                "(s)=>document.querySelector('#tv-tasks-container')?.textContent.includes(s)",
                                arg=expected, timeout=12000)
                        except Exception:
                            print("BROWSER DIAGNOSTICS", json.dumps({
                                "url": page.url,
                                "title": page.title(),
                                "javascriptErrors": errors,
                                "pageState": page.evaluate("""() => ({
                                    currentView: typeof currentView !== 'undefined' ? currentView : 'undefined',
                                    selectedDay: typeof selectedOperationalDay !== 'undefined' ? selectedOperationalDay : 'undefined',
                                    timezone: window.displayTimeZone,
                                    taskText: document.querySelector('#tv-tasks-container')?.innerText?.slice(0,1800),
                                    taskCount: Object.keys(typeof tasksData !== 'undefined' ? tasksData : {}).length,
                                    stateVersion: typeof stateVersion !== 'undefined' ? stateVersion : 'undefined'
                                })"""),
                            }, indent=2), flush=True)
                            page.screenshot(path=str(ARTIFACTS / f"debug-{channel}.png"), full_page=True)
                            raise
                        actual = page.locator("#tv-tasks-container").inner_text()
                        if expected not in actual or any(value in actual for value in absent):
                            raise AssertionError(f"Channel {channel} incorrect: {actual}")
                        page.wait_for_function("window.displayTimeZone==='America/New_York'",
                                               timeout=10000)
                        if page.title() != "Demo Display":
                            raise AssertionError("Configured site title not applied")
                        page.screenshot(path=str(ARTIFACTS / f"screen-{channel}.png"), full_page=True)
                        if errors:
                            raise AssertionError(f"Browser JavaScript errors: {errors}")
                        page.close()

                    mobile = browser.new_page(viewport={"width": 390, "height": 844})
                    mobile.goto(url + "/?channel=general", wait_until="domcontentloaded")
                    mobile.wait_for_function(
                        "()=>document.querySelector('#tv-tasks-container')?.textContent.includes('General active')",
                        timeout=25000)
                    if mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
                        raise AssertionError("Horizontal overflow on mobile")
                    mobile.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
                    mobile.close()
                finally:
                    browser.close()
            print("BROWSER ACCEPTANCE PASS: two channels, visibility windows, title, timezone, mobile")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
