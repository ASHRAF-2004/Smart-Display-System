"""Final public-production acceptance harness for thebekal.com.

Runs three independent public Chromium contexts, faults only the retained TV,
exercises ID-based realtime reconciliation, restarts both production children,
compares the untouched desktop baseline, and writes machine-readable evidence.
"""

from __future__ import annotations

import json
import math
import os
import socket
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
VENDOR = HERE / "vendor"
sys.path.insert(0, str(VENDOR))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright  # noqa: E402


PUBLIC_URL = "https://thebekal.com/"
LOCAL_URL = "http://127.0.0.1:8787/"
DB_PATH = ROOT / ".data" / "thebekal.db"
PYTHON = ROOT / ".tools" / "python" / "python.exe"
CHROME = Path(r"C:\Program Files\Google\Chrome Dev\Application\chrome.exe")
BASELINE_SERVER = ROOT / ".tools" / "visual-baseline" / "server.py"
LOG_DIR = ROOT / ".logs" / "final-acceptance"
ARTIFACT_DIR = HERE / "artifacts"
RESULT_PATH = LOG_DIR / "results.json"
RUN_LOG = LOG_DIR / "run.log"
TASK_DAYS = ("Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


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
        except Exception as exc:  # transient states are retained for evidence
            last_error = repr(exc)
        time.sleep(interval)
    raise AcceptanceFailure(f"Timed out waiting for {label}; last_error={last_error}")


def db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def db_task(task_id: int):
    with db_connection() as connection:
        row = connection.execute(
            "SELECT id,day,title,dept,priority,task_time,description,image_url,status,version FROM tasks WHERE id=?",
            (int(task_id),),
        ).fetchone()
        return dict(row) if row else None


def db_title_rows(title: str):
    with db_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM tasks WHERE title=? ORDER BY id", (title,))]


def db_id_count(task_id: int) -> int:
    with db_connection() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (int(task_id),)).fetchone()[0])


def codex_rows():
    with db_connection() as connection:
        return [dict(row) for row in connection.execute("SELECT id,title FROM tasks WHERE title LIKE 'CODEX-%' ORDER BY id")]


def state_version() -> int:
    with db_connection() as connection:
        return int(connection.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0])


def http_status(url: str, timeout: float = 10) -> int:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "thebekal-final-acceptance"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def local_api_delete(task_id: int) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:8787/api/tasks/{int(task_id)}",
        data=b"{}",
        method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


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
    if kind == "app":
        predicate = "$_.Name -eq 'python.exe' -and $_.CommandLine -match '[\\\\/]app\\.py\"?\\s+--port\\s+8787'"
    elif kind == "cloudflared":
        predicate = "$_.Name -eq 'cloudflared.exe' -and $_.ExecutablePath -like '*Slide show\\.tools\\cloudflared\\cloudflared.exe'"
    elif kind == "app_watchdog":
        predicate = "$_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'start-local-server\\.ps1'"
    elif kind == "cloud_watchdog":
        predicate = "$_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'start-cloudflared\\.ps1'"
    else:
        raise ValueError(kind)
    script = f"""
$p = Get-CimInstance Win32_Process | Where-Object {{ {predicate} }} | Select-Object -First 1
if (-not $p) {{ 'null' }} else {{
  $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner
  [pscustomobject]@{{pid=[int]$p.ProcessId;ppid=[int]$p.ParentProcessId;session=[int]$p.SessionId;owner=\"$($o.Domain)\\$($o.User)\";path=[string]$p.ExecutablePath;command=[string]$p.CommandLine}} | ConvertTo-Json -Compress
}}
"""
    return powershell_json(script)


def stop_process(pid: int) -> None:
    powershell_json(f"Stop-Process -Id {int(pid)} -Force\n@{{stopped={int(pid)}}} | ConvertTo-Json -Compress")


def acceptance_probe(page: Page) -> dict:
    return page.evaluate("() => { const p=window.__acceptanceProbe; return {eventSourceConstructions:p.eventSourceConstructions,nativeTransportConstructions:p.nativeTransportConstructions,activeNativeTransports:p.activeNativeTransports,maxActiveNativeTransports:p.maxActiveNativeTransports,readyEvents:p.readyEvents,stateEvents:p.stateEvents,stateFetchRequests:p.stateFetchRequests,stateFetchResponses200:p.stateFetchResponses200,token:p.token}; }")


def fault_only_tv_sse(page: Page, telemetry: dict):
    before = acceptance_probe(page)
    page.evaluate("() => window.__acceptanceProbe.faultSse()")
    wait_until(lambda: page.evaluate("() => window.__acceptanceProbe.activeNativeTransports") == 0, 5, "TV SSE transport closed")
    return {"beforeProbe": before, "beforeRequests": telemetry["eventRequests"], "beforeFailures": telemetry["failedEventRequests"]}


def restore_tv_sse(page: Page, _fault: dict) -> None:
    page.evaluate("() => window.__acceptanceProbe.restoreSse()")
    wait_until(lambda: page.evaluate("() => window.__acceptanceProbe.activeNativeTransports") == 1, 5, "TV SSE transport restored")


def restart_child(kind: str, public_required: bool = True) -> dict:
    old = process_info(kind)
    require(bool(old), f"{kind} child exists before restart", old)
    require(old["owner"].upper() == r"NT AUTHORITY\SYSTEM", f"{kind} child runs as SYSTEM", old)
    watchdog_kind = "app_watchdog" if kind == "app" else "cloud_watchdog"
    watchdog_before = process_info(watchdog_kind)
    require(bool(watchdog_before), f"{kind} watchdog exists", watchdog_before)
    stop_process(old["pid"])
    log("production child killed", kind=kind, old=old, watchdog=watchdog_before)

    def replacement():
        candidate = process_info(kind)
        if candidate and candidate["pid"] != old["pid"]:
            return candidate
        return None

    new = wait_until(replacement, 45, f"replacement {kind} child", 0.5)
    require(new["owner"].upper() == r"NT AUTHORITY\SYSTEM", f"replacement {kind} child runs as SYSTEM", new)
    require(new["session"] == 0, f"replacement {kind} child is hidden Session 0", new)
    watchdog_after = process_info(watchdog_kind)
    require(
        watchdog_after and watchdog_after["pid"] == watchdog_before["pid"],
        f"{kind} watchdog retained while child restarted",
        {"before": watchdog_before, "after": watchdog_after},
    )
    wait_until(lambda: http_status(LOCAL_URL + "api/health", 2) == 200, 45, "localhost HTTP 200", 0.5)
    if public_required:
        wait_until(lambda: http_status(PUBLIC_URL + "api/health", 5) == 200, 90, "public HTTP 200", 1)
    return {"old": old, "new": new, "watchdog": watchdog_after}


INIT_PROBE = r"""
(() => {
  const NativeEventSource = window.EventSource;
  const probe = {
    eventSourceConstructions: 0,
    nativeTransportConstructions: 0,
    activeNativeTransports: 0,
    maxActiveNativeTransports: 0,
    readyEvents: 0,
    stateEvents: 0,
    stateFetchRequests: 0,
    stateFetchResponses200: 0,
    token: `${Date.now()}-${Math.random()}-${crypto.randomUUID ? crypto.randomUUID() : ''}`
  };
  const NativeFetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    let isState = false;
    try {
      const raw = typeof args[0] === 'string' ? args[0] : args[0].url;
      isState = new URL(raw, location.href).pathname === '/api/state';
    } catch {}
    if (isState) probe.stateFetchRequests += 1;
    return NativeFetch(...args).then(response => {
      if (isState && response.status === 200) probe.stateFetchResponses200 += 1;
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
    _fault() {
      this._faulted = true;
      this._closeTransport();
    }
    _restore() {
      if (this._closed || !this._faulted) return;
      this._faulted = false;
      this._openTransport();
    }
    close() {
      this._closed = true;
      this._closeTransport();
    }
    get readyState() {
      return this._native ? this._native.readyState : NativeEventSource.CLOSED;
    }
  }
  ['CONNECTING', 'OPEN', 'CLOSED'].forEach(name => {
    Object.defineProperty(InstrumentedEventSource, name, { value: NativeEventSource[name] });
    Object.defineProperty(InstrumentedEventSource.prototype, name, { value: NativeEventSource[name] });
  });
  probe.faultSse = () => logicalSource && logicalSource._fault();
  probe.restoreSse = () => logicalSource && logicalSource._restore();
  window.EventSource = InstrumentedEventSource;
  window.__acceptanceProbe = probe;
})();
"""


def attach_telemetry(page: Page, name: str, telemetry: dict) -> None:
    record = telemetry[name] = {
        "stateRequests": 0,
        "stateResponses200": 0,
        "eventRequests": 0,
        "eventResponses200": 0,
        "failedEventRequests": 0,
        "navigations": 0,
        "consoleErrors": [],
        "pageErrors": [],
        "timeline": [],
    }

    def path_of(url: str) -> str:
        return urlparse(url).path

    def requested(request) -> None:
        path = path_of(request.url)
        if path == "/api/state":
            record["stateRequests"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "state-request", "url": request.url})
        elif path == "/api/events":
            record["eventRequests"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "events-request", "url": request.url})

    def responded(response) -> None:
        path = path_of(response.url)
        if path == "/api/state" and response.status == 200:
            record["stateResponses200"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "state-200"})
        elif path == "/api/events" and response.status == 200:
            record["eventResponses200"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "events-200"})

    def failed(request) -> None:
        if path_of(request.url) == "/api/events":
            record["failedEventRequests"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "events-failed", "failure": request.failure})

    def console(message) -> None:
        if message.type == "error":
            record["consoleErrors"].append(message.text)

    def page_error(error) -> None:
        record["pageErrors"].append(str(error))

    def navigated(frame) -> None:
        if frame == page.main_frame:
            record["navigations"] += 1
            record["timeline"].append({"at": utc_now(), "kind": "navigation", "url": frame.url})

    page.on("request", requested)
    page.on("response", responded)
    page.on("requestfailed", failed)
    page.on("console", console)
    page.on("pageerror", page_error)
    page.on("framenavigated", navigated)


def make_context(browser: Browser, viewport: tuple[int, int]) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        device_scale_factor=1,
        locale="en-US",
        color_scheme="dark",
        reduced_motion="reduce",
        ignore_https_errors=False,
    )
    context.add_init_script(INIT_PROBE)
    return context


def flat_task_expression() -> str:
    return "Object.values(tasksData).flat()"


def page_task(page: Page, task_id: int):
    return page.evaluate(
        "id => { const task = Object.values(tasksData).flat().find(item => Number(item.id) === Number(id)); return task || null; }",
        int(task_id),
    )


def wait_page_task(page: Page, task_id: int, title: str | None = None, status: str | None = None, exists: bool = True, timeout: float = 30):
    def predicate():
        task = page_task(page, task_id)
        if not exists:
            return task is None
        if task is None:
            return False
        if title is not None and task.get("title") != title:
            return False
        if status is not None and task.get("status") != status:
            return False
        return task

    return wait_until(predicate, timeout, f"page task {task_id} exists={exists} title={title} status={status}")


def dom_count(page: Page, task_id: int) -> int:
    return page.locator(f'[data-task-id="{int(task_id)}"]').count()


def wait_dom_count(page: Page, task_id: int, expected: int, timeout: float = 20) -> int:
    return int(wait_until(lambda: dom_count(page, task_id) == expected and expected or (dom_count(page, task_id) == 0 and expected == 0), timeout, f"DOM count {expected} for {task_id}")) if expected else (0 if wait_until(lambda: dom_count(page, task_id) == 0, timeout, f"DOM count 0 for {task_id}") else -1)


def ui_create_task(page: Page, title: str, description: str) -> dict:
    page.evaluate("() => { switchView('admin'); changeActiveDay(0); }")
    page.select_option("#admin-day-select", "0")
    page.locator("#task-title").fill(title)
    page.locator("#task-dept").select_option("General Office")
    page.locator("#task-priority").select_option("Important")
    page.locator("#task-time").fill("03:15 PM - 04:00 PM")
    page.locator("#task-desc").fill(description)
    with page.expect_response(lambda response: urlparse(response.url).path == "/api/tasks" and response.request.method == "POST", timeout=30000) as response_info:
        page.locator("#add-task-form button[type=submit]").click()
    response = response_info.value
    body = response.json()
    require(response.status == 201, "UI create returned committed HTTP 201", {"status": response.status, "body": body})
    require(body.get("operation") == "created" and body.get("task", {}).get("id") == body.get("result"), "UI create returned committed task delta", body)
    return body


def browser_patch(page: Page, task_id: int, changes: dict) -> dict:
    result = page.evaluate(
        """async ({id, changes}) => {
          const response = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
            method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(changes)
          });
          return {status: response.status, body: await response.json()};
        }""",
        {"id": int(task_id), "changes": changes},
    )
    require(result["status"] == 200, "Browser PATCH committed", result)
    return result["body"]


def app_toggle_status(page: Page, task_id: int) -> None:
    page.evaluate("id => toggleTaskStatus(id)", int(task_id))


def app_delete_task(page: Page, task_id: int) -> None:
    page.evaluate("id => deleteTask(id)", int(task_id))


def assert_single_everywhere(pages: dict[str, Page], task_id: int) -> dict:
    counts = {name: dom_count(page, task_id) for name, page in pages.items()}
    counts["sqlite"] = db_id_count(task_id)
    require(all(value == 1 for value in counts.values()), f"ID {task_id} exists exactly once everywhere", counts)
    return counts


def start_baseline_server() -> tuple[subprocess.Popen, int]:
    port = 8790
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            port = 8791
    process = subprocess.Popen(
        [str(PYTHON), str(BASELINE_SERVER), "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    wait_until(lambda: http_status(f"http://127.0.0.1:{port}/", 2) == 200, 15, "visual baseline server")
    return process, port


def rect_for(page: Page, selector: str):
    box = page.locator(selector).first.bounding_box()
    if not box:
        return None
    return {key: float(box[key]) for key in ("x", "y", "width", "height")}


def parent_rect_for(page: Page, selector: str):
    box = page.locator(selector).first.locator("..").bounding_box()
    if not box:
        return None
    return {key: float(box[key]) for key in ("x", "y", "width", "height")}


VISUAL_SELECTORS = {
    "body": "body",
    "header": "header",
    "headerInner": "header > div",
    "controls": "header > div > div:last-child",
    "navigation": "header > div > div:last-child > div:last-child",
    "main": "main",
    "tvView": "#view-tv",
    "statusBar": "#view-tv > div:first-child",
    "tvFrame": "#view-tv > div:nth-child(2)",
    "tvHeader": "#view-tv > div:nth-child(2) > div:first-child",
    "ticker": "#tv-ticker-text",
    "taskArea": "#tv-tasks-container",
    "firstTaskCard": "#tv-tasks-container > div:first-child",
    "directivesCard": "#tv-directives-container",
    "progress": "#tv-progress-bar",
}


def visual_fingerprint(page: Page) -> dict:
    return page.evaluate(
        """selectors => Object.fromEntries(Object.entries(selectors).map(([name, selector]) => {
          const element = document.querySelector(selector);
          if (!element) return [name, null];
          const r = element.getBoundingClientRect();
          const s = getComputedStyle(element);
          return [name, {
            rect: {x:r.x,y:r.y,width:r.width,height:r.height},
            style: {
              display:s.display, position:s.position, fontFamily:s.fontFamily, fontSize:s.fontSize,
              fontWeight:s.fontWeight, color:s.color, backgroundColor:s.backgroundColor,
              borderTopWidth:s.borderTopWidth, borderTopColor:s.borderTopColor,
              borderRadius:s.borderRadius, boxShadow:s.boxShadow, padding:s.padding,
              margin:s.margin, gap:s.gap, gridTemplateColumns:s.gridTemplateColumns
            }
          }];
        }))""",
        VISUAL_SELECTORS,
    )


def compare_fingerprints(baseline: dict, current: dict) -> list[dict]:
    mismatches = []
    for name in VISUAL_SELECTORS:
        left, right = baseline.get(name), current.get(name)
        if left is None or right is None:
            if left != right:
                mismatches.append({"selector": name, "kind": "missing", "baseline": left, "current": right})
            continue
        for key, left_value in left["rect"].items():
            right_value = right["rect"][key]
            if abs(float(left_value) - float(right_value)) > 0.6:
                mismatches.append({"selector": name, "kind": f"rect.{key}", "baseline": left_value, "current": right_value})
        for key, left_value in left["style"].items():
            right_value = right["style"][key]
            if left_value != right_value:
                mismatches.append({"selector": name, "kind": f"style.{key}", "baseline": left_value, "current": right_value})
    return mismatches


def mask_rectangles(size: tuple[int, int], rectangles: list[dict]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for rect in rectangles:
        if not rect:
            continue
        x1 = max(0, math.floor(rect["x"]) - 2)
        y1 = max(0, math.floor(rect["y"]) - 2)
        x2 = min(size[0] - 1, math.ceil(rect["x"] + rect["width"]) + 2)
        y2 = min(size[1] - 1, math.ceil(rect["y"] + rect["height"]) + 2)
        draw.rectangle((x1, y1, x2, y2), fill=255)
    return mask


def compare_pixels(baseline_path: Path, current_path: Path, rectangles: list[dict], diff_path: Path) -> dict:
    baseline = Image.open(baseline_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")
    require(baseline.size == current.size, "Visual images have identical dimensions", {"baseline": baseline.size, "current": current.size})
    width, height = baseline.size
    ignored = mask_rectangles((width, height), rectangles)
    ignored_bytes = ignored.tobytes()
    left = baseline.tobytes()
    right = current.tobytes()
    changed = bytearray(width * height)
    tile_counts: dict[tuple[int, int], int] = {}
    changed_count = 0
    valid_count = 0
    for pixel_index in range(width * height):
        if ignored_bytes[pixel_index] != 0:
            continue
        valid_count += 1
        offset = pixel_index * 3
        d0 = abs(left[offset] - right[offset])
        d1 = abs(left[offset + 1] - right[offset + 1])
        d2 = abs(left[offset + 2] - right[offset + 2])
        if max(d0, d1, d2) > 24 and d0 + d1 + d2 > 45:
            changed[pixel_index] = 255
            changed_count += 1
            x, y = pixel_index % width, pixel_index // width
            tile = (x // 32, y // 32)
            tile_counts[tile] = tile_counts.get(tile, 0) + 1
    changed_image = Image.frombytes("L", (width, height), bytes(changed))
    changed_image.save(diff_path)
    occupied = set(tile_counts)
    regions = []
    while occupied:
        seed = occupied.pop()
        component = {seed}
        stack = [seed]
        while stack:
            tx, ty = stack.pop()
            for nx in range(tx - 1, tx + 2):
                for ny in range(ty - 1, ty + 2):
                    neighbor = (nx, ny)
                    if neighbor in occupied:
                        occupied.remove(neighbor)
                        component.add(neighbor)
                        stack.append(neighbor)
        min_x = min(tile[0] for tile in component) * 32
        min_y = min(tile[1] for tile in component) * 32
        max_x = min(width, (max(tile[0] for tile in component) + 1) * 32)
        max_y = min(height, (max(tile[1] for tile in component) + 1) * 32)
        regions.append({
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x,
            "height": max_y - min_y,
            "changedPixels": sum(tile_counts[tile] for tile in component),
        })
    regions.sort(key=lambda region: region["changedPixels"], reverse=True)
    return {
        "width": width,
        "height": height,
        "validComparedPixels": valid_count,
        "maskedPixels": width * height - valid_count,
        "changedPixels": changed_count,
        "changedPercentage": round((changed_count / valid_count * 100) if valid_count else 100.0, 6),
        "meaningfulDifferenceBoundingRegions": regions[:20],
        "pixelThreshold": {"maxChannel": 24, "sumChannels": 45},
    }


def prepare_visual_page(page: Page, url: str, current: bool) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function("() => typeof setLanguage === 'function' && typeof updateTvDisplay === 'function'", timeout=30000)
    page.evaluate("() => { setLanguage('en'); switchView('tv'); document.getElementById('toast-container').innerHTML=''; window.scrollTo(0,0); }")
    if current:
        page.wait_for_function("() => Object.values(tasksData).flat().length === 8", timeout=30000)
    else:
        page.wait_for_function("() => document.querySelectorAll('#tv-tasks-container > div').length === 2", timeout=30000)
    page.add_style_tag(content="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}")
    page.evaluate("() => document.fonts && document.fonts.ready")
    page.wait_for_timeout(500)


def run_visual_comparison(browser: Browser, baseline_port: int) -> dict:
    visual_dir = ARTIFACT_DIR / "visual"
    visual_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
        label = f"{width}x{height}"
        baseline_context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, locale="en-US", color_scheme="dark", reduced_motion="reduce")
        current_context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, locale="en-US", color_scheme="dark", reduced_motion="reduce")
        try:
            baseline_page = baseline_context.new_page()
            current_page = current_context.new_page()
            prepare_visual_page(baseline_page, f"http://127.0.0.1:{baseline_port}/", current=False)
            prepare_visual_page(current_page, PUBLIC_URL, current=True)

            raw_baseline_path = visual_dir / f"original-raw-{label}.png"
            normalized_baseline_path = visual_dir / f"original-normalized-{label}.png"
            current_path = visual_dir / f"current-{label}.png"
            diff_path = visual_dir / f"diff-mask-{label}.png"

            save_button = baseline_page.locator("button", has_text="Save All").first
            require(save_button.count() == 1, f"Original baseline Save All exists at {label}")
            save_box = save_button.bounding_box()
            baseline_page.screenshot(path=str(raw_baseline_path), full_page=False, animations="disabled")
            save_button.evaluate("element => element.remove()")
            baseline_page.wait_for_timeout(100)

            baseline_fingerprint = visual_fingerprint(baseline_page)
            current_fingerprint = visual_fingerprint(current_page)
            layout_mismatches = compare_fingerprints(baseline_fingerprint, current_fingerprint)

            # The clock card auto-sizes with locale-formatted live text, so its
            # exact border is part of the specifically authorized clock region.
            dynamic_rectangles = [
                parent_rect_for(baseline_page, "#live-clock"),
                parent_rect_for(current_page, "#live-clock"),
            ]
            baseline_page.screenshot(path=str(normalized_baseline_path), full_page=False, animations="disabled")
            current_page.screenshot(path=str(current_path), full_page=False, animations="disabled")
            pixels = compare_pixels(normalized_baseline_path, current_path, dynamic_rectangles, diff_path)
            passed = pixels["changedPercentage"] <= 0.05 and not layout_mismatches
            results[label] = {
                **pixels,
                "layoutMismatches": layout_mismatches,
                "authorizedSaveAllRegion": save_box,
                "dynamicMasks": dynamic_rectangles,
                "rawOriginal": str(raw_baseline_path.relative_to(ROOT)),
                "normalizedOriginal": str(normalized_baseline_path.relative_to(ROOT)),
                "current": str(current_path.relative_to(ROOT)),
                "diffMask": str(diff_path.relative_to(ROOT)),
                "result": "PASS" if passed else "FAIL",
            }
            log("visual comparison", viewport=label, result=results[label])
        finally:
            baseline_context.close()
            current_context.close()
    require(all(item["result"] == "PASS" for item in results.values()), "Desktop design preserved at all target viewports", results)
    return results


def security_snapshot() -> dict:
    def as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    # Some Defender provider combinations do not terminate when they share one
    # PowerShell host. Independent read-only queries are deterministic here.
    antivirus = as_list(
        powershell_json(
            "Get-CimInstance -Namespace root\\SecurityCenter2 -ClassName AntiVirusProduct "
            "| Select-Object displayName,productState | ConvertTo-Json -Compress"
        )
    )
    defender = powershell_json(
        "Get-MpComputerStatus | Select-Object AMRunningMode,RealTimeProtectionEnabled "
        "| ConvertTo-Json -Compress"
    )
    preferences = powershell_json(
        "$p=Get-MpPreference; [pscustomobject]@{"
        "ExclusionPath=@($p.ExclusionPath);ExclusionProcess=@($p.ExclusionProcess);"
        "ExclusionExtension=@($p.ExclusionExtension);ExclusionIpAddress=@($p.ExclusionIpAddress)"
        "} | ConvertTo-Json -Compress -Depth 4"
    )
    firewall = as_list(
        powershell_json(
            "Get-NetFirewallProfile | Select-Object Name,@{n='Enabled';e={[bool]$_.Enabled}} "
            "| ConvertTo-Json -Compress"
        )
    )
    reason_services = as_list(
        powershell_json(
            "Get-Service | Where-Object {$_.Name -match '^rs'} "
            "| Select-Object Name,@{n='Status';e={[string]$_.Status}},"
            "@{n='StartType';e={[string]$_.StartType}} | ConvertTo-Json -Compress"
        )
    )
    listeners = as_list(
        powershell_json(
            "$app=Get-CimInstance Win32_Process | Where-Object {"
            "$_.Name -eq 'python.exe' -and $_.CommandLine -match '[\\\\/]app\\.py\"?\\s+--port\\s+8787'"
            "} | Select-Object -First 1; if($app){Get-NetTCPConnection -State Listen -OwningProcess $app.ProcessId "
            "| Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress}else{'[]'}"
        )
    )
    sqlite_processes = as_list(
        powershell_json(
            "Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^sqlite'} "
            "| Select-Object Name,ProcessId | ConvertTo-Json -Compress"
        )
    )
    exclusions = []
    for key in ("ExclusionPath", "ExclusionProcess", "ExclusionExtension", "ExclusionIpAddress"):
        exclusions.extend(item for item in as_list(preferences.get(key)) if item)
    normalized_project = os.path.normcase(str(ROOT))
    project_exclusions = [item for item in exclusions if normalized_project in os.path.normcase(str(item))]
    return {
        "antivirus": antivirus,
        "defenderMode": defender["AMRunningMode"],
        "defenderRealtime": bool(defender["RealTimeProtectionEnabled"]),
        "firewall": firewall,
        "reasonServices": reason_services,
        "projectRelevantExclusions": project_exclusions,
        "preExistingHostExclusionCount": len(exclusions),
        "appListeners": listeners,
        "sqliteProcesses": sqlite_processes,
    }


def cleanup_codex(pages: dict[str, Page] | None = None) -> dict:
    before = codex_rows()
    for row in before:
        try:
            local_api_delete(row["id"])
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
    wait_until(lambda: len(codex_rows()) == 0, 20, "CODEX database cleanup")
    if pages:
        for name, page in pages.items():
            wait_until(
                lambda page=page: page.evaluate("() => !Object.values(tasksData).flat().some(task => task.title.startsWith('CODEX-'))"),
                30,
                f"CODEX cleanup reconciled in {name}",
            )
            require(page.locator('[data-task-id]').evaluate_all("elements => elements.filter(el => el.textContent.includes('CODEX-')).length") == 0, f"No CODEX DOM records in {name}")
    return {"deleted": before, "remaining": codex_rows()}


def run() -> dict:
    RUN_LOG.write_text("", encoding="utf-8")
    started = utc_now()
    run_started_monotonic = time.monotonic()
    results = {
        "startedAt": started,
        "saveAllRemoved": False,
        "manualSaveRequired": True,
        "staleStateResync": "FAIL",
        "duplicateEventProtection": "FAIL",
        "desktopDesignPreserved": "FAIL",
        "liveCreate": "FAIL",
        "liveUpdate": "FAIL",
        "liveStatus": "FAIL",
        "liveDelete": "FAIL",
        "mobileToTv": "FAIL",
        "applicationRestartSseReconnect": "FAIL",
        "cloudflaredSseReconnect": "FAIL",
        "retainedTvConnection": "FAIL",
        "databaseIntegrity": "FAIL",
        "cleanBrowserPersistence": "FAIL",
        "publicHttp200": False,
        "testDataCleanup": "FAIL",
        "overall": "FAIL",
    }
    evidence: dict = {"telemetry": {}, "cycles": [], "mutations": [], "files": {"database": str(DB_PATH)}}
    browser = None
    tv_browser = None
    contexts: list[BrowserContext] = []
    baseline_process = None
    pages: dict[str, Page] = {}
    playwright_manager = None
    try:
        require(DB_PATH.resolve() == (ROOT / ".data" / "thebekal.db").resolve() and DB_PATH.is_file(), "SQLite database remains at project .data path", str(DB_PATH))
        with db_connection() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        require(integrity == "ok", "SQLite PRAGMA integrity_check", integrity)
        results["databaseIntegrity"] = "PASS"
        require(len(codex_rows()) == 0, "No CODEX records exist before final run", codex_rows())
        require(http_status(LOCAL_URL) == 200, "Local application HTTP 200")
        require(http_status(PUBLIC_URL) == 200, "Public application HTTP 200")
        results["publicHttp200"] = True

        baseline_process, baseline_port = start_baseline_server()
        playwright_manager = sync_playwright().start()
        browser = playwright_manager.chromium.launch(
            executable_path=str(CHROME),
            headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-background-networking"],
        )
        tv_browser_marker = f"{os.getpid()}-{int(time.time() * 1000)}"
        tv_browser = playwright_manager.chromium.launch(
            executable_path=str(CHROME),
            headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-background-networking", f"--thebekal-tv-acceptance={tv_browser_marker}"],
        )
        evidence["browser"] = {"engine": "Chromium", "executable": str(CHROME), "version": browser.version, "deviceScaleFactor": 1, "zoom": 1}

        specifications = {"tv": (1920, 1080), "mobile": (390, 844), "desktop": (1440, 900)}
        telemetry: dict = evidence["telemetry"]
        for name, viewport in specifications.items():
            owning_browser = tv_browser if name == "tv" else browser
            context = make_context(owning_browser, viewport)
            contexts.append(context)
            page = context.new_page()
            pages[name] = page
            attach_telemetry(page, name, telemetry)
            page.goto(PUBLIC_URL, wait_until="domcontentloaded", timeout=60000)
        retained_start = time.monotonic()

        for name, page in pages.items():
            page.wait_for_function("() => Object.values(tasksData).flat().length === 8", timeout=30000)
            wait_until(lambda page=page: page.evaluate("() => window.__acceptanceProbe.eventSourceConstructions") == 1, 15, f"one EventSource in {name}")
            require(acceptance_probe(page)["stateFetchResponses200"] >= 1, f"Initial authoritative state reconciliation in {name}", {"probe": acceptance_probe(page), "network": telemetry[name]})
        retained_token = pages["tv"].evaluate("() => window.__acceptanceProbe.token")
        retained_navigation_count = telemetry["tv"]["navigations"]

        html_source = (ROOT / "youruni_tv_task_management_system update 17 Aug.html").read_text(encoding="utf-8")
        save_all_checks = {
            "visibleTextCount": pages["tv"].get_by_text("Save All", exact=True).count(),
            "functionType": pages["tv"].evaluate("() => typeof saveAllSystemData"),
            "sourceContainsSaveAll": "Save All" in html_source,
            "sourceContainsSaveHandler": "saveAllSystemData" in html_source,
            "eventSourceConstructionsInSource": html_source.count("new EventSource("),
            "reconnectTimerPresent": "setTimeout(connectRealtime" in html_source,
            "localTaskStorage": pages["tv"].evaluate("() => localStorage.getItem('youruni_tasks')"),
        }
        require(
            save_all_checks["visibleTextCount"] == 0
            and save_all_checks["functionType"] == "undefined"
            and not save_all_checks["sourceContainsSaveAll"]
            and not save_all_checks["sourceContainsSaveHandler"],
            "Save All UI and handlers are absent",
            save_all_checks,
        )
        require(save_all_checks["eventSourceConstructionsInSource"] == 1 and not save_all_checks["reconnectTimerPresent"], "Exactly one intended EventSource with native reconnect", save_all_checks)
        require(save_all_checks["localTaskStorage"] is None, "Business task state is not browser-local", save_all_checks)
        results["saveAllRemoved"] = True
        results["manualSaveRequired"] = False
        evidence["saveAll"] = save_all_checks

        stale_title = f"CODEX-FINAL-STALE-{int(time.time() * 1000)}"
        stale_response = ui_create_task(pages["mobile"], stale_title, "stale-state initial")
        stale_id = int(stale_response["result"])
        immediate_create_rows = db_title_rows(stale_title)
        require(len(immediate_create_rows) == 1 and immediate_create_rows[0]["id"] == stale_id, "Create committed immediately to SQLite", immediate_create_rows)
        for page in pages.values():
            wait_page_task(page, stale_id, stale_title, "pending")
        initial_counts = assert_single_everywhere(pages, stale_id)
        results["liveCreate"] = "PASS"
        results["mobileToTv"] = "PASS"
        evidence["mutations"].append({"kind": "create", "id": stale_id, "title": stale_title, "sqliteImmediately": True, "counts": initial_counts})

        tv_context = contexts[0]
        stale_fault = fault_only_tv_sse(pages["tv"], telemetry["tv"])
        time.sleep(0.5)
        offline_health = {
            "tv": pages["tv"].evaluate("async () => (await fetch('/api/health')).ok"),
            "mobile": pages["mobile"].evaluate("async () => (await fetch('/api/health')).ok"),
            "desktop": pages["desktop"].evaluate("async () => (await fetch('/api/health')).ok"),
        }
        require(all(offline_health.values()), "TV fault blocks only its SSE while ordinary requests and other clients remain online", offline_health)
        stale_new_title = stale_title + "-CURRENT"
        browser_patch(pages["mobile"], stale_id, {"title": stale_new_title, "desc": "changed while TV offline"})
        immediate_edit = db_task(stale_id)
        require(immediate_edit and immediate_edit["title"] == stale_new_title and immediate_edit["description"] == "changed while TV offline", "Edit committed immediately to SQLite", immediate_edit)
        app_toggle_status(pages["mobile"], stale_id)
        immediate_status = db_task(stale_id)
        require(immediate_status and immediate_status["status"] == "completed", "Status committed immediately to SQLite", immediate_status)
        wait_page_task(pages["mobile"], stale_id, stale_new_title, "completed")
        wait_page_task(pages["desktop"], stale_id, stale_new_title, "completed")
        tv_stale = page_task(pages["tv"], stale_id)
        require(tv_stale and tv_stale["title"] == stale_title and tv_stale["status"] == "pending", "Faulted TV intentionally retains old state", tv_stale)
        state_responses_before = telemetry["tv"]["stateResponses200"]
        event_responses_before = telemetry["tv"]["eventResponses200"]
        restore_tv_sse(pages["tv"], stale_fault)
        wait_until(lambda: acceptance_probe(pages["tv"])["readyEvents"] > stale_fault["beforeProbe"]["readyEvents"], 30, "TV EventSource ready after isolated fault")
        wait_until(lambda: acceptance_probe(pages["tv"])["stateFetchResponses200"] > stale_fault["beforeProbe"]["stateFetchResponses200"], 30, "TV authoritative reconciliation after isolated fault")
        wait_page_task(pages["tv"], stale_id, stale_new_title, "completed")
        post_reconnect_title = stale_new_title + "-LIVE"
        browser_patch(pages["mobile"], stale_id, {"title": post_reconnect_title, "desc": "next mutation after stale resync"})
        wait_page_task(pages["tv"], stale_id, post_reconnect_title, "completed")
        wait_page_task(pages["desktop"], stale_id, post_reconnect_title, "completed")
        results["staleStateResync"] = "PASS"
        results["liveUpdate"] = "PASS"
        results["liveStatus"] = "PASS"
        evidence["staleState"] = {
            "id": stale_id,
            "old": {"title": stale_title, "status": "pending"},
            "sqliteCurrent": db_task(stale_id),
            "faultIsolation": offline_health,
            "stateResponsesBeforeRestore": state_responses_before,
            "stateResponsesAfterRestore": telemetry["tv"]["stateResponses200"],
            "eventResponsesBeforeRestore": event_responses_before,
            "eventResponsesAfterRestore": telemetry["tv"]["eventResponses200"],
            "probeAfterRestore": acceptance_probe(pages["tv"]),
            "noRefresh": True,
        }

        duplicate_title = f"CODEX-FINAL-DUPLICATE-{int(time.time() * 1000)}"
        duplicate_response = ui_create_task(pages["mobile"], duplicate_title, "duplicate-cycle initial")
        duplicate_id = int(duplicate_response["result"])
        for page in pages.values():
            wait_page_task(page, duplicate_id, duplicate_title, "pending")
        assert_single_everywhere(pages, duplicate_id)

        current_duplicate_title = duplicate_title
        for cycle in range(1, 6):
            cycle_fault = fault_only_tv_sse(pages["tv"], telemetry["tv"])
            time.sleep(0.5)
            old_tv = page_task(pages["tv"], duplicate_id)
            current_duplicate_title = f"{duplicate_title}-R{cycle}"
            mutation_page = pages["mobile"] if cycle % 2 else pages["desktop"]
            browser_patch(mutation_page, duplicate_id, {"title": current_duplicate_title, "desc": f"offline reconciliation cycle {cycle}"})
            wait_page_task(pages["mobile"], duplicate_id, current_duplicate_title)
            wait_page_task(pages["desktop"], duplicate_id, current_duplicate_title)
            require(page_task(pages["tv"], duplicate_id)["title"] == old_tv["title"], f"Cycle {cycle} TV remains stale while isolated", {"old": old_tv, "current": page_task(pages["tv"], duplicate_id)})
            before_state = telemetry["tv"]["stateResponses200"]
            before_events = telemetry["tv"]["eventResponses200"]
            restore_tv_sse(pages["tv"], cycle_fault)
            wait_until(lambda cycle_fault=cycle_fault: acceptance_probe(pages["tv"])["readyEvents"] > cycle_fault["beforeProbe"]["readyEvents"], 30, f"cycle {cycle} EventSource ready")
            wait_until(lambda cycle_fault=cycle_fault: acceptance_probe(pages["tv"])["stateFetchResponses200"] > cycle_fault["beforeProbe"]["stateFetchResponses200"], 30, f"cycle {cycle} authoritative reconciliation")
            wait_page_task(pages["tv"], duplicate_id, current_duplicate_title)

            live_desc = f"live-after-reconnect-{cycle}"
            browser_patch(pages["mobile"], duplicate_id, {"desc": live_desc})
            wait_until(lambda: page_task(pages["tv"], duplicate_id).get("desc") == live_desc, 20, f"cycle {cycle} next live mutation")
            counts = assert_single_everywhere(pages, duplicate_id)
            evidence["cycles"].append({
                "cycle": cycle,
                "title": current_duplicate_title,
                "counts": counts,
                "stateReconciled": acceptance_probe(pages["tv"])["stateFetchResponses200"] > cycle_fault["beforeProbe"]["stateFetchResponses200"],
                "nextLiveMutation": live_desc,
            })

        direct_state = pages["desktop"].evaluate(
            """() => {
              const state = new CanonicalTaskStore();
              const base = {id: 424242, day:'Saturday', title:'first'};
              state.upsertTask(base);
              state.upsertTask({...base});
              const afterDuplicate = {size:state.size, title:state.get(424242).title};
              state.upsertTask({...base, title:'latest'});
              const afterNewer = {size:state.size, title:state.get(424242).title};
              state.deleteTask(424242);
              const afterDelete = {size:state.size};
              let secondDeleteThrew = false;
              try { state.deleteTask(424242); } catch { secondDeleteThrew = true; }
              return {afterDuplicate, afterNewer, afterDelete, afterSecondDelete:{size:state.size, threw:secondDeleteThrew}};
            }"""
        )
        require(
            direct_state == {
                "afterDuplicate": {"size": 1, "title": "first"},
                "afterNewer": {"size": 1, "title": "latest"},
                "afterDelete": {"size": 0},
                "afterSecondDelete": {"size": 0, "threw": False},
            },
            "Direct canonical state idempotence",
            direct_state,
        )
        app_delete_task(pages["mobile"], duplicate_id)
        wait_until(lambda: db_id_count(duplicate_id) == 0, 20, "duplicate task immediate SQLite delete")
        for page in pages.values():
            wait_page_task(page, duplicate_id, exists=False)
            wait_dom_count(page, duplicate_id, 0)
        results["duplicateEventProtection"] = "PASS"
        results["liveDelete"] = "PASS"
        evidence["directIdempotence"] = direct_state

        clean_context = make_context(browser, (1440, 900))
        contexts.append(clean_context)
        clean_page = clean_context.new_page()
        clean_page.goto(PUBLIC_URL, wait_until="domcontentloaded", timeout=60000)
        wait_page_task(clean_page, stale_id, post_reconnect_title, "completed")
        require(dom_count(clean_page, stale_id) == 1, "Clean browser renders SQLite task exactly once", {"id": stale_id, "count": dom_count(clean_page, stale_id)})
        require(clean_page.evaluate("() => localStorage.getItem('youruni_tasks')") is None, "Clean browser has no local business task state")
        results["cleanBrowserPersistence"] = "PASS"
        clean_context.close()
        contexts.remove(clean_context)

        probe_before_app_restart = {name: acceptance_probe(page) for name, page in pages.items()}
        state_before_app_restart = {name: telemetry[name]["stateResponses200"] for name in pages}
        event_before_app_restart = {name: telemetry[name]["eventResponses200"] for name in pages}
        app_restart = restart_child("app")
        for name, page in pages.items():
            wait_until(lambda name=name, page=page: acceptance_probe(page)["readyEvents"] > probe_before_app_restart[name]["readyEvents"], 45, f"{name} EventSource ready after app restart")
            wait_until(lambda name=name, page=page: acceptance_probe(page)["stateFetchResponses200"] > probe_before_app_restart[name]["stateFetchResponses200"], 45, f"{name} reconciled after app restart")
            wait_page_task(page, stale_id, post_reconnect_title, "completed")
            require(dom_count(page, stale_id) == 1, f"No duplicate after app restart in {name}", dom_count(page, stale_id))
        app_recovered_title = post_reconnect_title + "-APP"
        browser_patch(pages["mobile"], stale_id, {"title": app_recovered_title, "desc": "live after app child recovery"})
        for page in pages.values():
            wait_page_task(page, stale_id, app_recovered_title, "completed")
        results["applicationRestartSseReconnect"] = "PASS"
        evidence["applicationRestart"] = app_restart

        probe_before_cloud_restart = {name: acceptance_probe(page) for name, page in pages.items()}
        state_before_cloud_restart = {name: telemetry[name]["stateResponses200"] for name in pages}
        event_before_cloud_restart = {name: telemetry[name]["eventResponses200"] for name in pages}
        cloud_restart = restart_child("cloudflared")
        for name, page in pages.items():
            wait_until(lambda name=name, page=page: acceptance_probe(page)["readyEvents"] > probe_before_cloud_restart[name]["readyEvents"], 60, f"{name} EventSource ready after cloudflared restart")
            wait_until(lambda name=name, page=page: acceptance_probe(page)["stateFetchResponses200"] > probe_before_cloud_restart[name]["stateFetchResponses200"], 60, f"{name} reconciled after cloudflared restart")
            wait_page_task(page, stale_id, app_recovered_title, "completed")
            require(dom_count(page, stale_id) == 1, f"No duplicate after cloudflared restart in {name}", dom_count(page, stale_id))
        cloud_recovered_title = app_recovered_title + "-TUNNEL"
        browser_patch(pages["mobile"], stale_id, {"title": cloud_recovered_title, "desc": "live after cloudflared child recovery"})
        for page in pages.values():
            wait_page_task(page, stale_id, cloud_recovered_title, "completed")
        results["cloudflaredSseReconnect"] = "PASS"
        evidence["cloudflaredRestart"] = cloud_restart

        for name, page in pages.items():
            probe = acceptance_probe(page)
            require(probe["eventSourceConstructions"] >= 1 and probe["maxActiveNativeTransports"] == 1 and probe["activeNativeTransports"] == 1, f"Exactly one active EventSource connection retained in {name}", probe)
        require(pages["tv"].evaluate("() => window.__acceptanceProbe.token") == retained_token, "Same retained TV document survived all faults and restarts")
        require(telemetry["tv"]["navigations"] == retained_navigation_count, "Retained TV never reloaded", {"initial": retained_navigation_count, "final": telemetry["tv"]["navigations"]})
        results["retainedTvConnection"] = "PASS"

        mobile_metrics = pages["mobile"].evaluate(
            """() => ({
              innerWidth:window.innerWidth,clientWidth:document.documentElement.clientWidth,
              htmlScrollWidth:document.documentElement.scrollWidth,bodyScrollWidth:document.body.scrollWidth,
              saveAllVisible:[...document.querySelectorAll('button')].some(button => button.textContent.includes('Save All')),
              eventSources:window.__acceptanceProbe.eventSourceConstructions
            })"""
        )
        require(
            mobile_metrics["innerWidth"] == 390
            and mobile_metrics["htmlScrollWidth"] <= mobile_metrics["clientWidth"]
            and mobile_metrics["bodyScrollWidth"] <= mobile_metrics["clientWidth"]
            and not mobile_metrics["saveAllVisible"],
            "390x844 mobile has no page-level overflow and no Save All",
            mobile_metrics,
        )
        evidence["mobile390x844"] = mobile_metrics

        app_delete_task(pages["mobile"], stale_id)
        immediate_delete = db_task(stale_id)
        require(immediate_delete is None, "Delete committed immediately to SQLite", immediate_delete)
        for page in pages.values():
            wait_page_task(page, stale_id, exists=False)
        cleanup = cleanup_codex(pages)
        require(len(cleanup["remaining"]) == 0, "Final CODEX test data count is zero", cleanup)
        results["testDataCleanup"] = "PASS"
        evidence["cleanup"] = cleanup

        visual = run_visual_comparison(browser, baseline_port)
        results["desktopDesignPreserved"] = "PASS"
        evidence["desktopVisual"] = visual

        with db_connection() as connection:
            final_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            final_codex_count = connection.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE 'CODEX-%'").fetchone()[0]
            final_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        require(final_integrity == "ok", "Final SQLite integrity", final_integrity)
        require(final_codex_count == 0, "Final SQLite CODEX row count zero", final_codex_count)
        evidence["database"] = {"path": str(DB_PATH), "integrity": final_integrity, "codexRows": final_codex_count, "legitimateRows": final_count, "stateVersion": state_version(), "sourceOfTruth": "SQLite"}

        sensitive = {}
        for path in (".data/", ".tools/", ".logs/", ".backup/", "DEPLOYMENT.md"):
            sensitive["/" + path] = http_status(PUBLIC_URL + path)
        require(all(status == 404 for status in sensitive.values()), "Sensitive public paths remain blocked", sensitive)
        security = security_snapshot()
        antivirus_names = [item["displayName"] for item in security["antivirus"]]
        require("Reason Cybersecurity" in antivirus_names, "Reason Cybersecurity remains registered and active", security["antivirus"])
        require(security["defenderMode"] == "Passive Mode" and not security["defenderRealtime"], "Microsoft Defender remains passive under third-party antivirus", security)
        require(all(profile["Enabled"] for profile in security["firewall"]), "All Windows Firewall profiles remain enabled", security["firewall"])
        require(len(security["projectRelevantExclusions"]) == 0, "No antivirus exclusion covers this project", security["projectRelevantExclusions"])
        require(
            len(security["appListeners"]) == 1
            and security["appListeners"][0]["LocalAddress"] == "127.0.0.1"
            and security["appListeners"][0]["LocalPort"] == 8787,
            "Application origin remains loopback-only",
            security["appListeners"],
        )
        require(len(security["sqliteProcesses"]) == 0, "SQLite has no network-listener process", security["sqliteProcesses"])
        evidence["security"] = {**security, "sensitivePublicStatuses": sensitive, "configurationChanged": False}

        retained_duration = round(time.monotonic() - retained_start, 3)
        evidence["retainedTvDurationSeconds"] = retained_duration
        evidence["completedAt"] = utc_now()
        evidence["runDurationSeconds"] = round(time.monotonic() - run_started_monotonic, 3)
        results["overall"] = "PASS"
        return {**results, "evidence": evidence}
    finally:
        try:
            if codex_rows():
                cleanup_codex(pages if pages else None)
        except Exception as cleanup_error:
            log("emergency cleanup failed", error=repr(cleanup_error))
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
        if tv_browser:
            try:
                tv_browser.close()
            except Exception:
                pass
        if playwright_manager:
            try:
                playwright_manager.stop()
            except Exception:
                pass
        if baseline_process:
            try:
                baseline_process.terminate()
                baseline_process.wait(timeout=5)
            except Exception:
                try:
                    baseline_process.kill()
                except Exception:
                    pass


def main() -> int:
    output = None
    try:
        output = run()
        code = 0
    except Exception as exc:
        output = {
            "finishedAt": utc_now(),
            "overall": "FAIL",
            "error": str(exc),
            "errorType": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        log("acceptance failed", error=output)
        code = 1
    output["finishedAt"] = utc_now()
    RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
