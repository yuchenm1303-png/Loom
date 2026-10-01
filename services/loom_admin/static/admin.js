(() => {
  const API = location.hostname === 'loom.smirel.com' ? '/admin-api/v1' : '/api/v1';
  const $ = (id) => document.getElementById(id);
  const state = { access: sessionStorage.getItem('loom_admin_access') || '', refresh: sessionStorage.getItem('loom_admin_refresh') || '', me: null, overview: null, users: [], sessions: [], audit: [], flags: [] };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtTime = (value) => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', { hour12:false }) : '—';
  const shortSession = (value) => value ? `${value.slice(0,8)}…${value.slice(-5)}` : '—';

  function saveTokens(payload) {
    if (payload.access_token) { state.access = payload.access_token; sessionStorage.setItem('loom_admin_access', state.access); }
    if (payload.refresh_token) { state.refresh = payload.refresh_token; sessionStorage.setItem('loom_admin_refresh', state.refresh); }
  }
  function clearTokens() { state.access=''; state.refresh=''; state.me=null; sessionStorage.removeItem('loom_admin_access'); sessionStorage.removeItem('loom_admin_refresh'); }

  async function rawRequest(path, options={}) {
    const headers = { 'Content-Type':'application/json', ...(options.headers||{}) };
    if (state.access) headers.Authorization = `Bearer ${state.access}`;
    const response = await fetch(API + path, { ...options, headers, credentials:'same-origin' });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { response, payload };
  }

  async function refreshAccess() {
    if (!state.refresh) return false;
    const response = await fetch(API + '/auth/refresh', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({refresh_token:state.refresh}) });
    if (!response.ok) { clearTokens(); return false; }
    const payload = await response.json(); saveTokens(payload); return true;
  }

  async function request(path, options={}, retry=true) {
    let { response, payload } = await rawRequest(path, options);
    if (response.status === 401 && retry && await refreshAccess()) ({ response, payload } = await rawRequest(path, options));
    if (!response.ok) { const err = new Error(payload?.error?.message || `HTTP ${response.status}`); err.code = payload?.error?.code || ''; err.status=response.status; throw err; }
    return payload;
  }

  function setHeader(ok, text) {
    const el = $('headerStatus'); if (!el) return;
    el.querySelector('span').textContent = text; el.dataset.state = ok ? 'ok' : 'bad';
  }
  function setAuthenticated(on) { $('authPanel').hidden = on; $('adminContent').hidden = !on; $('logoutButton').hidden = !on; }

  async function login(email, password) {
    const response = await fetch(API + '/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email,password}) });
    const payload = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(payload?.error?.message || '登录失败');
    saveTokens(payload);
    try { state.me = (await request('/admin/me')).user; }
    catch (error) { clearTokens(); throw error; }
  }

  function renderOverview() {
    const o = state.overview || {};
    [['kpiUsers','users'],['kpiActive','active_users'],['kpiActive24','active_24h'],['kpiSessions','active_sessions'],['kpiNew','registrations_24h'],['kpiDisabled','disabled_users'],['kpiOwners','owners'],['kpiAudit','audit_events_24h']].forEach(([id,key]) => $(id).textContent = String(o[key] ?? 0));
    $('generatedAt').textContent = o.generated_at ? fmtTime(o.generated_at) : new Date().toLocaleString('zh-CN',{hour12:false});
    $('adminIdentity').textContent = `${state.me.email} · ${state.me.role}`;
    $('serviceState').textContent = 'Healthy'; $('serviceState').dataset.state='ok';
    renderActivity();
  }

  function renderActivity() {
    const now = Math.floor(Date.now()/1000), start = now - 24*3600;
    const userBuckets = new Array(24).fill(0), sessionBuckets = new Array(24).fill(0);
    state.sessions.forEach(s => {
      const t = Number(s.last_used_at || 0); if (t < start) return; const i=Math.max(0,Math.min(23,Math.floor((t-start)/3600))); sessionBuckets[i]++;
    });
    state.users.forEach(u => { const t=Number(u.last_seen_at||0); if(t<start)return; const i=Math.max(0,Math.min(23,Math.floor((t-start)/3600))); userBuckets[i]++; });
    const rail=$('activityRail'); rail.innerHTML='';
    userBuckets.forEach((n,i)=>{ const span=document.createElement('span'); span.className = n ? 'is-active' : 'is-idle'; span.title=`${i}: ${n}`; rail.appendChild(span); });
    $('activitySummary').textContent = `${userBuckets.reduce((a,b)=>a+b,0)} 个活动时段`;
    const spark=$('sessionSpark'); spark.innerHTML=''; const max=Math.max(1,...sessionBuckets);
    sessionBuckets.forEach(n=>{const bar=document.createElement('i'); bar.style.height=`${8 + (n/max)*82}%`; bar.style.opacity=n?'.95':'.16'; spark.appendChild(bar);});
    $('sessionActivitySummary').textContent = `${sessionBuckets.reduce((a,b)=>a+b,0)} 次会话活动`;
    const labels=['-24h','','','','','','-18h','','','','','','-12h','','','','','','-6h','','','','','now'];
    $('activityAxis').innerHTML=labels.map(x=>`<span>${x}</span>`).join(''); $('sessionAxis').innerHTML=$('activityAxis').innerHTML;
  }

  function renderUsers(filter='') {
    const q=filter.trim().toLowerCase(); const users=state.users.filter(u=>!q || `${u.email} ${u.display_name||''} ${u.role}`.toLowerCase().includes(q));
    $('usersHint').textContent=`${users.length} 个用户`;
    $('usersBody').innerHTML = users.length ? users.map(u=>`<tr>
      <td><div class="loom-admin-user-cell"><strong>${escapeHtml(u.display_name || u.email)}</strong><small>${escapeHtml(u.email)}</small></div></td>
      <td><span class="loom-admin-badge ${u.status==='active'?'is-ok':'is-bad'}">${escapeHtml(u.status)}</span></td>
      <td>${state.me?.role==='owner' ? `<select data-role-user="${u.id}" aria-label="用户角色"><option value="user" ${u.role==='user'?'selected':''}>user</option><option value="admin" ${u.role==='admin'?'selected':''}>admin</option><option value="owner" ${u.role==='owner'?'selected':''}>owner</option></select>` : `<span class="loom-admin-badge">${escapeHtml(u.role)}</span>`}</td>
      <td>${u.active_sessions}</td><td>${fmtTime(u.last_seen_at)}</td><td>${fmtTime(u.created_at)}</td>
      <td><div class="loom-admin-actions"><button class="loom-admin-action" data-status-user="${u.id}" data-next-status="${u.status==='active'?'disabled':'active'}">${u.status==='active'?'停用':'恢复'}</button><button class="loom-admin-action is-danger" data-revoke-user="${u.id}">撤销会话</button></div></td>
    </tr>`).join('') : '<tr><td colspan="7" class="loom-admin-empty">没有匹配用户</td></tr>';
  }

  function renderSessions() {
    $('sessionsHint').textContent=`${state.sessions.length} 条`;
    const now=Math.floor(Date.now()/1000);
    $('sessionsBody').innerHTML = state.sessions.length ? state.sessions.map(s=>{ const live=!s.revoked_at && Number(s.refresh_expires_at)>now; return `<tr><td>${escapeHtml(s.email)}</td><td><code>${escapeHtml(shortSession(s.id))}</code></td><td><span class="loom-admin-badge">${escapeHtml(s.role)}</span></td><td>${fmtTime(s.created_at)}</td><td>${fmtTime(s.last_used_at)}</td><td>${fmtTime(s.refresh_expires_at)}</td><td><span class="loom-admin-badge ${live?'is-ok':'is-bad'}">${live?'active':'closed'}</span></td></tr>`; }).join('') : '<tr><td colspan="7" class="loom-admin-empty">暂无会话</td></tr>';
  }

  function renderFlags() {
    $('flagsList').innerHTML = state.flags.length ? state.flags.map(f=>`<div class="loom-admin-flag-row"><div><strong>${escapeHtml(f.key)}</strong><small>updated ${fmtTime(f.updated_at)}</small></div><button class="loom-admin-toggle ${f.enabled?'is-on':''}" data-flag-key="${escapeHtml(f.key)}" data-flag-enabled="${f.enabled?'1':'0'}" aria-label="切换 ${escapeHtml(f.key)}"></button></div>`).join('') : '<div class="loom-admin-empty">还没有 Feature Flag</div>';
  }

  function renderAudit() {
    $('auditHint').textContent=`${state.audit.length} 条`;
    $('auditList').innerHTML = state.audit.length ? state.audit.map(e=>`<div class="loom-admin-audit-item"><strong>${escapeHtml(e.action)}</strong><span>${escapeHtml(e.actor_email || 'system')}</span><code>${escapeHtml(`${e.target_type||''}:${e.target_id||''}`)}</code><span>${fmtTime(e.created_at)}</span></div>`).join('') : '<div class="loom-admin-empty">暂无管理操作</div>';
  }

  function renderSystem() {
    $('systemHealth').innerHTML = [
      ['Account API','Healthy'],['Admin API','Protected'],['SQLite','Online'],['Loom Web','Online']
    ].map(([a,b])=>`<div class="loom-admin-health-item"><span>${a}</span><b>${b}</b></div>`).join('');
  }

  async function loadAll() {
    setHeader(true,'Loading');
    const [overview, users, sessions, audit, flags] = await Promise.all([
      request('/admin/overview'), request('/admin/users'), request('/admin/sessions'), request('/admin/audit'), request('/admin/feature-flags')
    ]);
    state.overview=overview; state.users=users.users||[]; state.sessions=sessions.sessions||[]; state.audit=audit.events||[]; state.flags=flags.flags||[];
    renderOverview(); renderUsers($('userSearch').value); renderSessions(); renderFlags(); renderAudit(); renderSystem(); setHeader(true,'Operational'); setAuthenticated(true);
  }

  async function mutate(path, body) { await request(path,{method:'POST',body:JSON.stringify(body)}); await loadAll(); }

  $('loginForm').addEventListener('submit', async e=>{ e.preventDefault(); $('loginError').textContent=''; const button=e.currentTarget.querySelector('button'); button.disabled=true; try { await login($('loginEmail').value.trim(), $('loginPassword').value); $('loginPassword').value=''; await loadAll(); } catch(err){ $('loginError').textContent = err.code==='ADMIN_REQUIRED' ? '这个 Loom 账号没有管理员权限。' : (err.message||'登录失败'); setHeader(false,'Access denied'); } finally { button.disabled=false; } });
  $('logoutButton').addEventListener('click',()=>{ clearTokens(); setAuthenticated(false); setHeader(false,'Signed out'); });
  $('refreshButton').addEventListener('click',()=>loadAll().catch(showLoadError));
  $('userSearch').addEventListener('input',e=>renderUsers(e.target.value));
  $('usersBody').addEventListener('click', async e=>{ const status=e.target.closest('[data-status-user]'); const revoke=e.target.closest('[data-revoke-user]'); try { if(status){ const next=status.dataset.nextStatus; if(next==='disabled' && !confirm('确认停用这个账号并撤销其会话？')) return; await mutate('/admin/users/status',{user_id:Number(status.dataset.statusUser),status:next}); } if(revoke){ if(!confirm('确认撤销这个用户的全部有效会话？')) return; await mutate('/admin/users/revoke-sessions',{user_id:Number(revoke.dataset.revokeUser)}); } } catch(err){ alert(err.message||'操作失败'); } });
  $('usersBody').addEventListener('change', async e=>{ const select=e.target.closest('[data-role-user]'); if(!select)return; try { await mutate('/admin/users/role',{user_id:Number(select.dataset.roleUser),role:select.value}); } catch(err){ alert(err.message||'修改角色失败'); await loadAll(); } });
  $('flagsList').addEventListener('click', async e=>{ const button=e.target.closest('[data-flag-key]'); if(!button)return; try { await mutate('/admin/feature-flags',{key:button.dataset.flagKey,enabled:button.dataset.flagEnabled!=='1',value:{}}); } catch(err){ alert(err.message||'修改失败'); } });
  $('addFlagButton').addEventListener('click', async ()=>{ const key=prompt('Feature Flag key（a-z / 0-9 / . _ -）'); if(!key)return; try { await mutate('/admin/feature-flags',{key:key.trim(),enabled:false,value:{}}); } catch(err){ alert(err.message||'创建失败'); } });

  function showLoadError(err){ if(err?.status===401 || err?.status===403){ clearTokens(); setAuthenticated(false); $('loginError').textContent = err?.status===403 ? '这个账号没有管理员权限。' : '登录已过期，请重新登录。'; setHeader(false,'Access required'); return; } setHeader(false,'Service error'); console.error(err); }

  (async()=>{ try { if(state.access || state.refresh){ state.me=(await request('/admin/me')).user; await loadAll(); } else { setAuthenticated(false); setHeader(false,'Admin sign in'); } } catch(err){ showLoadError(err); } })();
})();
