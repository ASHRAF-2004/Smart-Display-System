"""Public read-only security acceptance and authorized watchdog-child recovery."""
import sys,json,time,sqlite3,traceback,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'.tools/upgrade/vendor'),str(ROOT/'.tools/final-acceptance/vendor'),str(ROOT/'.tools/ux-acceptance')]
import requests
import ux_acceptance as infrastructure
from playwright.sync_api import sync_playwright
OUT=ROOT/'.logs/upgrade-production'
OUT.mkdir(exist_ok=True)
RESULT={}
URL='https://thebekal.com'
def check(name,ok,evidence=None):
    RESULT[name]={'status':'PASS' if ok else 'FAIL','evidence':evidence}
    print(name,RESULT[name]['status'],flush=True)
    if not ok:raise AssertionError(f'{name}: {evidence}')
def health(url,timeout=60):
    start=time.time()
    while time.time()-start<timeout:
        try:
            if requests.get(url,timeout=5).status_code==200:return True
        except requests.RequestException:pass
        time.sleep(1)
    return False
def run():
    check('localhost_200',health('http://127.0.0.1:8787/'))
    check('public_200',health(URL+'/'))
    for method,path in [('POST','/api/tasks'),('PATCH','/api/tasks/1'),('DELETE','/api/tasks/1'),('POST','/api/tasks/1/media'),('PUT','/api/selected-day'),('PUT','/api/settings'),('POST','/api/tasks/reset'),('POST','/api/tasks/import')]:
        r=requests.request(method,URL+path,json={},headers={'Origin':URL},timeout=10)
        check('anonymous_'+method+path,r.status_code==401,r.status_code)
    for path in ['/.data/','/.data/thebekal.db','/.data/media/','/.tools/','/.logs/','/.backup/','/DEPLOYMENT.md','/security.py','/app.py']:
        r=requests.get(URL+path,timeout=10);check('blocked_'+path,r.status_code==404,r.status_code)
    check('cross_origin_auth',requests.post(URL+'/api/auth/challenge',json={},headers={'Origin':'https://evil.example'},timeout=10).status_code==403)
    with sqlite3.connect(ROOT/'.data/thebekal.db') as c:
        before=c.execute('SELECT id,title,status,version FROM tasks ORDER BY id').fetchall()
        check('six_editors',c.execute("SELECT count(*) FROM authorized_users WHERE role='editor' AND active=1").fetchone()[0]==6)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome Dev\Application\chrome.exe',headless=True)
        desktop=browser.new_context(viewport={'width':1440,'height':900});page=desktop.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(URL);page.wait_for_function("window.staffAuth?.role==='viewer'")
        page.evaluate("switchView('admin')")
        check('viewer_readonly_ui',page.locator('#view-admin button:visible,#view-admin input:visible,#view-admin textarea:visible').count()==0)
        check('viewer_day_disabled',page.locator('#admin-day-select').is_disabled())
        expected_footer="© Copyright 2026 YourUni. All rights reserved. Developed by ASHRAF2004"
        check('footer',page.locator('#youruni-site-attribution').inner_text().strip()==expected_footer)
        source=(ROOT/'youruni_tv_task_management_system update 17 Aug.html').read_text('utf-8')
        check('no_save_all','Save All' not in source and 'saveAll' not in source)
        mobilectx=browser.new_context(viewport={'width':390,'height':844});mobile=mobilectx.new_page();mobile.goto(URL)
        tvctx=browser.new_context(viewport={'width':1920,'height':1080});tv=tvctx.new_page();tv.goto(URL);tv.locator('#enter-tv-presentation-btn').click();tv.wait_for_function("document.fullscreenElement?.id==='view-tv'")
        navigations=[];tv.on('framenavigated',lambda f:navigations.append(f.url))
        snapshots=[];tv.on('response',lambda r:snapshots.append(r.url) if '/api/state' in r.url and r.status==200 else None)
        check('native_fullscreen',True)
        check('tv_no_scroll',tv.evaluate("(()=>{const e=document.getElementById('view-tv'),b=document.getElementById('tv-tasks-container');return e.scrollHeight<=e.clientHeight&&b.scrollHeight<=b.clientHeight&&b.scrollWidth<=b.clientWidth})()"))
        tv.screenshot(path=str(OUT/'public-tv-1920.png'))
        check('tv_sections_removed',tv.locator('#tv-directives-container,#tv-supervisors-container,#tv-progress-bar').count()==0)
        for kind,watch in [('app','app_watchdog'),('cloudflared','cloud_watchdog')]:
            old=infrastructure.process_info(kind);watchdog=infrastructure.process_info(watch)
            check(kind+'_system_before',bool(old) and old['owner'].upper()==r'NT AUTHORITY\SYSTEM' and old['session']==0,old)
            count=len(snapshots)
            infrastructure.powershell_json(f"Stop-Process -Id {int(old['pid'])} -Force\n@{{stopped={int(old['pid'])}}} | ConvertTo-Json -Compress")
            start=time.time();new=None
            while time.time()-start<60:
                candidate=infrastructure.process_info(kind)
                if candidate and candidate['pid']!=old['pid']:new=candidate;break
                tv.wait_for_timeout(1000)
            check(kind+'_replacement',bool(new) and new['owner'].upper()==r'NT AUTHORITY\SYSTEM' and new['session']==0,new)
            check(kind+'_watchdog_retained',infrastructure.process_info(watch)['pid']==watchdog['pid'])
            check(kind+'_public_recovered',health(URL+'/'))
            check(kind+'_local_recovered',health('http://127.0.0.1:8787/'))
            start=time.time()
            while len(snapshots)<=count and time.time()-start<60:tv.wait_for_timeout(1000)
            check(kind+'_sse_reconciled',len(snapshots)>count)
            check(kind+'_fullscreen_retained',tv.evaluate("document.fullscreenElement?.id==='view-tv'"))
            check(kind+'_no_duplicates',tv.evaluate("(()=>{const ids=[...document.querySelectorAll('#tv-tasks-container [data-task-id]')].map(e=>e.dataset.taskId);return new Set(ids).size===ids.length})()"))
        check('no_browser_reload',not navigations,navigations)
        for width,height in [(320,568),(360,800),(390,844),(430,932),(768,1024)]:
            mobile.set_viewport_size({'width':width,'height':height})
            for theme in ['light','dark']:
                mobile.evaluate('(v)=>applyTheme(v)',theme)
                check(f'mobile_{width}_{theme}',mobile.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth'))
                mobile.screenshot(path=str(OUT/f'public-mobile-{width}-{theme}.png'))
        mobile.evaluate("applyTheme('light')");mobile.reload();check('theme_persistence',mobile.locator('html').get_attribute('data-theme')=='light')
        check('theme_device_local',tv.locator('html').get_attribute('data-theme')=='dark')
        check('javascript_errors',not errors,errors)
        browser.close()
    check('security_environment',bool(infrastructure.security_snapshot()),'See historical harness log for detailed read-only snapshot')
    details=infrastructure.powershell_json(r"""
$services=@(Get-Service | Where-Object {$_.DisplayName -match 'Reason Security Engine|Reason Cybersecurity EDR'} | Select-Object Name,Status)
$portRules=@(Get-NetFirewallPortFilter | Where-Object {$_.LocalPort -eq '8787'} | Get-NetFirewallRule | Where-Object {$_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True'} | Select-Object DisplayName,Action)
@{reasonServices=$services;inbound8787=$portRules} | ConvertTo-Json -Depth 5 -Compress
""")
    check('reason_active',any(s['Status']==4 for s in details['reasonServices']),details['reasonServices'])
    check('no_inbound_8787',not details['inbound8787'],details['inbound8787'])
    with sqlite3.connect(ROOT/'.data/thebekal.db') as c:
        check('company_records_preserved',c.execute('SELECT id,title,status,version FROM tasks ORDER BY id').fetchall()==before)
        check('sqlite_integrity',c.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
        check('cleanup',c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'CODEX-UPGRADE-TEST-%'").fetchone()[0]==0 and c.execute('SELECT count(*) FROM sessions').fetchone()[0]==0)
    check('original_baseline_untouched',hashlib.sha256((ROOT/'.backup/youruni_tv_task_management_system update 17 Aug.original.html').read_bytes()).hexdigest()=='12a31fed782956835e24ad1ea732199fd8f94382365e92f4ea18cf0422273e57')

if __name__=='__main__':
    try:run()
    except Exception:RESULT['exception']={'status':'FAIL','evidence':traceback.format_exc()};print(traceback.format_exc(),flush=True)
    finally:(OUT/'results.json').write_text(json.dumps(RESULT,indent=2),encoding='utf-8')
    sys.exit(any(v['status']=='FAIL' for v in RESULT.values()))
