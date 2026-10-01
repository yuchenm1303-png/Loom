(() => {
  const API = '/api/v1';
  const $ = (id) => document.getElementById(id);
  const state = {
    access: sessionStorage.getItem('loom_admin_access') || '',
    refresh: sessionStorage.getItem('loom_admin_refresh') || '',
    me: null,
    overview: null,
    users: [],
    sessions: [],
    system: null,
    health: { admin:false, account:false, loom:false },
    range: '24h',
    selectedUser: null,
    selectedModelAccess: null,
  };
  const BUILTIN_MODELS = ['Ling-3.0-flash','Ling-3.0-flash-VL','Ling-3.0-tiny','Ling-2.6-1T','Ring-2.6-1T','Ling-2.6-flash'];

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtTime = (value) => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
  const shortSession = (value) => value ? `${value.slice(0,8)}…${value.slice(-5)}` : '—';

  function saveTokens(payload) {
    if (payload.access_token) { state.access = payload.access_token; sessionStorage.setItem('loom_admin_access', state.access); }
    if (payload.refresh_token) { state.refresh = payload.refresh_token; sessionStorage.setItem('loom_admin_refresh', state.refresh); }
  }
  function clearTokens() {
    state.access=''; state.refresh=''; state.me=null;
    sessionStorage.removeItem('loom_admin_access'); sessionStorage.removeItem('loom_admin_refresh');
  }

  async function rawRequest(path, options={}) {
    const headers = {'Content-Type':'application/json', ...(options.headers || {})};
    if (state.access) headers.Authorization = `Bearer ${state.access}`;
    const response = await fetch(API + path, {...options, headers, credentials:'same-origin', cache:'no-store'});
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return {response, payload};
  }
  async function refreshAccess() {
    if (!state.refresh) return false;
    const response = await fetch(API + '/auth/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({refresh_token:state.refresh}), cache:'no-store'});
    if (!response.ok) { clearTokens(); return false; }
    saveTokens(await response.json());
    return true;
  }
  async function request(path, options={}, retry=true) {
    let {response, payload} = await rawRequest(path, options);
    if (response.status === 401 && retry && await refreshAccess()) ({response, payload} = await rawRequest(path, options));
    if (!response.ok) {
      const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
      error.code = payload?.error?.code || ''; error.status = response.status; throw error;
    }
    return payload;
  }
  async function probe(path) {
    try { return (await fetch(path, {cache:'no-store', credentials:'same-origin'})).ok; }
    catch (_) { return false; }
  }

  function setHeader(ok, text) {
    const el=$('headerStatus'); el.querySelector('span').textContent=text; el.dataset.state=ok?'ok':'bad';
  }
  function setAuthenticated(on) { $('authPanel').hidden=on; $('adminContent').hidden=!on; $('logoutButton').hidden=!on; }

  async function login(email, password) {
    const response = await fetch(API + '/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email,password}), cache:'no-store'});
    const payload = await response.json().catch(()=>({}));
    if (!response.ok) { const e=new Error(payload?.error?.message || '登录失败'); e.code=payload?.error?.code||''; e.status=response.status; throw e; }
    saveTokens(payload);
    try { state.me=(await request('/admin/me')).user; }
    catch (error) { clearTokens(); throw error; }
  }

  function renderOverview() {
    const o=state.overview || {};
    [['kpiUsers','users'],['kpiActive','active_users'],['kpiTotalSessions','total_sessions'],['kpiSessions','active_sessions'],['kpiActive24','active_24h'],['kpiNew','registrations_24h'],['kpiAdmins','admins'],['kpiOwners','owners']]
      .forEach(([id,key]) => $(id).textContent=String(o[key] ?? 0));
    $('generatedAt').textContent=o.generated_at ? fmtTime(o.generated_at) : '—';
    $('adminIdentity').textContent=`${state.me.email} · ${state.me.role}`;
    $('serviceState').textContent=state.health.account ? 'Healthy' : 'Unavailable';
    $('serviceState').dataset.state=state.health.account?'ok':'bad';
    renderActivity();
  }

  function rangeConfig() {
    if (state.range === '7d') return {seconds:7*86400,buckets:28,unit:6*3600,start:'-7d',mid:'-3.5d'};
    if (state.range === '30d') return {seconds:30*86400,buckets:30,unit:86400,start:'-30d',mid:'-15d'};
    return {seconds:86400,buckets:24,unit:3600,start:'-24h',mid:'-12h'};
  }
  function renderActivity() {
    const cfg=rangeConfig(), now=Math.floor(Date.now()/1000), start=now-cfg.seconds;
    const users=new Array(cfg.buckets).fill(0), sessions=new Array(cfg.buckets).fill(0);
    state.users.forEach(u => { const t=Number(u.last_seen_at||0); if(t<start)return; users[Math.min(cfg.buckets-1,Math.max(0,Math.floor((t-start)/cfg.unit)))]++; });
    state.sessions.forEach(s => { const t=Number(s.last_used_at||0); if(t<start)return; sessions[Math.min(cfg.buckets-1,Math.max(0,Math.floor((t-start)/cfg.unit)))]++; });
    $('activityKicker').textContent=`${state.range.toUpperCase()} ACTIVITY`;
    $('activityMeta').textContent=`${state.range.toUpperCase()} · 基于真实 last active 记录`;
    const rail=$('activityRail'); rail.innerHTML=''; rail.style.gridTemplateColumns=`repeat(${cfg.buckets},minmax(0,1fr))`;
    users.forEach((n,i)=>{const el=document.createElement('span');el.className=n?'is-active':'is-idle';el.title=`bucket ${i+1}: ${n}`;rail.appendChild(el)});
    $('activitySummary').textContent=`${users.reduce((a,b)=>a+b,0)} users`;
    const spark=$('sessionSpark'); spark.innerHTML=''; const max=Math.max(1,...sessions);
    sessions.forEach(n=>{const bar=document.createElement('i');bar.style.height=`${8+(n/max)*82}%`;bar.style.opacity=n?'.95':'.16';spark.appendChild(bar)});
    $('sessionActivitySummary').textContent=`${sessions.reduce((a,b)=>a+b,0)} sessions`;
    const labels=new Array(cfg.buckets).fill(''); labels[0]=cfg.start; labels[Math.floor((cfg.buckets-1)/2)]=cfg.mid; labels[cfg.buckets-1]='now';
    $('activityAxis').innerHTML=labels.map(x=>`<span>${x}</span>`).join(''); $('sessionAxis').innerHTML=$('activityAxis').innerHTML;
  }

  function verifiedLabel(value) {
    if (value === true) return '<span class="loom-admin-badge is-ok">Verified</span>';
    if (value === false) return '<span class="loom-admin-badge is-bad">Unverified</span>';
    return '<span class="loom-admin-badge">Not configured</span>';
  }
  function renderUsers(filter='') {
    const q=filter.trim().toLowerCase();
    const users=state.users.filter(u=>!q || `${u.email} ${u.role} ${u.status}`.toLowerCase().includes(q));
    $('usersHint').textContent=`${users.length} users`;
    $('usersBody').innerHTML=users.length ? users.map(u=>{
      const self=Number(u.id)===Number(state.me.id);
      return `<tr>
        <td><button class="loom-admin-user-link" data-user-detail="${u.id}"><strong>${escapeHtml(u.email)}</strong>${u.display_name?`<small>${escapeHtml(u.display_name)}</small>`:''}</button></td>
        <td>${state.me?.role==='owner'?`<select data-role-user="${u.id}" aria-label="Role"><option value="user" ${u.role==='user'?'selected':''}>user</option><option value="admin" ${u.role==='admin'?'selected':''}>admin</option><option value="owner" ${u.role==='owner'?'selected':''}>owner</option></select>`:`<span class="loom-admin-badge">${escapeHtml(u.role)}</span>`}</td>
        <td><span class="loom-admin-badge ${u.status==='active'?'is-ok':'is-bad'}">${escapeHtml(u.status)}</span></td>
        <td>${verifiedLabel(u.verified)}</td><td>${u.active_sessions}</td><td>${fmtTime(u.created_at)}</td><td>${fmtTime(u.last_seen_at)}</td>
        <td><div class="loom-admin-actions"><button class="loom-admin-action" data-status-user="${u.id}" data-next-status="${u.status==='active'?'disabled':'active'}" ${self&&u.status==='active'?'disabled':''}>${u.status==='active'?'Disable':'Enable'}</button><button class="loom-admin-action is-danger" data-revoke-user="${u.id}">Revoke sessions</button></div></td>
      </tr>`;
    }).join('') : '<tr><td colspan="8" class="loom-admin-empty">没有匹配用户</td></tr>';
  }
  function renderSessions() {
    const now=Math.floor(Date.now()/1000); $('sessionsHint').textContent=`${state.sessions.length} sessions`;
    $('sessionsBody').innerHTML=state.sessions.length ? state.sessions.map(s=>{
      const live=!s.revoked_at && Number(s.refresh_expires_at)>now;
      return `<tr><td>${escapeHtml(s.email)}</td><td><code title="${escapeHtml(s.id)}">${escapeHtml(shortSession(s.id))}</code></td><td>${fmtTime(s.created_at)}</td><td>${fmtTime(s.last_used_at)}</td><td>${fmtTime(s.refresh_expires_at)}</td><td><span class="loom-admin-badge ${live?'is-ok':'is-bad'}">${live?'active':(s.revoked_at?'revoked':'expired')}</span></td><td>${live?`<button class="loom-admin-action is-danger" data-revoke-session="${escapeHtml(s.id)}">Revoke</button>`:'—'}</td></tr>`;
    }).join('') : '<tr><td colspan="7" class="loom-admin-empty">暂无会话</td></tr>';
  }
  function healthRow(name, ok, good='Healthy', bad='Unavailable') {
    return `<div class="loom-admin-health-item"><span>${escapeHtml(name)}</span><b class="${ok?'is-ok':'is-bad'}">${escapeHtml(ok?good:bad)}</b></div>`;
  }
  function renderSystem() {
    const s=state.system || {}; const smtp=s.smtp?.status;
    $('systemHealth').innerHTML=[
      healthRow('Admin UI',state.health.admin),
      healthRow('Account API',state.health.account),
      healthRow('Database',s.database?.status==='healthy','Healthy','Unavailable'),
      healthRow('Loom Web',state.health.loom),
      healthRow('SMTP',smtp==='configured','Configured',smtp==='not_configured'?'Not configured':'Unavailable'),
    ].join('');
    $('releaseInfo').innerHTML=`<strong>${escapeHtml(s.release || 'unknown')}</strong><small>Account API · ${fmtTime(s.generated_at)}</small>`;
  }
  async function loadHealth() {
    const [admin,account,loom]=await Promise.all([probe('/healthz'),probe('/api/healthz'),probe('/ops/loom-healthz')]);
    state.health={admin,account,loom};
  }
  async function loadAll() {
    setHeader(true,'Loading');
    const [overview,users,sessions,system]=await Promise.all([request('/admin/overview'),request('/admin/users'),request('/admin/sessions'),request('/admin/system'),loadHealth()]);
    state.overview=overview; state.users=users.users||[]; state.sessions=sessions.sessions||[]; state.system=system;
    renderOverview(); renderUsers($('userSearch').value); renderSessions(); renderSystem();
    const ok=state.health.admin&&state.health.account; setHeader(ok,ok?'Operational':'Degraded'); setAuthenticated(true);
  }
  async function mutate(path,body={}) { await request(path,{method:'POST',body:JSON.stringify(body)}); await loadAll(); }
  async function openUserDetail(id) {
    const [userPayload, accessPayload] = await Promise.all([request(`/admin/users/${id}`), request(`/admin/users/${id}/model-access`)]);
    const u=userPayload.user, access=accessPayload.access || {enabled:true,models:[]}; state.selectedUser=u; state.selectedModelAccess=access;
    $('detailTitle').textContent=u.email;
    const enabledModels=new Set((access.models||[]).map(String));
    const modelRows=BUILTIN_MODELS.map(model=>`<label class="loom-admin-model-toggle"><input type="checkbox" data-model-access-id="${escapeHtml(model)}" ${enabledModels.has(model)?'checked':''}><span>${escapeHtml(model)}</span></label>`).join('');
    $('userDetailBody').innerHTML=`<div><span>Role</span><strong>${escapeHtml(u.role)}</strong></div><div><span>Status</span><strong>${escapeHtml(u.status)}</strong></div><div><span>Verified</span><strong>${u.verified==null?'Not configured':(u.verified?'Verified':'Unverified')}</strong></div><div><span>Active sessions</span><strong>${u.active_sessions}</strong></div><div><span>Created</span><strong>${fmtTime(u.created_at)}</strong></div><div><span>Last active</span><strong>${fmtTime(u.last_seen_at)}</strong></div><section class="loom-admin-model-access"><div class="loom-admin-model-access-head"><span>Built-in models</span><label><input id="modelAccessEnabled" type="checkbox" ${access.enabled!==false?'checked':''}> Enabled</label></div><small>${access.source==='override'?'Per-user override':'Using Loom default policy'}</small><div class="loom-admin-model-grid">${modelRows}</div><button id="saveModelAccessButton" class="loom-admin-action" type="button">Save model access</button></section>`;
    $('detailStatusButton').textContent=u.status==='active'?'Disable':'Enable'; $('detailStatusButton').disabled=Number(u.id)===Number(state.me.id)&&u.status==='active'; $('userDialog').showModal();
  }


  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';const b=e.currentTarget.querySelector('button');b.disabled=true;try{await login($('loginEmail').value.trim(),$('loginPassword').value);$('loginPassword').value='';await loadAll()}catch(err){$('loginError').textContent=err.code==='ADMIN_REQUIRED'?'这个 Loom 账号没有管理员权限。':(err.message||'登录失败');setHeader(false,'Access denied')}finally{b.disabled=false}});
  $('logoutButton').addEventListener('click',async()=>{const refresh=state.refresh;if(refresh){try{await fetch(API+'/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),cache:'no-store'})}catch(_){}}clearTokens();setAuthenticated(false);setHeader(false,'Signed out')});
  $('refreshButton').addEventListener('click',()=>loadAll().catch(showLoadError)); $('userSearch').addEventListener('input',e=>renderUsers(e.target.value));
  $('activityRangeControl').addEventListener('click',e=>{const b=e.target.closest('[data-range]');if(!b)return;state.range=b.dataset.range;document.querySelectorAll('#activityRangeControl [data-range]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderActivity()});
  $('usersBody').addEventListener('click',async e=>{const detail=e.target.closest('[data-user-detail]'),status=e.target.closest('[data-status-user]'),revoke=e.target.closest('[data-revoke-user]');try{if(detail)return await openUserDetail(Number(detail.dataset.userDetail));if(status){const id=Number(status.dataset.statusUser),next=status.dataset.nextStatus;if(next==='disabled'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;return await mutate(`/admin/users/${id}/${next==='disabled'?'disable':'enable'}`)}if(revoke){if(!confirm('确认撤销这个用户的全部有效会话？'))return;return await mutate('/admin/users/revoke-sessions',{user_id:Number(revoke.dataset.revokeUser)})}}catch(err){alert(err.message||'操作失败')}});
  $('usersBody').addEventListener('change',async e=>{const select=e.target.closest('[data-role-user]');if(!select)return;try{await mutate('/admin/users/role',{user_id:Number(select.dataset.roleUser),role:select.value})}catch(err){alert(err.message||'修改角色失败');await loadAll()}});
  $('sessionsBody').addEventListener('click',async e=>{const revoke=e.target.closest('[data-revoke-session]');if(!revoke||!confirm('确认撤销这个 Session？'))return;try{await mutate(`/admin/sessions/${encodeURIComponent(revoke.dataset.revokeSession)}/revoke`)}catch(err){alert(err.message||'撤销失败')}});
  $('userDialog').addEventListener('click',async e=>{
    const save=e.target.closest('#saveModelAccessButton'); if(!save)return; const u=state.selectedUser;if(!u)return;
    const models=[...$('userDetailBody').querySelectorAll('[data-model-access-id]:checked')].map(el=>el.dataset.modelAccessId);
    const enabled=Boolean($('modelAccessEnabled')?.checked); save.disabled=true;
    try { const result=await request('/admin/users/model-access',{method:'POST',body:JSON.stringify({user_id:Number(u.id),enabled,models})}); state.selectedModelAccess=result.access; save.textContent='Saved'; setTimeout(()=>{save.textContent='Save model access'},900); }
    catch(err){alert(err.message||'修改内置模型权限失败')} finally{save.disabled=false}
  });
  $('closeUserDialog').addEventListener('click',()=>$('userDialog').close());
  $('detailStatusButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u)return;const next=u.status==='active'?'disable':'enable';if(next==='disable'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;try{await mutate(`/admin/users/${u.id}/${next}`);$('userDialog').close()}catch(err){alert(err.message||'操作失败')}});
  $('detailRevokeButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u||!confirm('确认撤销这个用户的全部有效会话？'))return;try{await mutate('/admin/users/revoke-sessions',{user_id:Number(u.id)});$('userDialog').close()}catch(err){alert(err.message||'操作失败')}});

  function showLoadError(err) { if(err?.status===401||err?.status===403){clearTokens();setAuthenticated(false);$('loginError').textContent=err.status===403?'这个账号没有管理员权限。':'登录已过期，请重新登录。';setHeader(false,'Access required');return}setHeader(false,'Service error');console.error(err); }
  (async()=>{try{if(state.access||state.refresh){state.me=(await request('/admin/me')).user;await loadAll()}else{setAuthenticated(false);setHeader(false,'Admin sign in')}}catch(err){showLoadError(err)}})();
})();
