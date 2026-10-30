/* Extends the existing canonical SQLite/SSE client. No second state store. */
window.staffAuth = {role:'viewer'};
(() => {
    const el = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const editor = () => window.staffAuth.role === 'editor';
    let serverOffset = 0;
    const serverNow = () => Date.now() + serverOffset;
    const siteTimeZone = () => window.displayTimeZone || 'UTC';
    const channelParam = new URLSearchParams(location.search).get('channel') || 'general';
    const displayChannel = /^[a-z0-9][a-z0-9-]{0,31}$/.test(channelParam) ? channelParam : 'general';
    const zoneParts = date => Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:siteTimeZone(),year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(p=>[p.type,p.value]));
    const inputsFor = iso => {
        if (!iso) return ['', ''];
        const p=zoneParts(new Date(iso));
        return [`${p.year}-${p.month}-${p.day}`, `${p.hour}:${p.minute}`];
    };
    const zoneOffset = utcMillis => {
        const zone=new Intl.DateTimeFormat('en-US',{timeZone:siteTimeZone(),timeZoneName:'longOffset'}).formatToParts(new Date(utcMillis)).find(p=>p.type==='timeZoneName')?.value || 'GMT';
        if(zone==='GMT'||zone==='UTC') return 0;
        const m=/GMT([+-])(\d{1,2})(?::?(\d{2}))?$/.exec(zone);
        if(!m) throw Error('Unsupported display timezone offset');
        return (m[1]==='-'?-1:1)*(Number(m[2])*60+Number(m[3]||0));
    };
    // Convert a wall-clock value in the instance timezone to UTC, rejecting DST gaps.
    const zonedIso = (date,time) => {
        if(!date&&!time) return null;
        if(!date||!time||!/^\d{4}-\d{2}-\d{2}$/.test(date)||!/^\d{2}:\d{2}$/.test(time)) return 'invalid';
        const naive=Date.parse(`${date}T${time}:00Z`);
        if(!Number.isFinite(naive)) return 'invalid';
        let utc=naive;
        for(let i=0;i<5;i++){
            const next=naive-zoneOffset(utc)*60000;
            if(next===utc)break;
            utc=next;
        }
        const p=zoneParts(new Date(utc));
        return `${p.year}-${p.month}-${p.day}`===date&&`${p.hour}:${p.minute}`===time ? new Date(utc).toISOString() : 'invalid';
    };
    const activeOnScreen = task => {
        const now=serverNow();
        return (!task.visibleFromUtc || Date.parse(task.visibleFromUtc)<=now)
            && (!task.visibleUntilUtc || Date.parse(task.visibleUntilUtc)>now);
    };
    const overdue = task => task.status !== 'completed' && task.deadlineUtc && Date.parse(task.deadlineUtc) < serverNow();
    const deadlineLabel = task => task.deadlineUtc ? new Intl.DateTimeFormat('en-US',{timeZone:siteTimeZone(),day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(task.deadlineUtc)) + ' ('+siteTimeZone()+')' : task.time || '';
    const safeImage = url => /^(?:\/api\/media\/[a-f0-9]{32}|https:\/\/|data:image\/(?:png|jpeg|webp|gif);base64,)/i.test(url || '') ? url : '';
    const mediaMarkup = (task, tv=false) => {
        if (task.media?.mimeType?.startsWith('video/')) return `<video src="${esc(task.media.url)}" ${tv?'autoplay muted loop playsinline':'controls playsinline'} preload="metadata" ${tv?'':'class="task-thumbnail"'} aria-label="${esc(task.title)} video"></video>`;
        const url = safeImage(task.media?.url || task.imageUrl);
        return url ? `<img src="${esc(url)}" alt="${esc(task.title)}" ${tv?'':'class="task-thumbnail"'}>` : '';
    };
    let tvPage=0, tvPages=1, tvDay='', tvSignature='';
    const logoMarkup = () => {
        const custom=safeImage(customLogoUrl);
        if (custom) return `<img class="tv-brand-mark" src="${esc(custom)}" alt="Display logo">`;
        const source = el('tv-default-logo-svg');
        if (!source) return '';
        const clone = source.cloneNode(true);
        clone.removeAttribute('id');
        clone.removeAttribute('class');
        clone.classList.add('tv-brand-mark');
        clone.setAttribute('width','330');
        clone.setAttribute('height','65');
        clone.setAttribute('aria-label','Display logo');
        return clone.outerHTML;
    };
    const fallbackMedia = node => {
        const box = node.closest('.tv-media');
        if (box) { box.innerHTML = logoMarkup(); box.setAttribute('aria-label','Media unavailable'); }
        else node.hidden = true;
    };
    window.updateTvDisplay = function () {
        const t=translations[currentLang] || translations.en;
        safeSetText('tv-day-title', `${t.tvDayTitlePrefix}${t.days[currentDayIndex]}`);
        safeSetText('tv-ticker-text',tickerText);
        if (el('tv-ticker-bar')) el('tv-ticker-bar').style.display=String(tickerText||'').trim()?'':'none';
        safeSetText('txt-tv-status-bar', 'TV display preview · Channel: '+displayChannel);
        const box=el('tv-tasks-container');
        if (!box) return;
        if (currentView !== 'tv') { box.querySelectorAll('video').forEach(v=>v.pause()); return; }
        const tasks=(tasksData[selectedOperationalDay] || []).filter(t=>t.status!=='completed' && (t.channel||'general')===displayChannel && activeOnScreen(t));
        if (tvDay !== selectedOperationalDay) { tvDay=selectedOperationalDay; tvPage=0; }
        const width=box.clientWidth || 1000, height=box.clientHeight || 400;
        const maxCols=width>=1300?3:width>=650?2:1;
        const maxRows=Math.max(1,Math.min(3,Math.floor((height+14)/250)));
        const capacity=maxCols*maxRows;
        tvPages=Math.max(1,Math.ceil(tasks.length/capacity));
        tvPage=Math.min(tvPage,tvPages-1);
        const visible=tasks.slice(tvPage*capacity,(tvPage+1)*capacity);
        const cols=visible.length<=1?1:Math.min(maxCols,visible.length);
        const rows=Math.max(1,Math.ceil(visible.length/cols));
        box.style.setProperty('--tv-cols',cols); box.style.setProperty('--tv-rows',rows);
        safeSetText('tv-task-count',`${tasks.length} active ${tasks.length===1?'task':'tasks'} · Screen: ${displayChannel}`);
        safeSetText('tv-page-dots',tvPages>1?`Page ${tvPage+1} of ${tvPages} · Changes automatically every 15 seconds`:'');
        el('tv-page-dots').hidden=tvPages===1;
        const signature=JSON.stringify([visible.map(t=>[t.id,t.version,overdue(t)]),cols,rows,selectedOperationalDay,currentLang,customLogoUrl]);
        if (signature===tvSignature && box.childElementCount) return;
        tvSignature=signature;
        box.querySelectorAll('video').forEach(v=>v.pause());
        box.innerHTML=visible.length ? visible.map(task=>`<article class="tv-card ${visible.length===1?'hero':''} ${rows>1?'compact':''}" data-task-id="${task.id}">
            <div class="tv-media">${mediaMarkup(task,true)||logoMarkup()}</div>
            <div class="tv-copy"><h4>${esc(task.title)}</h4>
            <p class="tv-deadline ${overdue(task)?'yu-overdue':''}">${overdue(task)?'Overdue · ':''}${esc(deadlineLabel(task))}</p>
            ${task.desc?`<p class="tv-summary">${esc(task.desc)}</p>`:''}</div></article>`).join('') : '<article class="tv-card hero tv-empty-state"><div class="tv-media">'+logoMarkup()+'</div><div class="tv-copy"><h4>No active tasks</h4><p class="tv-summary">Announcements will continue to update live.</p></div></article>';
        box.querySelectorAll('img,video').forEach(node=>node.addEventListener('error',()=>fallbackMedia(node),{once:true}));
        box.querySelectorAll('video').forEach(v=>{v.muted=true; v.play().catch(()=>{v.setAttribute('aria-label','Video autoplay unavailable');});});
    };
    window.renderAdminTasks = function () {
        safeSetText('admin-current-day-label',selectedOperationalDay);
        const list=el('admin-tasks-list');
        if (!list || currentView!=='admin') return;
        const tasks=tasksData[selectedOperationalDay] || [];
        list.innerHTML=tasks.length?tasks.map(task=>`<article class="task-row" data-task-id="${task.id}">${mediaMarkup(task)}<div class="task-row-content"><h4>${esc(task.title)}</h4><p class="yu-muted">${esc(task.dept)} · ${esc(task.priority)} · Screen: ${esc(task.channel||'general')}</p><p>${esc(task.desc)}</p><p class="${overdue(task)?'yu-overdue':'yu-muted'}">${overdue(task)?'Overdue · ':''}${esc(deadlineLabel(task))}</p><span class="yu-muted">${task.status==='completed'?'Completed':(!activeOnScreen(task)?(task.visibleFromUtc&&Date.parse(task.visibleFromUtc)>serverNow()?'Scheduled':'Off screen'):'Pending')}</span></div>${editor()?`<div class="task-row-actions"><button class="yu-button" data-edit="${task.id}">Edit</button><button class="yu-button" data-status="${task.id}">${task.status==='completed'?'Reopen':'Complete'}</button><button class="yu-button yu-danger" data-delete="${task.id}" aria-label="Delete ${esc(task.title)}">Delete</button></div>`:''}</article>`).join(''):'<p class="yu-muted">No tasks for this day.</p>';
        list.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>openTaskEditor(Number(b.dataset.edit)));
        list.querySelectorAll('[data-status]').forEach(b=>b.onclick=async()=>{
            const task=canonicalTaskState.get(b.dataset.status);
            if(task.status==='completed' && (!task.deadlineUtc || Date.parse(task.deadlineUtc)<=serverNow())) { openTaskEditor(task.id); el('edit-status').value='pending'; validateDeadline(); return; }
            b.disabled=true; await toggleTaskStatus(task.id); b.disabled=false;
        });
        list.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{if(confirm('Delete this task and its media?')) deleteTask(Number(b.dataset.delete));});
        list.querySelectorAll('img,video').forEach(node=>node.addEventListener('error',()=>fallbackMedia(node),{once:true}));
    };
    window.renderAdminDirectivesEditor = function () {
        const box=el('admin-directives-editor-list');if(!box)return;box.replaceChildren();
        directivesData.forEach((text,i)=>{
            const row=document.createElement('div');row.className='staff-actions';
            const input=document.createElement('input');input.value=text;input.className='yu-button';input.setAttribute('aria-label',`Directive ${i+1}`);input.onchange=()=>updateDirectiveField(i,input.value);
            const button=document.createElement('button');button.className='yu-button yu-danger';button.textContent='Remove';button.onclick=()=>removeDirectiveRow(i);row.append(input,button);box.append(row);
        });
    };
    window.renderAdminSupervisorsEditor = function () {
        const box=el('admin-supervisors-editor-list');if(!box)return;box.replaceChildren();
        supervisorsData.forEach((person,i)=>{
            const row=document.createElement('div');row.className='staff-actions';
            ['title','name'].forEach(field=>{const input=document.createElement('input');input.value=person[field];input.className='yu-button';input.setAttribute('aria-label',`Supervisor ${field} ${i+1}`);input.onchange=()=>updateSupervisorField(i,field,input.value);row.append(input);});
            const button=document.createElement('button');button.className='yu-button yu-danger';button.textContent='Remove';button.onclick=()=>removeSupervisorRow(i);row.append(button);box.append(row);
        });
    };
    const ACCESS_COPY = {
        en:{heading:'Sign in to manage the live display',body:'The Admin Panel is protected. Use an approved Google account to update tasks, deadlines, media and the broadcast day.',secure:'Google verifies your identity. Smart Display never receives or stores your Google password.',benefits:['Publish changes live','Manage media and deadlines','Keep every screen in sync'],preparing:'Preparing secure Google sign-in…',ready:'Choose your approved Google account to continue.',retry:'Try Google sign-in again',signed:'Signed in as',logout:'Logout',viewerNav:'Admin Panel — staff sign-in required',editorNav:'Admin Panel — signed in'},
        ar:{heading:'سجّل الدخول لإدارة شاشة العرض المباشر',body:'لوحة المدير والموارد البشرية محمية. استخدم حساب Google معتمداً لتحديث المهام والمواعيد والوسائط ويوم العرض.',secure:'تتحقق Google من هويتك. لا تستلم Smart Display كلمة مرور Google الخاصة بك ولا تخزنها.',benefits:['نشر التغييرات مباشرة','إدارة الوسائط والمواعيد','مزامنة جميع الشاشات'],preparing:'جارٍ تجهيز تسجيل الدخول الآمن…',ready:'اختر حساب Google المعتمد للمتابعة.',retry:'إعادة محاولة تسجيل الدخول',signed:'تم تسجيل الدخول باسم',logout:'تسجيل الخروج',viewerNav:'لوحة المدير والموارد البشرية — يلزم تسجيل الدخول',editorNav:'لوحة المدير والموارد البشرية — تم تسجيل الدخول'},
        ms:{heading:'Log masuk untuk mengurus paparan langsung',body:'Panel Pengurus / HR dilindungi. Gunakan akaun Google yang diluluskan untuk mengemas kini tugasan, tarikh akhir, media dan hari siaran.',secure:'Google mengesahkan identiti anda. Smart Display tidak menerima atau menyimpan kata laluan Google anda.',benefits:['Terbitkan perubahan secara langsung','Urus media dan tarikh akhir','Pastikan semua skrin disegerakkan'],preparing:'Menyediakan log masuk Google yang selamat…',ready:'Pilih akaun Google yang diluluskan untuk meneruskan.',retry:'Cuba log masuk Google lagi',signed:'Log masuk sebagai',logout:'Log keluar',viewerNav:'Panel Pengurus / HR — log masuk kakitangan diperlukan',editorNav:'Panel Pengurus / HR — telah log masuk'}
    };
    const accessCopy=()=>ACCESS_COPY[currentLang]||ACCESS_COPY.en;
    function updateManagerAccessCopy(){
        const copy=accessCopy();
        safeSetText('manager-access-title',copy.heading);safeSetText('manager-access-body',copy.body);safeSetText('manager-access-security-text',copy.secure);
        copy.benefits.forEach((value,index)=>safeSetText(`manager-access-benefit-${index+1}`,value));
        safeSetText('staff-sign-in-label',copy.retry);safeSetText('staff-logout-label',copy.logout);
        safeSetText('staff-session-label',copy.signed);
        ['btn-admin-view','mobile-btn-admin-view'].forEach(id=>{
            const button=el(id);if(!button)return;
            button.setAttribute('aria-label',editor()?copy.editorNav:copy.viewerNav);
            button.title=editor()?copy.editorNav:copy.viewerNav;
            const icon=button.querySelector('i');if(icon)icon.className=`fa-solid ${editor()?'fa-user-shield':'fa-lock'} ${editor()?'text-brand-yellow':''}`;
        });
    }
    function applyManagerAccessState(){
        const isEditor=editor(),workspace=el('manager-workspace'),gate=el('manager-access-gate');
        if(workspace){workspace.inert=!isEditor;workspace.setAttribute('aria-hidden',isEditor?'false':'true');}
        if(gate){gate.inert=isEditor;gate.setAttribute('aria-hidden',isEditor?'true':'false');}
        el('admin-day-select').disabled=!isEditor;
        safeSetText('staff-identity',isEditor?`${staffAuth.name} · ${staffAuth.email}`:'');
        updateManagerAccessCopy();
    }
    window.refreshStaffSession = async () => {
        try {
            const state=await smartdisplayApi('/api/auth/session');
            if(state.serverTime) serverOffset=Date.parse(state.serverTime)-Date.now();
            window.staffAuth=state;
            document.documentElement.dataset.role=state.role;
            applyManagerAccessState();
            document.querySelectorAll('.editor-only').forEach(node=>{node.inert=!editor();});
            if(!editor() && el('task-editor').open) el('task-editor').close();
            renderAdminTasks();
            if(!editor()&&currentView==='admin')void prepareGoogleSignIn();
        } catch { safeSetText('staff-error','Unable to check staff access. Public viewing is still available; try again.'); }
    };
    let googleReady;
    const loadGoogle = () => googleReady ||= new Promise((resolve,reject)=>{
        const s=document.createElement('script'); s.src='https://accounts.google.com/gsi/client'; s.async=true; s.onload=resolve; s.onerror=()=>{googleReady=null;reject(Error('Google sign-in could not load. Check your connection and try again.'));}; document.head.appendChild(s);
    });
    let googlePreparing=false,googleRendered=false,lastGoogleWidth=0;
    function renderGoogleButton(){
        const host=el('google-button'),zone=el('google-signin-zone');
        if(!host||!zone||!window.google?.accounts?.id)return;
        const width=Math.max(200,Math.min(280,Math.floor(zone.clientWidth-36)));
        host.replaceChildren();
        google.accounts.id.renderButton(host,{theme:document.documentElement.dataset.theme==='dark'?'filled_black':'outline',size:'large',shape:'pill',text:'signin_with',width});
        lastGoogleWidth=width;googleRendered=true;
    }
    async function prepareGoogleSignIn() {
        if(editor()||googlePreparing||googleRendered)return;
        const button=el('staff-sign-in'),zone=el('google-signin-zone'),copy=accessCopy();
        googlePreparing=true;button.hidden=true;zone?.classList.add('is-loading');safeSetText('staff-login-status',copy.preparing);safeSetText('staff-error','');
        try {
            if (!staffAuth.clientId) throw Error('Staff sign-in is awaiting Google configuration. Public viewing is available.');
            await loadGoogle();
            const {nonce}=await smartdisplayApi('/api/auth/challenge',{method:'POST',body:'{}'});
            google.accounts.id.initialize({client_id:staffAuth.clientId,nonce,auto_select:false,callback:async response=>{
                zone?.classList.add('is-verifying');safeSetText('staff-login-status','Verifying your Google account…');
                try {
                    await smartdisplayApi('/api/auth/google',{method:'POST',body:JSON.stringify({credential:response.credential})});
                    el('google-button').replaceChildren();googleRendered=false;safeSetText('staff-error','');await refreshStaffSession();
                    if(editor())showToast(`Welcome, ${staffAuth.name}. Editor controls are ready.`);
                } catch(e){safeSetText('staff-error',e.message);button.hidden=false;}
                finally{zone?.classList.remove('is-verifying');}
            }});
            renderGoogleButton();safeSetText('staff-login-status',copy.ready);
        } catch(e) { safeSetText('staff-error',e.message);button.hidden=false; }
        finally {googlePreparing=false;button.disabled=false;zone?.classList.remove('is-loading');}
    }
    let editingId=null, originalTask=null, selectedFile=null, removeMedia=false, objectUrl=null, saving=false, returnFocus=null;
    function preview(task=null) {
        const area=el('edit-media-preview');
        area.querySelectorAll('video').forEach(v=>v.pause());
        if(objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl=null;
        if(selectedFile) {
            objectUrl=URL.createObjectURL(selectedFile);
            area.innerHTML=selectedFile.type.startsWith('video/')?`<video class="media-preview" controls muted playsinline src="${objectUrl}"></video>`:`<img class="media-preview" alt="Selected media preview" src="${objectUrl}">`;
            safeSetText('edit-media-info',`${selectedFile.name} · ${selectedFile.type} · ${(selectedFile.size/1024/1024).toFixed(1)} MB`);
        } else if(task && !removeMedia && (task.media||task.imageUrl)) {
            area.innerHTML=mediaMarkup(task); area.querySelectorAll('img,video').forEach(n=>n.className='media-preview');
            safeSetText('edit-media-info',task.media?.name || 'Current image');
        } else {area.replaceChildren();safeSetText('edit-media-info','No media attached');}
        el('edit-remove-media').hidden=!area.childElementCount;
    }
    function chooseFile(file) {
        if(!file) return;
        if(!['image/jpeg','image/png','image/webp','image/gif','video/mp4','video/webm'].includes(file.type)||file.size>25*1024*1024) {
            safeSetText('edit-error','Choose JPG, PNG, WebP, GIF, MP4 or WebM, up to 25 MB.'); return;
        }
        selectedFile=file; removeMedia=false; safeSetText('edit-error',''); preview();
    }
    function deadlineValue() {
        const date=el('edit-date').value,time=el('edit-time').value;
        if(!date&&!time) return null;
        if(!date||!time) return 'invalid';
        if(originalTask?.deadlineUtc) {
            const [oldDate,oldTime]=inputsFor(originalTask.deadlineUtc);
            if(date===oldDate&&time===oldTime) return originalTask.deadlineUtc;
        }
        return zonedIso(date,time);
    }
    function validateDeadline(focus=false) {
        const value=deadlineValue(), active=el('edit-status').value!=='completed';
        const unchanged=originalTask && Date.parse(value)===Date.parse(originalTask.deadlineUtc);
        const historical=originalTask && originalTask.status==='pending' && (unchanged||(!value&&!originalTask.deadlineUtc));
        const valid=value!=='invalid' && (!active||historical||(value && Date.parse(value)>serverNow()));
        const parts=inputsFor(new Date(serverNow()+60000).toISOString());
        el('edit-date').min=active&&!historical?parts[0]:'';
        el('edit-time').min=active&&!historical&&el('edit-date').value===parts[0]?parts[1]:'';
        const message=valid?'':'Deadline must be in the future using '+siteTimeZone()+'.';
        safeSetText('deadline-error',message);
        el('edit-date').setAttribute('aria-invalid',String(!valid)); el('edit-time').setAttribute('aria-invalid',String(!valid));
        if(!valid&&focus) el(!el('edit-date').value?'edit-date':'edit-time').focus();
        return valid;
    }
    function visibilityValue(prefix) {
        const date=el(prefix+'-date').value,time=el(prefix+'-time').value;
        return zonedIso(date,time);
    }
    function validateVisibility(focus=false) {
        const start=visibilityValue('visible-from'),end=visibilityValue('visible-until');
        const valid=start!=='invalid' && end!=='invalid' && (!start||!end||Date.parse(start)<Date.parse(end));
        safeSetText('visibility-error',valid?'':'Use complete visibility dates; the end must be after the start in '+siteTimeZone()+'.');
        if(!valid&&focus)el('visible-from-date').focus();
        return valid;
    }
    window.openTaskEditor=function(id=null) {
        if(!editor()) return;
        returnFocus=document.activeElement; editingId=id; originalTask=id?canonicalTaskState.get(id):null;
        selectedFile=null;removeMedia=false;saving=false;
        el('task-editor-form').reset(); safeSetText('edit-error',''); safeSetText('deadline-error','');
        safeSetText('task-editor-title',id?'Edit Task':'Add Task'); safeSetText('edit-save',id?'Save Changes':'Create Task');
        el('edit-upload-progress').hidden=true;
        const task=originalTask||{day:selectedOperationalDay,dept:'General',priority:'Normal',status:'pending',channel:displayChannel};
        for(const [field,key] of [['title','title'],['desc','desc'],['day','day'],['dept','dept'],['priority','priority'],['status','status'],['schedule','time']]) el('edit-'+field).value=task[key]||'';
        el('edit-channel').value=task.channel || 'general';
        const [date,time]=inputsFor(task.deadlineUtc);el('edit-date').value=date;el('edit-time').value=time;
        for(const [prefix,iso] of [['visible-from',task.visibleFromUtc],['visible-until',task.visibleUntilUtc]]){
            const parts=inputsFor(iso);el(prefix+'-date').value=parts[0];el(prefix+'-time').value=parts[1];
        }
        safeSetText('deadline-zone','All dates in '+siteTimeZone());
        safeSetText('visibility-zone','Optional display window · '+siteTimeZone());
        safeSetText('visibility-error','');
        preview(task); validateDeadline(); el('task-editor').showModal(); el('edit-title').focus();
    };
    function uploadMedia(taskId,file) {
        return new Promise((resolve,reject)=>{
            const xhr=new XMLHttpRequest();xhr.open('POST',`/api/tasks/${taskId}/media`);
            xhr.setRequestHeader('Content-Type',file.type);xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));xhr.setRequestHeader('X-CSRF-Token',staffAuth.csrfToken||'');
            xhr.timeout=120000;
            xhr.upload.onprogress=e=>{if(e.lengthComputable){el('edit-upload-progress').hidden=false;el('edit-upload-progress').value=e.loaded/e.total*100;}};
            xhr.onload=()=>{let data;try{data=JSON.parse(xhr.responseText);}catch{data={message:'Upload failed. Please retry.'};} xhr.status>=200&&xhr.status<300?resolve(data):reject(Error(data.message||data.error));};
            xhr.onerror=xhr.ontimeout=()=>reject(Error('Upload interrupted. Your task is saved; retry to attach the media.'));
            xhr.send(file);
        });
    }
    async function saveTask(event) {
        event.preventDefault(); if(saving||!editor()||!validateDeadline(true)||!validateVisibility(true)) return;
        if(!el('task-editor-form').reportValidity()) return;
        saving=true;el('edit-save').disabled=true;el('edit-cancel').disabled=true;el('edit-close').disabled=true;
        safeSetText('edit-error','');safeSetText('edit-save','Saving…');
        try {
            const payload={title:el('edit-title').value.trim(),desc:el('edit-desc').value.trim(),day:el('edit-day').value,dept:el('edit-dept').value.trim(),priority:el('edit-priority').value,status:el('edit-status').value,time:el('edit-schedule').value.trim(),deadlineUtc:deadlineValue(),visibleFromUtc:visibilityValue('visible-from'),visibleUntilUtc:visibilityValue('visible-until'),channel:el('edit-channel').value.trim()};
            if(originalTask && !payload.deadlineUtc && !originalTask.deadlineUtc) delete payload.deadlineUtc;
            const data=await smartdisplayApi(editingId?`/api/tasks/${editingId}`:'/api/tasks',{method:editingId?'PATCH':'POST',body:JSON.stringify(payload)});
            acceptCommittedChange(data);editingId=data.task.id;originalTask=data.task;
            if(selectedFile) {const media=await uploadMedia(editingId,selectedFile);acceptCommittedChange(media);selectedFile=null;}
            else if(removeMedia) acceptCommittedChange(await smartdisplayApi(`/api/tasks/${editingId}/media`,{method:'DELETE',body:'{}'}));
            el('task-editor').close();showToast('Task saved. All displays update live.');
        } catch(e) {safeSetText('edit-error',e.message);}
        finally {saving=false;el('edit-save').disabled=false;el('edit-cancel').disabled=false;el('edit-close').disabled=false;safeSetText('edit-save','Save Changes');}
    }
    window.addEventListener('DOMContentLoaded',()=>{
        // Remove management-only sections from the television surface entirely.
        el('tv-directives-container')?.closest('.tv-body-grid > div')?.remove();
        const addPanel=el('add-task-form').parentElement;
        addPanel.classList.add('editor-only');
        // Preserve legacy IDs in a hidden inert template for existing language helpers.
        const legacy=document.createElement('div');legacy.hidden=true;legacy.inert=true;
        while(addPanel.firstChild) legacy.appendChild(addPanel.firstChild);
        addPanel.append(legacy);
        const add=document.createElement('button');add.id='add-task-button';add.className='yu-button yu-primary';add.textContent='Add Task';add.onclick=()=>openTaskEditor();addPanel.append(add);
        const hint=document.createElement('p');hint.className='yu-muted';hint.textContent='Create a task with a deadline and optional image or video. Changes appear live.';addPanel.append(hint);
        el('admin-directives-editor-list').closest('.col-span-12 > div.bg-slate-50')?.classList.add('editor-only');
        el('view-sheets').classList.add('editor-only');
        document.querySelectorAll('[onclick="switchView(\'sheets\')"]').forEach(n=>n.classList.add('editor-only'));
        el('btn-reset-tasks')?.closest('button')?.classList.add('editor-only');
        const adminView=el('view-admin'),workspace=adminView.firstElementChild;
        workspace.id='manager-workspace';workspace.classList.add('manager-workspace','editor-only');
        adminView.insertAdjacentHTML('afterbegin',`<section id="manager-access-gate" class="manager-access-gate viewer-only" aria-labelledby="manager-access-title">
            <div class="manager-access-visual" aria-hidden="true"><span class="access-orbit orbit-one"></span><span class="access-orbit orbit-two"></span><div class="access-emblem"><i class="fa-solid fa-user-shield"></i><span class="access-live-dot"></span></div></div>
            <div class="manager-access-content"><h2 id="manager-access-title" tabindex="-1">Sign in to manage the live display</h2><p id="manager-access-body" class="manager-access-body">The Admin Panel is protected. Use an approved Google account to update tasks, deadlines, media and the broadcast day.</p>
            <ul class="manager-access-benefits"><li><i class="fa-solid fa-bolt"></i><span id="manager-access-benefit-1">Publish changes live</span></li><li><i class="fa-solid fa-photo-film"></i><span id="manager-access-benefit-2">Manage media and deadlines</span></li><li><i class="fa-solid fa-display"></i><span id="manager-access-benefit-3">Keep every screen in sync</span></li></ul>
            <div id="google-signin-zone" class="google-signin-zone" aria-live="polite"><div class="google-loader" aria-hidden="true"><i class="fa-solid fa-circle-notch fa-spin"></i></div><p id="staff-login-status">Preparing secure Google sign-in…</p><div id="google-button" class="google-button-host"></div><button id="staff-sign-in" class="yu-button yu-primary" type="button" hidden><i class="fa-brands fa-google" aria-hidden="true"></i><span id="staff-sign-in-label">Try Google sign-in again</span></button><p id="staff-error" class="yu-error" role="alert"></p></div>
            <p id="manager-access-security" class="manager-access-security"><i class="fa-solid fa-lock" aria-hidden="true"></i><span id="manager-access-security-text">Google verifies your identity. Smart Display never receives or stores your Google password.</span></p></div>
        </section>
        <section id="staff-session-panel" class="staff-session-panel editor-only" aria-label="Signed-in Editor">
            <div class="staff-avatar" aria-hidden="true"><i class="fa-solid fa-user-check"></i></div><div class="staff-session-copy"><span id="staff-session-label">Signed in as</span><strong id="staff-identity"></strong></div><span class="staff-editor-badge"><i class="fa-solid fa-circle-check" aria-hidden="true"></i> Editor</span><button id="staff-logout" class="yu-button" type="button"><i class="fa-solid fa-arrow-right-from-bracket" aria-hidden="true"></i><span id="staff-logout-label">Logout</span></button>
        </section>`);
        document.body.insertAdjacentHTML('beforeend',`<dialog id="task-editor" class="task-dialog" aria-labelledby="task-editor-title"><form id="task-editor-form" novalidate>
            <div class="form-heading"><h2 id="task-editor-title">Edit Task</h2><button type="button" id="edit-close" class="yu-button" aria-label="Close task editor"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button></div>
            <label>Task title<input id="edit-title" required maxlength="2000" autocomplete="off"></label>
            <label>Description<textarea id="edit-desc" rows="3" maxlength="2000"></textarea></label>
            <div class="form-pair"><label>Weekday<select id="edit-day">${TASK_DAYS.map(d=>`<option>${d}</option>`).join('')}</select></label><label>Department<input id="edit-dept" required maxlength="2000" list="department-options"><datalist id="department-options"><option>General</option><option>Operations</option><option>Facilities</option><option>People</option></datalist></label></div>
            <label>Screen channel (lowercase, e.g. general or lobby)<input id="edit-channel" required maxlength="32" pattern="[a-z0-9][a-z0-9-]{0,31}" aria-describedby="channel-hint"><span id="channel-hint" class="yu-muted">Open /?channel=lobby on a different TV to show only lobby items. Default is general.</span></label>
            <div class="form-pair"><label>Priority<select id="edit-priority"><option>Normal</option><option>Important</option><option>Urgent</option></select></label><label>Status<select id="edit-status"><option value="pending">Pending</option><option value="completed">Completed</option></select></label></div>
            <div><div class="form-pair"><label><span><i class="fa-regular fa-calendar" aria-hidden="true"></i> Deadline date</span><input id="edit-date" type="date" aria-describedby="deadline-zone deadline-error"></label><label><span><i class="fa-regular fa-clock" aria-hidden="true"></i> Deadline time</span><input id="edit-time" type="time" aria-describedby="deadline-zone deadline-error"></label></div><p id="deadline-zone" class="yu-muted">All dates in configured display timezone</p><p id="deadline-error" class="yu-error" role="alert"></p></div>
            <fieldset class="visibility-fieldset"><legend>Show on display (optional)</legend><p class="yu-muted">Separate from the completion deadline. Leave empty to show immediately or indefinitely.</p>
            <div class="form-pair"><label>Start date<input id="visible-from-date" type="date" aria-describedby="visibility-zone visibility-error"></label><label>Start time<input id="visible-from-time" type="time" aria-describedby="visibility-zone visibility-error"></label></div>
            <div class="form-pair"><label>End date<input id="visible-until-date" type="date" aria-describedby="visibility-zone visibility-error"></label><label>End time<input id="visible-until-time" type="time" aria-describedby="visibility-zone visibility-error"></label></div>
            <p id="visibility-zone" class="yu-muted">All dates in configured display timezone</p><p id="visibility-error" class="yu-error" role="alert"></p></fieldset>
            <label>Schedule note (optional)<input id="edit-schedule" maxlength="2000" placeholder="Existing schedule or time range"><span class="yu-muted">The deadline is set using the date and time pickers above.</span></label>
            <div id="edit-dropzone" class="media-picker"><label>Image or video<input id="edit-file" type="file" accept=".jpg,.jpeg,.png,.webp,.gif,.mp4,.webm"></label><p class="yu-muted">Choose a file or drop it here. JPG, PNG, WebP, GIF, MP4 or WebM · up to 25 MB.</p><div id="edit-media-preview"></div><p id="edit-media-info" class="yu-muted"></p><button type="button" id="edit-remove-media" class="yu-button yu-danger" hidden>Remove media</button><progress id="edit-upload-progress" max="100" value="0" aria-label="Media upload progress" hidden></progress></div>
            <p id="edit-error" class="yu-error" role="alert"></p><div class="form-actions"><button id="edit-cancel" class="yu-button" type="button">Cancel</button><button id="edit-save" class="yu-button yu-primary" type="submit">Save Changes</button></div>
        </form></dialog>`);
        el('staff-sign-in').onclick=()=>prepareGoogleSignIn();
        el('staff-logout').onclick=async()=>{try{await smartdisplayApi('/api/auth/logout',{method:'POST',body:'{}'});googleRendered=false;await refreshStaffSession();showToast('Signed out. Public viewing remains available.');}catch(e){safeSetText('staff-error',e.message);}};
        el('task-editor-form').onsubmit=saveTask;
        window.resetDefaultTasks=()=>showToast('Demo reset is not available. Your tasks are preserved.');
        el('edit-close').onclick=el('edit-cancel').onclick=()=>{if(!saving)el('task-editor').close();};
        el('task-editor').addEventListener('cancel',e=>{if(saving)e.preventDefault();});
        el('task-editor').addEventListener('close',()=>{if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;el('edit-media-preview').replaceChildren();returnFocus?.focus();});
        ['edit-date','edit-time','edit-status'].forEach(id=>el(id).addEventListener('input',()=>validateDeadline()));
        ['visible-from-date','visible-from-time','visible-until-date','visible-until-time'].forEach(id=>el(id).addEventListener('input',()=>validateVisibility()));
        el('edit-file').onchange=e=>chooseFile(e.target.files[0]);
        el('edit-remove-media').onclick=()=>{selectedFile=null;removeMedia=true;el('edit-file').value='';preview();};
        const drop=el('edit-dropzone');
        drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag-active');};drop.ondragleave=()=>drop.classList.remove('drag-active');
        drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag-active');chooseFile(e.dataTransfer.files[0]);};
        // Logo load failure is local presentation fallback, never a business mutation.
        ['custom-logo-img','tv-custom-logo-img'].forEach(id=>{el(id).onerror=()=>{el(id).classList.add('hidden');el(id==='custom-logo-img'?'default-logo-svg':'tv-default-logo-svg').classList.remove('hidden');};});
        const switchViewBase=window.switchView;
        window.switchView=viewName=>{
            switchViewBase(viewName);applyManagerAccessState();
            if(viewName==='admin'&&!editor()){
                void prepareGoogleSignIn();
            }
        };
        const setLanguageBase=window.setLanguage;
        window.setLanguage=lang=>{setLanguageBase(lang);updateManagerAccessCopy();};
        const toggleThemeBase=window.toggleTheme;
        window.toggleTheme=()=>{
            toggleThemeBase();
            if(!editor()&&currentView==='admin'&&googleRendered)renderGoogleButton();
        };
        updateManagerAccessCopy();applyManagerAccessState();
        new ResizeObserver(()=>{
            const zone=el('google-signin-zone');
            if(!editor()&&currentView==='admin'&&googleRendered&&zone){
                const next=Math.max(200,Math.min(280,Math.floor(zone.clientWidth-36)));
                if(Math.abs(next-lastGoogleWidth)>4)renderGoogleButton();
            }
        }).observe(el('google-signin-zone'));
        new ResizeObserver(()=>updateTvDisplay()).observe(el('tv-tasks-container'));
        setInterval(()=>{if(currentView==='tv'&&tvPages>1&&!document.hidden){tvPage=(tvPage+1)%tvPages;updateTvDisplay();}},15000);
        setInterval(()=>{updateTvDisplay();if(currentView==='admin')renderAdminTasks();},30000);
        setInterval(refreshStaffSession,60000);
        window.addEventListener('focus',refreshStaffSession);
        void refreshStaffSession();updateTvDisplay();
    });
})();
