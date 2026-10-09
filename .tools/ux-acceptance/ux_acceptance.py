"""Production UX acceptance harness for thebekal.com.

Exercises TV presentation/native fullscreen/fallback, global themes, responsive
mobile layouts, permanent attribution, live CRUD/SSE, watchdog recovery,
desktop visual stability, database integrity, and read-only security checks.
"""

from __future__ import annotations

import json
import os
import sqlite3
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

from PIL import Image, ImageChops, ImageDraw  # noqa: E402
from playwright.sync_api import Page, Route, sync_playwright  # noqa: E402


PUBLIC_URL = "https://thebekal.com/"
LOCAL_URL = "http://127.0.0.1:8787/"
DB_PATH = ROOT / ".data" / "thebekal.db"
FRONTEND = ROOT / "youruni_tv_task_management_system update 17 Aug.html"
ORIGINAL_BASELINE = ROOT / ".backup" / "youruni_tv_task_management_system update 17 Aug.original.html"
PYTHON = ROOT / ".tools" / "python" / "python.exe"
CHROME = Path(r"C:\Program Files\Google\Chrome Dev\Application\chrome.exe")
BASELINE_SERVER = ROOT / ".tools" / "visual-baseline" / "server.py"
ARTIFACTS = HERE / "artifacts"
LOG_DIR = ROOT / ".logs" / "ux-acceptance"
RESULT_PATH = LOG_DIR / "results.json"
RUN_LOG = LOG_DIR / "run.log"
EXACT_ATTRIBUTION = "© Copyright 2026 YourUni. All rights reserved. Developed by ASHRAF2004"
MOBILE_VIEWPORTS = ((320, 568), (360, 800), (390, 844), (430, 932), (768, 1024))
DESKTOP_VIEWPORTS = ((1366, 768), (1440, 900), (1920, 1080))


class AcceptanceFailure(RuntimeError):
    pass


ARTIFACTS.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
RUN_LOG.write_text("", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str, **details) -> None:
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now_iso(), "message": message, **details}, ensure_ascii=False, default=str) + "\n")


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
        except Exception as exc:  # transient errors are retained as evidence
            last_error = repr(exc)
        time.sleep(interval)
    raise AcceptanceFailure(f"Timed out waiting for {label}; last_error={last_error}")


def http_status(url: str, timeout: float = 10) -> int:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "YourUni-UX-Acceptance/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def get_json(url: str, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "YourUni-UX-Acceptance/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def db_find_exact(title: str):
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT id,day,title,dept,priority,task_time,description,image_url,status,version FROM tasks WHERE title=? ORDER BY id",
            (title,),
        ).fetchall()
        return [dict(row) for row in rows]


def db_prefix_rows(prefix: str = "CODEX-UX-TEST-") -> list[dict]:
    with db_connection() as connection:
        rows = connection.execute("SELECT id,title FROM tasks WHERE title LIKE ? ORDER BY id", (prefix + "%",)).fetchall()
        return [dict(row) for row in rows]


def local_api_delete(task_id: int) -> dict:
    request = urllib.request.Request(
        LOCAL_URL + f"api/tasks/{int(task_id)}",
        data=b"{}",
        method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def cleanup_test_rows() -> list[int]:
    removed = []
    for row in db_prefix_rows():
        try:
            local_api_delete(int(row["id"]))
            removed.append(int(row["id"]))
        except Exception as exc:
            log("cleanup API retry needed", row=row, error=repr(exc))
    return removed


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
    return powershell_json(
        f"""
$p = Get-CimInstance Win32_Process | Where-Object {{ {predicate} }} | Select-Object -First 1
if (-not $p) {{ 'null' }} else {{
  $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner
  [pscustomobject]@{{pid=[int]$p.ProcessId;ppid=[int]$p.ParentProcessId;session=[int]$p.SessionId;owner=\"$($o.Domain)\\$($o.User)\";path=[string]$p.ExecutablePath;command=[string]$p.CommandLine}} | ConvertTo-Json -Compress
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


def browser_request(page: Page, method: str, path: str, payload: dict | None = None) -> dict:
    return page.evaluate(
        """async ({method,path,payload}) => {
          const response = await fetch(path, {
            method,
            headers: {'Content-Type':'application/json'},
            body: payload === null ? undefined : JSON.stringify(payload),
            cache: 'no-store'
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
          return body;
        }""",
        {"method": method, "path": path, "payload": payload},
    )


def dom_count(page: Page, task_id: int) -> int:
    return int(page.locator(f'[data-task-id="{int(task_id)}"]').count())


def task_text(page: Page, task_id: int) -> str:
    locator = page.locator(f'[data-task-id="{int(task_id)}"]')
    return locator.first.inner_text() if locator.count() else ""


def load_public(page: Page) -> None:
    page.goto(PUBLIC_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_function("() => document.documentElement.dataset.theme && document.querySelector('#youruni-site-attribution')", timeout=20_000)
    page.wait_for_timeout(400)


def presentation_metrics(page: Page) -> dict:
    return page.evaluate(
        """() => {
          const surface = document.querySelector('#view-tv');
          const frame = document.querySelector('#tv-display-frame');
          const rect = surface.getBoundingClientRect();
          return {
            mode: surface.classList.contains('tv-presentation-mode'),
            nativeElement: document.fullscreenElement ? document.fullscreenElement.id : null,
            headerDisplay: getComputedStyle(document.querySelector('#app-header')).display,
            footerDisplay: getComputedStyle(document.querySelector('#youruni-site-attribution')).display,
            exitDisplay: getComputedStyle(document.querySelector('#exit-tv-presentation-btn')).display,
            rect: {x:rect.x,y:rect.y,width:rect.width,height:rect.height},
            viewport: {width:innerWidth,height:innerHeight},
            doc: {scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,scrollHeight:document.documentElement.scrollHeight,clientHeight:document.documentElement.clientHeight},
            frameBackground: getComputedStyle(frame).backgroundColor,
            theme: document.documentElement.dataset.theme
          };
        }"""
    )


def theme_metrics(page: Page, view: str) -> dict:
    page.evaluate("view => switchView(view)", view)
    page.wait_for_timeout(120)
    return page.evaluate(
        """view => {
          const root = document.documentElement;
          const body = document.body;
          const target = view === 'tv' ? document.querySelector('#tv-display-frame') : document.querySelector(`#view-${view} > div`);
          const input = view === 'admin' ? document.querySelector('#task-title') : (view === 'sheets' ? document.querySelector('#gsheet-url-input') : null);
          const footer = document.querySelector('#youruni-site-attribution');
          return {
            theme: root.dataset.theme,
            rootDarkClass: root.classList.contains('dark'),
            bodyBackground: getComputedStyle(body).backgroundColor,
            bodyColor: getComputedStyle(body).color,
            surfaceBackground: target ? getComputedStyle(target).backgroundColor : null,
            surfaceColor: target ? getComputedStyle(target).color : null,
            inputBackground: input ? getComputedStyle(input).backgroundColor : null,
            inputColor: input ? getComputedStyle(input).color : null,
            placeholderColor: input ? getComputedStyle(input, '::placeholder').color : null,
            footerBackground: getComputedStyle(footer).backgroundColor,
            footerColor: getComputedStyle(footer).color,
            scrollWidth: root.scrollWidth,
            clientWidth: root.clientWidth
          };
        }""",
        view,
    )


def mobile_audit(browser) -> dict:
    evidence = {}
    for width, height in MOBILE_VIEWPORTS:
        key = f"{width}x{height}"
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error, bucket=errors: bucket.append(str(error)))
        load_public(page)

        dark_views = {}
        page.evaluate("applyTheme('dark', true)")
        for view in ("tv", "admin", "sheets"):
            dark_views[view] = theme_metrics(page, view)
            require(dark_views[view]["scrollWidth"] == dark_views[view]["clientWidth"], f"{key} dark {view} has no page overflow", dark_views[view])

        page.evaluate("switchView('admin')")
        page.wait_for_timeout(200)
        form_metrics = page.evaluate(
            """() => {
              const form=document.querySelector('#add-task-form').getBoundingClientRect();
              const input=document.querySelector('#task-title').getBoundingClientRect();
              const controls=[...document.querySelectorAll('#view-admin input:not([type=file]),#view-admin select,#view-admin textarea')].filter(el=>el.offsetParent!==null);
              const actionButtons=[...document.querySelectorAll('#admin-tasks-list [data-task-id] button')];
              return {
                form:{left:form.left,right:form.right,width:form.width},
                input:{left:input.left,right:input.right,width:input.width,font:getComputedStyle(document.querySelector('#task-title')).fontSize},
                controlsWithinViewport:controls.every(el=>{const r=el.getBoundingClientRect();return r.left>=-0.5&&r.right<=innerWidth+0.5&&r.width>0}),
                actionHitAreas:actionButtons.map(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height}}),
                taskCards:[...document.querySelectorAll('#admin-tasks-list [data-task-id]')].map(el=>el.getBoundingClientRect().width)
              };
            }"""
        )
        require(form_metrics["form"]["left"] >= 0 and form_metrics["form"]["right"] <= width + 0.5, f"{key} form fits viewport", form_metrics)
        require(form_metrics["controlsWithinViewport"], f"{key} form controls fit viewport", form_metrics)
        require(form_metrics["input"]["font"] == "16px", f"{key} phone input remains readable", form_metrics)
        require(all(item["height"] >= 43 for item in form_metrics["actionHitAreas"]), f"{key} task actions are touch sized", form_metrics["actionHitAreas"])
        page.screenshot(path=str(ARTIFACTS / f"mobile-{key}-dark-admin.png"), full_page=True, animations="disabled")

        page.click("#mobile-menu-button")
        page.wait_for_timeout(250)
        drawer = page.evaluate(
            """() => {const layer=document.querySelector('#mobile-nav-layer');const d=document.querySelector('#mobile-nav-drawer').getBoundingClientRect();return {open:layer.classList.contains('is-open'),aria:layer.getAttribute('aria-hidden'),rect:{left:d.left,right:d.right,top:d.top,bottom:d.bottom,width:d.width,height:d.height},viewport:{width:innerWidth,height:innerHeight},bodyOverflow:getComputedStyle(document.body).overflow};}"""
        )
        require(drawer["open"] and drawer["aria"] == "false", f"{key} mobile drawer opens", drawer)
        require(drawer["rect"]["left"] >= 0 and drawer["rect"]["right"] <= width + 0.5 and drawer["rect"]["height"] <= height + 0.5, f"{key} drawer fits viewport", drawer)
        page.screenshot(path=str(ARTIFACTS / f"mobile-{key}-drawer.png"), full_page=False, animations="disabled")
        page.click("#mobile-btn-tv-view")
        page.wait_for_timeout(180)
        require(not page.locator("#mobile-nav-layer").evaluate("el => el.classList.contains('is-open')"), f"{key} destination closes drawer")

        page.evaluate("applyTheme('light', true)")
        page.wait_for_timeout(350)
        light_views = {}
        for view in ("tv", "admin", "sheets"):
            light_views[view] = theme_metrics(page, view)
            require(light_views[view]["scrollWidth"] == light_views[view]["clientWidth"], f"{key} light {view} has no page overflow", light_views[view])
        require(light_views["admin"]["bodyBackground"] != dark_views["admin"]["bodyBackground"], f"{key} global Day theme changes application", {"dark": dark_views["admin"], "light": light_views["admin"]})
        require(light_views["admin"]["inputBackground"] != dark_views["admin"]["inputBackground"], f"{key} Day theme reaches forms", {"dark": dark_views["admin"], "light": light_views["admin"]})

        page.evaluate("switchView('sheets'); toggleManualPasteBox()")
        page.wait_for_timeout(100)
        sheets_form = page.evaluate(
            """() => {const t=document.querySelector('#manual-csv-input').getBoundingClientRect();return {left:t.left,right:t.right,bottom:t.bottom,width:t.width,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth};}"""
        )
        require(sheets_form["left"] >= 0 and sheets_form["right"] <= width + 0.5, f"{key} expandable form fits viewport", sheets_form)

        page.evaluate("switchView('tv')")
        page.locator("#youruni-site-attribution").scroll_into_view_if_needed()
        footer = page.evaluate(
            """() => {const el=document.querySelector('#youruni-site-attribution');const r=el.getBoundingClientRect();return {text:el.innerText,rect:{left:r.left,right:r.right,width:r.width,height:r.height,bottom:r.bottom},visible:!!(r.width&&r.height),scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth};}"""
        )
        require(footer["text"].strip() == EXACT_ATTRIBUTION, f"{key} exact attribution is visible", footer)
        require(footer["visible"] and footer["rect"]["left"] >= 0 and footer["rect"]["right"] <= width + 0.5, f"{key} attribution is responsive", footer)
        page.evaluate("scrollTo(0, 0)")
        page.wait_for_timeout(80)
        page.screenshot(path=str(ARTIFACTS / f"mobile-{key}-light-tv.png"), full_page=True, animations="disabled")

        page.evaluate("applyTheme('dark', true)")
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(250)
        dark_persisted = page.evaluate("document.documentElement.dataset.theme")
        page.evaluate("applyTheme('light', true)")
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(250)
        light_persisted = page.evaluate("document.documentElement.dataset.theme")
        require(dark_persisted == "dark" and light_persisted == "light", f"{key} theme persists through refresh", {"dark": dark_persisted, "light": light_persisted})

        evidence[key] = {
            "dark": dark_views,
            "light": light_views,
            "form": form_metrics,
            "drawer": drawer,
            "expandableForm": sheets_form,
            "footer": footer,
            "themePersistence": {"dark": dark_persisted, "light": light_persisted},
            "pageErrors": errors,
            "result": "PASS",
        }
        context.close()
    return evidence


def mask_rects(page: Page, selectors: tuple[str, ...], padding: int = 8) -> list[tuple[int, int, int, int]]:
    rects = []
    for selector in selectors:
        try:
            for rect in page.locator(selector).evaluate_all("els => els.filter(el => el.offsetParent !== null).map(el => {const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};})"):
                left = max(0, int(rect["x"]) - padding)
                top = max(0, int(rect["y"]) - padding)
                right = max(left, int(rect["x"] + rect["width"]) + padding)
                bottom = max(top, int(rect["y"] + rect["height"]) + padding)
                rects.append((left, top, right, bottom))
        except Exception:
            continue
    return rects


def pixel_compare(original: Path, current: Path, masks: list[tuple[int, int, int, int]]) -> dict:
    before = Image.open(original).convert("RGB")
    after = Image.open(current).convert("RGB")
    require(before.size == after.size, "desktop screenshot dimensions match", {"original": before.size, "current": after.size})
    diff = ImageChops.difference(before, after)
    channels = diff.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    changed = maximum.point(lambda value: 255 if value > 14 else 0)
    valid = Image.new("L", before.size, 255)
    drawer = ImageDraw.Draw(valid)
    for rect in masks:
        drawer.rectangle(rect, fill=0)
    meaningful = ImageChops.multiply(changed, valid)
    histogram = meaningful.histogram()
    changed_pixels = int(histogram[255])
    valid_pixels = int(sum(valid.histogram()[1:]))
    percentage = (changed_pixels / valid_pixels * 100.0) if valid_pixels else 100.0
    bbox = meaningful.getbbox()
    overlay = Image.new("RGB", before.size, (0, 0, 0))
    overlay.paste((255, 0, 70), mask=meaningful)
    overlay.save(current.with_name(current.stem + "-meaningful-diff.png"))
    return {
        "validComparedPixels": valid_pixels,
        "maskedPixels": before.size[0] * before.size[1] - valid_pixels,
        "changedPixels": changed_pixels,
        "changedPercentage": round(percentage, 6),
        "meaningfulDifferenceBoundingBox": list(bbox) if bbox else None,
    }


def desktop_visual_audit(playwright, snapshot: dict) -> dict:
    port = 8790
    baseline_url = f"http://127.0.0.1:{port}/"
    baseline_process = None
    if http_status(baseline_url, 1) != 200:
        baseline_process = subprocess.Popen(
            [str(PYTHON), str(BASELINE_SERVER), "--port", str(port)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        wait_until(lambda: http_status(baseline_url, 2) == 200, 15, "loopback visual baseline server", 0.25)

    browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
    results = {}
    dynamic = (
        "#live-clock",
        "#live-date",
        "#tv-tasks-container",
        "#tv-task-count",
        "#tv-progress-percent",
        "#tv-progress-bar",
        "#tv-progress-text",
        "#tv-ticker-text",
        "#enter-tv-presentation-btn",
        "#youruni-site-attribution",
    )
    try:
        for width, height in DESKTOP_VIEWPORTS:
            key = f"{width}x{height}"
            baseline_context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
            current_context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
            baseline_context.route("**/api/state", lambda route, body=json.dumps(snapshot): route.fulfill(status=200, content_type="application/json", body=body))
            baseline_context.route("**/api/events", lambda route: route.abort())
            baseline_page = baseline_context.new_page()
            current_page = current_context.new_page()
            baseline_page.goto(baseline_url, wait_until="domcontentloaded", timeout=60_000)
            current_page.goto(PUBLIC_URL, wait_until="domcontentloaded", timeout=60_000)
            baseline_page.evaluate("""() => { const save=[...document.querySelectorAll('button')].find(button => button.textContent.trim().includes('Save All')); if (save) save.remove(); }""")
            for page in (baseline_page, current_page):
                page.evaluate("""() => {
                  try { localStorage.setItem('youruni_theme','dark'); } catch (_) {}
                  document.documentElement.classList.add('dark');
                  document.documentElement.dataset.theme='dark';
                  if (typeof setLanguage==='function') setLanguage('en');
                  if (typeof switchView==='function') switchView('tv');
                  for (const [svgId,imgId] of [['default-logo-svg','custom-logo-img'],['tv-default-logo-svg','tv-custom-logo-img']]) {
                    const svg=document.getElementById(svgId), img=document.getElementById(imgId);
                    if (svg) svg.classList.remove('hidden');
                    if (img) img.classList.add('hidden');
                  }
                }""")
                page.add_style_tag(content="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}")
                page.evaluate("document.fonts && document.fonts.ready")
                page.wait_for_timeout(700)

            original_path = ARTIFACTS / f"desktop-{key}-original.png"
            current_path = ARTIFACTS / f"desktop-{key}-current.png"
            baseline_page.screenshot(path=str(original_path), full_page=False, animations="disabled")
            current_page.screenshot(path=str(current_path), full_page=False, animations="disabled")
            masks = mask_rects(baseline_page, dynamic) + mask_rects(current_page, dynamic)
            comparison = pixel_compare(original_path, current_path, masks)
            comparison["maskRects"] = [list(rect) for rect in masks]
            comparison["result"] = "PASS" if comparison["changedPercentage"] <= 0.2 else "FAIL"
            require(comparison["result"] == "PASS", f"desktop stable UI preserved at {key}", comparison)
            results[key] = comparison
            baseline_context.close()
            current_context.close()
    finally:
        browser.close()
        if baseline_process is not None:
            baseline_process.terminate()
            try:
                baseline_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                baseline_process.kill()
    return results


def security_snapshot() -> dict:
    snapshot = powershell_json(
        r"""
$av = @(Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct | Select-Object displayName,productState)
$mp = Get-MpComputerStatus | Select-Object AMRunningMode,AntivirusEnabled,RealTimeProtectionEnabled
$fw = @(Get-NetFirewallProfile | Select-Object Name,Enabled)
$pref = Get-MpPreference
$exclusionPaths = @($pref.ExclusionPath | Where-Object { $_ })
$exclusionProcesses = @($pref.ExclusionProcess | Where-Object { $_ })
$exclusionExtensions = @($pref.ExclusionExtension | Where-Object { $_ })
$projectRoot = 'C:\Users\user\Desktop\Slide show'
$projectRelevant = @($exclusionPaths | Where-Object { $_.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) })
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -eq 8787} | Select-Object LocalAddress,LocalPort,OwningProcess)
$sqlite = @(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^sqlite'} | Select-Object Name,ProcessId,ExecutablePath)
[pscustomobject]@{
  antivirus=$av
  defender=$mp
  firewall=$fw
  exclusionPathCount=$exclusionPaths.Count
  exclusionProcessCount=$exclusionProcesses.Count
  exclusionExtensionCount=$exclusionExtensions.Count
  projectRelevantExclusionCount=$projectRelevant.Count
  listeners=$listeners
  sqliteProcesses=$sqlite
} | ConvertTo-Json -Depth 8 -Compress
"""
    )
    antivirus = snapshot.get("antivirus") or []
    if isinstance(antivirus, dict):
        antivirus = [antivirus]
    firewall = snapshot.get("firewall") or []
    if isinstance(firewall, dict):
        firewall = [firewall]
    listeners = snapshot.get("listeners") or []
    if isinstance(listeners, dict):
        listeners = [listeners]
    require(any("reason" in str(item.get("displayName", "")).lower() for item in antivirus), "Reason Cybersecurity remains registered", antivirus)
    require(str(snapshot.get("defender", {}).get("AMRunningMode", "")).lower().startswith("passive"), "Microsoft Defender remains passive", snapshot.get("defender"))
    require(len(firewall) >= 3 and all(bool(item.get("Enabled")) for item in firewall), "Windows Firewall remains enabled", firewall)
    require(int(snapshot.get("projectRelevantExclusionCount", 0)) == 0, "project-relevant antivirus exclusions remain absent", snapshot)
    require(listeners and all(item.get("LocalAddress") == "127.0.0.1" for item in listeners), "application origin remains loopback-only", listeners)
    require(not snapshot.get("sqliteProcesses"), "SQLite has no network service process", snapshot.get("sqliteProcesses"))
    return snapshot


def main() -> int:
    results = {
        "tvPresentationMode": "FAIL",
        "tvNativeFullscreen": "FAIL",
        "tvPresentationFallback": "FAIL",
        "tvExitFullscreen": "FAIL",
        "tvRealtimeInPresentation": "FAIL",
        "tvReconnectInPresentation": "FAIL",
        "globalDayTheme": "FAIL",
        "globalNightTheme": "FAIL",
        "themePersistence": "FAIL",
        "mobile320": "FAIL",
        "mobile360": "FAIL",
        "mobile390": "FAIL",
        "mobile430": "FAIL",
        "mobile768": "FAIL",
        "mobileDayTheme": "FAIL",
        "mobileNightTheme": "FAIL",
        "attributionVisible": "FAIL",
        "attributionTest": "FAIL",
        "attributionResponsive": "FAIL",
        "desktopDesignPreserved": "FAIL",
        "liveCrudRegression": "FAIL",
        "databaseIntegrity": "FAIL",
        "publicHttp200": False,
        "testDataCleanup": "FAIL",
        "overall": "FAIL",
    }
    evidence = {}
    browser = None
    failure = None
    cleanup_test_rows()

    try:
        require(DB_PATH.resolve() == (ROOT / ".data" / "thebekal.db").resolve() and DB_PATH.is_file(), "SQLite database remains at project path", DB_PATH)
        require(http_status(LOCAL_URL) == 200, "localhost returns HTTP 200")
        require(http_status(PUBLIC_URL, 20) == 200, "public HTTPS returns HTTP 200")
        results["publicHttp200"] = True

        source = FRONTEND.read_text(encoding="utf-8")
        public_html = urllib.request.urlopen(urllib.request.Request(PUBLIC_URL, headers={"User-Agent": "YourUni-UX-Acceptance/1.0"}), timeout=20).read().decode("utf-8")
        attribution_checks = {
            "stableFooterId": 'id="youruni-site-attribution"' in source,
            "sourceComment": "YOURUNI PROJECT ATTRIBUTION" in source and "intentional project requirement" in source,
            "sourceExactText": EXACT_ATTRIBUTION in source,
            "publicExactText": EXACT_ATTRIBUTION in public_html,
            "singleCentralizedText": source.count(EXACT_ATTRIBUTION) == 1,
        }
        require(all(attribution_checks.values()), "automated attribution source/public presence test", attribution_checks)
        results["attributionVisible"] = "PASS"
        results["attributionTest"] = "PASS"
        require("Save All" not in source and "saveAll" not in source, "Save All remains absent")
        evidence["attribution"] = attribution_checks

        with sync_playwright() as playwright:
            mobile_browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
            try:
                mobile = mobile_audit(mobile_browser)
            finally:
                mobile_browser.close()
            evidence["mobile"] = mobile
            for width, height in MOBILE_VIEWPORTS:
                results[f"mobile{width}"] = "PASS"
            results["mobileDayTheme"] = "PASS"
            results["mobileNightTheme"] = "PASS"
            results["attributionResponsive"] = "PASS"
            results["themePersistence"] = "PASS"

            browser = playwright.chromium.launch(
                executable_path=str(CHROME),
                headless=True,
                args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
            )
            tv_context = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
            mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
            desktop_context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, ignore_https_errors=True, locale="en-US", timezone_id="Asia/Kuala_Lumpur")
            fallback_context = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1, ignore_https_errors=True)
            tv = tv_context.new_page()
            mobile_page = mobile_context.new_page()
            desktop = desktop_context.new_page()
            fallback = fallback_context.new_page()
            tv_network = {"stateResponses200": 0, "eventRequests": 0}

            def tv_response(response):
                path = urlparse(response.url).path
                if path == "/api/state" and response.status == 200:
                    tv_network["stateResponses200"] += 1

            def tv_request(request):
                if urlparse(request.url).path == "/api/events":
                    tv_network["eventRequests"] += 1

            tv.on("response", tv_response)
            tv.on("request", tv_request)
            for page in (tv, mobile_page, desktop, fallback):
                load_public(page)

            # Global theme reaches every desktop view and all common form/surface elements.
            desktop.evaluate("applyTheme('dark', true)")
            dark_desktop = {view: theme_metrics(desktop, view) for view in ("tv", "admin", "sheets")}
            desktop.evaluate("applyTheme('light', true)")
            desktop.wait_for_timeout(350)
            light_desktop = {view: theme_metrics(desktop, view) for view in ("tv", "admin", "sheets")}
            require(all(item["theme"] == "dark" and item["rootDarkClass"] for item in dark_desktop.values()), "Night theme applies to every desktop view", dark_desktop)
            require(all(item["theme"] == "light" and not item["rootDarkClass"] for item in light_desktop.values()), "Day theme applies to every desktop view", light_desktop)
            require(light_desktop["admin"]["bodyBackground"] != dark_desktop["admin"]["bodyBackground"], "global Day/Night backgrounds differ", {"dark": dark_desktop, "light": light_desktop})
            require(light_desktop["admin"]["inputBackground"] != dark_desktop["admin"]["inputBackground"], "global Day/Night forms differ", {"dark": dark_desktop["admin"], "light": light_desktop["admin"]})
            results["globalDayTheme"] = "PASS"
            results["globalNightTheme"] = "PASS"
            evidence["desktopThemes"] = {"dark": dark_desktop, "light": light_desktop}

            # Native fullscreen and browser-driven fullscreenchange exit.
            tv.evaluate("applyTheme('dark', true); switchView('tv')")
            tv.click("#enter-tv-presentation-btn")
            wait_until(lambda: tv.evaluate("document.querySelector('#view-tv').classList.contains('tv-presentation-mode')"), 5, "TV presentation mode")
            native = wait_until(lambda: tv.evaluate("document.fullscreenElement && document.fullscreenElement.id"), 5, "native element fullscreen")
            native_metrics = presentation_metrics(tv)
            require(native == "view-tv", "native fullscreen targets only TV view", native_metrics)
            require(native_metrics["headerDisplay"] == "none" and native_metrics["footerDisplay"] == "none" and native_metrics["exitDisplay"] == "flex", "presentation hides chrome and keeps exit", native_metrics)
            require(abs(native_metrics["rect"]["width"] - native_metrics["viewport"]["width"]) <= 1 and abs(native_metrics["rect"]["height"] - native_metrics["viewport"]["height"]) <= 1, "presentation fills viewport", native_metrics)
            require(native_metrics["doc"]["scrollWidth"] == native_metrics["doc"]["clientWidth"] and native_metrics["doc"]["scrollHeight"] == native_metrics["doc"]["clientHeight"], "presentation has no page overflow", native_metrics)
            tv.screenshot(path=str(ARTIFACTS / "tv-native-fullscreen-dark.png"), full_page=False, animations="disabled")
            results["tvPresentationMode"] = "PASS"
            results["tvNativeFullscreen"] = "PASS"

            tv.evaluate("document.exitFullscreen()")
            wait_until(lambda: not tv.evaluate("document.querySelector('#view-tv').classList.contains('tv-presentation-mode')"), 5, "fullscreenchange exits presentation")
            change_exit = presentation_metrics(tv)
            require(change_exit["headerDisplay"] != "none" and change_exit["footerDisplay"] != "none", "browser fullscreen exit restores app chrome", change_exit)

            tv.click("#enter-tv-presentation-btn")
            wait_until(lambda: tv.evaluate("document.fullscreenElement && document.fullscreenElement.id === 'view-tv'"), 5, "native fullscreen re-entry")
            tv.click("#exit-tv-presentation-btn")
            wait_until(lambda: not tv.evaluate("document.fullscreenElement") and not tv.evaluate("document.querySelector('#view-tv').classList.contains('tv-presentation-mode')"), 5, "explicit fullscreen exit")
            explicit_exit = presentation_metrics(tv)
            require(explicit_exit["headerDisplay"] != "none" and explicit_exit["footerDisplay"] != "none", "explicit exit restores normal TV view", explicit_exit)
            results["tvExitFullscreen"] = "PASS"

            # Re-enter and retain this same TV page through CRUD and app-child recovery.
            tv.click("#enter-tv-presentation-btn")
            wait_until(lambda: tv.evaluate("document.fullscreenElement && document.fullscreenElement.id === 'view-tv'"), 5, "retained TV native fullscreen")
            tv.evaluate("applyTheme('light', false)")
            tv.wait_for_timeout(350)
            presentation_light = presentation_metrics(tv)
            tv.evaluate("applyTheme('dark', false)")
            tv.wait_for_timeout(350)
            presentation_dark = presentation_metrics(tv)
            require(presentation_light["theme"] == "light" and presentation_dark["theme"] == "dark" and presentation_light["frameBackground"] != presentation_dark["frameBackground"], "global theme works inside presentation", {"light": presentation_light, "dark": presentation_dark})

            # Fallback: force Fullscreen API rejection; presentation remains useful and exit works.
            fallback.evaluate("""() => Object.defineProperty(Element.prototype, 'requestFullscreen', {configurable:true,value:function(){return Promise.reject(new DOMException('Blocked by acceptance harness','NotAllowedError'));}})""")
            fallback.click("#enter-tv-presentation-btn")
            fallback.wait_for_timeout(350)
            fallback_metrics = presentation_metrics(fallback)
            require(fallback_metrics["mode"] and fallback_metrics["nativeElement"] is None, "presentation fallback survives native rejection", fallback_metrics)
            require(fallback_metrics["headerDisplay"] == "none" and fallback_metrics["footerDisplay"] == "none" and fallback_metrics["exitDisplay"] == "flex", "fallback hides app chrome and keeps exit", fallback_metrics)
            fallback.screenshot(path=str(ARTIFACTS / "tv-presentation-fallback.png"), full_page=False, animations="disabled")
            fallback.click("#exit-tv-presentation-btn")
            fallback.wait_for_timeout(180)
            require(not presentation_metrics(fallback)["mode"], "fallback exit control restores normal app")
            results["tvPresentationFallback"] = "PASS"

            test_title = "CODEX-UX-TEST-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            desktop.evaluate("switchView('tv')")
            mobile_page.evaluate("applyTheme('dark', true); switchView('admin')")
            mobile_page.select_option("#admin-day-select", "0")
            mobile_page.fill("#task-title", test_title)
            mobile_page.select_option("#task-dept", "General Office")
            mobile_page.select_option("#task-priority", "Important")
            mobile_page.fill("#task-time", "UX acceptance")
            mobile_page.fill("#task-desc", "Created from mobile while TV is in presentation mode")
            mobile_page.click("#add-task-form button[type='submit']")
            created = wait_until(lambda: (rows[0] if len(rows := db_find_exact(test_title)) == 1 else None), 15, "mobile create committed to SQLite")
            task_id = int(created["id"])
            for name, page in (("TV", tv), ("Mobile", mobile_page), ("Desktop", desktop)):
                wait_until(lambda page=page: dom_count(page, task_id) == 1, 15, f"{name} sees created task exactly once")
            require(db_task(task_id) is not None, "create is immediately persistent in SQLite", db_task(task_id))

            updated_title = test_title + "-UPDATED"
            browser_request(desktop, "PATCH", f"/api/tasks/{task_id}", {"title": updated_title, "desc": "Edited from desktop while TV is fullscreen"})
            wait_until(lambda: (row := db_task(task_id)) and row["title"] == updated_title and row["description"] == "Edited from desktop while TV is fullscreen", 15, "desktop edit committed to SQLite")
            for name, page in (("TV", tv), ("Mobile", mobile_page), ("Desktop", desktop)):
                wait_until(lambda page=page: updated_title in task_text(page, task_id), 15, f"{name} receives edited task")
                require(dom_count(page, task_id) == 1, f"{name} retains one task DOM after edit", dom_count(page, task_id))

            status_button = mobile_page.locator(f'[data-task-id="{task_id}"] button').first
            status_button.click()
            wait_until(lambda: (row := db_task(task_id)) and row["status"] == "completed", 15, "mobile status change committed to SQLite")
            wait_until(lambda: "Completed" in task_text(tv, task_id), 15, "TV receives status change while fullscreen")

            before_restart_state = tv_network["stateResponses200"]
            restart = restart_application_child()
            def tv_reconciled_after_restart():
                tv.wait_for_timeout(50)  # pumps Playwright network events in sync mode
                return tv_network["stateResponses200"] > before_restart_state

            wait_until(tv_reconciled_after_restart, 45, "TV authoritative state reconciliation after app restart", 0.5)
            after_restart_layout = presentation_metrics(tv)
            require(after_restart_layout["mode"] and after_restart_layout["nativeElement"] == "view-tv", "TV presentation survives application restart", after_restart_layout)
            recovered_title = test_title + "-RECOVERED"
            browser_request(desktop, "PATCH", f"/api/tasks/{task_id}", {"title": recovered_title, "desc": "Live after SYSTEM application recovery"})
            wait_until(lambda: recovered_title in task_text(tv, task_id), 20, "next mutation reaches retained fullscreen TV")
            wait_until(lambda: recovered_title in task_text(mobile_page, task_id), 20, "next mutation reaches mobile after app recovery")
            require(dom_count(tv, task_id) == 1 and dom_count(mobile_page, task_id) == 1 and dom_count(desktop, task_id) == 1, "no duplicates after application recovery", {"tv": dom_count(tv, task_id), "mobile": dom_count(mobile_page, task_id), "desktop": dom_count(desktop, task_id)})
            results["tvReconnectInPresentation"] = "PASS"

            desktop.evaluate("switchView('admin')")
            wait_until(lambda: dom_count(desktop, task_id) == 1, 10, "desktop admin task before delete")
            desktop.locator(f'[data-task-id="{task_id}"] button').last.click()
            wait_until(lambda: db_task(task_id) is None, 15, "desktop delete committed to SQLite")
            for name, page in (("TV", tv), ("Mobile", mobile_page), ("Desktop", desktop)):
                wait_until(lambda page=page: dom_count(page, task_id) == 0, 15, f"{name} receives delete")
            results["tvRealtimeInPresentation"] = "PASS"
            results["liveCrudRegression"] = "PASS"
            evidence["presentation"] = {
                "native": native_metrics,
                "browserFullscreenChangeExit": change_exit,
                "explicitExit": explicit_exit,
                "fallback": fallback_metrics,
                "themeLight": presentation_light,
                "themeDark": presentation_dark,
                "network": tv_network,
                "restart": restart,
                "afterRestartLayout": after_restart_layout,
                "taskId": task_id,
            }

            fallback_context.close()
            desktop_context.close()
            mobile_context.close()
            tv_context.close()
            browser.close()
            browser = None

            snapshot = get_json(LOCAL_URL + "api/state")
            desktop_visual = desktop_visual_audit(playwright, snapshot)
            evidence["desktopVisual"] = {
                "baseline": str(ORIGINAL_BASELINE.relative_to(ROOT)).replace("\\", "/"),
                "browser": "Chrome Dev",
                "deviceScaleFactor": 1,
                "authorizedMasks": ["clock/date", "dynamic tasks/progress/ticker", "new fullscreen control", "new normal-page footer"],
                "viewports": desktop_visual,
            }
            results["desktopDesignPreserved"] = "PASS"

        with db_connection() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        require(integrity == "ok", "SQLite integrity_check", integrity)
        results["databaseIntegrity"] = "PASS"
        evidence["database"] = {"path": str(DB_PATH), "integrityCheck": integrity, "sourceOfTruth": "SQLite backend API"}

        sensitive = {}
        for path in ("/.data/", "/.tools/", "/.logs/", "/.backup/", "/DEPLOYMENT.md"):
            sensitive[path] = http_status(PUBLIC_URL.rstrip("/") + path, 10)
        require(all(status not in (200, 206) for status in sensitive.values()), "sensitive project paths remain blocked", sensitive)
        security = security_snapshot()
        cloud_child = process_info("cloudflared")
        cloud_watchdog = process_info("cloud_watchdog")
        require(cloud_child and cloud_child["owner"].upper() == r"NT AUTHORITY\SYSTEM" and cloud_child["session"] == 0, "Cloudflare connector remains SYSTEM Session 0", cloud_child)
        require(bool(cloud_watchdog), "Cloudflare watchdog remains present", cloud_watchdog)
        evidence["security"] = security
        evidence["sensitivePaths"] = sensitive
        evidence["cloudflare"] = {"child": cloud_child, "watchdog": cloud_watchdog}

        remaining = db_prefix_rows()
        require(not remaining, "temporary UX test data cleaned", remaining)
        results["testDataCleanup"] = "PASS"
        results["overall"] = "PASS"
    except Exception as exc:
        failure = {"error": repr(exc), "traceback": traceback.format_exc()}
        log("acceptance failed", **failure)
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        try:
            removed = cleanup_test_rows()
            remaining = db_prefix_rows()
            evidence["cleanup"] = {"removedIds": removed, "remaining": remaining}
            if not remaining:
                results["testDataCleanup"] = "PASS"
        except Exception as cleanup_error:
            evidence["cleanupError"] = repr(cleanup_error)
        if failure:
            evidence["failure"] = failure

    payload = {
        "generatedAt": now_iso(),
        "productionUrl": PUBLIC_URL,
        "frontend": str(FRONTEND),
        "exactAttribution": EXACT_ATTRIBUTION,
        "results": results,
        "evidence": evidence,
    }
    RESULT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"results": results, "resultPath": str(RESULT_PATH), "failure": failure}, indent=2, ensure_ascii=False))
    return 0 if results["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
