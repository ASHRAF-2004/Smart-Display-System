"""Public-production acceptance for the SQLite-backed global operational day."""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENDOR = ROOT / ".tools" / "final-acceptance" / "vendor"
sys.path.insert(0, str(VENDOR))

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402


PUBLIC_URL = "https://thebekal.com/"
LOCAL_URL = "http://127.0.0.1:8787/"
DB_PATH = ROOT / ".data" / "thebekal.db"
APP_LOG = ROOT / ".logs" / "application-server.log"
LOG_DIR = ROOT / ".logs" / "global-day-acceptance"
ARTIFACT_DIR = HERE / "artifacts"
RESULT_PATH = LOG_DIR / "results.json"
RUN_LOG = LOG_DIR / "run.log"
CHROME = Path(r"C:\Program Files\Google\Chrome Dev\Application\chrome.exe")
DAYS = ("Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
TEST_PREFIX = "CODEX-GLOBAL-DAY-TEST-"
FOOTER_TEXT = "© Copyright 2026 YourUni. All rights reserved. Developed by ASHRAF2004"


class AcceptanceFailure(RuntimeError):
    pass


LOG_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str, **details) -> None:
    entry = {"at": utc_now(), "message": message, **details}
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def require(condition: bool, name: str, evidence=None) -> None:
    log("check", name=name, passed=bool(condition), evidence=evidence)
    if not condition:
        raise AcceptanceFailure(f"{name}: {evidence}")


def wait_until(predicate, timeout: float, label: str, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(interval)
    raise AcceptanceFailure(f"Timed out waiting for {label}; last_error={last_error}")


def db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def db_selected_day() -> dict:
    with db_connection() as connection:
        row = connection.execute(
            "SELECT key,value_json,updated_at,version FROM settings WHERE key=?",
            ("selectedDay",),
        ).fetchone()
        return {**dict(row), "value": json.loads(row["value_json"])} if row else {}


def db_state_version() -> int:
    with db_connection() as connection:
        return int(connection.execute("SELECT value FROM metadata WHERE key=?", ("state_version",)).fetchone()[0])


def db_day_task_ids(day: str) -> list[int]:
    with db_connection() as connection:
        return [int(row[0]) for row in connection.execute("SELECT id FROM tasks WHERE day=? ORDER BY id", (day,))]


def db_task(task_id: int):
    with db_connection() as connection:
        row = connection.execute(
            "SELECT id,day,title,dept,priority,task_time,description,status,version FROM tasks WHERE id=?",
            (int(task_id),),
        ).fetchone()
        return dict(row) if row else None


def test_rows() -> list[dict]:
    with db_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT id,title FROM tasks WHERE title LIKE ? ORDER BY id", (TEST_PREFIX + "%",))]


def all_codex_rows() -> list[dict]:
    with db_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT id,title FROM tasks WHERE title LIKE 'CODEX-%' ORDER BY id")]


def sqlite_integrity() -> str:
    with db_connection() as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def api_request(method: str, path: str, payload: dict | None = None, public: bool = False) -> tuple[int, dict]:
    base = PUBLIC_URL if public else LOCAL_URL
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path.lstrip("/"),
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Cache-Control": "no-cache", "User-Agent": "thebekal-global-day-acceptance"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return int(exc.code), body


def http_status(url: str, timeout: float = 10) -> int:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "thebekal-global-day-acceptance"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def cleanup_test_rows() -> list[dict]:
    rows = test_rows()
    for row in rows:
        status, body = api_request("DELETE", f"api/tasks/{int(row['id'])}", {})
        if status not in (200, 400):
            raise AcceptanceFailure(f"cleanup failed for {row}: HTTP {status} {body}")
    wait_until(lambda: not test_rows(), 20, "global-day test row cleanup")
    return rows


def powershell_json(script: str):
    completed = subprocess.run(
        [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'\n" + script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise AcceptanceFailure(f"PowerShell failed ({completed.returncode}): {completed.stderr.strip()}")
    output = completed.stdout.strip()
    return json.loads(output) if output else None


def process_info(kind: str):
    predicates = {
        "app": "$_.Name -eq 'python.exe' -and $_.CommandLine -match '[\\\\/]app\\.py\"?\\s+--port\\s+8787'",
        "cloudflared": "$_.Name -eq 'cloudflared.exe' -and $_.ExecutablePath -like '*Slide show\\.tools\\cloudflared\\cloudflared.exe'",
        "app_watchdog": "$_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'start-local-server\\.ps1'",
        "cloud_watchdog": "$_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'start-cloudflared\\.ps1'",
    }
    predicate = predicates[kind]
    return powershell_json(
        f"""
$p = Get-CimInstance Win32_Process | Where-Object {{ {predicate} }} | Select-Object -First 1
if (-not $p) {{ 'null' }} else {{
  $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner
  [pscustomobject]@{{pid=[int]$p.ProcessId;ppid=[int]$p.ParentProcessId;session=[int]$p.SessionId;owner="$($o.Domain)\\$($o.User)";path=[string]$p.ExecutablePath;command=[string]$p.CommandLine}} | ConvertTo-Json -Compress
}}
"""
    )


def restart_application_child() -> dict:
    old = process_info("app")
    watchdog = process_info("app_watchdog")
    require(bool(old), "application child exists before restart", old)
    require(bool(watchdog), "application watchdog exists before restart", watchdog)
    require(old["owner"].upper() == r"NT AUTHORITY\SYSTEM" and old["session"] == 0, "application child is SYSTEM Session 0", old)
    powershell_json(f"Stop-Process -Id {int(old['pid'])} -Force\n@{{stopped={int(old['pid'])}}} | ConvertTo-Json -Compress")

    def replacement():
        candidate = process_info("app")
        return candidate if candidate and int(candidate["pid"]) != int(old["pid"]) else None

    new = wait_until(replacement, 45, "watchdog replacement application child", 0.5)
    watchdog_after = process_info("app_watchdog")
    require(new["owner"].upper() == r"NT AUTHORITY\SYSTEM" and new["session"] == 0, "replacement child is SYSTEM Session 0", new)
    require(watchdog_after and watchdog_after["pid"] == watchdog["pid"], "application watchdog retained", {"before": watchdog, "after": watchdog_after})
    wait_until(lambda: http_status(LOCAL_URL + "api/health", 2) == 200, 45, "localhost recovery", 0.5)
    wait_until(lambda: http_status(PUBLIC_URL + "api/health", 5) == 200, 90, "public recovery", 1)
    return {"old": old, "new": new, "watchdog": watchdog_after}


def app_log_count(fragment: str) -> int:
    return APP_LOG.read_text(encoding="utf-8", errors="replace").count(fragment)


INIT_PROBE = r"""
(() => {
  const NativeEventSource = window.EventSource;
  const NativeFetch = window.fetch.bind(window);
  const probe = {
    eventSourceConstructions: 0,
    nativeTransportConstructions: 0,
    activeNativeTransports: 0,
    maxActiveNativeTransports: 0,
    readyEvents: 0,
    stateEvents: 0,
    stateFetchRequests: 0,
    stateFetchResponses200: 0,
    selectedDayMutations: 0,
    token: `${Date.now()}-${Math.random()}-${crypto.randomUUID ? crypto.randomUUID() : ''}`
  };
  window.fetch = (...args) => {
    let pathname = '';
    let method = 'GET';
    try {
      const raw = typeof args[0] === 'string' ? args[0] : args[0].url;
      pathname = new URL(raw, location.href).pathname;
      method = String((args[1] && args[1].method) || (args[0] && args[0].method) || 'GET').toUpperCase();
    } catch {}
    if (pathname === '/api/state') probe.stateFetchRequests += 1;
    if (pathname === '/api/selected-day' && method === 'PUT') probe.selectedDayMutations += 1;
    return NativeFetch(...args).then(response => {
      if (pathname === '/api/state' && response.status === 200) probe.stateFetchResponses200 += 1;
      return response;
    });
  };
  let logicalSource = null;
  class InstrumentedEventSource extends EventTarget {
    constructor(url, options) {
      super();
      probe.eventSourceConstructions += 1;
      this.url = String(url);
      this.withCredentials = !!(options && options.withCredentials);
      this._options = options;
      this._native = null;
      this._faulted = false;
      this._closed = false;
      logicalSource = this;
      this._openTransport();
    }
    _openTransport() {
      if (this._native) this._closeTransport();
      const native = new NativeEventSource(this.url, this._options);
      this._native = native;
      probe.nativeTransportConstructions += 1;
      probe.activeNativeTransports += 1;
      probe.maxActiveNativeTransports = Math.max(probe.maxActiveNativeTransports, probe.activeNativeTransports);
      const forward = type => native.addEventListener(type, event => {
        if (this._native !== native || this._faulted || this._closed) return;
        if (type === 'ready') probe.readyEvents += 1;
        if (type === 'state') probe.stateEvents += 1;
        const forwarded = event instanceof MessageEvent
          ? new MessageEvent(type, {data:event.data, origin:event.origin, lastEventId:event.lastEventId})
          : new Event(type);
        this.dispatchEvent(forwarded);
        const handler = this[`on${type}`];
        if (typeof handler === 'function') handler.call(this, forwarded);
      });
      ['open', 'message', 'ready', 'state', 'heartbeat', 'error'].forEach(forward);
    }
    _closeTransport() {
      if (!this._native) return;
      this._native.close();
      this._native = null;
      probe.activeNativeTransports = Math.max(0, probe.activeNativeTransports - 1);
    }
    _fault() { this._faulted = true; this._closeTransport(); }
    _restore() {
      if (this._closed || !this._faulted) return;
      this._faulted = false;
      this._openTransport();
    }
    close() { this._closed = true; this._closeTransport(); }
    get readyState() { return this._native ? this._native.readyState : NativeEventSource.CLOSED; }
  }
  ['CONNECTING', 'OPEN', 'CLOSED'].forEach(name => {
    Object.defineProperty(InstrumentedEventSource, name, {value: NativeEventSource[name]});
    Object.defineProperty(InstrumentedEventSource.prototype, name, {value: NativeEventSource[name]});
  });
  probe.faultSse = () => logicalSource && logicalSource._fault();
  probe.restoreSse = () => logicalSource && logicalSource._restore();
  window.EventSource = InstrumentedEventSource;
  window.__acceptanceProbe = probe;
})();
"""


def make_context(browser: Browser, viewport: tuple[int, int]) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        device_scale_factor=1,
        locale="en-US",
        timezone_id="Asia/Kuala_Lumpur",
        color_scheme="dark",
        reduced_motion="reduce",
        ignore_https_errors=False,
    )
    context.add_init_script(INIT_PROBE)
    return context


def attach_telemetry(page: Page, name: str, telemetry: dict) -> None:
    record = telemetry[name] = {
        "stateRequests": 0,
        "stateResponses200": 0,
        "eventRequests": 0,
        "selectedDayMutations": 0,
        "taskMutations": 0,
        "navigations": 0,
        "consoleErrors": [],
        "pageErrors": [],
    }

    def requested(request) -> None:
        path = urlparse(request.url).path
        method = request.method.upper()
        if path == "/api/state":
            record["stateRequests"] += 1
        elif path == "/api/events":
            record["eventRequests"] += 1
        elif path == "/api/selected-day" and method == "PUT":
            record["selectedDayMutations"] += 1
        elif path.startswith("/api/tasks") and method in ("POST", "PATCH", "DELETE"):
            record["taskMutations"] += 1

    def responded(response) -> None:
        if urlparse(response.url).path == "/api/state" and response.status == 200:
            record["stateResponses200"] += 1

    def navigated(frame) -> None:
        if frame == page.main_frame:
            record["navigations"] += 1

    page.on("request", requested)
    page.on("response", responded)
    page.on("framenavigated", navigated)
    page.on("console", lambda message: record["consoleErrors"].append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: record["pageErrors"].append(str(error)))


def probe(page: Page) -> dict:
    return page.evaluate(
        """() => { const p=window.__acceptanceProbe; return {
          eventSourceConstructions:p.eventSourceConstructions,
          nativeTransportConstructions:p.nativeTransportConstructions,
          activeNativeTransports:p.activeNativeTransports,
          maxActiveNativeTransports:p.maxActiveNativeTransports,
          readyEvents:p.readyEvents,stateEvents:p.stateEvents,
          stateFetchRequests:p.stateFetchRequests,stateFetchResponses200:p.stateFetchResponses200,
          selectedDayMutations:p.selectedDayMutations,token:p.token
        }; }"""
    )


def load_public(page: Page) -> None:
    page.goto(PUBLIC_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_function(
        "() => typeof selectedOperationalDay === 'string' && TASK_DAYS.includes(selectedOperationalDay) && window.__acceptanceProbe.stateFetchResponses200 > 0",
        timeout=30_000,
    )
    page.wait_for_timeout(250)


def page_day(page: Page) -> dict:
    return page.evaluate(
        """() => ({
          selectedDay:selectedOperationalDay,
          dayIndex:currentDayIndex,
          selectorValue:document.querySelector('#admin-day-select')?.value,
          selectorText:document.querySelector('#admin-day-select')?.selectedOptions?.[0]?.textContent,
          tvTitle:document.querySelector('#tv-day-title')?.textContent,
          adminLabel:document.querySelector('#admin-current-day-label')?.textContent,
          view:currentView,
          presentation:document.querySelector('#view-tv')?.classList.contains('tv-presentation-mode'),
          fullscreenElement:document.fullscreenElement?.id || null,
          theme:document.documentElement.dataset.theme
        })"""
    )


def wait_page_day(page: Page, day: str, timeout: float = 30) -> dict:
    expected_index = DAYS.index(day)

    def current():
        value = page_day(page)
        return value if value["selectedDay"] == day and value["dayIndex"] == expected_index and value["selectorValue"] == str(expected_index) else None

    return wait_until(current, timeout, f"page selected day {day}")


def rendered_task_ids(page: Page, view: str) -> list[int]:
    selector = "#tv-tasks-container [data-task-id]" if view == "tv" else "#admin-tasks-list [data-task-id]"
    return sorted(int(value) for value in page.locator(selector).evaluate_all("elements => elements.map(element => element.dataset.taskId)"))


def assert_day_everywhere(pages: dict[str, Page], day: str) -> dict:
    states = {name: wait_page_day(page, day) for name, page in pages.items()}
    expected = db_day_task_ids(day)
    for name, page in pages.items():
        view = "tv" if name == "tv" else "admin"
        wait_until(lambda page=page, view=view: rendered_task_ids(page, view) == expected, 20, f"{name} renders {day} task IDs")
    rendered = {name: rendered_task_ids(page, "tv" if name == "tv" else "admin") for name, page in pages.items()}
    require(all(ids == expected for ids in rendered.values()), f"all clients render authoritative {day} content", {"expected": expected, "rendered": rendered})
    return {"states": states, "taskIds": expected, "rendered": rendered}


def successful_day_change(
    origin_name: str,
    origin: Page,
    pages: dict[str, Page],
    telemetry: dict,
    target_day: str,
) -> dict:
    before_requests = {name: data["selectedDayMutations"] for name, data in telemetry.items() if name in pages}
    before_probe = {name: probe(page)["selectedDayMutations"] for name, page in pages.items()}
    before_http = app_log_count('"PUT /api/selected-day HTTP/1.1" 200')
    before_commit = app_log_count("mutation committed operation=selected_day_changed")
    before_broadcast = app_log_count("broadcast event=state")

    with origin.expect_response(
        lambda response: urlparse(response.url).path == "/api/selected-day" and response.request.method == "PUT",
        timeout=30_000,
    ) as response_info:
        origin.select_option("#admin-day-select", str(DAYS.index(target_day)))
    response = response_info.value
    body = response.json()
    require(response.status == 200, f"{origin_name} day mutation returned HTTP 200", {"status": response.status, "body": body})
    require(body.get("operation") == "selected_day_changed" and body.get("selectedDay") == target_day, "committed day response is revisioned", body)

    convergence = assert_day_everywhere(pages, target_day)
    for page in pages.values():
        page.wait_for_timeout(250)
    time.sleep(0.5)
    after_requests = {name: data["selectedDayMutations"] for name, data in telemetry.items() if name in pages}
    after_probe = {name: probe(page)["selectedDayMutations"] for name, page in pages.items()}
    request_delta = {name: after_requests[name] - before_requests[name] for name in pages}
    probe_delta = {name: after_probe[name] - before_probe[name] for name in pages}
    http_delta = app_log_count('"PUT /api/selected-day HTTP/1.1" 200') - before_http
    commit_delta = app_log_count("mutation committed operation=selected_day_changed") - before_commit
    broadcast_delta = app_log_count("broadcast event=state") - before_broadcast
    require(request_delta.get(origin_name) == 1 and sum(request_delta.values()) == 1, "one user change creates exactly one selected-day PUT", request_delta)
    require(probe_delta.get(origin_name) == 1 and sum(probe_delta.values()) == 1, "programmatic selector synchronization creates no mutation loop", probe_delta)
    require(http_delta == 1 and commit_delta == 1 and broadcast_delta == 1, "one PUT produces one commit and one SSE broadcast", {"http": http_delta, "commit": commit_delta, "broadcast": broadcast_delta})
    stored = db_selected_day()
    require(stored.get("value") == target_day, "selected day physically persisted in SQLite", stored)
    return {"origin": origin_name, "target": target_day, "response": body, "requestDelta": request_delta, "probeDelta": probe_delta, "serverLogDelta": {"http": http_delta, "commit": commit_delta, "broadcast": broadcast_delta}, "sqlite": stored, "convergence": convergence}


def browser_task_patch(page: Page, task_id: int, changes: dict) -> dict:
    result = page.evaluate(
        """async ({id,changes}) => {
          const response=await fetch(`/api/tasks/${encodeURIComponent(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)});
          return {status:response.status,body:await response.json()};
        }""",
        {"id": int(task_id), "changes": changes},
    )
    require(result["status"] == 200, "task PATCH returned HTTP 200", result)
    return result["body"]


def page_task(page: Page, task_id: int):
    return page.evaluate(
        "id => Object.values(tasksData).flat().find(task => Number(task.id) === Number(id)) || null",
        int(task_id),
    )


def wait_task(page: Page, task_id: int, title: str | None = None, status: str | None = None, exists: bool = True):
    def current():
        task = page_task(page, task_id)
        if not exists:
            return task is None
        if not task or (title is not None and task.get("title") != title) or (status is not None and task.get("status") != status):
            return False
        return task

    return wait_until(current, 30, f"task {task_id} exists={exists} title={title} status={status}")


def run() -> dict:
    RUN_LOG.write_text("", encoding="utf-8")
    started = utc_now()
    results = {
        "startedAt": started,
        "globalSelectedDay": "FAIL",
        "selectedDaySQLitePersistence": "FAIL",
        "selectedDayLiveManagerToTv": "FAIL",
        "selectedDayLiveMobileToTv": "FAIL",
        "selectedDayPresentationMode": "FAIL",
        "selectedDayCleanBrowser": "FAIL",
        "selectedDayRestartPersistence": "FAIL",
        "selectedDayReconnectResync": "FAIL",
        "selectedDayEventLoopProtection": "FAIL",
        "taskCrudRealtimeRegression": "FAIL",
        "mobile390": "FAIL",
        "themeSeparation": "FAIL",
        "saveAllAbsent": "FAIL",
        "attributionIntact": "FAIL",
        "databaseIntegrity": "FAIL",
        "publicHttp200": False,
        "overall": "FAIL",
        "evidence": {},
    }
    original_day = None
    browser = None
    contexts: list[BrowserContext] = []
    pages: dict[str, Page] = {}
    telemetry: dict = {}
    try:
        removed_before = cleanup_test_rows()
        require(not all_codex_rows(), "no pre-existing CODEX rows", all_codex_rows())
        initial_status, initial_state = api_request("GET", "api/state")
        require(initial_status == 200 and initial_state.get("selectedDay") in DAYS, "authoritative state includes valid selectedDay", initial_state)
        original_day = initial_state["selectedDay"]
        initial_setting = db_selected_day()
        require(initial_setting.get("value") == original_day, "initial selected day is SQLite-backed", initial_setting)

        invalid_version = db_state_version()
        invalid_status, invalid_body = api_request("PUT", "api/selected-day", {"selectedDay": "Funday"})
        require(invalid_status == 400 and invalid_body.get("error") == "invalid selected day", "server rejects unsupported day", {"status": invalid_status, "body": invalid_body})
        require(db_state_version() == invalid_version and db_selected_day().get("value") == original_day, "invalid day cannot mutate SQLite or revision", {"before": invalid_version, "after": db_state_version(), "setting": db_selected_day()})
        results["evidence"]["validation"] = {"status": invalid_status, "body": invalid_body, "revision": invalid_version}

        require(CHROME.exists(), "Chrome Dev executable exists", str(CHROME))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(CHROME),
                headless=True,
                args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "--disable-features=CalculateNativeWinOcclusion"],
            )
            specs = {"manager": (1440, 900), "mobile": (390, 844), "tv": (1920, 1080)}
            for name, viewport in specs.items():
                context = make_context(browser, viewport)
                contexts.append(context)
                page = context.new_page()
                attach_telemetry(page, name, telemetry)
                pages[name] = page
                load_public(page)

            pages["manager"].evaluate("() => switchView('admin')")
            pages["mobile"].evaluate("() => switchView('admin')")
            pages["tv"].evaluate("() => switchView('tv')")
            initial_convergence = assert_day_everywhere(pages, original_day)
            require(len({probe(page)["token"] for page in pages.values()}) == 3, "three independent public browser contexts", {name: probe(page)["token"] for name, page in pages.items()})
            results["evidence"]["initialConvergence"] = initial_convergence

            pages["manager"].evaluate("() => applyTheme('light', true)")
            pages["mobile"].evaluate("() => applyTheme('dark', true)")
            require(page_day(pages["manager"])["theme"] == "light" and page_day(pages["mobile"])["theme"] == "dark", "theme preferences differ by device", {name: page_day(page)["theme"] for name, page in pages.items()})

            manager_target = "Thursday" if original_day != "Thursday" else "Wednesday"
            manager_change = successful_day_change("manager", pages["manager"], pages, telemetry, manager_target)
            results["selectedDayLiveManagerToTv"] = "PASS"
            results["selectedDaySQLitePersistence"] = "PASS"

            mobile_target = "Friday" if manager_target != "Friday" else "Monday"
            mobile_change = successful_day_change("mobile", pages["mobile"], pages, telemetry, mobile_target)
            results["selectedDayLiveMobileToTv"] = "PASS"
            require(page_day(pages["manager"])["theme"] == "light" and page_day(pages["mobile"])["theme"] == "dark", "selected day does not synchronize browser-local theme", {name: page_day(page)["theme"] for name, page in pages.items()})
            results["themeSeparation"] = "PASS"

            mobile_metrics = pages["mobile"].evaluate(
                """() => { const root=document.documentElement; const select=document.querySelector('#admin-day-select'); const r=select.getBoundingClientRect(); return {
                  innerWidth,clientWidth:root.clientWidth,scrollWidth:root.scrollWidth,bodyScrollWidth:document.body.scrollWidth,
                  selector:{left:r.left,right:r.right,width:r.width,height:r.height,visible:!!select.offsetParent,value:select.value}
                }; }"""
            )
            require(mobile_metrics["scrollWidth"] == mobile_metrics["clientWidth"] == 390 and mobile_metrics["bodyScrollWidth"] <= 390, "390px mobile has no page-level overflow", mobile_metrics)
            require(mobile_metrics["selector"]["visible"] and mobile_metrics["selector"]["left"] >= 0 and mobile_metrics["selector"]["right"] <= 390 and mobile_metrics["selector"]["height"] >= 44, "mobile global day selector is visible and touch-usable", mobile_metrics)
            pages["mobile"].screenshot(path=str(ARTIFACT_DIR / "mobile-390-global-day.png"), full_page=True, animations="disabled")
            results["mobile390"] = "PASS"

            tv_page = pages["tv"]
            tv_page.bring_to_front()
            tv_page.locator("#enter-tv-presentation-btn").click()
            wait_until(lambda: page_day(tv_page)["presentation"], 10, "TV Presentation Mode")
            wait_until(lambda: page_day(tv_page)["fullscreenElement"] == "view-tv", 10, "native TV fullscreen")
            presentation_target = "Wednesday" if mobile_target != "Wednesday" else "Tuesday"
            presentation_change = successful_day_change("manager", pages["manager"], pages, telemetry, presentation_target)
            presentation_state = page_day(tv_page)
            require(presentation_state["presentation"] and presentation_state["fullscreenElement"] == "view-tv", "day change does not exit TV fullscreen/presentation", presentation_state)
            tv_page.screenshot(path=str(ARTIFACT_DIR / "tv-presentation-global-day.png"), full_page=False, animations="disabled")
            tv_page.locator("#exit-tv-presentation-btn").click()
            wait_until(lambda: not page_day(tv_page)["presentation"] and page_day(tv_page)["fullscreenElement"] is None, 10, "TV presentation exit")
            results["selectedDayPresentationMode"] = "PASS"

            clean_target = "Saturday"
            clean_change = successful_day_change("mobile", pages["mobile"], pages, telemetry, clean_target)
            clean_context = make_context(browser, (1280, 720))
            contexts.append(clean_context)
            clean_page = clean_context.new_page()
            clean_telemetry: dict = {}
            attach_telemetry(clean_page, "clean", clean_telemetry)
            load_public(clean_page)
            clean_state = wait_page_day(clean_page, clean_target)
            require(clean_telemetry["clean"]["stateResponses200"] >= 1, "clean browser retrieves authoritative /api/state", clean_telemetry["clean"])
            require(clean_state["selectedDay"] == clean_target, "clean browser starts on server selected day", clean_state)
            clean_context.close()
            contexts.remove(clean_context)
            results["selectedDayCleanBrowser"] = "PASS"

            restart_target = "Friday"
            restart_change = successful_day_change("manager", pages["manager"], pages, telemetry, restart_target)
            before_restart_probes = {name: probe(page) for name, page in pages.items()}
            restart = restart_application_child()
            require(db_selected_day().get("value") == restart_target, "selected day survives application restart in SQLite", db_selected_day())
            status, restarted_state = api_request("GET", "api/state")
            require(status == 200 and restarted_state.get("selectedDay") == restart_target, "replacement app serves persisted selected day", restarted_state)
            for name, page in pages.items():
                wait_until(
                    lambda page=page, name=name: probe(page)["readyEvents"] > before_restart_probes[name]["readyEvents"] and probe(page)["stateFetchResponses200"] > before_restart_probes[name]["stateFetchResponses200"],
                    60,
                    f"{name} SSE reconnect and authoritative reconciliation",
                    0.5,
                )
            restart_convergence = assert_day_everywhere(pages, restart_target)
            restart_clean_context = make_context(browser, (1024, 768))
            contexts.append(restart_clean_context)
            restart_clean_page = restart_clean_context.new_page()
            load_public(restart_clean_page)
            restart_clean_state = wait_page_day(restart_clean_page, restart_target)
            restart_clean_context.close()
            contexts.remove(restart_clean_context)
            results["selectedDayRestartPersistence"] = "PASS"

            before_fault = probe(tv_page)
            tv_page.evaluate("() => window.__acceptanceProbe.faultSse()")
            wait_until(lambda: probe(tv_page)["activeNativeTransports"] == 0, 5, "only TV SSE transport faulted")
            disconnected_target = "Monday" if restart_target != "Monday" else "Thursday"
            disconnected_change = successful_day_change("manager", pages["manager"], {"manager": pages["manager"], "mobile": pages["mobile"]}, telemetry, disconnected_target)
            tv_page.wait_for_timeout(700)
            disconnected_tv_state = page_day(tv_page)
            require(disconnected_tv_state["selectedDay"] == restart_target, "faulted TV intentionally remains on prior day", disconnected_tv_state)
            require(db_selected_day().get("value") == disconnected_target, "server advances while TV SSE is faulted", db_selected_day())
            tv_page.evaluate("() => window.__acceptanceProbe.restoreSse()")
            wait_until(lambda: probe(tv_page)["activeNativeTransports"] == 1, 5, "TV SSE transport restored")
            wait_until(lambda: probe(tv_page)["readyEvents"] > before_fault["readyEvents"] and probe(tv_page)["stateFetchResponses200"] > before_fault["stateFetchResponses200"], 30, "TV reconnect authoritative reconciliation")
            restored_tv = wait_page_day(tv_page, disconnected_target)
            require(rendered_task_ids(tv_page, "tv") == db_day_task_ids(disconnected_target), "restored TV renders authoritative day content", {"rendered": rendered_task_ids(tv_page, "tv"), "sqlite": db_day_task_ids(disconnected_target)})
            follow_target = "Tuesday" if disconnected_target != "Tuesday" else "Thursday"
            follow_change = successful_day_change("mobile", pages["mobile"], pages, telemetry, follow_target)
            results["selectedDayReconnectResync"] = "PASS"

            crud_day = "Wednesday"
            crud_day_change = successful_day_change("manager", pages["manager"], pages, telemetry, crud_day)
            title = TEST_PREFIX + datetime.now().strftime("%Y%m%d%H%M%S")
            mobile = pages["mobile"]
            mobile.evaluate("() => switchView('admin')")
            mobile.locator("#task-title").fill(title)
            mobile.locator("#task-dept").select_option("General Office")
            mobile.locator("#task-priority").select_option("Important")
            mobile.locator("#task-time").fill("03:15 PM - 04:00 PM")
            mobile.locator("#task-desc").fill("Global selected-day live CRUD regression")
            with mobile.expect_response(lambda response: urlparse(response.url).path == "/api/tasks" and response.request.method == "POST", timeout=30_000) as create_info:
                mobile.locator("#add-task-form button[type=submit]").click()
            create_response = create_info.value
            create_body = create_response.json()
            require(create_response.status == 201 and create_body.get("operation") == "created", "mobile UI create committed immediately", create_body)
            task_id = int(create_body["result"])
            wait_until(lambda: db_task(task_id) and db_task(task_id)["title"] == title, 10, "created task in SQLite")
            for page in pages.values():
                wait_task(page, task_id, title=title)
            require(all(page.locator(f'[data-task-id="{task_id}"]').count() == 1 for page in pages.values()), "created task renders once in every active client", {name: page.locator(f'[data-task-id="{task_id}"]').count() for name, page in pages.items()})

            updated_title = title + "-UPDATED"
            update_body = browser_task_patch(pages["manager"], task_id, {"title": updated_title})
            wait_until(lambda: db_task(task_id) and db_task(task_id)["title"] == updated_title, 10, "updated task in SQLite")
            for page in pages.values():
                wait_task(page, task_id, title=updated_title)

            mobile.evaluate("id => toggleTaskStatus(id)", task_id)
            wait_until(lambda: db_task(task_id) and db_task(task_id)["status"] == "completed", 15, "status change in SQLite")
            for page in pages.values():
                wait_task(page, task_id, title=updated_title, status="completed")

            mobile.evaluate("id => deleteTask(id)", task_id)
            wait_until(lambda: db_task(task_id) is None, 15, "deleted task absent from SQLite")
            for page in pages.values():
                wait_task(page, task_id, exists=False)
                wait_until(lambda page=page: page.locator(f'[data-task-id="{task_id}"]').count() == 0, 15, "deleted task absent from DOM")
            results["taskCrudRealtimeRegression"] = "PASS"

            restore_change = successful_day_change("manager", pages["manager"], pages, telemetry, original_day)
            pages["manager"].evaluate("() => applyTheme('light', true)")
            pages["mobile"].evaluate("() => applyTheme('dark', true)")
            pages["manager"].reload(wait_until="domcontentloaded", timeout=60_000)
            pages["mobile"].reload(wait_until="domcontentloaded", timeout=60_000)
            wait_page_day(pages["manager"], original_day)
            wait_page_day(pages["mobile"], original_day)
            require(page_day(pages["manager"])["theme"] == "light" and page_day(pages["mobile"])["theme"] == "dark", "theme remains device-local after refresh", {"manager": page_day(pages["manager"]), "mobile": page_day(pages["mobile"])})
            results["themeSeparation"] = "PASS"

            mobile.evaluate("() => switchView('admin')")
            final_mobile = mobile.evaluate("() => ({clientWidth:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,selectedDay,selector:document.querySelector('#admin-day-select').value})".replace("selectedDay", "selectedDay:selectedOperationalDay"))
            require(final_mobile["clientWidth"] == final_mobile["scrollWidth"] == 390, "mobile remains overflow-free after refresh", final_mobile)
            results["mobile390"] = "PASS"

            source_text = (ROOT / "youruni_tv_task_management_system update 17 Aug.html").read_text(encoding="utf-8")
            save_all_count = sum(page.get_by_text("Save All", exact=False).count() for page in pages.values())
            require("Save All" not in source_text and save_all_count == 0, "Save All remains absent", {"source": source_text.count("Save All"), "visible": save_all_count})
            results["saveAllAbsent"] = "PASS"
            for name, page in pages.items():
                footer = page.locator("#youruni-site-attribution")
                require(footer.count() == 1 and FOOTER_TEXT in footer.inner_text(), f"attribution intact in {name}", footer.inner_text() if footer.count() else None)
            require("YOURUNI PROJECT ATTRIBUTION" in source_text and source_text.count(FOOTER_TEXT) == 1, "attribution source protection remains intact")
            results["attributionIntact"] = "PASS"

            final_probes = {name: probe(page) for name, page in pages.items()}
            require(all(item["activeNativeTransports"] == 1 and item["maxActiveNativeTransports"] <= 1 for item in final_probes.values()), "one active SSE transport per page", final_probes)
            require(all(not telemetry[name]["pageErrors"] for name in pages), "no browser page errors", {name: telemetry[name]["pageErrors"] for name in pages})
            results["selectedDayEventLoopProtection"] = "PASS"

            cleanup_test_rows()
            require(not all_codex_rows(), "no CODEX rows remain after acceptance", all_codex_rows())
            integrity = sqlite_integrity()
            require(integrity == "ok", "SQLite integrity_check is ok", integrity)
            results["databaseIntegrity"] = "PASS"

            local_code = http_status(LOCAL_URL)
            public_code = http_status(PUBLIC_URL)
            require(local_code == 200 and public_code == 200, "local and public application return HTTP 200", {"local": local_code, "public": public_code})
            sensitive = {path: http_status(PUBLIC_URL.rstrip("/") + path) for path in ("/.data/", "/.tools/", "/.logs/", "/.backup/", "/DEPLOYMENT.md")}
            require(all(code == 404 for code in sensitive.values()), "sensitive public paths remain blocked", sensitive)
            app = process_info("app")
            cloud = process_info("cloudflared")
            app_watchdog = process_info("app_watchdog")
            cloud_watchdog = process_info("cloud_watchdog")
            require(all(item and item["owner"].upper() == r"NT AUTHORITY\SYSTEM" and item["session"] == 0 for item in (app, cloud, app_watchdog, cloud_watchdog)), "production children and watchdogs remain SYSTEM Session 0", {"app": app, "cloud": cloud, "appWatchdog": app_watchdog, "cloudWatchdog": cloud_watchdog})
            listener = powershell_json(
                f"$c=Get-NetTCPConnection -State Listen -OwningProcess {int(app['pid'])}; $c | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress"
            )
            listener_rows = listener if isinstance(listener, list) else [listener]
            require(listener_rows and all(row["LocalAddress"] == "127.0.0.1" and int(row["LocalPort"]) == 8787 for row in listener_rows), "application remains loopback-only", listener_rows)
            results["publicHttp200"] = True

            results["globalSelectedDay"] = "PASS"
            results["selectedDaySQLitePersistence"] = "PASS"
            results["selectedDayEventLoopProtection"] = "PASS"
            results["overall"] = "PASS"
            results["evidence"].update(
                {
                    "originalDayRestored": original_day,
                    "managerChange": manager_change,
                    "mobileChange": mobile_change,
                    "presentationChange": presentation_change,
                    "cleanBrowser": {"change": clean_change, "state": clean_state},
                    "restart": {"change": restart_change, "process": restart, "convergence": restart_convergence, "newBrowser": restart_clean_state},
                    "disconnect": {"change": disconnected_change, "faultedTv": disconnected_tv_state, "restoredTv": restored_tv, "nextLiveChange": follow_change},
                    "crud": {"dayChange": crud_day_change, "create": create_body, "update": update_body, "deletedId": task_id},
                    "restore": restore_change,
                    "mobile": mobile_metrics,
                    "telemetry": telemetry,
                    "finalProbes": final_probes,
                    "sqliteSelectedDay": db_selected_day(),
                    "sqliteIntegrity": integrity,
                    "http": {"local": local_code, "public": public_code, "sensitive": sensitive},
                    "infrastructure": {"app": app, "cloudflared": cloud, "appWatchdog": app_watchdog, "cloudWatchdog": cloud_watchdog, "listener": listener_rows},
                    "cleanupBefore": removed_before,
                    "cleanupAfter": all_codex_rows(),
                }
            )
    except Exception as exc:
        results["error"] = repr(exc)
        results["traceback"] = traceback.format_exc()
        log("acceptance failed", error=repr(exc), traceback=results["traceback"])
    finally:
        try:
            cleanup_test_rows()
        except Exception as cleanup_error:
            results.setdefault("cleanupErrors", []).append(repr(cleanup_error))
        try:
            if original_day in DAYS and db_selected_day().get("value") != original_day:
                status, body = api_request("PUT", "api/selected-day", {"selectedDay": original_day})
                if status != 200:
                    results.setdefault("cleanupErrors", []).append(f"day restore HTTP {status}: {body}")
        except Exception as restore_error:
            results.setdefault("cleanupErrors", []).append(repr(restore_error))
        for context in reversed(contexts):
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        results["finishedAt"] = utc_now()
        results["remainingTestRows"] = test_rows()
        results["selectedDayAfterCleanup"] = db_selected_day() if DB_PATH.exists() else None
        results["overall"] = "PASS" if results.get("overall") == "PASS" and not results.get("cleanupErrors") and not results["remainingTestRows"] else "FAIL"
        RESULT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        log("acceptance complete", overall=results["overall"], result=str(RESULT_PATH))
    return results


if __name__ == "__main__":
    outcome = run()
    print(json.dumps({"overall": outcome["overall"], "result": str(RESULT_PATH), "error": outcome.get("error")}, ensure_ascii=False))
    raise SystemExit(0 if outcome["overall"] == "PASS" else 1)
