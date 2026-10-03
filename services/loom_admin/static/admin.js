(() => {
  const API = '/api/v1';
  const $ = (id) => document.getElementById(id);
  const state = {
    access: sessionStorage.getItem('loom_admin_access') || '',
    refresh: sessionStorage.getItem('loom_admin_refresh') || '',
    me: null,
    overview: {}, agent: {}, users: [], sessions: [], system: {}, devices: [], runs: [],
    usage: {}, tools: {}, models: {}, audit: [], flags: [],
    health: { admin:false, account:false, loom:false },
    activityRange: '24h', usageRange: '24h', selectedUser: null, selectedModelAccess: null,
    page: 'overview', refreshTimer: null,
  };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtTime = (value) => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
  const shortId = (value, a=8, b=5) => value ? `${String(value).slice(0,a)}…${String(value).slice(-b)}` : '—';
  const fmtNumber = (value) => new Intl.NumberFormat('en-US').format(Number(value || 0));
  const fmtCompact = (value) => new Intl.NumberFormat('en-US', {notation:'compact', maximumFractionDigits:1}).format(Number(value || 0));
  const fmtDuration = (seconds) => {
    const s=Math.max(0,Math.round(Number(seconds||0))); if(s<60)return `${s}s`; const m=Math.floor(s/60); if(m<60)return `${m}m ${s%60}s`; const h=Math.floor(m/60); return `${h}h ${m%60}m`;
  };
  const activeRun = (run) => ['running','waiting_approval'].includes(String(run?.status||''));

  function saveTokens(payload) {
    if (payload.access_token) { state.access=payload.access_token; sessionStorage.setItem('loom_admin_access', state.access); }
    if (payload.refresh_token) { state.refresh=payload.refresh_token; sessionStorage.setItem('loom_admin_refresh', state.refresh); }
  }
  function clearTokens() {
    state.access=''; state.refresh=''; state.me=null;
    sessionStorage.removeItem('loom_admin_access'); sessionStorage.removeItem('loom_admin_refresh');
    if(state.refreshTimer){clearInterval(state.refreshTimer);state.refreshTimer=null;}
  }
  async function rawRequest(path, options={}) {
    const headers={'Content-Type':'application/json', ...(options.headers||{})};
    if(state.access) headers.Authorization=`Bearer ${state.access}`;
    const response=await fetch(API+path,{...options,headers,credentials:'same-origin',cache:'no-store'});
    let payload={}; try{payload=await response.json()}catch(_){}
    return {response,payload};
  }
  async function refreshAccess() {
    if(!state.refresh)return false;
    const response=await fetch(API+'/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:state.refresh}),cache:'no-store'});
    if(!response.ok){clearTokens();return false;} saveTokens(await response.json()); return true;
  }
  async function request(path,options={},retry=true){
    let {response,payload}=await rawRequest(path,options);
    if(response.status===401&&retry&&await refreshAccess())({response,payload}=await rawRequest(path,options));
    if(!response.ok){const e=new Error(payload?.error?.message||`HTTP ${response.status}`);e.code=payload?.error?.code||'';e.status=response.status;throw e;}
    return payload;
  }
  async function probe(path){try{return (await fetch(path,{cache:'no-store',credentials:'same-origin'})).ok}catch(_){return false}}
  function setHeader(ok,text){const el=$('headerStatus');el.querySelector('span').textContent=text;el.dataset.state=ok?'ok':'bad';}
  function setAuthenticated(on){$('authPanel').hidden=on;$('adminContent').hidden=!on;$('logoutButton').hidden=!on;$('sectionNav').hidden=!on;}

  async function login(email,password){
    const response=await fetch(API+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password}),cache:'no-store'});
    const payload=await response.json().catch(()=>({}));
    if(!response.ok){const e=new Error(payload?.error?.message||'登录失败');e.code=payload?.error?.code||'';e.status=response.status;throw e;}
    saveTokens(payload);
    try{state.me=(await request('/admin/me')).user}catch(error){clearTokens();throw error;}
  }

  function renderOverview(){
    const o=state.overview||{}, a=state.agent||{};
    $('kpiUsers').textContent=fmtNumber(o.users); $('kpiOnlineDevices').textContent=fmtNumber(a.online_devices);
    $('kpiActiveRuns').textContent=fmtNumber(a.active_runs); $('kpiApprovals').textContent=fmtNumber(a.waiting_approvals);
    $('kpiTokens24').textContent=fmtCompact(a.tokens_24h); $('kpiToolCalls24').textContent=fmtNumber(a.tool_calls_24h);
    $('kpiSessions').textContent=fmtNumber(o.active_sessions); $('kpiNew').textContent=fmtNumber(o.registrations_24h);
    $('generatedAt').textContent=fmtTime(a.generated_at||o.generated_at); $('adminIdentity').textContent=`${state.me.email} · ${state.me.role}`;
    $('serviceState').textContent=state.health.account?'Healthy':'Unavailable'; $('serviceState').dataset.state=state.health.account?'ok':'bad';
    $('loomWebState').textContent=state.health.loom?'Healthy':'Unavailable'; $('loomWebState').dataset.state=state.health.loom?'ok':'bad';
    renderActivity();renderOverviewControls();
  }
  function renderOverviewControls(){
    const root=$('overviewFlags'); if(!root)return;
    const flags=Array.isArray(state.flags)?state.flags:[];
    root.innerHTML=flags.length?flags.slice(0,8).map(f=>`<div class="loom-admin-flag-row"><div><strong>${escapeHtml(f.key)}</strong><small>${escapeHtml(JSON.stringify(f.value??{}))}</small></div><label class="loom-admin-switch"><input type="checkbox" data-overview-flag-toggle="${escapeHtml(f.key)}" ${f.enabled?'checked':''}><span></span></label></div>`).join(''):'<p class="loom-admin-empty-block">暂无全局 Feature Flag。这里不会伪造没有实际消费链路的开关。</p>';
  }
  function rangeConfig(range){
    if(range==='7d')return{seconds:7*86400,buckets:28,unit:6*3600,start:'-7d',mid:'-3.5d'};
    if(range==='30d')return{seconds:30*86400,buckets:30,unit:86400,start:'-30d',mid:'-15d'};
    return{seconds:86400,buckets:24,unit:3600,start:'-24h',mid:'-12h'};
  }
  function renderActivity(){
    const cfg=rangeConfig(state.activityRange),now=Math.floor(Date.now()/1000),start=now-cfg.seconds;
    const users=new Array(cfg.buckets).fill(0),runs=new Array(cfg.buckets).fill(0);
    state.users.forEach(u=>{const t=Number(u.last_seen_at||0);if(t<start)return;users[Math.min(cfg.buckets-1,Math.max(0,Math.floor((t-start)/cfg.unit)))]++});
    state.runs.forEach(r=>{const t=Number(r.started_at||0);if(t<start)return;runs[Math.min(cfg.buckets-1,Math.max(0,Math.floor((t-start)/cfg.unit)))]++});
    const rail=$('activityRail');rail.innerHTML='';rail.style.gridTemplateColumns=`repeat(${cfg.buckets},minmax(0,1fr))`;
    users.forEach((n,i)=>{const el=document.createElement('span');el.className=n?'is-active':'is-idle';el.title=`bucket ${i+1}: ${n}`;rail.appendChild(el)});
    $('activitySummary').textContent=`${users.reduce((a,b)=>a+b,0)} users`;
    const spark=$('runSpark');spark.innerHTML='';const max=Math.max(1,...runs);runs.forEach(n=>{const bar=document.createElement('i');bar.style.height=`${8+(n/max)*82}%`;bar.style.opacity=n?'.95':'.16';spark.appendChild(bar)});
    $('runActivitySummary').textContent=`${runs.reduce((a,b)=>a+b,0)} runs`;
    const labels=new Array(cfg.buckets).fill('');labels[0]=cfg.start;labels[Math.floor((cfg.buckets-1)/2)]=cfg.mid;labels[cfg.buckets-1]='now';
    $('activityAxis').innerHTML=labels.map(x=>`<span>${x}</span>`).join('');$('runAxis').innerHTML=$('activityAxis').innerHTML;
  }

  function verifiedLabel(value){return value===true?'<span class="loom-admin-badge is-ok">Verified</span>':value===false?'<span class="loom-admin-badge is-bad">Unverified</span>':'<span class="loom-admin-badge">Unknown</span>'}
  function renderUsers(filter=''){
    const q=filter.trim().toLowerCase(),users=state.users.filter(u=>!q||`${u.email} ${u.display_name||''} ${u.role} ${u.status}`.toLowerCase().includes(q));
    $('usersHint').textContent=`${users.length} users`;
    $('usersBody').innerHTML=users.length?users.map(u=>{const self=Number(u.id)===Number(state.me.id);return `<tr>
      <td><button class="loom-admin-user-link" data-user-detail="${u.id}"><strong>${escapeHtml(u.email)}</strong>${u.display_name?`<small>${escapeHtml(u.display_name)}</small>`:''}</button></td>
      <td>${state.me?.role==='owner'?`<select data-role-user="${u.id}" aria-label="Role"><option value="user" ${u.role==='user'?'selected':''}>user</option><option value="admin" ${u.role==='admin'?'selected':''}>admin</option><option value="owner" ${u.role==='owner'?'selected':''}>owner</option></select>`:`<span class="loom-admin-badge">${escapeHtml(u.role)}</span>`}</td>
      <td><span class="loom-admin-badge ${u.status==='active'?'is-ok':'is-bad'}">${escapeHtml(u.status)}</span></td><td>${verifiedLabel(u.email_verified??u.verified)}</td><td>${u.active_sessions}</td><td>${fmtTime(u.created_at)}</td><td>${fmtTime(u.last_seen_at)}</td>
      <td><div class="loom-admin-actions"><button class="loom-admin-action" data-status-user="${u.id}" data-next-status="${u.status==='active'?'disabled':'active'}" ${self&&u.status==='active'?'disabled':''}>${u.status==='active'?'Disable':'Enable'}</button><button class="loom-admin-action is-danger" data-revoke-user="${u.id}">Revoke</button></div></td></tr>`}).join(''):'<tr><td colspan="8" class="loom-admin-empty">没有匹配用户</td></tr>';
  }
  function renderDevices(){
    $('devicesHint').textContent=`${state.devices.length} devices`;
    $('devicesBody').innerHTML=state.devices.length?state.devices.map(d=>`<tr><td>${escapeHtml(d.email)}</td><td><strong>${escapeHtml(d.name||'Loom Host')}</strong><small class="loom-admin-subline" title="${escapeHtml(d.device_id)}">${escapeHtml(shortId(d.device_id,7,4))}</small></td><td>${escapeHtml(d.platform||'—')}</td><td>${escapeHtml(d.app_version||'—')}</td><td>${escapeHtml(d.host_version||'—')}</td><td>${Number(d.host_protocol||0)} / ${Number(d.bootstrap_protocol||0)}</td><td>${escapeHtml(d.host_mode||'—')}</td><td>${fmtTime(d.last_seen_at)}</td><td><span class="loom-admin-badge ${d.online?'is-ok':'is-bad'}">${d.online?'online':'offline'}</span></td></tr>`).join(''):'<tr><td colspan="9" class="loom-admin-empty">还没有 Host telemetry。设备连接 Loom Web 后会自动出现。</td></tr>';
  }
  function runStatusBadge(status){const s=String(status||'unknown');const cls=s==='completed'?'is-ok':s==='waiting_approval'?'is-warn':['failed','interrupted','cancelled'].includes(s)?'is-bad':'is-live';return `<span class="loom-admin-badge ${cls}">${escapeHtml(s)}</span>`}
  function renderRuns(filter=''){
    const q=filter.trim().toLowerCase(),runs=state.runs.filter(r=>!q||`${r.email} ${r.model||''} ${r.provider||''} ${r.status}`.toLowerCase().includes(q));
    $('runsHint').textContent=`${runs.length} runs`;
    const now=Math.floor(Date.now()/1000);
    $('runsBody').innerHTML=runs.length?runs.map(r=>{const end=Number(r.completed_at||now);const duration=Math.max(0,end-Number(r.started_at||end));const model=[r.provider,r.model].filter(Boolean).join(' · ')||'—';return `<tr><td>${escapeHtml(r.email)}</td><td><strong>${escapeHtml(model)}</strong><small class="loom-admin-subline" title="${escapeHtml(r.turn_id)}">${escapeHtml(shortId(r.turn_id,7,4))}</small></td><td>${runStatusBadge(r.status)}</td><td>${fmtTime(r.started_at)}</td><td>${fmtDuration(duration)}</td><td>${fmtNumber(r.tool_count)}</td><td>${fmtNumber(r.approval_count)}</td><td>${fmtCompact(r.total_tokens)}</td><td>${activeRun(r)?`<button class="loom-admin-action is-danger" data-interrupt-run="${r.id}">Interrupt</button>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="9" class="loom-admin-empty">暂无 Agent Run telemetry。</td></tr>';
  }
  function aggregateRuns(range){
    const cfg=rangeConfig(range),cutoff=Math.floor(Date.now()/1000)-cfg.seconds,models=new Map(),users=new Map();
    state.runs.filter(r=>Number(r.completed_at||0)>=cutoff).forEach(r=>{const mk=`${r.provider||'local'}\u0000${r.model||'unknown'}`;const m=models.get(mk)||{provider:r.provider||'local',model:r.model||'unknown',runs:0,total_tokens:0};m.runs++;m.total_tokens+=Number(r.total_tokens||0);models.set(mk,m);const u=users.get(r.email)||{email:r.email,runs:0,total_tokens:0};u.runs++;u.total_tokens+=Number(r.total_tokens||0);users.set(r.email,u)});
    return{models:[...models.values()].sort((a,b)=>b.total_tokens-a.total_tokens),users:[...users.values()].sort((a,b)=>b.total_tokens-a.total_tokens)};
  }
  function renderUsage(){
    const summary=state.usage?.ranges?.[state.usageRange]||{};$('usageTokens').textContent=fmtCompact(summary.total_tokens);$('usageRuns').textContent=fmtNumber(summary.runs);$('usageInput').textContent=fmtCompact(summary.input_tokens);$('usageOutput').textContent=fmtCompact(summary.output_tokens);$('usageDuration').textContent=fmtDuration(summary.avg_duration);
    const agg=aggregateRuns(state.usageRange),maxModel=Math.max(1,...agg.models.map(x=>x.total_tokens));
    $('modelsList').innerHTML=agg.models.length?agg.models.slice(0,12).map(m=>`<div class="loom-admin-metric-row"><div><strong>${escapeHtml(m.model)}</strong><small>${escapeHtml(m.provider)} · ${m.runs} runs</small></div><div class="loom-admin-metric-value"><span>${fmtCompact(m.total_tokens)}</span><i style="--metric:${Math.max(4,(m.total_tokens/maxModel)*100)}%"></i></div></div>`).join(''):'<p class="loom-admin-empty-block">当前范围暂无模型用量。</p>';
    const maxUser=Math.max(1,...agg.users.map(x=>x.total_tokens));$('usageUsersList').innerHTML=agg.users.length?agg.users.slice(0,12).map(u=>`<div class="loom-admin-metric-row"><div><strong>${escapeHtml(u.email)}</strong><small>${u.runs} runs</small></div><div class="loom-admin-metric-value"><span>${fmtCompact(u.total_tokens)}</span><i style="--metric:${Math.max(4,(u.total_tokens/maxUser)*100)}%"></i></div></div>`).join(''):'<p class="loom-admin-empty-block">当前范围暂无用户用量。</p>';
    const daily=Array.isArray(state.usage?.daily)?state.usage.daily:[],max=Math.max(1,...daily.map(x=>Number(x.total_tokens||0)));$('usageDailyChart').innerHTML=daily.length?daily.map(d=>`<div title="${escapeHtml(d.day)} · ${fmtNumber(d.total_tokens)} tokens"><i style="height:${Math.max(4,(Number(d.total_tokens||0)/max)*100)}%"></i><span>${escapeHtml(String(d.day||'').slice(5))}</span></div>`).join(''):'<p class="loom-admin-empty-block">暂无趋势数据。</p>';
  }
  function renderTools(){
    const t=state.tools||{};$('toolCallsTotal').textContent=fmtNumber(t.tool_calls);$('approvalsTotal').textContent=fmtNumber(t.approvals);$('approvalsWaiting').textContent=fmtNumber(t.waiting);
    const tools=Array.isArray(t.tools)?t.tools:[],max=Math.max(1,...tools.map(x=>Number(x.calls||0)));$('toolsList').innerHTML=tools.length?tools.map(x=>`<div class="loom-admin-tool-row"><div><strong>${escapeHtml(x.tool_name)}</strong><small>Last · ${fmtTime(x.last_used_at)}</small></div><span>${fmtNumber(x.calls)}</span><i style="--metric:${Math.max(3,(Number(x.calls||0)/max)*100)}%"></i></div>`).join(''):'<p class="loom-admin-empty-block">还没有工具调用 telemetry。</p>';
  }
  function renderSessions(){
    const now=Math.floor(Date.now()/1000);$('sessionsHint').textContent=`${state.sessions.length} sessions`;$('sessionsBody').innerHTML=state.sessions.length?state.sessions.map(s=>{const live=!s.revoked_at&&Number(s.refresh_expires_at)>now;return `<tr><td>${escapeHtml(s.email)}</td><td><code title="${escapeHtml(s.id)}">${escapeHtml(shortId(s.id))}</code></td><td>${fmtTime(s.created_at)}</td><td>${fmtTime(s.last_used_at)}</td><td>${fmtTime(s.refresh_expires_at)}</td><td><span class="loom-admin-badge ${live?'is-ok':'is-bad'}">${live?'active':(s.revoked_at?'revoked':'expired')}</span></td><td>${live?`<button class="loom-admin-action is-danger" data-revoke-session="${escapeHtml(s.id)}">Revoke</button>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="7" class="loom-admin-empty">暂无会话</td></tr>';
  }
  function renderUserWorkspace(){
    const u=state.selectedUser;if(!u)return;const uid=Number(u.id),now=Math.floor(Date.now()/1000);
    const devices=state.devices.filter(d=>Number(d.user_id)===uid||d.email===u.email);
    const runs=state.runs.filter(r=>Number(r.user_id)===uid||r.email===u.email);
    const sessions=state.sessions.filter(x=>Number(x.user_id)===uid||x.email===u.email);
    $('userKpiDevices').textContent=fmtNumber(devices.length);$('userKpiRuns').textContent=fmtNumber(runs.filter(activeRun).length);$('userKpiTokens').textContent=fmtCompact(runs.reduce((n,r)=>n+Number(r.total_tokens||0),0));$('userKpiTools').textContent=fmtNumber(runs.reduce((n,r)=>n+Number(r.tool_count||0),0));$('userKpiSessions').textContent=fmtNumber(sessions.filter(x=>!x.revoked_at&&Number(x.refresh_expires_at)>now).length);
    $('userDevicesBody').innerHTML=devices.length?devices.map(d=>`<tr><td><strong>${escapeHtml(d.name||'Loom Host')}</strong><small class="loom-admin-subline">${escapeHtml(shortId(d.device_id,7,4))}</small></td><td>${escapeHtml(d.platform||'—')}</td><td>${escapeHtml(d.app_version||'—')}</td><td>${escapeHtml(d.host_version||'—')}</td><td>${fmtTime(d.last_seen_at)}</td><td><span class="loom-admin-badge ${d.online?'is-ok':'is-bad'}">${d.online?'online':'offline'}</span></td></tr>`).join(''):'<tr><td colspan="6" class="loom-admin-empty">这个账号还没有 Host telemetry。</td></tr>';
    $('userRunsBody').innerHTML=runs.length?runs.map(r=>{const end=Number(r.completed_at||now),duration=Math.max(0,end-Number(r.started_at||end)),model=[r.provider,r.model].filter(Boolean).join(' · ')||'—';return `<tr><td><strong>${escapeHtml(model)}</strong><small class="loom-admin-subline">${escapeHtml(shortId(r.turn_id,7,4))}</small></td><td>${runStatusBadge(r.status)}</td><td>${fmtTime(r.started_at)}</td><td>${fmtDuration(duration)}</td><td>${fmtNumber(r.tool_count)}</td><td>${fmtCompact(r.total_tokens)}</td><td>${activeRun(r)?`<button class="loom-admin-action is-danger" data-interrupt-run="${r.id}">Interrupt</button>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="7" class="loom-admin-empty">这个账号暂无 Agent Run。</td></tr>';
    $('userSessionsBody').innerHTML=sessions.length?sessions.map(x=>{const live=!x.revoked_at&&Number(x.refresh_expires_at)>now;return `<tr><td><code>${escapeHtml(shortId(x.id))}</code></td><td>${fmtTime(x.created_at)}</td><td>${fmtTime(x.last_used_at)}</td><td>${fmtTime(x.refresh_expires_at)}</td><td><span class="loom-admin-badge ${live?'is-ok':'is-bad'}">${live?'active':(x.revoked_at?'revoked':'expired')}</span></td><td>${live?`<button class="loom-admin-action is-danger" data-revoke-session="${escapeHtml(x.id)}">Revoke</button>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="6" class="loom-admin-empty">这个账号暂无 Session。</td></tr>';
  }

  function renderFlags(){
    $('flagsHint').textContent=`${state.flags.length} flags`;$('flagsList').innerHTML=state.flags.length?state.flags.map(f=>`<div class="loom-admin-flag-row"><div><strong>${escapeHtml(f.key)}</strong><small>${escapeHtml(JSON.stringify(f.value??{}))} · ${fmtTime(f.updated_at)}</small></div><label class="loom-admin-switch"><input type="checkbox" data-flag-toggle="${escapeHtml(f.key)}" ${f.enabled?'checked':''}><span></span></label></div>`).join(''):'<p class="loom-admin-empty-block">还没有 Feature Flag。</p>';
  }
  function renderAudit(filter=''){
    const q=filter.trim().toLowerCase(),events=state.audit.filter(e=>!q||`${e.action} ${e.actor_email||''} ${e.target_type||''} ${e.target_id||''}`.toLowerCase().includes(q));$('auditHint').textContent=`${events.length} events`;$('auditList').innerHTML=events.length?events.map(e=>`<article class="account-card cards loom-admin-audit-row"><div><span class="loom-admin-badge">${escapeHtml(e.action)}</span><strong>${escapeHtml(e.actor_email||'system')}</strong><small>${escapeHtml(e.target_type||'')}${e.target_id?` · ${escapeHtml(shortId(e.target_id,10,5))}`:''}</small></div><div class="loom-admin-audit-meta"><code>${escapeHtml(JSON.stringify(e.metadata||{}))}</code><time>${fmtTime(e.created_at)}</time></div></article>`).join(''):'<p class="loom-admin-empty-block">没有匹配的审计记录。</p>';
  }
  function healthRow(name,ok,good='Healthy',bad='Unavailable'){return `<div class="loom-admin-health-item"><span>${escapeHtml(name)}</span><b class="${ok?'is-ok':'is-bad'}">${escapeHtml(ok?good:bad)}</b></div>`}
  function renderSystem(){
    const s=state.system||{},smtp=s.smtp?.status,a=state.agent||{};$('systemHealth').innerHTML=[healthRow('Admin UI',state.health.admin),healthRow('Account API',state.health.account),healthRow('Database',s.database?.status==='healthy','Healthy','Unavailable'),healthRow('Loom Web Gateway',state.health.loom),healthRow('Model Gateway',s.model_gateway?.status==='healthy','Healthy','Unavailable'),healthRow('SMTP',smtp==='configured','Configured',smtp==='not_configured'?'Not configured':'Unavailable')].join('');
    $('telemetryHealth').innerHTML=[healthRow('Agent telemetry',s.telemetry?.status==='configured','Configured','Not configured'),healthRow('Shared search',s.search?.status==='configured','Configured','Not configured'),healthRow('Online Hosts',Number(a.online_devices||0)>0,`${a.online_devices||0} online`,'No Host online'),healthRow('Active runs',true,`${a.active_runs||0} active`,'—'),healthRow('Pending commands',Number(a.pending_commands||0)===0,'Clear',`${a.pending_commands} pending`)].join('');
    $('releaseInfo').innerHTML=`<strong>${escapeHtml(s.release||'unknown')}</strong><small>Account API · ${fmtTime(s.generated_at)}</small><small>Known Hosts · ${fmtNumber(a.known_devices||0)}</small>`;
  }

  async function loadHealth(){const [admin,account,loom]=await Promise.all([probe('/healthz'),probe('/api/healthz'),probe('/ops/loom-healthz')]);state.health={admin,account,loom};}
  async function loadAll(){
    setHeader(true,'Loading');
    const [overview,agent,users,sessions,system,devices,runs,usage,tools,models,audit,flags]=await Promise.all([
      request('/admin/overview'),request('/admin/agent-overview'),request('/admin/users'),request('/admin/sessions'),request('/admin/system'),request('/admin/devices'),request('/admin/runs'),request('/admin/usage'),request('/admin/tools'),request('/admin/models'),request('/admin/audit'),request('/admin/feature-flags'),loadHealth()
    ]);
    state.overview=overview;state.agent=agent;state.users=users.users||[];state.sessions=sessions.sessions||[];state.system=system;state.devices=devices.devices||[];state.runs=runs.runs||[];state.usage=usage;state.tools=tools;state.models=models;state.audit=audit.events||[];state.flags=flags.flags||[];
    renderOverview();renderUsers($('userSearch').value);renderDevices();renderRuns($('runSearch').value);renderUsage();renderTools();renderSessions();renderFlags();renderAudit($('auditSearch').value);renderSystem();if(state.selectedUser)renderUserWorkspace();
    const ok=state.health.admin&&state.health.account;setHeader(ok,ok?'Operational':'Degraded');setAuthenticated(true);startAutoRefresh();applyRoute();
  }
  async function loadAgentOps(){
    if(!state.me)return;
    try{const [agent,devices,runs,usage,tools,models]=await Promise.all([request('/admin/agent-overview'),request('/admin/devices'),request('/admin/runs'),request('/admin/usage'),request('/admin/tools'),request('/admin/models')]);state.agent=agent;state.devices=devices.devices||[];state.runs=runs.runs||[];state.usage=usage;state.tools=tools;state.models=models;renderOverview();renderDevices();renderRuns($('runSearch').value);renderUsage();renderTools();renderSystem();if(state.selectedUser)renderUserWorkspace();}catch(err){if(err?.status===401||err?.status===403)showLoadError(err);}
  }
  function startAutoRefresh(){if(state.refreshTimer)return;state.refreshTimer=setInterval(()=>void loadAgentOps(),10000);}
  async function mutate(path,body={}){await request(path,{method:'POST',body:JSON.stringify(body)});await loadAll();}
  function modelCatalog(){const seen=new Set(),out=[];const add=x=>{const v=String(x||'').trim();if(v&&!seen.has(v.toLowerCase())){seen.add(v.toLowerCase());out.push(v)}};(state.models?.managed_models||[]).forEach(add);(state.models?.observed||[]).forEach(x=>add(x.model));(state.selectedModelAccess?.models||[]).forEach(add);return out;}
  async function openUserDetail(id,{navigate=true}={}){
    const [userPayload,accessPayload]=await Promise.all([request(`/admin/users/${id}`),request(`/admin/users/${id}/model-access`)]);const u=userPayload.user,access=accessPayload.access||{enabled:true,models:[]};state.selectedUser=u;state.selectedModelAccess=access;$('detailTitle').textContent=u.email;const enabled=new Set((access.models||[]).map(String));const rows=modelCatalog().map(model=>`<label class="loom-admin-model-toggle"><input type="checkbox" data-model-access-id="${escapeHtml(model)}" ${enabled.has(model)?'checked':''}><span>${escapeHtml(model)}</span></label>`).join('');
    const verified=Boolean(u.email_verified??u.verified);
    $('userDetailBody').innerHTML=`<div><span>Role</span><strong>${escapeHtml(u.role)}</strong></div><div><span>Status</span><strong>${escapeHtml(u.status)}</strong></div><div><span>Verified</span><strong>${verified?'Verified':'Unverified'}</strong></div><div><span>Active sessions</span><strong>${u.active_sessions}</strong></div><div><span>Created</span><strong>${fmtTime(u.created_at)}</strong></div><div><span>Last active</span><strong>${fmtTime(u.last_seen_at)}</strong></div><section class="loom-admin-model-access"><div class="loom-admin-model-access-head"><span>Built-in model access</span><label><input id="modelAccessEnabled" type="checkbox" ${access.enabled!==false?'checked':''}> Enabled</label></div><small>${access.source==='override'?'Per-user override':'Using Loom default policy'}</small><div class="loom-admin-model-grid">${rows||'<p class="loom-admin-empty-block">No managed model catalog.</p>'}</div><button id="saveModelAccessButton" class="loom-admin-action" type="button">Save model access</button></section>`;
    $('detailStatusButton').textContent=u.status==='active'?'Disable':'Enable';$('detailStatusButton').disabled=Number(u.id)===Number(state.me.id)&&u.status==='active';renderUserWorkspace();
    if(navigate)location.hash=`user/${u.id}`;else showPage('user');
  }
  async function saveFlag(key,enabled,value){const result=await request('/admin/feature-flags',{method:'POST',body:JSON.stringify({key,enabled,value})});const next=result.flag;const i=state.flags.findIndex(f=>f.key===next.key);if(i>=0)state.flags[i]=next;else state.flags.push(next);state.flags.sort((a,b)=>a.key.localeCompare(b.key));renderFlags();renderOverviewControls();}

  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';const b=e.currentTarget.querySelector('button');b.disabled=true;try{await login($('loginEmail').value.trim(),$('loginPassword').value);$('loginPassword').value='';await loadAll()}catch(err){$('loginError').textContent=err.code==='ADMIN_REQUIRED'?'这个 Loom 账号没有管理员权限。':(err.message||'登录失败');setHeader(false,'Access denied')}finally{b.disabled=false}});
  $('logoutButton').addEventListener('click',async()=>{const refresh=state.refresh;if(refresh){try{await fetch(API+'/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),cache:'no-store'})}catch(_){}}clearTokens();setAuthenticated(false);setHeader(false,'Signed out')});
  $('refreshButton').addEventListener('click',()=>loadAll().catch(showLoadError));$('userSearch').addEventListener('input',e=>renderUsers(e.target.value));$('runSearch').addEventListener('input',e=>renderRuns(e.target.value));$('auditSearch').addEventListener('input',e=>renderAudit(e.target.value));
  $('activityRangeControl').addEventListener('click',e=>{const b=e.target.closest('[data-range]');if(!b)return;state.activityRange=b.dataset.range;document.querySelectorAll('#activityRangeControl [data-range]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderActivity()});
  $('usageRangeControl').addEventListener('click',e=>{const b=e.target.closest('[data-range]');if(!b)return;state.usageRange=b.dataset.range;document.querySelectorAll('#usageRangeControl [data-range]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderUsage()});
  $('usersBody').addEventListener('click',async e=>{const detail=e.target.closest('[data-user-detail]'),status=e.target.closest('[data-status-user]'),revoke=e.target.closest('[data-revoke-user]');try{if(detail)return await openUserDetail(Number(detail.dataset.userDetail));if(status){const id=Number(status.dataset.statusUser),next=status.dataset.nextStatus;if(next==='disabled'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;return await mutate(`/admin/users/${id}/${next==='disabled'?'disable':'enable'}`)}if(revoke){if(!confirm('确认撤销这个用户的全部有效会话？'))return;return await mutate('/admin/users/revoke-sessions',{user_id:Number(revoke.dataset.revokeUser)})}}catch(err){alert(err.message||'操作失败')}});
  $('usersBody').addEventListener('change',async e=>{const select=e.target.closest('[data-role-user]');if(!select)return;try{await mutate('/admin/users/role',{user_id:Number(select.dataset.roleUser),role:select.value})}catch(err){alert(err.message||'修改角色失败');await loadAll()}});
  async function handleInterrupt(button){if(!button||!confirm('确认中断这个正在运行的 Agent Turn？该操作会写入审计日志。'))return;button.disabled=true;button.textContent='Queuing…';try{await request('/admin/runs/interrupt',{method:'POST',body:JSON.stringify({run_id:Number(button.dataset.interruptRun)})});button.textContent='Queued';setTimeout(()=>void loadAgentOps(),3500)}catch(err){button.disabled=false;button.textContent='Interrupt';alert(err.message||'中断失败')}}
  async function handleSessionRevoke(revoke){if(!revoke||!confirm('确认撤销这个 Session？'))return;try{await mutate(`/admin/sessions/${encodeURIComponent(revoke.dataset.revokeSession)}/revoke`);if(state.selectedUser)await openUserDetail(state.selectedUser.id,{navigate:false})}catch(err){alert(err.message||'撤销失败')}}
  $('runsBody').addEventListener('click',e=>void handleInterrupt(e.target.closest('[data-interrupt-run]')));
  $('userRunsBody').addEventListener('click',e=>void handleInterrupt(e.target.closest('[data-interrupt-run]')));
  $('sessionsBody').addEventListener('click',e=>void handleSessionRevoke(e.target.closest('[data-revoke-session]')));
  $('userSessionsBody').addEventListener('click',e=>void handleSessionRevoke(e.target.closest('[data-revoke-session]')));
  $('flagForm').addEventListener('submit',async e=>{e.preventDefault();$('flagError').textContent='';const key=$('flagKey').value.trim();let value;try{value=JSON.parse($('flagValue').value||'{}')}catch(_){$('flagError').textContent='JSON value 格式不正确。';return}const b=e.currentTarget.querySelector('button');b.disabled=true;try{await saveFlag(key,$('flagEnabled').checked,value);$('flagKey').value='';$('flagValue').value='{}'}catch(err){$('flagError').textContent=err.message||'保存失败'}finally{b.disabled=false}});
  $('flagsList').addEventListener('change',async e=>{const input=e.target.closest('[data-flag-toggle]');if(!input)return;const flag=state.flags.find(f=>f.key===input.dataset.flagToggle);if(!flag)return;input.disabled=true;try{await saveFlag(flag.key,input.checked,flag.value)}catch(err){input.checked=!input.checked;alert(err.message||'修改失败')}finally{input.disabled=false}});
  $('overviewFlags').addEventListener('change',async e=>{const input=e.target.closest('[data-overview-flag-toggle]');if(!input)return;const flag=state.flags.find(f=>f.key===input.dataset.overviewFlagToggle);if(!flag)return;input.disabled=true;try{await saveFlag(flag.key,input.checked,flag.value)}catch(err){input.checked=!input.checked;alert(err.message||'修改失败')}finally{input.disabled=false}});
  $('user').addEventListener('click',async e=>{const save=e.target.closest('#saveModelAccessButton');if(!save)return;const u=state.selectedUser;if(!u)return;const models=[...$('userDetailBody').querySelectorAll('[data-model-access-id]:checked')].map(el=>el.dataset.modelAccessId),enabled=Boolean($('modelAccessEnabled')?.checked);save.disabled=true;try{const result=await request('/admin/users/model-access',{method:'POST',body:JSON.stringify({user_id:Number(u.id),enabled,models})});state.selectedModelAccess=result.access;save.textContent='Saved';setTimeout(()=>{save.textContent='Save model access'},900)}catch(err){alert(err.message||'修改模型权限失败')}finally{save.disabled=false}});
  $('closeUserDialog').addEventListener('click',()=>{location.hash='users'});
  $('detailStatusButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u)return;const next=u.status==='active'?'disable':'enable';if(next==='disable'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;try{await mutate(`/admin/users/${u.id}/${next}`);await openUserDetail(u.id,{navigate:false})}catch(err){alert(err.message||'操作失败')}});
  $('detailRevokeButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u||!confirm('确认撤销这个用户的全部有效会话？'))return;try{await mutate('/admin/users/revoke-sessions',{user_id:Number(u.id)});await openUserDetail(u.id,{navigate:false})}catch(err){alert(err.message||'操作失败')}});

  const ADMIN_PAGES=new Set(['overview','users','devices','runs','usage','tools','sessions','flags','audit','system']);
  function showPage(page){
    state.page=page;document.querySelectorAll('[data-admin-page]').forEach(el=>{el.hidden=el.dataset.adminPage!==page});
    const current=page==='user'?'users':page;document.querySelectorAll('#sectionNav a').forEach(a=>a.setAttribute('aria-current',a.getAttribute('href')===`#${current}`?'page':'false'));
    requestAnimationFrame(()=>window.scrollTo({top:0,behavior:'auto'}));
  }
  function routeState(){const raw=decodeURIComponent(location.hash.replace(/^#/,'')||'overview');if(raw.startsWith('user/')){const id=Number(raw.split('/')[1]||0);return id>0?{page:'user',userId:id}:{page:'users'}}return{page:ADMIN_PAGES.has(raw)?raw:'overview'}}
  function applyRoute(){const route=routeState();if(route.page==='user'){showPage('user');if(state.me&&(!state.selectedUser||Number(state.selectedUser.id)!==route.userId))void openUserDetail(route.userId,{navigate:false}).catch(showLoadError);else if(state.selectedUser)renderUserWorkspace();return}showPage(route.page)}
  function setupNav(){window.addEventListener('hashchange',applyRoute);document.addEventListener('click',e=>{const target=e.target.closest('[data-admin-route]');if(!target)return;e.preventDefault();location.hash=target.dataset.adminRoute});applyRoute()}
  function showLoadError(err){if(err?.status===401||err?.status===403){clearTokens();setAuthenticated(false);$('loginError').textContent=err.status===403?'这个账号没有管理员权限。':'登录已过期，请重新登录。';setHeader(false,'Access required');return}setHeader(false,'Service error');console.error(err);}
  setupNav();(async()=>{try{if(state.access||state.refresh){state.me=(await request('/admin/me')).user;await loadAll()}else{setAuthenticated(false);setHeader(false,'Admin sign in')}}catch(err){showLoadError(err)}})();
})();
