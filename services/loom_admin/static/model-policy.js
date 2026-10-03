(() => {
  const POLICY_API = '/policy/v1';
  const ACCOUNT_API = '/api/v1';
  const $ = (id) => document.getElementById(id);
  const policyState = {
    models: [], modelGroups: [], groups: [], users: [],
    selectedGroupId: null, selectedGroup: null,
    selectedAccountId: null, selectedAccountPolicy: null, selectedAccountAccess: null,
    dialogUserId: null, activeTab: 'global', loading: false,
  };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const accessToken = () => sessionStorage.getItem('loom_admin_access') || '';
  const refreshToken = () => sessionStorage.getItem('loom_admin_refresh') || '';

  async function refreshAccess() {
    const refresh = refreshToken();
    if (!refresh) return false;
    const response = await fetch(ACCOUNT_API + '/auth/refresh', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({refresh_token: refresh}), cache: 'no-store',
    });
    if (!response.ok) return false;
    const payload = await response.json().catch(() => ({}));
    if (payload.access_token) sessionStorage.setItem('loom_admin_access', payload.access_token);
    if (payload.refresh_token) sessionStorage.setItem('loom_admin_refresh', payload.refresh_token);
    return Boolean(payload.access_token);
  }

  async function authedFetch(url, options = {}, retry = true) {
    const headers = {'Content-Type':'application/json', ...(options.headers || {})};
    const token = accessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    let response = await fetch(url, {...options, headers, cache:'no-store', credentials:'same-origin'});
    if (response.status === 401 && retry && await refreshAccess()) return authedFetch(url, options, false);
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
      error.status = response.status; error.code = payload?.error?.code || '';
      throw error;
    }
    return payload;
  }
  const policyRequest = (path, options = {}) => authedFetch(POLICY_API + path, options);
  const accountRequest = (path, options = {}) => authedFetch(ACCOUNT_API + path, options);

  function installStyles() {
    if ($('loomModelPolicyStyles')) return;
    const style = document.createElement('style');
    style.id = 'loomModelPolicyStyles';
    style.textContent = `
      .loom-policy-shell{margin-top:14px}
      .loom-policy-tabs{display:flex;gap:6px;align-items:center;margin:14px 0 12px;padding:5px;width:max-content;max-width:100%;overflow:auto;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:rgba(2,7,12,.24)}
      .loom-policy-tab{border:0;border-radius:7px;padding:8px 12px;background:transparent;color:rgba(235,245,252,.52);font:inherit;font-size:11px;font-weight:650;cursor:pointer;white-space:nowrap}
      .loom-policy-tab[aria-selected="true"]{background:rgba(255,255,255,.08);color:#fff}.loom-policy-tab:hover{color:#fff}
      .loom-policy-pane[hidden]{display:none!important}
      .loom-policy-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.loom-policy-toolbar .loom-admin-action{min-width:auto}
      .loom-policy-note{margin:6px 0 0;color:rgba(220,235,245,.48);font-size:11px;line-height:1.55}
      .loom-policy-summary{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.loom-policy-summary span{padding:5px 8px;border:1px solid rgba(255,255,255,.08);border-radius:999px;background:rgba(0,0,0,.12);color:rgba(235,245,252,.58);font-size:10px}
      .loom-policy-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px}
      .loom-policy-card{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:58px;padding:11px 12px;border:1px solid rgba(255,255,255,.08);border-radius:11px;background:rgba(5,12,20,.20)}
      .loom-policy-card strong{display:block;color:rgba(248,251,255,.88);font-size:11px;font-weight:650;overflow-wrap:anywhere}.loom-policy-card small{display:block;margin-top:3px;color:rgba(225,239,248,.46);font-size:9px;line-height:1.4}
      .loom-policy-switch{position:relative;display:inline-flex;align-items:center;flex:0 0 auto}.loom-policy-switch input{position:absolute;opacity:0;pointer-events:none}
      .loom-policy-switch span{width:38px;height:22px;border-radius:999px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.11);position:relative;transition:.16s ease}
      .loom-policy-switch span:after{content:"";position:absolute;width:16px;height:16px;left:2px;top:2px;border-radius:50%;background:rgba(255,255,255,.68);transition:.16s ease}
      .loom-policy-switch input:checked+span{background:rgba(104,184,225,.26);border-color:rgba(136,205,239,.36)}.loom-policy-switch input:checked+span:after{transform:translateX(16px);background:#fff}.loom-policy-switch input:disabled+span{opacity:.35}
      .loom-policy-layout{display:grid;grid-template-columns:minmax(250px,.62fr) minmax(0,1.65fr);gap:14px;align-items:start}.loom-policy-layout>.account-card{min-height:0!important}
      .loom-policy-side-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.loom-policy-side-head h3{margin:2px 0 0;font-size:17px}.loom-policy-side-head small{color:rgba(230,241,249,.44)}
      .loom-policy-create{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;margin-bottom:10px}.loom-policy-create input,.loom-policy-search,.loom-policy-select{min-width:0;border:1px solid rgba(255,255,255,.09);border-radius:8px;padding:8px 10px;background:rgba(0,0,0,.16);color:rgba(250,252,255,.82);font:inherit;font-size:10px;outline:none}.loom-policy-create input:focus,.loom-policy-search:focus,.loom-policy-select:focus{border-color:rgba(141,205,237,.3)}
      .loom-policy-groups,.loom-policy-account-list{display:grid;gap:6px;max-height:520px;overflow:auto;padding-right:2px}
      .loom-policy-group-button,.loom-policy-account-button{width:100%;text-align:left;padding:10px 11px;border:1px solid rgba(255,255,255,.075);border-radius:10px;background:rgba(5,12,20,.18);color:inherit;cursor:pointer}
      .loom-policy-group-button[aria-current="true"],.loom-policy-account-button[aria-current="true"]{border-color:rgba(129,201,237,.32);background:rgba(61,142,182,.12)}
      .loom-policy-group-button strong,.loom-policy-group-button small,.loom-policy-account-button strong,.loom-policy-account-button small{display:block}.loom-policy-group-button strong,.loom-policy-account-button strong{font-size:11px}.loom-policy-group-button small,.loom-policy-account-button small{margin-top:3px;font-size:9px;opacity:.5}
      .loom-policy-group-head,.loom-policy-account-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:12px}.loom-policy-group-head h3,.loom-policy-account-head h3{margin:2px 0 3px;font-size:18px}
      .loom-policy-subsection{margin-top:15px;padding-top:14px;border-top:1px solid rgba(255,255,255,.07)}.loom-policy-subsection-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:9px}.loom-policy-subsection-head>span{color:rgba(230,241,249,.42);font-size:10px}
      .loom-policy-filter-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.loom-policy-filter-row .loom-policy-search{width:min(260px,100%)}
      .loom-policy-members{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:7px;max-height:330px;overflow:auto;padding-right:2px}
      .loom-policy-member{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 10px;border:1px solid rgba(255,255,255,.07);border-radius:9px;background:rgba(5,12,20,.16)}
      .loom-policy-member strong{display:block;font-size:10px;overflow-wrap:anywhere}.loom-policy-member small{display:block;margin-top:2px;font-size:9px;opacity:.48}
      .loom-policy-empty{padding:22px;text-align:center;color:rgba(235,247,255,.48);border:1px dashed rgba(255,255,255,.1);border-radius:10px;line-height:1.6}.loom-policy-empty .loom-admin-action{margin-top:10px}
      .loom-policy-error{color:#ffb6b6;font-size:11px;margin:8px 0 0}
      .loom-policy-source{display:inline-flex;align-items:center;gap:5px;margin-top:4px}.loom-policy-source i{width:5px;height:5px;border-radius:50%;background:rgba(158,213,241,.68)}
      .loom-policy-final{display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;border:1px solid rgba(255,255,255,.08);font-size:9px;font-weight:700}.loom-policy-final[data-on="true"]{color:#a7e8d2;border-color:rgba(128,224,193,.14)}.loom-policy-final[data-on="false"]{color:#ffc3c8;border-color:rgba(255,155,166,.14)}
      .loom-policy-account-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}.loom-policy-account-meta span{padding:4px 7px;border-radius:999px;border:1px solid rgba(255,255,255,.07);background:rgba(0,0,0,.12);font-size:9px;color:rgba(232,243,250,.56)}
      .loom-policy-account-master{display:flex;align-items:center;gap:10px;padding:9px 10px;border:1px solid rgba(255,255,255,.07);border-radius:10px;background:rgba(5,12,20,.18)}.loom-policy-account-master>div{flex:1}.loom-policy-account-master strong,.loom-policy-account-master small{display:block}.loom-policy-account-master strong{font-size:11px}.loom-policy-account-master small{margin-top:2px;font-size:9px;color:rgba(229,240,249,.45)}
      .loom-policy-model-actions{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}.loom-policy-choice{border:1px solid rgba(255,255,255,.08);border-radius:7px;padding:5px 7px;background:rgba(0,0,0,.13);color:rgba(235,245,252,.52);font:inherit;font-size:9px;font-weight:650;cursor:pointer}.loom-policy-choice:hover{color:#fff;background:rgba(255,255,255,.06)}.loom-policy-choice[aria-pressed="true"]{color:#fff;border-color:rgba(142,205,237,.26);background:rgba(78,153,191,.16)}.loom-policy-choice.is-deny[aria-pressed="true"]{color:#ffd1d5;border-color:rgba(255,158,170,.18);background:rgba(112,24,34,.14)}
      .loom-policy-card[data-hard-off="true"]{opacity:.58}.loom-policy-card[data-hard-off="true"] .loom-policy-choice{opacity:.55}
      .loom-policy-effective{grid-column:1/-1;margin-top:4px;padding:12px;border:1px solid rgba(112,210,255,.15);border-radius:10px;background:rgba(50,156,203,.055)}
      .loom-policy-effective-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:9px}
      @media(max-width:900px){.loom-policy-layout{grid-template-columns:1fr}.loom-policy-groups,.loom-policy-account-list{max-height:280px}.loom-policy-members{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function installUI() {
    if ($('models')) return;
    installStyles();
    const nav = $('sectionNav') || document.querySelector('.loom-admin-nav');
    if (nav && !nav.querySelector('a[href="#models"]')) {
      const system = [...nav.querySelectorAll('a')].find(a => a.getAttribute('href') === '#system');
      const link = document.createElement('a'); link.href = '#models'; link.textContent = '模型';
      if (system) nav.insertBefore(link, system); else nav.appendChild(link);
    }
    const anchor = $('sessions') || $('system');
    if (!anchor) return;
    const section = document.createElement('section');
    section.className = 'usage-accounts-section fade loom-policy-page'; section.id = 'models'; section.dataset.adminPage = 'models'; section.hidden = true;
    section.innerHTML = `
      <div class="usage-section-head">
        <div><p class="kicker">MODEL ACCESS CONTROL</p><h2>模型权限控制台</h2><p>全局、访问分组和单账号三层策略。全局禁用是硬限制，单账号显式规则优先于 Access Group。</p></div>
        <div class="loom-policy-toolbar"><button id="policyRefresh" class="loom-admin-action" type="button">刷新策略</button></div>
      </div>
      <div class="loom-policy-tabs" role="tablist" aria-label="模型权限范围">
        <button class="loom-policy-tab" type="button" role="tab" data-policy-tab="global" aria-selected="true">全局模型</button>
        <button class="loom-policy-tab" type="button" role="tab" data-policy-tab="groups" aria-selected="false">访问分组</button>
        <button class="loom-policy-tab" type="button" role="tab" data-policy-tab="accounts" aria-selected="false">账号控制</button>
      </div>
      <div class="loom-policy-shell">
        <div id="policyPaneGlobal" class="loom-policy-pane" data-policy-pane="global">
          <article class="account-card cards">
            <div class="account-head"><div><p class="kicker">GLOBAL POLICY</p><h2>全局模型</h2><p class="loom-policy-note">关闭模型大组或单个模型后，所有账号都会立即被硬拦截。</p></div><div class="loom-policy-toolbar"><span id="policyGlobalHint">Loading</span><button id="policyGlobalEnableAll" class="loom-admin-action" type="button">全部启用</button><button id="policyGlobalDisableAll" class="loom-admin-action is-danger" type="button">全部禁用</button></div></div>
            <div class="loom-policy-subsection-head"><strong>模型大组</strong><span>大组总开关</span></div>
            <div id="policyModelGroupGrid" class="loom-policy-grid"></div>
            <div class="loom-policy-subsection">
              <div class="loom-policy-subsection-head"><strong>逐模型控制</strong><div class="loom-policy-filter-row"><input id="policyGlobalSearch" class="loom-policy-search" type="search" placeholder="搜索模型"><select id="policyGlobalGroupFilter" class="loom-policy-select"><option value="all">全部分组</option></select></div></div>
              <div id="policyGlobalGrid" class="loom-policy-grid"></div>
            </div>
          </article>
        </div>
        <div id="policyPaneGroups" class="loom-policy-pane" data-policy-pane="groups" hidden>
          <div class="loom-policy-layout">
            <article class="account-card cards">
              <div class="loom-policy-side-head"><div><p class="kicker">ACCESS GROUPS</p><h3>访问分组</h3><small id="policyGroupCount">0 个分组</small></div></div>
              <form id="policyCreateGroupForm" class="loom-policy-create"><input id="policyCreateGroupName" type="text" maxlength="96" placeholder="新分组名称，例如 Pro"><button class="loom-admin-action" type="submit">创建</button></form>
              <div id="policyGroupList" class="loom-policy-groups"></div>
            </article>
            <article class="account-card cards" id="policyGroupPanel"><div id="policyGroupDetail" class="loom-policy-empty">先在左侧创建或选择一个分组。</div></article>
          </div>
        </div>
        <div id="policyPaneAccounts" class="loom-policy-pane" data-policy-pane="accounts" hidden>
          <div class="loom-policy-layout">
            <article class="account-card cards">
              <div class="loom-policy-side-head"><div><p class="kicker">ACCOUNT POLICY</p><h3>账号</h3><small id="policyAccountCount">0 个账号</small></div></div>
              <input id="policyAccountSearch" class="loom-policy-search" type="search" placeholder="搜索 Email / Role / Status" style="width:100%;margin-bottom:10px">
              <div id="policyAccountList" class="loom-policy-account-list"></div>
            </article>
            <article class="account-card cards"><div id="policyAccountDetail" class="loom-policy-empty">选择一个账号，进入它的独立模型控制台。</div></article>
          </div>
        </div>
      </div>
      <p id="policyError" class="loom-policy-error" role="alert"></p>`;
    anchor.parentNode.insertBefore(section, anchor);
    // admin.js may have already routed a direct #models page before this deferred
    // module was installed. Reconcile that one bootstrap race explicitly.
    if (decodeURIComponent(location.hash.replace(/^#/, '')) === 'models' && $('adminContent') && !$('adminContent').hidden) section.hidden = false;

    section.querySelector('.loom-policy-tabs').addEventListener('click', onTabClick);
    $('policyRefresh').addEventListener('click', loadPolicyFresh);
    $('policyGlobalEnableAll').addEventListener('click', () => bulkGlobal(true));
    $('policyGlobalDisableAll').addEventListener('click', () => bulkGlobal(false));
    $('policyGlobalGrid').addEventListener('change', onGlobalToggle);
    $('policyModelGroupGrid').addEventListener('change', onModelGroupToggle);
    $('policyGlobalSearch').addEventListener('input', renderGlobal);
    $('policyGlobalGroupFilter').addEventListener('change', renderGlobal);
    $('policyCreateGroupForm').addEventListener('submit', createGroup);
    $('policyGroupList').addEventListener('click', onGroupSelect);
    $('policyGroupDetail').addEventListener('change', onGroupDetailChange);
    $('policyGroupDetail').addEventListener('click', onGroupDetailClick);
    $('policyGroupDetail').addEventListener('input', event => { renderMemberFilter(); renderGroupModelFilter(event); });
    $('policyAccountSearch').addEventListener('input', renderAccounts);
    $('policyAccountList').addEventListener('click', onAccountSelect);
    $('policyAccountDetail').addEventListener('change', onAccountDetailChange);
    $('policyAccountDetail').addEventListener('click', onAccountDetailClick);
    $('policyAccountDetail').addEventListener('input', renderAccountModelFilter);
  }

  function showError(error) { const el = $('policyError'); if (el) el.textContent = error ? (error.message || String(error)) : ''; }
  function sourceLabel(decision) {
    const source = String(decision?.source || 'global');
    if (source === 'global_group') return '模型大组全局禁用';
    if (source === 'global') return '全局策略';
    if (source === 'account') return '账号总开关';
    if (source === 'user') return '账号单独规则';
    if (source === 'user_legacy') return '账号旧规则';
    if (source === 'group') return `访问分组${decision?.groups?.length ? ' · ' + decision.groups.join(', ') : ''}`;
    return source;
  }
  function modelSearchText(model) { return `${model.name || ''} ${model.model_id || ''} ${model.group_name || ''} ${model.group_id || ''}`.toLowerCase(); }

  function switchTab(tab, {scroll = false} = {}) {
    if (!['global','groups','accounts'].includes(tab)) tab = 'global';
    policyState.activeTab = tab;
    document.querySelectorAll('[data-policy-tab]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.policyTab === tab)));
    document.querySelectorAll('[data-policy-pane]').forEach(pane => { pane.hidden = pane.dataset.policyPane !== tab; });
    if (tab === 'groups' && !policyState.selectedGroupId && policyState.groups.length) selectGroup(policyState.groups[0].id).catch(showError);
    if (tab === 'accounts' && !policyState.selectedAccountId && policyState.users.length) selectAccount(policyState.users[0].id).catch(showError);
    if (scroll) $('models')?.scrollIntoView({behavior:'smooth', block:'start'});
  }
  function onTabClick(event) { const button = event.target.closest('[data-policy-tab]'); if (button) switchTab(button.dataset.policyTab); }

  function modelPageActive() { const page = $('models'); const content = $('adminContent'); return Boolean(page && content && !content.hidden && !page.hidden); }

  async function loadPolicy() {
    if (policyState.loading || !modelPageActive()) return;
    policyState.loading = true; showError(null);
    try {
      const [policy, users] = await Promise.all([policyRequest('/admin/state'), accountRequest('/admin/users')]);
      policyState.models = policy.models || []; policyState.modelGroups = policy.model_groups || []; policyState.groups = policy.groups || []; policyState.users = users.users || [];
      renderGlobal(); renderGroups(); renderAccounts();
      if (policyState.selectedGroupId && policyState.groups.some(g => Number(g.id) === Number(policyState.selectedGroupId))) await selectGroup(policyState.selectedGroupId);
      else { policyState.selectedGroupId = null; policyState.selectedGroup = null; renderGroupDetail(); }
      if (policyState.selectedAccountId && policyState.users.some(u => Number(u.id) === Number(policyState.selectedAccountId))) await selectAccount(policyState.selectedAccountId);
      else { policyState.selectedAccountId = null; policyState.selectedAccountPolicy = null; policyState.selectedAccountAccess = null; renderAccountDetail(); }
      switchTab(policyState.activeTab);
    } catch (error) { showError(error); }
    finally { policyState.loading = false; }
  }

  function renderGlobal() {
    const grid = $('policyGlobalGrid'), groupGrid = $('policyModelGroupGrid'); if (!grid || !groupGrid) return;
    const enabled = policyState.models.filter(m => m.enabled).length;
    $('policyGlobalHint').textContent = `${enabled} / ${policyState.models.length} 已启用`;
    groupGrid.innerHTML = policyState.modelGroups.map(group => `<div class="loom-policy-card"><div><strong>${escapeHtml(group.name)}</strong><small>${group.enabled ? '大组已启用' : '硬禁用'} · ${group.model_count || 0} 个模型</small></div><label class="loom-policy-switch" title="模型大组总开关"><input type="checkbox" data-global-model-group="${escapeHtml(group.id)}" ${group.enabled ? 'checked' : ''}><span></span></label></div>`).join('') || '<div class="loom-policy-empty">没有模型大组。</div>';
    const select = $('policyGlobalGroupFilter');
    const current = select.value || 'all';
    select.innerHTML = '<option value="all">全部分组</option>' + policyState.modelGroups.map(g => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`).join('');
    if ([...select.options].some(o => o.value === current)) select.value = current;
    const q = $('policyGlobalSearch').value.trim().toLowerCase(), group = select.value;
    const models = policyState.models.filter(m => (!q || modelSearchText(m).includes(q)) && (group === 'all' || String(m.group_id) === group));
    grid.innerHTML = models.map(model => `<div class="loom-policy-card"><div><strong>${escapeHtml(model.name || model.model_id)}</strong><small>${escapeHtml(model.group_name || model.group_id || '')} · ${model.enabled ? '全局可用' : '全局硬禁用'}</small></div><label class="loom-policy-switch"><input type="checkbox" data-global-model="${escapeHtml(model.model_id)}" ${model.enabled ? 'checked' : ''}><span></span></label></div>`).join('') || '<div class="loom-policy-empty">没有匹配的模型。</div>';
  }

  function renderGroups() {
    const list = $('policyGroupList'); if (!list) return;
    $('policyGroupCount').textContent = `${policyState.groups.length} 个分组`;
    list.innerHTML = policyState.groups.map(group => `<button class="loom-policy-group-button" type="button" data-group-id="${group.id}" aria-current="${Number(group.id) === Number(policyState.selectedGroupId)}"><strong>${escapeHtml(group.name)}</strong><small>${group.enabled ? '已启用' : '已暂停'} · ${group.member_count || 0} 个成员</small></button>`).join('') || '<div class="loom-policy-empty">还没有分组。<br>在上方输入名称即可创建。</div>';
  }
  async function selectGroup(id) { policyState.selectedGroupId = Number(id); renderGroups(); const payload = await policyRequest(`/admin/groups/${Number(id)}`); policyState.selectedGroup = payload.group; renderGroupDetail(); }
  function renderGroupDetail() {
    const root = $('policyGroupDetail'); if (!root) return; const group = policyState.selectedGroup;
    if (!group) { root.className = 'loom-policy-empty'; root.innerHTML = '先在左侧创建或选择一个分组。'; return; }
    root.className = '';
    const rules = new Map((group.models || []).map(item => [String(item.model_id), Boolean(item.enabled)]));
    const members = new Set((group.members || []).map(Number));
    root.innerHTML = `
      <div class="loom-policy-group-head"><div><p class="kicker">GROUP POLICY</p><h3>${escapeHtml(group.name)}</h3><p class="loom-policy-note">${group.member_count || 0} 个成员 · 关闭分组后只暂停这个分组的规则，不会停用账号。</p></div><label class="loom-policy-switch" title="启停分组"><input id="policyGroupEnabled" type="checkbox" ${group.enabled ? 'checked' : ''}><span></span></label></div>
      <div class="loom-policy-toolbar"><button class="loom-admin-action" type="button" data-group-bulk="on">全部模型允许</button><button class="loom-admin-action is-danger" type="button" data-group-bulk="off">全部模型禁止</button><button class="loom-admin-action is-danger" type="button" id="policyDeleteGroup">删除分组</button></div>
      <div class="loom-policy-subsection"><div class="loom-policy-subsection-head"><strong>组内模型权限</strong><div class="loom-policy-filter-row"><input id="policyGroupModelSearch" class="loom-policy-search" type="search" placeholder="搜索模型"><span>${policyState.models.length} 个</span></div></div><div class="loom-policy-grid">${policyState.models.map(model => { const value = rules.has(String(model.model_id)) ? rules.get(String(model.model_id)) : Boolean(model.enabled); return `<div class="loom-policy-card" data-group-model-search="${escapeHtml(modelSearchText(model))}"><div><strong>${escapeHtml(model.name || model.model_id)}</strong><small>${escapeHtml(model.group_name || '')} · ${model.enabled ? '全局可用' : '全局硬禁用'}</small></div><label class="loom-policy-switch"><input type="checkbox" data-group-model="${escapeHtml(model.model_id)}" ${value ? 'checked' : ''} ${model.enabled ? '' : 'disabled'}><span></span></label></div>`; }).join('')}</div></div>
      <div class="loom-policy-subsection"><div class="loom-policy-subsection-head"><strong>成员账号</strong><input id="policyMemberSearch" class="loom-policy-search" type="search" placeholder="搜索账号"></div><div id="policyMembers" class="loom-policy-members">${policyState.users.map(user => `<label class="loom-policy-member" data-member-search="${escapeHtml(`${user.email} ${user.role} ${user.status}`.toLowerCase())}"><div><strong>${escapeHtml(user.email)}</strong><small>${escapeHtml(user.role)} · ${escapeHtml(user.status)}</small></div><span class="loom-policy-switch"><input type="checkbox" data-group-member="${user.id}" ${members.has(Number(user.id)) ? 'checked' : ''}><span></span></span></label>`).join('')}</div></div>`;
  }
  function renderMemberFilter() { const input = $('policyMemberSearch'), members = $('policyMembers'); if (!input || !members) return; const q = input.value.trim().toLowerCase(); members.querySelectorAll('[data-member-search]').forEach(row => { row.hidden = Boolean(q) && !String(row.dataset.memberSearch || '').includes(q); }); }
  function renderGroupModelFilter() { const input = $('policyGroupModelSearch'), root = $('policyGroupDetail'); if (!input || !root) return; const q = input.value.trim().toLowerCase(); root.querySelectorAll('[data-group-model-search]').forEach(row => { row.hidden = Boolean(q) && !String(row.dataset.groupModelSearch || '').includes(q); }); }

  function renderAccounts() {
    const list = $('policyAccountList'); if (!list) return;
    const q = $('policyAccountSearch')?.value.trim().toLowerCase() || '';
    const users = policyState.users.filter(u => !q || `${u.email} ${u.display_name || ''} ${u.role} ${u.status}`.toLowerCase().includes(q));
    $('policyAccountCount').textContent = `${users.length} 个账号`;
    list.innerHTML = users.map(user => `<button class="loom-policy-account-button" type="button" data-policy-account-id="${user.id}" aria-current="${Number(user.id) === Number(policyState.selectedAccountId)}"><strong>${escapeHtml(user.email)}</strong><small>${escapeHtml(user.role)} · ${escapeHtml(user.status)}${user.display_name ? ' · ' + escapeHtml(user.display_name) : ''}</small></button>`).join('') || '<div class="loom-policy-empty">没有匹配账号。</div>';
  }
  async function selectAccount(id) {
    policyState.selectedAccountId = Number(id); renderAccounts();
    const [policyPayload, accessPayload] = await Promise.all([
      policyRequest(`/admin/users/${Number(id)}/policy`),
      accountRequest(`/admin/users/${Number(id)}/model-access`),
    ]);
    if (policyState.selectedAccountId !== Number(id)) return;
    policyState.selectedAccountPolicy = policyPayload;
    policyState.selectedAccountAccess = accessPayload.access || {enabled:true, models:[], source:'default'};
    renderAccountDetail();
  }
  function renderAccountDetail() {
    const root = $('policyAccountDetail'); if (!root) return;
    const user = policyState.users.find(u => Number(u.id) === Number(policyState.selectedAccountId));
    const payload = policyState.selectedAccountPolicy;
    if (!user || !payload) { root.className = 'loom-policy-empty'; root.innerHTML = '选择一个账号，进入它的独立模型控制台。'; return; }
    root.className = '';
    const access = payload.access || {}, accountAccess = policyState.selectedAccountAccess || {enabled:true, models:[], source:'default'};
    const rules = new Map((payload.rules || []).map(item => [String(item.model_id), Boolean(item.enabled)]));
    const decisions = new Map((access.decisions || []).map(item => [String(item.model_id), item]));
    const groups = access.groups || [];
    root.innerHTML = `
      <div class="loom-policy-account-head"><div><p class="kicker">ACCOUNT MODEL CONSOLE</p><h3>${escapeHtml(user.email)}</h3><div class="loom-policy-account-meta"><span>${escapeHtml(user.role)}</span><span>${escapeHtml(user.status)}</span><span>${groups.length ? groups.map(g => escapeHtml(g.name)).join(' · ') : '未加入 Access Group'}</span></div></div><button id="policyResetAccountRules" class="loom-admin-action" type="button">全部恢复继承</button></div>
      <div class="loom-policy-account-master"><div><strong>账号模型总开关</strong><small>${accountAccess.source === 'override' ? '这个账号已有单独的账号级设置' : '当前使用账号默认设置'} · 关闭后所有内置模型都会被硬禁用</small></div><label class="loom-policy-switch"><input id="policyAccountEnabled" type="checkbox" ${accountAccess.enabled !== false ? 'checked' : ''}><span></span></label></div>
      <div class="loom-policy-subsection"><div class="loom-policy-subsection-head"><strong>逐模型权限</strong><div class="loom-policy-filter-row"><input id="policyAccountModelSearch" class="loom-policy-search" type="search" placeholder="搜索模型"><span>${policyState.models.length} 个</span></div></div>
        <div class="loom-policy-grid">${policyState.models.map(model => {
          const id = String(model.model_id), decision = decisions.get(id) || {enabled:Boolean(model.enabled), source:'global'}, explicit = rules.has(id), explicitValue = rules.get(id), finalEnabled = Boolean(decision.enabled), hardOff = ['global','global_group','account'].includes(String(decision.source || '')) && !finalEnabled;
          return `<div class="loom-policy-card" data-account-model-search="${escapeHtml(modelSearchText(model))}" data-hard-off="${hardOff}"><div><strong>${escapeHtml(model.name || id)}</strong><small>${escapeHtml(model.group_name || '')}</small><span class="loom-policy-source"><i></i><small>${escapeHtml(sourceLabel(decision))}</small></span></div><div style="display:grid;gap:6px;justify-items:end"><span class="loom-policy-final" data-on="${finalEnabled}">${finalEnabled ? '最终允许' : '最终禁止'}</span><div class="loom-policy-model-actions"><button class="loom-policy-choice" type="button" data-account-model-inherit="${escapeHtml(id)}" aria-pressed="${!explicit}">继承</button><button class="loom-policy-choice" type="button" data-account-model-allow="${escapeHtml(id)}" aria-pressed="${explicit && explicitValue === true}">允许</button><button class="loom-policy-choice is-deny" type="button" data-account-model-deny="${escapeHtml(id)}" aria-pressed="${explicit && explicitValue === false}">禁止</button></div></div></div>`;
        }).join('')}</div>
      </div>`;
  }
  function renderAccountModelFilter() { const input = $('policyAccountModelSearch'), root = $('policyAccountDetail'); if (!input || !root) return; const q = input.value.trim().toLowerCase(); root.querySelectorAll('[data-account-model-search]').forEach(row => { row.hidden = Boolean(q) && !String(row.dataset.accountModelSearch || '').includes(q); }); }

  async function onGlobalToggle(event) { const input = event.target.closest('[data-global-model]'); if (!input) return; input.disabled = true; try { await policyRequest('/admin/global', {method:'POST', body:JSON.stringify({model_id:input.dataset.globalModel, enabled:Boolean(input.checked)})}); await loadPolicyFresh(); } catch (error) { input.checked = !input.checked; showError(error); } finally { input.disabled = false; } }
  async function onModelGroupToggle(event) { const input = event.target.closest('[data-global-model-group]'); if (!input) return; input.disabled = true; try { await policyRequest(`/admin/model-groups/${encodeURIComponent(input.dataset.globalModelGroup)}`, {method:'POST', body:JSON.stringify({enabled:Boolean(input.checked)})}); await loadPolicyFresh(); } catch (error) { input.checked = !input.checked; showError(error); } finally { input.disabled = false; } }
  async function bulkGlobal(enabled) { if (!confirm(`${enabled ? '启用' : '禁用'}全部内置模型？${enabled ? '' : ' 全局禁用会立即覆盖账号与分组设置。'}`)) return; try { await policyRequest('/admin/global/bulk', {method:'POST', body:JSON.stringify({enabled})}); await loadPolicyFresh(); } catch (error) { showError(error); } }
  async function createGroup(event) { event?.preventDefault(); const input = $('policyCreateGroupName'), name = input?.value.trim(); if (!name) { input?.focus(); return; } try { const payload = await policyRequest('/admin/groups', {method:'POST', body:JSON.stringify({name, enabled:true})}); if (input) input.value = ''; policyState.selectedGroupId = payload.group.id; await loadPolicyFresh(); switchTab('groups'); } catch (error) { showError(error); } }
  function onGroupSelect(event) { const button = event.target.closest('[data-group-id]'); if (button) selectGroup(Number(button.dataset.groupId)).catch(showError); }
  async function onGroupDetailChange(event) {
    const group = policyState.selectedGroup; if (!group) return;
    const groupToggle = event.target.closest('#policyGroupEnabled'), modelToggle = event.target.closest('[data-group-model]'), memberToggle = event.target.closest('[data-group-member]');
    try {
      if (groupToggle) { await policyRequest(`/admin/groups/${group.id}`, {method:'POST', body:JSON.stringify({enabled:Boolean(groupToggle.checked)})}); return await loadPolicyFresh(); }
      if (modelToggle) { modelToggle.disabled = true; await policyRequest(`/admin/groups/${group.id}/models`, {method:'POST', body:JSON.stringify({model_id:modelToggle.dataset.groupModel, enabled:Boolean(modelToggle.checked)})}); return await selectGroup(group.id); }
      if (memberToggle) { memberToggle.disabled = true; await policyRequest(`/admin/groups/${group.id}/members`, {method:'POST', body:JSON.stringify({user_id:Number(memberToggle.dataset.groupMember), enabled:Boolean(memberToggle.checked)})}); return await loadPolicyFresh(); }
    } catch (error) { showError(error); await selectGroup(group.id).catch(() => {}); }
  }
  async function onGroupDetailClick(event) {
    const group = policyState.selectedGroup; if (!group) return; const bulk = event.target.closest('[data-group-bulk]'), remove = event.target.closest('#policyDeleteGroup');
    try {
      if (bulk) { const enabled = bulk.dataset.groupBulk === 'on'; await policyRequest(`/admin/groups/${group.id}/models/bulk`, {method:'POST', body:JSON.stringify({enabled})}); return await selectGroup(group.id); }
      if (remove) { if (!confirm(`删除分组 “${group.name}”？账号本身不会被删除。`)) return; await policyRequest(`/admin/groups/${group.id}`, {method:'DELETE'}); policyState.selectedGroupId = null; policyState.selectedGroup = null; await loadPolicyFresh(); renderGroupDetail(); }
    } catch (error) { showError(error); }
  }
  function onAccountSelect(event) { const button = event.target.closest('[data-policy-account-id]'); if (button) selectAccount(Number(button.dataset.policyAccountId)).catch(showError); }
  async function onAccountDetailChange(event) {
    const input = event.target.closest('#policyAccountEnabled'); if (!input || !policyState.selectedAccountId) return;
    input.disabled = true;
    try {
      const current = policyState.selectedAccountAccess || {models:[]};
      await accountRequest('/admin/users/model-access', {method:'POST', body:JSON.stringify({user_id:policyState.selectedAccountId, enabled:Boolean(input.checked), models:Array.isArray(current.models) ? current.models : []})});
      await selectAccount(policyState.selectedAccountId);
    } catch (error) { showError(error); await selectAccount(policyState.selectedAccountId).catch(() => {}); }
  }
  async function onAccountDetailClick(event) {
    if (!policyState.selectedAccountId) return;
    const allow = event.target.closest('[data-account-model-allow]'), deny = event.target.closest('[data-account-model-deny]'), inherit = event.target.closest('[data-account-model-inherit]'), reset = event.target.closest('#policyResetAccountRules');
    try {
      if (reset) { if (!confirm('清除这个账号全部单独模型规则，恢复继承全局 / Access Group？')) return; await policyRequest(`/admin/users/${policyState.selectedAccountId}/models/reset`, {method:'POST', body:'{}'}); return await selectAccount(policyState.selectedAccountId); }
      const id = allow?.dataset.accountModelAllow || deny?.dataset.accountModelDeny || inherit?.dataset.accountModelInherit; if (!id) return;
      if (inherit) await policyRequest(`/admin/users/${policyState.selectedAccountId}/models/reset`, {method:'POST', body:JSON.stringify({model_ids:[id]})});
      else await policyRequest(`/admin/users/${policyState.selectedAccountId}/models`, {method:'POST', body:JSON.stringify({model_id:id, enabled:Boolean(allow)})});
      await selectAccount(policyState.selectedAccountId);
    } catch (error) { showError(error); }
  }

  async function loadPolicyFresh() { policyState.loading = false; await loadPolicy(); }

  async function renderDialogPolicy(userId) {
    policyState.dialogUserId = Number(userId);
    try {
      const payload = await policyRequest(`/admin/users/${Number(userId)}/policy`);
      if (policyState.dialogUserId !== Number(userId)) return;
      const body = $('userDetailBody'); if (!body) return; body.querySelector('.loom-policy-effective')?.remove();
      const access = payload.access || {}, rules = new Map((payload.rules || []).map(item => [String(item.model_id), Boolean(item.enabled)])), decisions = new Map((access.decisions || []).map(item => [String(item.model_id), item]));
      const groups = (access.groups || []).map(group => group.name).join(', ') || '无 Access Group';
      const section = document.createElement('section'); section.className = 'loom-policy-effective';
      section.innerHTML = `<div class="loom-policy-effective-head"><div><strong>账号模型策略</strong><div class="loom-policy-note">${escapeHtml(groups)} · 完整控制请使用“账号控制”模型控制台。</div></div><button class="loom-admin-action" type="button" data-open-account-console="${Number(userId)}">打开模型控制台</button></div><div class="loom-policy-summary"><span>${(access.decisions || []).filter(x => x.enabled).length} 个最终允许</span><span>${rules.size} 个账号单独规则</span></div>`;
      body.appendChild(section);
    } catch (error) { showError(error); }
  }

  function decorateUserRows() {
    const body = $('usersBody');
    if (!body) return;
    body.querySelectorAll('tr').forEach(row => {
      const userLink = row.querySelector('[data-user-detail]'), actions = row.querySelector('.loom-admin-actions');
      if (!userLink || !actions || actions.querySelector('[data-user-models]')) return;
      const button = document.createElement('button');
      button.className = 'loom-admin-action'; button.type = 'button'; button.textContent = '模型'; button.dataset.userModels = userLink.dataset.userDetail;
      actions.prepend(button);
    });
  }

  function installHooks() {
    decorateUserRows();
    const usersBody = $('usersBody');
    if (usersBody) new MutationObserver(decorateUserRows).observe(usersBody, {childList:true});
    document.addEventListener('click', event => {
      const direct = event.target.closest?.('[data-user-models],[data-open-account-console]');
      if (direct) {
        const id = Number(direct.dataset.userModels || direct.dataset.openAccountConsole); if (!id) return;
        event.preventDefault(); $('userDialog')?.close(); location.hash = 'models';
        window.setTimeout(() => { switchTab('accounts'); loadPolicyFresh().then(() => selectAccount(id)).catch(showError); }, 0); return;
      }
      const user = event.target.closest?.('[data-user-detail]');
      if (user) { policyState.dialogUserId = Number(user.dataset.userDetail); setTimeout(() => renderDialogPolicy(policyState.dialogUserId), 180); }
    }, true);
    const detailBody = $('userDetailBody'), dialog = $('userDialog');
    if (detailBody) new MutationObserver(() => { if (policyState.dialogUserId && dialog?.open && !detailBody.querySelector('.loom-policy-effective')) setTimeout(() => renderDialogPolicy(policyState.dialogUserId), 30); }).observe(detailBody, {childList:true});
    window.addEventListener('loom-admin:model-page-request', () => loadPolicyFresh());
    const adminContent = $('adminContent');
    if (adminContent) new MutationObserver(() => { if (modelPageActive()) loadPolicyFresh(); }).observe(adminContent, {attributes:true, attributeFilter:['hidden']});
  }

  installUI(); installHooks();
  if (modelPageActive()) loadPolicyFresh();
})();