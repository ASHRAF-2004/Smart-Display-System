"""Interactive production acceptance using a real approved Google identity.

The only human action is Google account selection. After Editor state appears,
the harness performs and cleans production records prefixed CODEX-UPGRADE-TEST-.
"""
import base64
import io
import json
import sqlite3
import sys
import time
import traceback
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.tools/final-acceptance/vendor'), str(ROOT / '.tools/upgrade/vendor')]
from PIL import Image
from playwright.sync_api import sync_playwright
import requests

URL = 'https://thebekal.com'
DB = ROOT / '.data/thebekal.db'
OUT = ROOT / '.logs/upgrade-real-google'
OUT.mkdir(exist_ok=True)
RESULT = {}
PREFIX = 'CODEX-UPGRADE-TEST-'
EXPECTED_EMAIL = 'thalththanwyd@gmail.com'

def check(name, ok, evidence=None):
    RESULT[name] = {'status': 'PASS' if ok else 'FAIL', 'evidence': evidence}
    print(name, RESULT[name]['status'], flush=True)
    if not ok:
        raise AssertionError(f'{name}: {evidence}')

def db_row(sql, args=()):
    with sqlite3.connect(DB) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, args).fetchone()

def fixture(fmt, animated=False):
    output = io.BytesIO()
    first = Image.new('RGB', (640, 360), '#381565')
    second = Image.new('RGB', (640, 360), '#f2b629')
    first.save(output, format=fmt, save_all=animated,
               append_images=[second] if animated else [], duration=200, loop=0)
    return output.getvalue()

def api(page, method, path, payload=None):
    return page.evaluate("""async ({method,path,payload}) => {
      const response = await fetch(path, {
        method,
        headers: {'Content-Type':'application/json','X-CSRF-Token':window.staffAuth.csrfToken},
        body: payload === null ? undefined : JSON.stringify(payload), cache:'no-store'
      });
      let body={}; try { body=await response.json(); } catch {}
      return {status:response.status,body};
    }""", {'method': method, 'path': path, 'payload': payload})

def upload(page, task_id, name, mime, raw):
    encoded = base64.b64encode(raw).decode()
    return page.evaluate("""async ({taskId,name,mime,encoded}) => {
      const binary=atob(encoded), bytes=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i);
      const response=await fetch(`/api/tasks/${taskId}/media`,{
        method:'POST',headers:{'Content-Type':mime,'X-File-Name':encodeURIComponent(name),'X-CSRF-Token':window.staffAuth.csrfToken},body:bytes
      });
      let body={};try{body=await response.json();}catch{}
      return {status:response.status,body};
    }""", {'taskId': task_id, 'name': name, 'mime': mime, 'encoded': encoded})

def wait_version(page, task):
    page.wait_for_function('(t)=>canonicalTaskState.get(t.id)?.version===t.version', arg=task)

def run():
    original_day = db_row("SELECT json_extract(value_json,'$') value FROM settings WHERE key='selectedDay'")['value']
    task_id = None
    old_cookie = None
    old_csrf = None
    browser = None
    editor = None
    try:
        with sync_playwright() as p:
            options = argparse.ArgumentParser()
            options.add_argument('--cdp')
            options.add_argument('--profile')
            args = options.parse_args()
            if args.profile:
                editor = p.chromium.launch_persistent_context(
                    args.profile,
                    executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                    headless=False,
                    viewport={'width': 1440, 'height': 900}
                )
                page = next((candidate for candidate in editor.pages if candidate.url.startswith(URL)), None) or editor.new_page()
                if not page.url.startswith(URL):
                    page.goto(URL)
                browser = p.chromium.launch(
                    executable_path=r'C:\Program Files\Google\Chrome Dev\Application\chrome.exe',
                    headless=True
                )
            elif args.cdp:
                browser = p.chromium.connect_over_cdp(args.cdp)
                editor = browser.contexts[0]
                page = next((candidate for candidate in editor.pages if candidate.url.startswith(URL)), None) or editor.new_page()
                if not page.url.startswith(URL):
                    page.goto(URL)
            else:
                browser = p.chromium.launch(
                    executable_path=r'C:\Program Files\Google\Chrome Dev\Application\chrome.exe',
                    headless=False,
                    args=['--start-maximized']
                )
                editor = browser.new_context(viewport={'width': 1440, 'height': 900})
                page = editor.new_page(); page.goto(URL)
            page.wait_for_function("window.staffAuth?.clientId?.startsWith('464713909773-')")
            print('WAITING_FOR_REAL_GOOGLE_SIGN_IN', flush=True)
            page.bring_to_front()
            page.wait_for_function("window.staffAuth?.role==='editor'", timeout=900_000)
            auth = page.evaluate('window.staffAuth')
            old_csrf = auth['csrfToken']
            check('real_google_login', auth.get('email') == EXPECTED_EMAIL, auth.get('email'))
            check('real_editor_role', auth.get('role') == 'editor', {
                'role': auth.get('role'), 'email': auth.get('email'), 'name': auth.get('name')
            })
            cookies = editor.cookies(URL)
            session_cookie = next(c for c in cookies if c['name'] == '__Host-youruni_session')
            old_cookie = session_cookie['value']
            check('real_secure_cookie', session_cookie['httpOnly'] and session_cookie['secure'] and session_cookie['sameSite'] == 'Lax')

            viewer_mobile = browser.new_context(viewport={'width': 390, 'height': 844})
            mobile = viewer_mobile.new_page(); mobile.goto(URL)
            viewer_tv = browser.new_context(viewport={'width': 1920, 'height': 1080})
            tv = viewer_tv.new_page(); tv.goto(URL)
            page.evaluate("switchView('admin')")
            mobile.evaluate("switchView('admin')")
            check('editor_controls_visible', page.locator('#add-task-button:visible').count() == 1 and not page.locator('#admin-day-select').is_disabled())
            check('viewer_controls_absent', mobile.locator('#view-admin button:visible,#view-admin input:visible,#view-admin textarea:visible').count() == 0 and mobile.locator('#admin-day-select').is_disabled())

            future = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
            payload = {'day': original_day, 'title': PREFIX + 'REAL-GOOGLE', 'dept': 'General Office',
                       'priority': 'Normal', 'status': 'pending', 'desc': 'Real Google acceptance task',
                       'time': 'Acceptance', 'deadlineUtc': future}
            made = api(page, 'POST', '/api/tasks', payload)
            check('authenticated_create', made['status'] == 201, made)
            task = made['body']['task']; task_id = task['id']
            wait_version(mobile, task); wait_version(tv, task)
            check('sse_create_to_viewers', True)
            actor = db_row('SELECT created_by,updated_by FROM tasks WHERE id=?', (task_id,))
            check('real_actor_identity', actor['created_by'] == auth['email'] and actor['updated_by'] == auth['email'], dict(actor))

            changed_deadline = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0).isoformat()
            edited = api(page, 'PATCH', f'/api/tasks/{task_id}', {
                'title': PREFIX + 'REAL-GOOGLE-EDITED', 'desc': 'Edited through the real Editor session',
                'priority': 'Urgent', 'deadlineUtc': changed_deadline
            })
            check('authenticated_edit_deadline', edited['status'] == 200, edited)
            task = edited['body']['task']; wait_version(mobile, task); wait_version(tv, task)
            check('sse_edit_deadline', True)

            png = upload(page, task_id, PREFIX + 'image.png', 'image/png', fixture('PNG'))
            check('authenticated_image_upload', png['status'] == 201, png)
            task = png['body']['task']; wait_version(mobile, task); wait_version(tv, task)
            tv.wait_for_selector(f'[data-task-id="{task_id}"] img')

            gif = upload(page, task_id, PREFIX + 'animated.gif', 'image/gif', fixture('GIF', True))
            check('authenticated_media_replace_gif', gif['status'] == 201, gif)
            task = gif['body']['task']; wait_version(mobile, task); wait_version(tv, task)

            video64 = page.evaluate("""async()=>{
              const c=document.createElement('canvas');c.width=320;c.height=180;const x=c.getContext('2d');
              const stream=c.captureStream(10),r=new MediaRecorder(stream,{mimeType:'video/webm'}),chunks=[];
              r.ondataavailable=e=>chunks.push(e.data);const done=new Promise(ok=>r.onstop=ok);r.start();
              for(let i=0;i<8;i++){x.fillStyle=i%2?'#381565':'#f2b629';x.fillRect(0,0,320,180);await new Promise(ok=>setTimeout(ok,100));}
              r.stop();await done;stream.getTracks().forEach(t=>t.stop());
              return btoa(String.fromCharCode(...new Uint8Array(await new Blob(chunks).arrayBuffer())));
            }""")
            video = upload(page, task_id, PREFIX + 'video.webm', 'video/webm', base64.b64decode(video64))
            check('authenticated_media_replace_video', video['status'] == 201, video)
            task = video['body']['task']; wait_version(mobile, task); wait_version(tv, task)
            tv.wait_for_selector(f'[data-task-id="{task_id}"] video')
            check('tv_video_muted', tv.locator(f'[data-task-id="{task_id}"] video').evaluate('v=>v.muted'))

            removed = api(page, 'DELETE', f'/api/tasks/{task_id}/media', {})
            check('authenticated_media_remove', removed['status'] == 200 and removed['body']['task']['media'] is None, removed)
            task = removed['body']['task']; wait_version(mobile, task); wait_version(tv, task)

            completed = api(page, 'PATCH', f'/api/tasks/{task_id}', {'status': 'completed'})
            check('authenticated_status_complete', completed['status'] == 200, completed)
            task = completed['body']['task']; wait_version(mobile, task); wait_version(tv, task)
            reopened = api(page, 'PATCH', f'/api/tasks/{task_id}', {'status': 'pending'})
            check('authenticated_status_reopen', reopened['status'] == 200, reopened)
            task = reopened['body']['task']; wait_version(mobile, task); wait_version(tv, task)

            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            rejected = api(page, 'PATCH', f'/api/tasks/{task_id}', {'deadlineUtc': past})
            check('real_backend_past_rejection', rejected['status'] == 400 and rejected['body'].get('error') == 'deadline_in_past', rejected)

            other_day = next(day for day in ['Saturday','Sunday','Monday','Tuesday','Wednesday','Thursday','Friday'] if day != original_day)
            selected = api(page, 'PUT', '/api/selected-day', {'selectedDay': other_day})
            check('authenticated_selected_day', selected['status'] == 200, selected)
            mobile.wait_for_function('(d)=>selectedOperationalDay===d', arg=other_day)
            tv.wait_for_function('(d)=>selectedOperationalDay===d', arg=other_day)
            restored = api(page, 'PUT', '/api/selected-day', {'selectedDay': original_day})
            check('selected_day_restored', restored['status'] == 200, restored)
            mobile.wait_for_function('(d)=>selectedOperationalDay===d', arg=original_day)
            tv.wait_for_function('(d)=>selectedOperationalDay===d', arg=original_day)
            check('sse_selected_day', True)

            tv.evaluate("switchView('tv')")
            tv.locator('#enter-tv-presentation-btn').click()
            tv.wait_for_function("document.fullscreenElement?.id==='view-tv'")
            check('real_tv_fullscreen', True)
            check('real_tv_no_scroll', tv.evaluate("(()=>{const v=document.getElementById('view-tv'),b=document.getElementById('tv-tasks-container');return v.scrollHeight<=v.clientHeight&&b.scrollHeight<=b.clientHeight&&b.scrollWidth<=b.clientWidth})()"))
            tv.screenshot(path=str(OUT / 'real-tv-fullscreen.png'))
            tv.locator('#exit-tv-presentation-btn').click()

            cookie_header = '__Host-youruni_session=' + old_cookie
            direct = page.request.post(URL + '/api/tasks', data=payload, headers={'Origin': URL, 'Cookie': cookie_header, 'X-CSRF-Token': 'forged'})
            check('authenticated_direct_csrf_block', direct.status == 403, direct.status)
            cross = page.request.post(URL + '/api/tasks', data=payload, headers={'Origin': 'https://evil.example', 'Cookie': cookie_header, 'X-CSRF-Token': auth['csrfToken']})
            check('authenticated_cross_origin_block', cross.status == 403, cross.status)

            deleted = api(page, 'DELETE', f'/api/tasks/{task_id}', {})
            check('authenticated_delete', deleted['status'] == 200, deleted)
            mobile.wait_for_function('(id)=>!canonicalTaskState.get(id)', arg=task_id)
            tv.wait_for_function('(id)=>!canonicalTaskState.get(id)', arg=task_id)
            task_id = None
            check('sse_delete', True)

            page.evaluate("switchView('admin')")
            with page.expect_response(lambda response: response.url.endswith('/api/auth/logout')) as logout_info:
                page.locator('#staff-logout').click()
            check('real_logout', logout_info.value.status == 200, logout_info.value.status)
            page.wait_for_function("window.staffAuth?.role==='viewer'")
            check('editor_controls_removed_after_logout', page.locator('#add-task-button:visible').count() == 0)
            browser.close(); browser = None
            if args.profile:
                editor.close(); editor = None
    finally:
        if browser:
            browser.close()
        if editor and hasattr(editor, 'close'):
            editor.close()
        with sqlite3.connect(DB) as connection:
            media_names = [row[0] for row in connection.execute("SELECT storage_name FROM task_media WHERE task_id IN (SELECT id FROM tasks WHERE title LIKE ?)", (PREFIX + '%',))]
            connection.execute("DELETE FROM tasks WHERE title LIKE ?", (PREFIX + '%',))
            connection.execute("DELETE FROM login_challenges")
            connection.commit()
            remaining = connection.execute("SELECT count(*) FROM tasks WHERE title LIKE ?", (PREFIX + '%',)).fetchone()[0]
            check('real_test_cleanup', remaining == 0, remaining)
            retained = connection.execute("SELECT role,active FROM authorized_users WHERE email=?", (EXPECTED_EMAIL,)).fetchone()
            check('developer_editor_retained_by_owner_request', retained == ('editor', 1), retained)
            check('real_sqlite_integrity', connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
        media_root = ROOT / '.data/media'
        for name in media_names:
            if Path(name).name == name and (media_root / name).is_file():
                (media_root / name).unlink()
        if old_cookie:
            session = requests.get(URL + '/api/auth/session', headers={'Cookie': '__Host-youruni_session=' + old_cookie}, timeout=15).json()
            check('logged_out_session_is_viewer', session.get('role') == 'viewer', session)
            denied = requests.post(URL + '/api/tasks', json={'title': PREFIX + 'DENIED'}, headers={
                'Origin': URL, 'Cookie': '__Host-youruni_session=' + old_cookie,
                'X-CSRF-Token': old_csrf or ''}, timeout=15)
            check('logged_out_session_mutation_denied', denied.status_code == 401, denied.status_code)

if __name__ == '__main__':
    try:
        run()
    except Exception:
        RESULT['exception'] = {'status': 'FAIL', 'evidence': traceback.format_exc()}
        print(traceback.format_exc(), flush=True)
    finally:
        (OUT / 'results.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
    sys.exit(any(item['status'] == 'FAIL' for item in RESULT.values()))
