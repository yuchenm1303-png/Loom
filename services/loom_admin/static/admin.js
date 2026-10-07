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
    activityRange: '24h', usageRange: '24h', runStatusFilter: 'all', userPresenceFilter: 'all', userSort: 'recent', selectedUser: null, selectedModelAccess: null, selectedToolAccess: null,
    page: 'overview', selectedUserOps: null, loaded: new Set(), refreshTimer: null,
  };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtTime = (value) => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
  const shortId = (value, a=8, b=5) => value ? `${String(value).slice(0,a)}…${String(value).slice(-b)}` : '—';
  const fmtNumber = (value) => new Intl.NumberFormat('en-US').format(Number(value || 0));
  const fmtCompact = (value) => new Intl.NumberFormat('en-US', {notation:'compact', maximumFractionDigits:1}).format(Number(value || 0));
  const fmtBytes = (value) => { const n=Number(value||0); if(!(n>0))return '—'; const units=['B','KB','MB','GB','TB']; let v=n,i=0; while(v>=1024&&i<units.length-1){v/=1024;i++;} return `${v>=10||i===0?v.toFixed(0):v.toFixed(1)} ${units[i]}`; };
  const fmtDuration = (seconds) => {
    const s=Math.max(0,Math.round(Number(seconds||0))); if(s<60)return `${s}s`; const m=Math.floor(s/60); if(m<60)return `${m}m ${s%60}s`; const h=Math.floor(m/60); return `${h}h ${m%60}m`;
  };
  const activeRun = (run) => ['running','waiting_approval'].includes(String(run?.status||''));

  function saveTokens(payload) {
    if (payload.access_token) { state.access=payload.access_token; sessionStorage.setItem('loom_admin_access', state.access); }
    if (payload.refresh_token) { state.refresh=payload.refresh_token; sessionStorage.setItem('loom_admin_refresh', state.refresh); }
  }
  function clearTokens() {
    state.access=''; state.refresh=''; state.me=null;state.selectedUser=null;state.selectedUserOps=null;state.selectedModelAccess=null;state.selectedToolAccess=null;
    state.loaded.clear();state.users=[];state.sessions=[];state.devices=[];state.runs=[];state.audit=[];state.flags=[];state.usage={};state.tools={};state.models={};
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
    const users=Number(o.users||0), online=Number(a.online_devices||0), known=Number(a.known_devices||0);
    const active=Number(a.active_runs||0), waiting=Number(a.waiting_approvals||0), runs24=Number(a.runs_24h||0), failed24=Number(a.failed_runs_24h||0);
    $('kpiUsers').textContent=fmtNumber(users); $('kpiOnlineDevices').textContent=fmtNumber(online);
    $('kpiActiveRuns').textContent=fmtNumber(active); $('kpiApprovals').textContent=fmtNumber(waiting);
    $('kpiTokens24').textContent=fmtCompact(a.tokens_24h); $('kpiToolCalls24').textContent=fmtNumber(a.tool_calls_24h);
    $('kpiSessions').textContent=fmtNumber(o.active_sessions); $('kpiNew').textContent=fmtNumber(o.registrations_24h);
    $('generatedAt').textContent=fmtTime(a.generated_at||o.generated_at); $('adminIdentity').textContent=`${state.me.email} · ${state.me.role}`;
    $('serviceState').textContent=state.health.account?'Healthy':'Unavailable'; $('serviceState').dataset.state=state.health.account?'ok':'bad';
    $('loomWebState').textContent=state.health.loom?'Healthy':'Unavailable'; $('loomWebState').dataset.state=state.health.loom?'ok':'bad';
    $('overviewRuns24').textContent=fmtNumber(runs24); $('overviewFailed24').textContent=fmtNumber(failed24); $('overviewKnownDevices').textContent=fmtNumber(known);
    const healthy=Boolean(state.health.account&&state.health.loom);
    $('overviewHealthTitle').textContent=healthy?'Control plane operational':'Control plane needs attention';
    $('overviewHealthSubtitle').textContent=healthy?'Account API 与 Loom Web 均可用':'至少有一个核心服务当前不可用';
    $('overviewHealthMark').dataset.state=healthy?'ok':'bad';
    const devicePct=known?Math.round((online/known)*100):0;
    $('overviewDeviceCoverage').textContent=known?`${online} / ${known} online`:'No hosts yet';
    $('overviewDeviceFill').style.width=`${Math.max(0,Math.min(100,devicePct))}%`;
    const successPct=runs24?Math.max(0,Math.round(((runs24-failed24)/runs24)*100)):100;
    $('overviewSuccessRate').textContent=runs24?`${successPct}%`:'No runs yet';
    $('overviewSuccessFill').style.width=`${runs24?successPct:0}%`;
    $('overviewSuccessFill').dataset.state=failed24?'warn':'ok';
    $('overviewFailureNote').textContent=runs24?`${failed24} failed of ${runs24}`:'Waiting for telemetry';
    $('overviewActiveInline').textContent=fmtNumber(active); $('overviewApprovalInline').textContent=fmtNumber(waiting);
    $('overviewWorkload').textContent=waiting?'Needs attention':active?'Agent active':'Idle';
    renderOverviewControls();
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

  function fmtRelative(value){
    if(!value)return '从未活跃';const delta=Math.max(0,Math.floor(Date.now()/1000)-Number(value));
    if(delta<60)return `${delta}s 前`;if(delta<3600)return `${Math.floor(delta/60)}m 前`;if(delta<86400)return `${Math.floor(delta/3600)}h 前`;return `${Math.floor(delta/86400)}d 前`;
  }
  function userPresence(u){
    if(u.status==='banned')return{key:'attention',label:'账号已封禁',cls:'is-bad'};
    if(u.status!=='active')return{key:'attention',label:'账号已停用',cls:'is-bad'};
    if(Number(u.waiting_approvals||0)>0)return{key:'attention',label:'等待审批',cls:'is-warn'};
    if(Number(u.active_runs||0)>0)return{key:'running',label:'Agent 运行中',cls:'is-live'};
    if(Number(u.online_devices||0)>0)return{key:'online',label:'Host 在线',cls:'is-ok'};
    if(u.account_online)return{key:'online',label:'近期活跃',cls:'is-ok'};
    return{key:'offline',label:'离线',cls:''};
  }
  function userMatchesPresence(u,filter){
    if(filter==='all')return true;if(filter==='online')return Number(u.online_devices||0)>0;
    if(filter==='running')return Number(u.active_runs||0)>0;
    if(filter==='attention')return u.status!=='active'||Number(u.waiting_approvals||0)>0||Number(u.failed_runs_24h||0)>0;
    if(filter==='offline')return Number(u.online_devices||0)===0;return true;
  }
  function userActivityBars(u){
    const activity=Array.isArray(u.activity_24h)?u.activity_24h:[],max=Math.max(1,...activity.map(x=>Number(x.runs||0)));
    return activity.map((x,i)=>{const runs=Number(x.runs||0),tokens=Number(x.tokens||0),height=runs?Math.max(14,(runs/max)*100):7;return `<i class="${runs?'is-active':''}" style="--bar:${height}%" title="${i}:00 · ${runs} runs · ${fmtCompact(tokens)} tokens"><span></span></i>`}).join('');
  }
  function userToolMix(u){
    const tools=Array.isArray(u.top_tools_24h)?u.top_tools_24h:[];if(!tools.length)return '<span class="loom-admin-user-tool-empty">24H 暂无工具调用</span>';
    const total=Math.max(1,tools.reduce((n,x)=>n+Number(x.calls||0),0));
    return `<div class="loom-admin-user-tool-bar">${tools.map((x,i)=>`<i class="tool-${i+1}" style="--share:${(Number(x.calls||0)/total)*100}%" title="${escapeHtml(x.tool_name)} · ${fmtNumber(x.calls)}"><span></span></i>`).join('')}</div><div class="loom-admin-user-tool-legend">${tools.map((x,i)=>`<span><i class="tool-${i+1}"></i>${escapeHtml(x.tool_name)} <b>${fmtNumber(x.calls)}</b></span>`).join('')}</div>`;
  }
  function healthPercent(value){if(value==null||value==='')return null;const n=Number(value);return Number.isFinite(n)?Math.max(0,Math.min(100,n)):null;}
  function healthClass(value,warn=75,bad=90){const n=healthPercent(value);return n==null?'':n>=bad?'is-bad':n>=warn?'is-warn':'is-ok';}
  function resourceGauge(label,value,meta=''){
    const n=healthPercent(value),cls=healthClass(n);return `<div class="loom-admin-health-metric ${cls}"><div><span>${escapeHtml(label)}</span><strong>${n==null?'—':Math.round(n)+'%'}</strong></div><i><b style="width:${n==null?0:n}%"></b></i>${meta?`<small>${escapeHtml(meta)}</small>`:''}</div>`;
  }
  function latencyGauge(value){const n=Number(value),valid=value!=null&&value!==''&&Number.isFinite(n)&&n>=0,cls=!valid?'':n>=250?'is-bad':n>=100?'is-warn':'is-ok',width=valid?Math.min(100,Math.max(4,n/5)):0;return `<div class="loom-admin-health-metric ${cls}"><div><span>RELAY</span><strong>${valid?Math.round(n)+'ms':'—'}</strong></div><i><b style="width:${width}%"></b></i><small>WebSocket RTT</small></div>`;}
  function capabilityChips(health){const c=health?.capabilities||{};return [['Browser',c.browser],['Computer',c.computer_use],['Terminal',c.terminal],['Files',c.files]].map(([label,on])=>`<span class="loom-admin-capability ${on?'is-on':''}"><i></i>${label}</span>`).join('');}
  function hostHealthMini(device){
    const h=device?.health;if(!device)return '';if(!h)return `<div class="loom-admin-host-health-empty"><b>HOST HEALTH V2</b><span>等待客户端 Host 升级后开始采集 CPU、内存、磁盘与 Relay 延迟。</span></div>`;
    const diskMeta=h.disk_total_bytes?`${fmtBytes(Number(h.disk_total_bytes)-Number(h.disk_free_bytes||0))} / ${fmtBytes(h.disk_total_bytes)}`:'';
    const sys=[device.platform,h.os_version||h.os_release,h.arch].filter(Boolean).join(' · ');
    return `<div class="loom-admin-host-health-mini"><div class="loom-admin-host-health-head"><span>SYSTEM HEALTH</span><b>${escapeHtml(sys||'Host telemetry')}</b><small>系统已运行 ${fmtDuration(h.system_uptime_seconds||0)} · Host RSS ${fmtBytes(h.host_rss_bytes)} · 更新 ${fmtRelative(h.updated_at)}</small></div><div class="loom-admin-host-health-grid">${resourceGauge('CPU',h.cpu_percent,`Host ${h.host_cpu_percent==null?'—':Math.round(h.host_cpu_percent)+'%'}`)}${resourceGauge('RAM',h.memory_percent,`${fmtBytes(h.memory_used_bytes)} / ${fmtBytes(h.memory_total_bytes)}`)}${resourceGauge('DISK',h.disk_percent,diskMeta)}${latencyGauge(h.relay_rtt_ms)}</div><div class="loom-admin-capabilities">${capabilityChips(h)}</div></div>`;
  }
  function healthTrend(device){const raw=Array.isArray(device?.health_history_24h)?device.health_history_24h:[],by=new Map(raw.map(x=>[Number(x.bucket),x]));return new Array(24).fill(0).map((_,i)=>{const x=by.get(i)||{},cpu=healthPercent(x.cpu_percent),mem=healthPercent(x.memory_percent),ago=23-i;return `<i title="${ago?'-'+ago+'h':'now'} · CPU ${cpu==null?'—':Math.round(cpu)+'%'} · RAM ${mem==null?'—':Math.round(mem)+'%'}"><span class="is-cpu" style="--v:${cpu??0}%"></span><span class="is-mem" style="--v:${mem??0}%"></span></i>`}).join('');}
  function deviceHealthCard(d){
    const h=d.health||null,system=h?[d.platform,h.os_version||h.os_release,h.arch].filter(Boolean).join(' · '):d.platform||'Unknown system',online=d.online?'ONLINE':'OFFLINE';
    return `<article class="account-card cards loom-admin-device-card ${d.online?'is-online':''}"><header><div><span class="utility-overline">${escapeHtml(d.email)}</span><h3>${escapeHtml(d.name||'Loom Host')}</h3><small>${escapeHtml(system)}</small></div><span class="loom-admin-presence-pill ${d.online?'is-ok':''}"><i></i>${online}</span></header>${h?`<div class="loom-admin-device-health-grid">${resourceGauge('CPU',h.cpu_percent,`Host ${h.host_cpu_percent==null?'—':Math.round(h.host_cpu_percent)+'%'}`)}${resourceGauge('RAM',h.memory_percent,`${fmtBytes(h.memory_used_bytes)} / ${fmtBytes(h.memory_total_bytes)}`)}${resourceGauge('DISK',h.disk_percent,h.disk_free_bytes?`${fmtBytes(h.disk_free_bytes)} free`:'')}${latencyGauge(h.relay_rtt_ms)}</div><div class="loom-admin-device-trend-head"><span>24H RESOURCE TREND</span><small>CPU / RAM</small></div><div class="loom-admin-device-health-trend">${healthTrend(d)}</div><div class="loom-admin-capabilities">${capabilityChips(h)}</div>`:`<div class="loom-admin-host-health-empty"><b>WAITING FOR HOST HEALTH V2</b><span>设备身份与心跳已在线；资源指标会在客户端升级后出现。</span></div>`}<footer><span>System uptime <b>${h?fmtDuration(h.system_uptime_seconds):'—'}</b></span><span>Host online <b>${d.online?fmtDuration(Math.max(0,Math.floor(Date.now()/1000)-Number(d.connected_at||0))):'—'}</b></span><span>Loom ${escapeHtml(d.app_version||'—')} · Host ${escapeHtml(d.host_version||'—')}</span><span>Heartbeat <b>${fmtRelative(d.last_seen_at)}</b></span></footer></article>`;
  }

  function renderUsers(filter=''){
    const q=filter.trim().toLowerCase(),presenceFilter=state.userPresenceFilter||'all',sort=state.userSort||'recent';
    const users=state.users.filter(u=>{const d=u.primary_device||{};const hay=`${u.email} ${u.display_name||''} ${u.role} ${u.status} ${d.name||''} ${d.platform||''}`.toLowerCase();return(!q||hay.includes(q))&&userMatchesPresence(u,presenceFilter)});
    users.sort((a,b)=>{if(sort==='runs')return Number(b.runs_24h||0)-Number(a.runs_24h||0);if(sort==='tokens')return Number(b.tokens_24h||0)-Number(a.tokens_24h||0);if(sort==='created')return Number(b.created_at||0)-Number(a.created_at||0);const ar=Math.max(Number(a.last_seen_at||0),Number(a.primary_device?.last_seen_at||0)),br=Math.max(Number(b.last_seen_at||0),Number(b.primary_device?.last_seen_at||0));return br-ar});
    $('usersHint').textContent=`${users.length} / ${state.users.length} users`;
    $('usersGrid').innerHTML=users.length?users.map(u=>{
      const self=Number(u.id)===Number(state.me.id),presence=userPresence(u),d=u.primary_device||null,verified=Boolean(u.email_verified??u.verified),score=u.success_rate_24h==null?null:Number(u.success_rate_24h),activity=userActivityBars(u),privileged=['admin','owner'].includes(String(u.role||'')),canBan=!self&&(!privileged||state.me?.role==='owner');
      const initial=escapeHtml((u.display_name||u.email||'?').trim().charAt(0).toUpperCase());
      const deviceTitle=d?escapeHtml(d.name||'Loom Host'):'未连接 Loom Host',deviceMeta=d?[d.platform,d.host_version?`Host ${d.host_version}`:'',d.app_version?`App ${d.app_version}`:''].filter(Boolean).map(escapeHtml).join(' · '):'等待该账号的 Host 首次连接';
      const deviceState=d?.online?`已在线 ${fmtDuration(d.uptime_seconds||0)}`:d?`最后心跳 ${fmtRelative(d.last_seen_at)}`:'No telemetry';
      const banAction=u.status==='banned'?'<button class="loom-admin-action is-primary" data-unban-user="'+u.id+'" '+(canBan?'':'disabled')+'>Unban</button>':'<button class="loom-admin-action is-danger" data-ban-user="'+u.id+'" data-ban-email="'+escapeHtml(u.email)+'" '+(canBan?'':'disabled')+'>Ban</button>';
      return `<article class="account-card cards loom-admin-user-card ${presence.cls}" data-user-card="${u.id}">
        <header class="loom-admin-user-card-head">
          <div class="loom-admin-user-avatar">${initial}</div>
          <div class="loom-admin-user-identity"><div><strong>${escapeHtml(u.email)}</strong><span class="loom-admin-presence-pill ${presence.cls}"><i></i>${escapeHtml(presence.label)}</span></div><small>${u.display_name?escapeHtml(u.display_name)+' · ':''}${verified?'邮箱已验证':'邮箱未验证'} · 注册 ${fmtRelative(u.created_at)}</small></div>
          <div class="loom-admin-user-role">${state.me?.role==='owner'?`<select data-role-user="${u.id}" aria-label="Role"><option value="user" ${u.role==='user'?'selected':''}>user</option><option value="admin" ${u.role==='admin'?'selected':''}>admin</option><option value="owner" ${u.role==='owner'?'selected':''}>owner</option></select>`:`<span class="loom-admin-badge">${escapeHtml(u.role)}</span>`}</div>
        </header>
        <section class="loom-admin-user-device ${d?.online?'is-online':''}">
          <div class="loom-admin-device-mark"><i></i></div><div class="loom-admin-user-device-copy"><span>PRIMARY HOST</span><strong>${deviceTitle}</strong><small>${deviceMeta}</small></div>
          <div class="loom-admin-user-device-state"><b>${d?.online?'ONLINE':d?'OFFLINE':'NO HOST'}</b><small>${deviceState}${Number(u.known_devices||0)>1?` · ${fmtNumber(u.known_devices)} 台设备`:''}</small></div>
        </section>
        ${hostHealthMini(d)}
        <div class="loom-admin-user-metric-grid">
          <div><span>RUNS 24H</span><strong>${fmtNumber(u.runs_24h)}</strong><small>${Number(u.active_runs||0)?`${fmtNumber(u.active_runs)} active`:'当前空闲'}</small></div>
          <div><span>TOKENS 24H</span><strong>${fmtCompact(u.tokens_24h)}</strong><small>Agent usage</small></div>
          <div><span>TOOLS 24H</span><strong>${fmtNumber(u.tool_calls_24h)}</strong><small>${fmtNumber(u.approvals_24h)} approvals</small></div>
          <div><span>SESSIONS</span><strong>${fmtNumber(u.active_sessions)}</strong><small>${u.account_online?'近期在线':'最近活跃 '+fmtRelative(u.last_seen_at)}</small></div>
        </div>
        <div class="loom-admin-user-visual-grid">
          <div class="loom-admin-user-chart"><div class="loom-admin-user-chart-head"><span>24H ACTIVITY</span><b>${fmtNumber(u.runs_24h)} runs</b></div><div class="loom-admin-user-spark">${activity}</div><div class="loom-admin-user-chart-axis"><span>-24h</span><span>-12h</span><span>now</span></div></div>
          <div class="loom-admin-user-success"><div class="loom-admin-user-ring ${score==null?'is-empty':''}" style="--score:${score??0}"><strong>${score==null?'—':score+'%'}</strong><span>SUCCESS</span></div><div><b>${fmtNumber(u.failed_runs_24h)} failed</b><small>24 小时异常 Run</small><small>平均耗时 ${fmtDuration(u.avg_duration_24h)}</small></div></div>
        </div>
        <div class="loom-admin-user-tools"><div class="loom-admin-user-chart-head"><span>TOOL MIX · 24H</span><b>${fmtNumber(u.tool_calls_24h)} calls</b></div>${userToolMix(u)}</div>
        <footer class="loom-admin-user-card-foot"><div class="loom-admin-user-times"><span>最近账号活动 <b>${fmtRelative(u.last_seen_at)}</b></span><span>Host 心跳 <b>${d?fmtRelative(d.last_seen_at):'—'}</b></span></div><div class="loom-admin-actions"><button class="loom-admin-action is-primary" data-user-detail="${u.id}">打开账号</button><button class="loom-admin-action" data-status-user="${u.id}" data-next-status="${u.status==='active'?'disabled':'active'}" ${u.status==='banned'||(self&&u.status==='active')?'disabled':''}>${u.status==='banned'?'Banned':u.status==='active'?'Disable':'Enable'}</button>${banAction}<button class="loom-admin-action is-danger" data-revoke-user="${u.id}">Revoke</button></div></footer>
      </article>`;
    }).join(''):'<div class="account-card cards loom-admin-users-empty"><strong>没有匹配账号</strong><span>调整搜索词或状态筛选后再试。</span></div>';
  }
  function renderDevices(){
    $('devicesHint').textContent=`${state.devices.length} devices`;
    $('devicesGrid').innerHTML=state.devices.length?state.devices.map(deviceHealthCard).join(''):'<div class="account-card cards loom-admin-users-empty"><strong>还没有 Host telemetry</strong><span>设备连接 Loom Web 后会自动出现；Host Health v2 会随 30 秒心跳持续更新。</span></div>';
  }
  function runStatusBadge(status){const s=String(status||'unknown');const cls=s==='completed'?'is-ok':s==='waiting_approval'?'is-warn':['failed','interrupted','cancelled'].includes(s)?'is-bad':'is-live';return `<span class="loom-admin-badge ${cls}">${escapeHtml(s)}</span>`}
  function runMatchesStatus(run,filter){
    const status=String(run?.status||'');
    if(filter==='active')return ['running','waiting_approval'].includes(status);
    if(filter==='failed')return ['failed','interrupted','cancelled'].includes(status);
    if(filter==='waiting_approval')return status==='waiting_approval';
    if(filter==='completed')return status==='completed';
    return true;
  }
  function renderRunMonitor(){
    const waiting=Number(state.agent?.waiting_approvals||0);
    const running=Math.max(0,Number(state.agent?.active_runs||0)-waiting);
    $('runKpiRunning').textContent=fmtNumber(running);$('runKpiWaiting').textContent=fmtNumber(waiting);
    $('runKpiFailed').textContent=fmtNumber(state.agent?.failed_runs_24h||0);$('runKpiTokens').textContent=fmtCompact(state.agent?.tokens_24h||0);
  }
  function renderRuns(filter=''){
    const q=filter.trim().toLowerCase(),statusFilter=state.runStatusFilter||'all';
    const runs=state.runs.filter(r=>runMatchesStatus(r,statusFilter)&&(!q||`${r.email} ${r.model||''} ${r.provider||''} ${r.status}`.toLowerCase().includes(q)));
    $('runsHint').textContent=`${runs.length} / ${state.runs.length} runs`;renderRunMonitor();
    const now=Math.floor(Date.now()/1000);
    $('runsBody').innerHTML=runs.length?runs.map(r=>{const end=Number(r.completed_at||now);const duration=Math.max(0,end-Number(r.started_at||end));const model=[r.provider,r.model].filter(Boolean).join(' · ')||'—';return `<tr><td>${escapeHtml(r.email)}</td><td><strong>${escapeHtml(model)}</strong><small class="loom-admin-subline" title="${escapeHtml(r.turn_id)}">${escapeHtml(shortId(r.turn_id,7,4))}</small></td><td>${runStatusBadge(r.status)}</td><td>${fmtTime(r.started_at)}</td><td>${fmtDuration(duration)}</td><td>${fmtNumber(r.tool_count)}</td><td>${fmtNumber(r.approval_count)}</td><td>${fmtCompact(r.total_tokens)}</td><td>${activeRun(r)?`<button class="loom-admin-action is-danger" data-interrupt-run="${r.id}">Interrupt</button>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="9" class="loom-admin-empty">当前筛选条件下没有 Run。</td></tr>';
  }
  function aggregateRuns(range){
    const cfg=rangeConfig(range),cutoff=Math.floor(Date.now()/1000)-cfg.seconds,models=new Map(),users=new Map();
    state.runs.filter(r=>Number(r.completed_at||0)>=cutoff).forEach(r=>{const mk=`${r.provider||'local'}\u0000${r.model||'unknown'}`;const m=models.get(mk)||{provider:r.provider||'local',model:r.model||'unknown',runs:0,total_tokens:0};m.runs++;m.total_tokens+=Number(r.total_tokens||0);models.set(mk,m);const u=users.get(r.email)||{email:r.email,runs:0,total_tokens:0};u.runs++;u.total_tokens+=Number(r.total_tokens||0);users.set(r.email,u)});
    return{models:[...models.values()].sort((a,b)=>b.total_tokens-a.total_tokens),users:[...users.values()].sort((a,b)=>b.total_tokens-a.total_tokens)};
  }
  function renderUsage(){
    const summary=state.usage?.ranges?.[state.usageRange]||{};$('usageTokens').textContent=fmtCompact(summary.total_tokens);$('usageRuns').textContent=fmtNumber(summary.runs);$('usageInput').textContent=fmtCompact(summary.input_tokens);$('usageOutput').textContent=fmtCompact(summary.output_tokens);$('usageDuration').textContent=fmtDuration(summary.avg_duration);
    const models=Array.isArray(state.usage?.models)?state.usage.models:[],users=Array.isArray(state.usage?.users)?state.usage.users:[],maxModel=Math.max(1,...models.map(x=>Number(x.total_tokens||0)));
    $('modelsList').innerHTML=models.length?models.slice(0,12).map(m=>`<div class="loom-admin-metric-row"><div><strong>${escapeHtml(m.model||'unknown')}</strong><small>${escapeHtml(m.provider||'local')} · ${fmtNumber(m.runs)} runs</small></div><div class="loom-admin-metric-value"><span>${fmtCompact(m.total_tokens)}</span><i style="--metric:${Math.max(4,(Number(m.total_tokens||0)/maxModel)*100)}%"></i></div></div>`).join(''):'<p class="loom-admin-empty-block">当前范围暂无模型用量。</p>';
    const maxUser=Math.max(1,...users.map(x=>Number(x.total_tokens||0)));$('usageUsersList').innerHTML=users.length?users.slice(0,12).map(u=>`<div class="loom-admin-metric-row"><div><strong>${escapeHtml(u.email)}</strong><small>${fmtNumber(u.runs)} runs</small></div><div class="loom-admin-metric-value"><span>${fmtCompact(u.total_tokens)}</span><i style="--metric:${Math.max(4,(Number(u.total_tokens||0)/maxUser)*100)}%"></i></div></div>`).join(''):'<p class="loom-admin-empty-block">当前范围暂无用户用量。</p>';
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
    const u=state.selectedUser,ops=state.selectedUserOps||{};if(!u)return;const now=Math.floor(Date.now()/1000);
    const devices=Array.isArray(ops.devices)?ops.devices:[],runs=Array.isArray(ops.runs)?ops.runs:[],sessions=Array.isArray(u.sessions)?u.sessions:[],summary=ops.summary||{};
    $('userKpiDevices').textContent=fmtNumber(summary.known_devices??devices.length);$('userKpiRuns').textContent=fmtNumber(summary.active_runs??runs.filter(activeRun).length);$('userKpiTokens').textContent=fmtCompact(summary.tokens_30d);$('userKpiTools').textContent=fmtNumber(summary.tool_calls_30d);$('userKpiSessions').textContent=fmtNumber(sessions.filter(x=>!x.revoked_at&&Number(x.refresh_expires_at)>now).length);
    $('userDevicesBody').innerHTML=devices.length?devices.map(d=>{const h=d.health||{};const resources=d.health?`CPU ${h.cpu_percent==null?'—':Math.round(h.cpu_percent)+'%'} · RAM ${h.memory_percent==null?'—':Math.round(h.memory_percent)+'%'} · Disk ${h.disk_percent==null?'—':Math.round(h.disk_percent)+'%'} · ${h.relay_rtt_ms==null?'—':Math.round(h.relay_rtt_ms)+'ms'}`:'等待 Health v2';return `<tr><td><strong>${escapeHtml(d.name||'Loom Host')}</strong><small class="loom-admin-subline">${escapeHtml(shortId(d.device_id,7,4))}</small></td><td>${escapeHtml([d.platform,h.os_version||h.os_release,h.arch].filter(Boolean).join(' · ')||'—')}</td><td>${escapeHtml(resources)}</td><td>${escapeHtml(d.app_version||'—')} / ${escapeHtml(d.host_version||'—')}</td><td>${d.health?fmtDuration(h.system_uptime_seconds||0):'—'}</td><td>${fmtTime(d.last_seen_at)}</td><td><span class="loom-admin-badge ${d.online?'is-ok':'is-bad'}">${d.online?'online':'offline'}</span></td></tr>`}).join(''):'<tr><td colspan="7" class="loom-admin-empty">这个账号还没有 Host telemetry。</td></tr>';
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
  async function loadOverview(force=false){
    if(!force&&state.loaded.has('overview'))return;setHeader(true,'Loading');
    const [overview,agent,flags]=await Promise.all([request('/admin/overview'),request('/admin/agent-overview'),request('/admin/feature-flags'),loadHealth()]);
    state.overview=overview;state.agent=agent;state.flags=flags.flags||[];state.loaded.add('overview');renderOverview();
    const ok=state.health.admin&&state.health.account;setHeader(ok,ok?'Operational':'Degraded');
  }
  async function loadPageData(page,{force=false}={}){
    if(page==='overview')return loadOverview(force);if(page==='user')return;if(!force&&state.loaded.has(page))return;
    if(page==='users'){const p=await request('/admin/users-operations');state.users=p.users||[];renderUsers($('userSearch').value)}
    else if(page==='devices'){const p=await request('/admin/devices');state.devices=p.devices||[];renderDevices()}
    else if(page==='runs'){const [p,a]=await Promise.all([request('/admin/runs'),request('/admin/agent-overview')]);state.runs=p.runs||[];state.agent=a||state.agent;renderRuns($('runSearch').value)}
    else if(page==='usage'){state.usage=await request('/admin/usage');renderUsage()}
    else if(page==='tools'){state.tools=await request('/admin/tools');renderTools()}
    else if(page==='sessions'){const p=await request('/admin/sessions');state.sessions=p.sessions||[];renderSessions()}
    else if(page==='flags'){const p=await request('/admin/feature-flags');state.flags=p.flags||[];renderFlags();renderOverviewControls()}
    else if(page==='audit'){const p=await request('/admin/audit');state.audit=p.events||[];renderAudit($('auditSearch').value)}
    else if(page==='models'){window.dispatchEvent(new CustomEvent('loom-admin:model-page-request'))}
    else if(page==='system'){const [system,agent]=await Promise.all([request('/admin/system'),request('/admin/agent-overview'),loadHealth()]);state.system=system;state.agent=agent;renderSystem()}
    state.loaded.add(page);
  }
  async function refreshSelectedUserOps(){if(!state.selectedUser)return;const id=state.selectedUser.id;const [userPayload,opsPayload]=await Promise.all([request(`/admin/users/${id}`),request(`/admin/users/${id}/agent-ops`)]);state.selectedUser=userPayload.user;state.selectedUserOps=opsPayload;renderUserWorkspace()}
  async function refreshCurrentPage(full=false){
    if(!state.me)return;if(state.page==='user'&&state.selectedUser){if(full)await openUserDetail(state.selectedUser.id,{navigate:false});else await refreshSelectedUserOps();return}
    await loadPageData(state.page,{force:true});
  }
  function startAutoRefresh(){if(state.refreshTimer)return;state.refreshTimer=setInterval(()=>void refreshCurrentPage(false).catch(err=>{if(err?.status===401||err?.status===403)showLoadError(err)}),10000)}
  async function mutate(path,body={}){await request(path,{method:'POST',body:JSON.stringify(body)});state.loaded.clear();await loadOverview(true);if(state.page==='user'&&state.selectedUser)await openUserDetail(state.selectedUser.id,{navigate:false});else if(state.page!=='overview')await loadPageData(state.page,{force:true})}
  function modelCatalog(){const seen=new Set(),out=[];const add=x=>{const v=String(x||'').trim();if(v&&!seen.has(v.toLowerCase())){seen.add(v.toLowerCase());out.push(v)}};(state.models?.managed_models||[]).forEach(add);(state.models?.observed||[]).forEach(x=>add(x.model));(state.selectedModelAccess?.models||[]).forEach(add);return out;}
  async function openUserDetail(id,{navigate=true}={}){
    const [userPayload,accessPayload,toolAccessPayload,opsPayload,modelsPayload]=await Promise.all([request(`/admin/users/${id}`),request(`/admin/users/${id}/model-access`),request(`/admin/users/${id}/tool-access`),request(`/admin/users/${id}/agent-ops`),request('/admin/models')]);const u=userPayload.user,access=accessPayload.access||{enabled:true,models:[]},toolAccess=toolAccessPayload.access||{computerUse:false,browserUse:false,source:'default'};state.selectedUser=u;state.selectedUserOps=opsPayload;state.selectedModelAccess=access;state.selectedToolAccess=toolAccess;state.models=modelsPayload||{};$('detailTitle').textContent=u.email;const enabled=new Set((access.models||[]).map(String));const rows=modelCatalog().map(model=>`<label class="loom-admin-model-toggle"><input type="checkbox" data-model-access-id="${escapeHtml(model)}" ${enabled.has(model)?'checked':''}><span>${escapeHtml(model)}</span></label>`).join('');
    const verified=Boolean(u.email_verified??u.verified),banMeta=u.status==='banned'?'<div><span>Banned at</span><strong>'+escapeHtml(fmtTime(u.banned_at))+'</strong></div><div><span>Ban reason</span><strong>'+escapeHtml(u.ban_reason||'—')+'</strong></div>':'';
    $('userDetailBody').innerHTML=`<div><span>Role</span><strong>${escapeHtml(u.role)}</strong></div><div><span>Status</span><strong>${escapeHtml(u.status)}</strong></div><div><span>Verified</span><strong>${verified?'Verified':'Unverified'}</strong></div><div><span>Active sessions</span><strong>${u.active_sessions}</strong></div><div><span>Created</span><strong>${fmtTime(u.created_at)}</strong></div><div><span>Last active</span><strong>${fmtTime(u.last_seen_at)}</strong></div>${banMeta}<section class="loom-admin-model-access"><div class="loom-admin-model-access-head"><span>Built-in model access</span><label><input id="modelAccessEnabled" type="checkbox" ${access.enabled!==false?'checked':''}> Enabled</label></div><small>${access.source==='override'?'Per-user override':'Using Loom default policy'}</small><div class="loom-admin-model-grid">${rows||'<p class="loom-admin-empty-block">No managed model catalog.</p>'}</div><button id="saveModelAccessButton" class="loom-admin-action" type="button">Save model access</button></section><section class="loom-admin-model-access"><div class="loom-admin-model-access-head"><span>Automation access</span><small>${toolAccess.source==='override'?'Per-user override':'Disabled by default'}</small></div><small>Computer Use and Browser automation are account-gated. They stay unavailable until explicitly enabled here.</small><div class="loom-admin-model-grid"><label class="loom-admin-model-toggle"><input id="computerUseAccessEnabled" type="checkbox" ${toolAccess.computerUse===true?'checked':''}><span>Computer Use</span></label><label class="loom-admin-model-toggle"><input id="browserUseAccessEnabled" type="checkbox" ${toolAccess.browserUse===true?'checked':''}><span>Browser automation</span></label></div><button id="saveToolAccessButton" class="loom-admin-action" type="button">Save automation access</button></section>`;
    const self=Number(u.id)===Number(state.me.id),privileged=['admin','owner'].includes(String(u.role||'')),canBan=!self&&(!privileged||state.me?.role==='owner');
    $('detailStatusButton').textContent=u.status==='banned'?'Banned':u.status==='active'?'Disable':'Enable';$('detailStatusButton').disabled=u.status==='banned'||(self&&u.status==='active');
    $('detailBanButton').textContent=u.status==='banned'?'Unban':'Ban account';$('detailBanButton').classList.toggle('is-danger',u.status!=='banned');$('detailBanButton').classList.toggle('is-primary',u.status==='banned');$('detailBanButton').disabled=!canBan;
    renderUserWorkspace();state.loaded.add('user');
    if(navigate)location.hash=`user/${u.id}`;else showPage('user');
  }
  function closeBanDialog(){
    const dialog=$('banDialog');if(dialog?.open)dialog.close();$('banDialogError').textContent='';$('banReason').value='';if(dialog)delete dialog.dataset.userId;
  }
  function openBanDialog(userId,email){
    const dialog=$('banDialog');dialog.dataset.userId=String(userId);$('banDialogTarget').textContent=String(email||'');$('banDialogError').textContent='';$('banReason').value='';dialog.showModal();requestAnimationFrame(()=>$('banReason').focus());
  }
  async function unbanUser(userId){
    if(!confirm('确认解封这个账号？解封后可以重新登录，但之前已撤销的 Session 不会恢复。'))return;
    await mutate('/admin/users/'+Number(userId)+'/unban');
  }

  async function saveFlag(key,enabled,value){const result=await request('/admin/feature-flags',{method:'POST',body:JSON.stringify({key,enabled,value})});const next=result.flag;const i=state.flags.findIndex(f=>f.key===next.key);if(i>=0)state.flags[i]=next;else state.flags.push(next);state.flags.sort((a,b)=>a.key.localeCompare(b.key));renderFlags();renderOverviewControls();}

  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';const b=e.currentTarget.querySelector('button');b.disabled=true;try{await login($('loginEmail').value.trim(),$('loginPassword').value);$('loginPassword').value='';setAuthenticated(true);await loadOverview(true);startAutoRefresh();await applyRoute()}catch(err){$('loginError').textContent=err.code==='ADMIN_REQUIRED'?'这个 Loom 账号没有管理员权限。':(err.message||'登录失败');setHeader(false,'Access denied')}finally{b.disabled=false}});
  $('logoutButton').addEventListener('click',async()=>{const refresh=state.refresh;if(refresh){try{await fetch(API+'/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),cache:'no-store'})}catch(_){}}clearTokens();setAuthenticated(false);setHeader(false,'Signed out')});
  $('refreshButton').addEventListener('click',()=>void refreshCurrentPage(true).catch(showLoadError));$('userSearch').addEventListener('input',e=>renderUsers(e.target.value));$('userPresenceFilter').addEventListener('click',e=>{const b=e.target.closest('[data-user-presence]');if(!b)return;state.userPresenceFilter=b.dataset.userPresence||'all';document.querySelectorAll('#userPresenceFilter [data-user-presence]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderUsers($('userSearch').value)});$('userSort').addEventListener('change',e=>{state.userSort=e.target.value||'recent';renderUsers($('userSearch').value)});$('runSearch').addEventListener('input',e=>renderRuns(e.target.value));$('runStatusFilter').addEventListener('click',e=>{const b=e.target.closest('[data-run-status]');if(!b)return;state.runStatusFilter=b.dataset.runStatus||'all';document.querySelectorAll('#runStatusFilter [data-run-status]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderRuns($('runSearch').value)});$('auditSearch').addEventListener('input',e=>renderAudit(e.target.value));
  $('usageRangeControl').addEventListener('click',e=>{const b=e.target.closest('[data-range]');if(!b)return;state.usageRange=b.dataset.range;document.querySelectorAll('#usageRangeControl [data-range]').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));renderUsage()});
  $('usersGrid').addEventListener('click',async e=>{const detail=e.target.closest('[data-user-detail]'),status=e.target.closest('[data-status-user]'),ban=e.target.closest('[data-ban-user]'),unban=e.target.closest('[data-unban-user]'),revoke=e.target.closest('[data-revoke-user]');try{if(detail)return await openUserDetail(Number(detail.dataset.userDetail));if(ban)return openBanDialog(Number(ban.dataset.banUser),ban.dataset.banEmail||'');if(unban)return await unbanUser(Number(unban.dataset.unbanUser));if(status){const id=Number(status.dataset.statusUser),next=status.dataset.nextStatus;if(next==='disabled'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;return await mutate(`/admin/users/${id}/${next==='disabled'?'disable':'enable'}`)}if(revoke){if(!confirm('确认撤销这个用户的全部有效会话？'))return;return await mutate('/admin/users/revoke-sessions',{user_id:Number(revoke.dataset.revokeUser)})}}catch(err){alert(err.message||'操作失败')}});
  $('usersGrid').addEventListener('change',async e=>{const select=e.target.closest('[data-role-user]');if(!select)return;try{await mutate('/admin/users/role',{user_id:Number(select.dataset.roleUser),role:select.value})}catch(err){alert(err.message||'修改角色失败');await loadPageData('users',{force:true})}});
  async function handleInterrupt(button){if(!button||!confirm('确认中断这个正在运行的 Agent Turn？该操作会写入审计日志。'))return;button.disabled=true;button.textContent='Queuing…';try{await request('/admin/runs/interrupt',{method:'POST',body:JSON.stringify({run_id:Number(button.dataset.interruptRun)})});button.textContent='Queued';setTimeout(()=>void refreshCurrentPage(false).catch(showLoadError),3500)}catch(err){button.disabled=false;button.textContent='Interrupt';alert(err.message||'中断失败')}}
  async function handleSessionRevoke(revoke){if(!revoke||!confirm('确认撤销这个 Session？'))return;try{await mutate(`/admin/sessions/${encodeURIComponent(revoke.dataset.revokeSession)}/revoke`);if(state.selectedUser)await openUserDetail(state.selectedUser.id,{navigate:false})}catch(err){alert(err.message||'撤销失败')}}
  $('runsBody').addEventListener('click',e=>void handleInterrupt(e.target.closest('[data-interrupt-run]')));
  $('userRunsBody').addEventListener('click',e=>void handleInterrupt(e.target.closest('[data-interrupt-run]')));
  $('sessionsBody').addEventListener('click',e=>void handleSessionRevoke(e.target.closest('[data-revoke-session]')));
  $('userSessionsBody').addEventListener('click',e=>void handleSessionRevoke(e.target.closest('[data-revoke-session]')));
  $('flagForm').addEventListener('submit',async e=>{e.preventDefault();$('flagError').textContent='';const key=$('flagKey').value.trim();let value;try{value=JSON.parse($('flagValue').value||'{}')}catch(_){$('flagError').textContent='JSON value 格式不正确。';return}const b=e.currentTarget.querySelector('button');b.disabled=true;try{await saveFlag(key,$('flagEnabled').checked,value);$('flagKey').value='';$('flagValue').value='{}'}catch(err){$('flagError').textContent=err.message||'保存失败'}finally{b.disabled=false}});
  $('flagsList').addEventListener('change',async e=>{const input=e.target.closest('[data-flag-toggle]');if(!input)return;const flag=state.flags.find(f=>f.key===input.dataset.flagToggle);if(!flag)return;input.disabled=true;try{await saveFlag(flag.key,input.checked,flag.value)}catch(err){input.checked=!input.checked;alert(err.message||'修改失败')}finally{input.disabled=false}});
  $('overviewFlags').addEventListener('change',async e=>{const input=e.target.closest('[data-overview-flag-toggle]');if(!input)return;const flag=state.flags.find(f=>f.key===input.dataset.overviewFlagToggle);if(!flag)return;input.disabled=true;try{await saveFlag(flag.key,input.checked,flag.value)}catch(err){input.checked=!input.checked;alert(err.message||'修改失败')}finally{input.disabled=false}});
  $('user').addEventListener('click',async e=>{const save=e.target.closest('#saveModelAccessButton'),saveTools=e.target.closest('#saveToolAccessButton'),u=state.selectedUser;if(!u||(!save&&!saveTools))return;if(saveTools){const computerUse=Boolean($('computerUseAccessEnabled')?.checked),browserUse=Boolean($('browserUseAccessEnabled')?.checked);saveTools.disabled=true;try{const result=await request('/admin/users/tool-access',{method:'POST',body:JSON.stringify({user_id:Number(u.id),computerUse,browserUse})});state.selectedToolAccess=result.access;saveTools.textContent='Saved';setTimeout(()=>{saveTools.textContent='Save automation access'},900)}catch(err){alert(err.message||'修改自动化权限失败')}finally{saveTools.disabled=false}return}const models=[...$('userDetailBody').querySelectorAll('[data-model-access-id]:checked')].map(el=>el.dataset.modelAccessId),enabled=Boolean($('modelAccessEnabled')?.checked);save.disabled=true;try{const result=await request('/admin/users/model-access',{method:'POST',body:JSON.stringify({user_id:Number(u.id),enabled,models})});state.selectedModelAccess=result.access;save.textContent='Saved';setTimeout(()=>{save.textContent='Save model access'},900)}catch(err){alert(err.message||'修改模型权限失败')}finally{save.disabled=false}});
  $('closeUserDialog').addEventListener('click',()=>{location.hash='users'});
  $('detailStatusButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u)return;const next=u.status==='active'?'disable':'enable';if(next==='disable'&&!confirm('确认停用这个账号并立即撤销其会话？'))return;try{await mutate(`/admin/users/${u.id}/${next}`);await openUserDetail(u.id,{navigate:false})}catch(err){alert(err.message||'操作失败')}});
  $('detailRevokeButton').addEventListener('click',async()=>{const u=state.selectedUser;if(!u||!confirm('确认撤销这个用户的全部有效会话？'))return;try{await mutate('/admin/users/revoke-sessions',{user_id:Number(u.id)});await openUserDetail(u.id,{navigate:false})}catch(err){alert(err.message||'操作失败')}});

  const ADMIN_PAGES=new Set(['overview','users','devices','runs','usage','tools','sessions','models','flags','audit','system']);
  function showPage(page){
    state.page=page;document.querySelectorAll('[data-admin-page]').forEach(el=>{el.hidden=el.dataset.adminPage!==page});
    const current=page==='user'?'users':page;document.querySelectorAll('#sectionNav a').forEach(a=>a.setAttribute('aria-current',a.getAttribute('href')===`#${current}`?'page':'false'));
    window.dispatchEvent(new CustomEvent('loom-admin:pagechange',{detail:{page}}));
    requestAnimationFrame(()=>window.scrollTo({top:0,behavior:'auto'}));
  }
  function routeState(){const raw=decodeURIComponent(location.hash.replace(/^#/,'')||'overview');if(raw.startsWith('user/')){const id=Number(raw.split('/')[1]||0);return id>0?{page:'user',userId:id}:{page:'users'}}return{page:ADMIN_PAGES.has(raw)?raw:'overview'}}
  async function applyRoute(){const route=routeState();if(route.page==='user'){showPage('user');if(state.me&&(!state.selectedUser||Number(state.selectedUser.id)!==route.userId))await openUserDetail(route.userId,{navigate:false});else if(state.selectedUser)renderUserWorkspace();return}showPage(route.page);if(state.me)await loadPageData(route.page)}
  function setupNav(){window.addEventListener('hashchange',()=>void applyRoute().catch(showLoadError));document.addEventListener('click',e=>{const target=e.target.closest('[data-admin-route]');if(!target)return;e.preventDefault();location.hash=target.dataset.adminRoute});void applyRoute().catch(showLoadError)}
  function showLoadError(err){if(err?.status===401||err?.status===403){clearTokens();setAuthenticated(false);$('loginError').textContent=err.status===403?'这个账号没有管理员权限。':'登录已过期，请重新登录。';setHeader(false,'Access required');return}setHeader(false,'Service error');console.error(err);}
  setupNav();(async()=>{try{if(state.access||state.refresh){state.me=(await request('/admin/me')).user;setAuthenticated(true);await loadOverview(true);startAutoRefresh();await applyRoute()}else{setAuthenticated(false);setHeader(false,'Admin sign in')}}catch(err){showLoadError(err)}})();
})();
