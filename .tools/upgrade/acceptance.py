"""Isolated acceptance: real HTTP, real RS256 verification, local test certificates only.

Never imports or alters the production server process; fixture certificate transport
is patched only in this harness process. No production debug/auth bypass exists.
"""
import sys, json, time, threading, base64, io, traceback, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parents[2]
STAGE=Path(__file__).resolve().parent/'stage'
sys.path[:0]=[str(STAGE),str(ROOT/'.tools/upgrade/vendor'),str(ROOT/'.tools/final-acceptance/vendor')]
import app, security, requests
sys.path.insert(0,str(ROOT/'.tools/upgrade'))
import backup as backup_tool
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from google.auth.transport import requests as google_requests
from PIL import Image
from playwright.sync_api import sync_playwright

OUT=ROOT/'.logs/upgrade-staging'
OUT.mkdir(exist_ok=True)
RESULT={}
PREFIX='CODEX-UPGRADE-TEST-'
PORT=8791
URL=f'http://127.0.0.1:{PORT}'
KEY=rsa.generate_private_key(public_exponent=65537,key_size=2048)
NAME=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'isolated-test')])
CERT=x509.CertificateBuilder().subject_name(NAME).issuer_name(NAME).public_key(KEY.public_key()).serial_number(1).not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1)).sign(KEY,hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
class CertificateResponse:
    status=200
    data=json.dumps({'isolated-key':CERT}).encode()
google_requests.Request.__call__=lambda *a,**kw:CertificateResponse()
# Configuration replacement exists only in the isolated harness memory.
security.config=lambda:{'google_client_id':'isolated-client.apps.googleusercontent.com'}

def check(name,value,detail=None):
    RESULT[name]={'status':'PASS' if value else 'FAIL','evidence':detail}
    print(name,RESULT[name]['status'],flush=True)
    if not value: raise AssertionError(f'{name}: {detail}')

def req(method,path,data=None,cookie='',csrf='',origin=URL,headers=None,raw=None):
    h={'Origin':origin,'Cookie':cookie,'X-CSRF-Token':csrf,**(headers or {})}
    return requests.request(method,URL+path,json=data if raw is None else None,data=raw,headers=h,timeout=15)

def jwt(claims):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x,separators=(',',':')).encode()).rstrip(b'=')
    message=enc({'alg':'RS256','kid':'isolated-key'})+b'.'+enc(claims)
    return (message+b'.'+base64.urlsafe_b64encode(KEY.sign(message,padding.PKCS1v15(),hashes.SHA256())).rstrip(b'=')).decode()

def login(**changes):
    challenge=req('POST','/api/auth/challenge',{})
    claims={'iss':'https://accounts.google.com','aud':'isolated-client.apps.googleusercontent.com','exp':int(time.time())+3600,'iat':int(time.time())-10,'email':'AMMAR@YOUR-UNI.COM','email_verified':True,'sub':'isolated-ammar','nonce':challenge.json()['nonce']}
    claims.update(changes)
    return req('POST','/api/auth/google',{'credential':jwt(claims)},cookie=challenge.headers['Set-Cookie'].split(';')[0])

def task(**changes):
    value={'day':'Saturday','title':PREFIX+'Task','dept':'General Office','priority':'Normal','status':'pending','desc':'Prepare the YourUni briefing. Verify student documents before the deadline.','deadlineUtc':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()}
    value.update(changes);return value

def image_bytes(fmt='PNG',animated=False):
    out=io.BytesIO();a=Image.new('RGB',(800,450),'#381565');b=Image.new('RGB',(800,450),'#f2b629')
    a.save(out,format=fmt,save_all=animated,append_images=[b] if animated else [],duration=150,loop=0)
    return out.getvalue()

def run():
    app.initialise_database()
    with app.db() as c:
        c.execute('DELETE FROM tasks WHERE title LIKE ?', (PREFIX+'%',))
        c.execute('DELETE FROM sessions');c.execute('DELETE FROM login_challenges')
    server=app.ThreadingHTTPServer(('127.0.0.1',PORT),app.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        check('anonymous_read',req('GET','/api/state').status_code==200)
        for method,path in [('POST','/api/tasks'),('PATCH','/api/tasks/1'),('DELETE','/api/tasks/1'),('POST','/api/tasks/1/media'),('PUT','/api/selected-day'),('PUT','/api/settings'),('POST','/api/tasks/reset'),('POST','/api/tasks/import')]:
            check('anonymous_'+method+path,req(method,path,{}).status_code==401)
        for name,claims,status in [('expired',{'exp':int(time.time())-100},401),('wrong_audience',{'aud':'wrong'},401),('unverified_email',{'email_verified':False},401),('wrong_issuer',{'iss':'https://evil.example'},401),('non_allowlisted',{'email':'outsider@example.com'},403)]:
            response=login(**claims);check(name,response.status_code==status,response.status_code)
        challenge=req('POST','/api/auth/challenge',{})
        check('forged_credential',req('POST','/api/auth/google',{'credential':'bad.token.forged'},cookie=challenge.headers['Set-Cookie'].split(';')[0]).status_code==401)
        response=login();check('allowlisted_signed_login',response.status_code==200,response.text)
        cookie=response.headers['Set-Cookie'].split(';')[0];csrf=response.json()['csrfToken']
        check('cookie_security',all(x in response.headers['Set-Cookie'] for x in ['HttpOnly','Secure','SameSite=Lax','Max-Age=604800']))
        write=lambda method,path,data=None,**kw:req(method,path,data,cookie,csrf,**kw)
        for email in security.SEEDED_EDITORS:
            approved=login(email=email,sub='isolated-'+email.split('@')[0]);check('approved_'+email,approved.status_code==200)
        with app.db() as c:c.execute("UPDATE authorized_users SET active=0 WHERE email='ammar@your-uni.com'")
        check('deactivated_session_403',write('POST','/api/tasks',task()).status_code==403)
        with app.db() as c:c.execute("UPDATE authorized_users SET active=1 WHERE email='ammar@your-uni.com'")
        check('csrf',req('POST','/api/tasks',task(),cookie,'bad').status_code==403)
        check('cross_origin',write('POST','/api/tasks',task(),origin='https://evil.example').status_code==403)
        for label,dt in [('yesterday',datetime.now(timezone.utc)-timedelta(days=1)),('earlier_today',datetime.now(timezone.utc)-timedelta(hours=1))]:
            r=write('POST','/api/tasks',task(deadlineUtc=dt.isoformat()));check(label,r.status_code==400 and r.json()['error']=='deadline_in_past')
        r=write('POST','/api/tasks',task(deadlineUtc=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()));check('future_today',r.status_code==201,r.text)
        tid=r.json()['task']['id']
        check('identity',app.db().execute('SELECT created_by,updated_by FROM tasks WHERE id=?',(tid,)).fetchone()[:]==('ammar@your-uni.com','ammar@your-uni.com'))
        for field,value in [('title',PREFIX+'Edited'),('desc','Full edited description'),('day','Monday'),('priority','Urgent'),('status','completed')]:
            r=write('PATCH',f'/api/tasks/{tid}',{field:value});check('edit_'+field,r.status_code==200 and r.json()['task'][field]==value,r.text)
        past=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        check('completed_historical',write('PATCH',f'/api/tasks/{tid}',{'deadlineUtc':past}).status_code==200)
        check('reopen_past_rejected',write('PATCH',f'/api/tasks/{tid}',{'status':'pending'}).status_code==400)
        with app.db() as c:c.execute("UPDATE tasks SET status='pending' WHERE id=?",(tid,))
        r=write('PATCH',f'/api/tasks/{tid}',{'desc':'Unrelated overdue edit'})
        check('overdue_unrelated_edit',r.status_code==200 and r.json()['task']['overdue'])
        check('overdue_past_change',write('PATCH',f'/api/tasks/{tid}',{'deadlineUtc':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}).status_code==400)
        r=write('POST',f'/api/tasks/{tid}/media',headers={'Content-Type':'image/png','X-File-Name':'briefing.png'},raw=image_bytes())
        check('image_upload',r.status_code==201,r.text);media_url=r.json()['task']['media']['url']
        check('image_display',req('GET',media_url).content==image_bytes())
        r=write('POST',f'/api/tasks/{tid}/media',headers={'Content-Type':'image/gif','X-File-Name':'briefing.gif'},raw=image_bytes('GIF',True));check('gif_replace',r.status_code==201,r.text)
        check('replaced_url_removed',req('GET',media_url).status_code==404)
        for name,filename,mime,data in [('html','x.html','text/html',b'<html>bad</html>'),('executable','x.exe','application/octet-stream',b'MZbad'),('mismatch','x.png','image/png',b'<script>alert(1)</script>'),('traversal','../app.py','image/png',image_bytes())]:
            check('media_'+name,write('POST',f'/api/tasks/{tid}/media',headers={'Content-Type':mime,'X-File-Name':filename},raw=data).status_code==400)
        import http.client
        oversized=http.client.HTTPConnection('127.0.0.1',PORT,timeout=10)
        oversized.request('POST',f'/api/tasks/{tid}/media',headers={'Origin':URL,'Cookie':cookie,'X-CSRF-Token':csrf,'Content-Length':str(security.MAX_MEDIA+1),'Content-Type':'image/png','X-File-Name':'x.png'})
        check('media_oversized',oversized.getresponse().status==413);oversized.close()
        for path in ['/.data/','/.data/thebekal.db','/.data/media/','/.tools/','/.logs/','/.backup/','/DEPLOYMENT.md','/app.py','/api/media/../../app.py']:
            check('blocked_'+path,req('GET',path).status_code==404)
        check('media_remove',write('DELETE',f'/api/tasks/{tid}/media',{}).json()['task']['media'] is None)
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome Dev\Application\chrome.exe',headless=True)
            manager=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='America/New_York')
            token=cookie.split('=',1)[1]
            manager.add_cookies([{'name':security.COOKIE,'value':token,'url':URL.replace('http:','https:')+'/','secure':True,'httpOnly':True,'sameSite':'Lax'}])
            page=manager.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(URL);page.wait_for_function("window.staffAuth?.role==='editor'")
            page.evaluate("switchView('admin')")
            phone=browser.new_context(viewport={'width':390,'height':844},timezone_id='Asia/Tokyo');mobile=phone.new_page();mobile.goto(URL)
            tvctx=browser.new_context(viewport={'width':1920,'height':1080},timezone_id='Europe/London');tv=tvctx.new_page();tv.goto(URL);tv.wait_for_function('typeof window.reconcileYouruni===\'function\'')
            check('viewer_ui',mobile.locator('.task-row-actions:visible').count()==0 and mobile.locator('#add-task-button:visible').count()==0)
            page.locator('#add-task-button').click();page.locator('#edit-title').fill(PREFIX+'Browser Task')
            check('semantic_deadline',page.locator('#edit-date').get_attribute('type')=='date' and page.locator('#edit-time').get_attribute('type')=='time')
            page.locator('#edit-date').fill('2020-01-01');page.locator('#edit-time').fill('10:00');page.locator('#edit-save').click()
            check('frontend_past_rejection',page.locator('#task-editor').is_visible() and bool(page.locator('#deadline-error').inner_text()))
            local=(datetime.now(timezone.utc)+timedelta(days=2,hours=8))
            page.locator('#edit-date').fill(local.strftime('%Y-%m-%d'));page.locator('#edit-time').fill('15:30');page.locator('#edit-day').select_option('Saturday')
            page.locator('#edit-file').set_input_files({'name':'CODEX-UPGRADE-TEST-image.png','mimeType':'image/png','buffer':image_bytes()})
            page.locator('#edit-save').click();page.wait_for_function("!document.getElementById('task-editor').open")
            with app.db() as c:bid=c.execute('SELECT id FROM tasks WHERE title=?',(PREFIX+'Browser Task',)).fetchone()[0]
            tv.wait_for_selector(f'[data-task-id="{bid}"]')
            mobile.wait_for_function('(id)=>canonicalTaskState.get(id)?.media?.mimeType===\'image/png\'',arg=bid)
            check('live_create_media',True)
            expected=local.strftime('%Y-%m-%d')+'T07:30:00+00:00'
            check('timezone_independent_storage',app.db().execute('SELECT deadline_utc FROM tasks WHERE id=?',(bid,)).fetchone()[0]==expected)
            # Browser-generated valid animated video fixture, requiring no ffmpeg.
            video64=page.evaluate('''async()=>{const c=document.createElement('canvas');c.width=320;c.height=180;const ctx=c.getContext('2d');const stream=c.captureStream(10);const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});const chunks=[];recorder.ondataavailable=e=>chunks.push(e.data);const done=new Promise(r=>recorder.onstop=r);recorder.start();for(let i=0;i<12;i++){ctx.fillStyle=i%2?'#381565':'#f2b629';ctx.fillRect(0,0,320,180);await new Promise(r=>setTimeout(r,100));}recorder.stop();await done;stream.getTracks().forEach(t=>t.stop());const data=await new Blob(chunks).arrayBuffer();return btoa(String.fromCharCode(...new Uint8Array(data)));}''')
            video=base64.b64decode(video64)
            r=write('POST',f'/api/tasks/{bid}/media',headers={'Content-Type':'video/webm','X-File-Name':'CODEX-UPGRADE-TEST-video.webm'},raw=video);check('video_upload',r.status_code==201,r.text)
            tv.wait_for_selector(f'[data-task-id="{bid}"] video');tv.wait_for_function("()=>{const v=document.querySelector('video');return v&&v.readyState>=2&&v.muted}")
            check('video_live_muted',True)
            media_path=r.json()['task']['media']['url']
            check('video_range',req('GET',media_path,headers={'Range':'bytes=0-99'}).status_code==206)
            if page.evaluate("MediaRecorder.isTypeSupported('video/mp4')"):
                mp4=page.evaluate('''async()=>{const c=document.createElement('canvas');c.width=320;c.height=180;const ctx=c.getContext('2d');const s=c.captureStream(10);const r=new MediaRecorder(s,{mimeType:'video/mp4'});const chunks=[];r.ondataavailable=e=>chunks.push(e.data);const done=new Promise(ok=>r.onstop=ok);r.start();for(let i=0;i<10;i++){ctx.fillStyle=i%2?'#381565':'#f2b629';ctx.fillRect(0,0,320,180);await new Promise(ok=>setTimeout(ok,100));}r.stop();await done;s.getTracks().forEach(t=>t.stop());return btoa(String.fromCharCode(...new Uint8Array(await new Blob(chunks).arrayBuffer())));}''')
                r=write('POST',f'/api/tasks/{bid}/media',headers={'Content-Type':'video/mp4','X-File-Name':'CODEX-UPGRADE-TEST-video.mp4'},raw=base64.b64decode(mp4));check('mp4_upload',r.status_code==201,r.text)
                tv.wait_for_function('(id)=>canonicalTaskState.get(id)?.media?.mimeType===\'video/mp4\'',arg=bid)
                tv.wait_for_function("()=>{const v=document.querySelector('video');return v&&v.readyState>=2&&v.muted}")
                check('mp4_playback',True)
            else:check('mp4_fixture_available',False)
            saved_backup=backup_tool.backup(STAGE)
            check('backup_restore_integrity',(saved_backup/'manifest.json').exists(),str(saved_backup))
            import shutil
            shutil.copy2(saved_backup/'manifest.json',OUT/'backup-validation.json')
            assert saved_backup.resolve().is_relative_to((STAGE/'.backup').resolve())
            for attempt in range(10):
                try:
                    shutil.rmtree(saved_backup)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.25)
            app.initialise_database()
            check('migration_idempotence_media_session',write('PATCH',f'/api/tasks/{bid}',{'desc':'After migration'}).json()['task']['media'] is not None)
            for method,path,payload in [('PATCH',f'/api/tasks/{bid}',{'title':PREFIX+'Realtime edit','deadlineUtc':(datetime.now(timezone.utc)+timedelta(days=4)).isoformat()}),('PATCH',f'/api/tasks/{bid}',{'status':'completed'}),('PATCH',f'/api/tasks/{bid}',{'status':'pending'})]:
                r=write(method,path,payload);check('live_mutation_'+str(payload),r.status_code==200)
                for client in [page,mobile,tv]:client.wait_for_function('(task)=>canonicalTaskState.get(task.id)?.version===task.version',arg=r.json()['task'])
            write('DELETE',f'/api/tasks/{bid}/media',{})
            for client in [page,mobile,tv]:client.wait_for_function('(id)=>canonicalTaskState.get(id)?.media===null',arg=bid)
            write('DELETE',f'/api/tasks/{bid}',{})
            for client in [page,mobile,tv]:client.wait_for_function('(id)=>!canonicalTaskState.get(id)',arg=bid)
            check('live_status_deadline_media_delete',True)
            write('PUT','/api/selected-day',{'selectedDay':'Monday'});tv.wait_for_function("selectedOperationalDay==='Monday'");mobile.wait_for_function("selectedOperationalDay==='Monday'");check('global_day_sync',True)
            write('PUT','/api/selected-day',{'selectedDay':'Saturday'})
            tv.wait_for_function("selectedOperationalDay==='Saturday'")
            tv.locator('#enter-tv-presentation-btn').click();tv.wait_for_function("document.fullscreenElement?.id==='view-tv'")
            check('native_fullscreen',True)
            # Isolated TV content fixture; database records are never deleted in production.
            with app.db() as c:c.execute("UPDATE tasks SET status='completed' WHERE title NOT LIKE ?",(PREFIX+'%',))
            for dims in [(1920,1080),(1366,768)]:
                tv.set_viewport_size({'width':dims[0],'height':dims[1]})
                for count in [1,2,3,4,6,9,12,20]:
                    with app.db() as c:c.execute('DELETE FROM tasks WHERE title LIKE ?', (PREFIX+'%',))
                    ids=[]
                    for i in range(count):ids.append(write('POST','/api/tasks',task(title=PREFIX+f'Visual {i+1}')).json()['task']['id'])
                    tv.evaluate('reconcileYouruni()');tv.wait_for_timeout(180)
                    metrics=tv.evaluate("""()=>{const b=document.getElementById('tv-tasks-container');const v=document.getElementById('view-tv');return {bw:b.clientWidth,sw:b.scrollWidth,bh:b.clientHeight,sh:b.scrollHeight,vh:v.clientHeight,vsh:v.scrollHeight,cards:b.children.length,font:getComputedStyle(b.querySelector('h4')).fontSize,page:document.getElementById('tv-page-dots').textContent}}""")
                    check(f'tv_{dims}_{count}',metrics['sw']<=metrics['bw']+1 and metrics['sh']<=metrics['bh']+1 and metrics['vsh']<=metrics['vh']+1 and float(metrics['font'][:-2])>=22,metrics)
                    tv.screenshot(path=str(OUT/f'tv-{dims[0]}-{count}.png'))
                    if count==20:
                        before=tv.locator('#tv-page-dots').inner_text();tv.wait_for_function('(before)=>document.getElementById("tv-page-dots").textContent!==before',arg=before,timeout=17000);check('automatic_paging_'+str(dims),True)
            tv.locator('#exit-tv-presentation-btn').click()
            tv.evaluate("()=>{document.getElementById('view-tv').requestFullscreen=()=>Promise.reject(Error('test refusal'));}")
            tv.locator('#enter-tv-presentation-btn').click();check('fullscreen_fallback',tv.locator('#view-tv').evaluate("e=>e.classList.contains('tv-presentation-mode')"));tv.locator('#exit-tv-presentation-btn').click()
            for width,height in [(320,568),(360,800),(390,844),(430,932),(768,1024)]:
                for theme in ['light','dark']:
                    page.set_viewport_size({'width':width,'height':height});page.evaluate('(theme)=>applyTheme(theme)',theme);page.evaluate("switchView('admin')")
                    page.locator('#add-task-button').click();page.locator('#edit-title').fill(PREFIX+'Responsive')
                    metrics=page.evaluate("""()=>({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,d:document.getElementById('task-editor').getBoundingClientRect().toJSON(),font:getComputedStyle(document.getElementById('edit-title')).fontSize})""")
                    check(f'mobile_editor_{width}_{theme}',metrics['sw']<=metrics['cw'] and metrics['d']['width']<=width and metrics['d']['height']<=height and metrics['font']=='16px',metrics)
                    page.screenshot(path=str(OUT/f'editor-{width}-{theme}.png'));page.locator('#edit-cancel').click()
                    mobile.set_viewport_size({'width':width,'height':height});mobile.evaluate('(theme)=>applyTheme(theme)',theme)
                    check(f'mobile_viewer_{width}_{theme}',mobile.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth'))
                    mobile.screenshot(path=str(OUT/f'viewer-{width}-{theme}.png'))
            check('removed_tv_sections',tv.locator('#tv-directives-container,#tv-supervisors-container,#tv-progress-bar').count()==0)
            mobile.evaluate("switchView('admin')")
            check('viewer_all_mutations_hidden',mobile.locator('#view-admin button:visible,#view-admin input:visible,#view-admin textarea:visible').count()==0)
            page.evaluate("switchView('tv')");page.wait_for_selector('.tv-card');check('tv_return_navigation',True)
            expected_footer="© Copyright 2026 YourUni. All rights reserved. Developed by ASHRAF2004"
            check('footer',page.locator('#youruni-site-attribution').inner_text().strip()==expected_footer)
            check('javascript_errors',not errors,errors)
            import subprocess
            worker=None
            worker_url='http://127.0.0.1:8792'
            def start_worker():
                child=subprocess.Popen([str(ROOT/'.tools/python/python.exe'),str(STAGE/'app.py'),'--port','8792'],creationflags=0x08000000)
                for _ in range(100):
                    try:
                        if requests.get(worker_url+'/api/health',timeout=1).status_code==200:return child
                    except requests.RequestException:pass
                    time.sleep(.1)
                child.terminate();raise AssertionError('Isolated worker did not start')
            try:
                r=write('POST','/api/tasks',task(title=PREFIX+'Restart media'));rid=r.json()['task']['id']
                worker=start_worker()
                retainedctx=browser.new_context(viewport={'width':1366,'height':768})
                retainedctx.add_cookies([{'name':security.COOKIE,'value':token,'url':worker_url.replace('http:','https:')+'/','secure':True,'httpOnly':True,'sameSite':'Lax'}])
                retained=retainedctx.new_page();retained.goto(worker_url);retained.wait_for_function("window.staffAuth?.role==='editor'")
                navs=[];retained.on('framenavigated',lambda frame:navs.append(frame.url))
                for label,mime,blob in [('png','image/png',image_bytes()),('gif','image/gif',image_bytes('GIF',True)),('webm','video/webm',video)]:
                    r=requests.post(worker_url+f'/api/tasks/{rid}/media',data=blob,headers={'Origin':worker_url,'Cookie':cookie,'X-CSRF-Token':csrf,'Content-Type':mime,'X-File-Name':PREFIX+label+'.'+label},timeout=15)
                    check('restart_upload_'+label,r.status_code==201,r.text)
                    media=r.json()['task']['media']
                    retained.wait_for_function('(m)=>canonicalTaskState.get(m.id)?.media?.id===m.media',arg={'id':rid,'media':media['id']})
                    worker.terminate();worker.wait(timeout=10);worker=start_worker()
                    check('restart_media_'+label,requests.get(worker_url+media['url'],timeout=10).content==blob)
                    auth=requests.get(worker_url+'/api/auth/session',headers={'Cookie':cookie},timeout=10).json();check('restart_session_'+label,auth['role']=='editor')
                    retained.evaluate('reconcileYouruni()')
                    retained.wait_for_function('(m)=>canonicalTaskState.get(m.id)?.media?.id===m.media',arg={'id':rid,'media':media['id']})
                check('restart_no_browser_navigation',not navs,navs)
                retainedctx.close()
            finally:
                if worker:worker.terminate();worker.wait(timeout=10)
            browser.close()
        check('logout',write('POST','/api/auth/logout',{}).status_code==200 and write('POST','/api/tasks',task()).status_code==401)
        response=login();cookie=response.headers['Set-Cookie'].split(';')[0];csrf=response.json()['csrfToken']
        with app.db() as c:c.execute('UPDATE sessions SET expires_at=0')
        check('expired_session',write('POST','/api/tasks',task()).status_code==401)
    finally:
        with app.db() as c:
            c.execute('DELETE FROM tasks WHERE title LIKE ?', (PREFIX+'%',));c.execute('DELETE FROM sessions');c.execute('DELETE FROM login_challenges')
            c.execute("UPDATE authorized_users SET google_sub=NULL WHERE google_sub LIKE 'isolated-%'")
            check('cleanup',c.execute('SELECT count(*) FROM tasks WHERE title LIKE ?', (PREFIX+'%',)).fetchone()[0]==0)
            check('sqlite_integrity',c.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
        security.cleanup_media([p.name for p in (STAGE/'.data/media').iterdir()])
        server.shutdown()

if __name__=='__main__':
    try:run()
    except Exception as exc:RESULT['exception']={'status':'FAIL','evidence':traceback.format_exc()};print(traceback.format_exc(),flush=True)
    finally:(OUT/'results.json').write_text(json.dumps(RESULT,indent=2),encoding='utf-8')
    sys.exit(any(x['status']=='FAIL' for x in RESULT.values()))
