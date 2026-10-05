(() => {
  const POLICY_API = '/policy/v1';
  const ACCOUNT_API = '/api/v1';
  const $ = (id) => document.getElementById(id);
  const policyState = {
    models: [], modelGroups: [], groups: [], users: [],
    selectedGroupId: null, selectedGroup: null,
    selectedAccountId: null, selectedAccountPolicy: null, selectedAccountAccess: null,
    dialogUserId: null, activeTab: 'global', loading: false,
    accountFilters: {query:'', group:'all', status:'all', view:'comfortable'},
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

  function installUI() {
    if ($('models')) return;
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
    $('policyAccountDetail').addEventListener('change', event => { onAccountDetailChange(event); onAccountFilterChange(event); });
    $('policyAccountDetail').addEventListener('click', onAccountDetailClick);
    $('policyAccountDetail').addEventListener('input', onAccountFilterInput);
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
  function accountModelDescriptor(model, rules, decisions) {
    const id = String(model.model_id);
    const decision = decisions.get(id) || {enabled:Boolean(model.enabled), source:'global'};
    const explicit = rules.has(id);
    const explicitValue = rules.get(id);
    const finalEnabled = Boolean(decision.enabled);
    const source = String(decision.source || 'global');
    const hardOff = ['global','global_group','account'].includes(source) && !finalEnabled;
    return {model, id, decision, explicit, explicitValue, finalEnabled, source, hardOff};
  }

  function renderAccountDetail() {
    const root = $('policyAccountDetail'); if (!root) return;
    const user = policyState.users.find(u => Number(u.id) === Number(policyState.selectedAccountId));
    const payload = policyState.selectedAccountPolicy;
    if (!user || !payload) { root.className = 'loom-policy-empty'; root.innerHTML = '选择一个账号，进入它的独立模型控制台。'; return; }
    root.className = '';
    const access = payload.access || {};
    const accountAccess = policyState.selectedAccountAccess || {enabled:true, models:[], source:'default'};
    const rules = new Map((payload.rules || []).map(item => [String(item.model_id), Boolean(item.enabled)]));
    const decisions = new Map((access.decisions || []).map(item => [String(item.model_id), item]));
    const descriptors = policyState.models.map(model => accountModelDescriptor(model, rules, decisions));
    const memberships = Array.isArray(payload.memberships) ? payload.memberships : [];
    const allowedCount = descriptors.filter(item => item.finalEnabled).length;
    const blockedCount = descriptors.length - allowedCount;
    const explicitCount = descriptors.filter(item => item.explicit).length;
    const hardCount = descriptors.filter(item => item.hardOff).length;
    const activeGroups = memberships.filter(item => item.member && item.enabled);
    const pausedMemberships = memberships.filter(item => item.member && !item.enabled);

    const providerMap = new Map();
    for (const item of descriptors) {
      const key = String(item.model.group_id || 'other');
      if (!providerMap.has(key)) providerMap.set(key, {id:key, name:item.model.group_name || key || 'Other', models:[]});
      providerMap.get(key).models.push(item);
    }
    for (const group of policyState.modelGroups) {
      const key = String(group.id);
      if (providerMap.has(key)) providerMap.get(key).name = group.name || providerMap.get(key).name;
    }
    const providers = [...providerMap.values()];
    const providerOptions = providers.map(provider => `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.name)}</option>`).join('');
    const groupSummary = activeGroups.length ? activeGroups.map(group => escapeHtml(group.name)).join(' · ') : '未加入启用中的 Access Group';
    const membershipMarkup = memberships.length
      ? memberships.map(group => `<button class="loom-policy-membership-chip" type="button" data-account-group-toggle="${group.id}" data-paused="${!group.enabled}" aria-pressed="${Boolean(group.member)}" title="${group.enabled ? '点击加入或移出该 Access Group' : '该分组已暂停；成员关系仍可编辑'}">${escapeHtml(group.name)}${group.enabled ? '' : ' · 已暂停'}</button>`).join('')
      : '<span class="loom-policy-note">还没有 Access Group。可先在“访问分组”中创建。</span>';

    const providerMarkup = providers.map(provider => {
      const providerAllowed = provider.models.filter(item => item.finalEnabled).length;
      const cards = provider.models.map(item => {
        const source = sourceLabel(item.decision);
        return `<article class="loom-policy-account-model" data-account-model-id="${escapeHtml(item.id)}" data-account-model-search="${escapeHtml(modelSearchText(item.model))}" data-account-model-group="${escapeHtml(provider.id)}" data-account-model-state="${item.finalEnabled ? 'allowed' : 'blocked'}" data-account-model-explicit="${item.explicit}" data-account-model-hard="${item.hardOff}" data-account-model-source="${escapeHtml(item.source)}" data-final="${item.finalEnabled ? 'allowed' : 'blocked'}" data-explicit="${item.explicit}" data-hard="${item.hardOff}">
          <div class="loom-policy-account-model-top"><div class="loom-policy-account-model-title"><strong>${escapeHtml(item.model.name || item.id)}</strong><small>${escapeHtml(provider.name)}</small><small class="loom-policy-account-model-id">${escapeHtml(item.id)}</small></div><span class="loom-policy-final" data-on="${item.finalEnabled}">${item.finalEnabled ? '最终允许' : '最终禁止'}</span></div>
          <div class="loom-policy-account-model-reason"><i></i><span>${escapeHtml(source)}${item.hardOff ? ' · 硬限制，账号规则无法覆盖' : item.explicit ? ' · 账号显式覆盖' : ' · 当前继承'}</span></div>
          <div class="loom-policy-account-model-bottom"><span class="loom-policy-note">${item.explicit ? (item.explicitValue ? '单独允许' : '单独禁止') : '继承策略'}</span><div class="loom-policy-model-actions"><button class="loom-policy-choice" type="button" data-account-model-inherit="${escapeHtml(item.id)}" aria-pressed="${!item.explicit}">继承</button><button class="loom-policy-choice" type="button" data-account-model-allow="${escapeHtml(item.id)}" aria-pressed="${item.explicit && item.explicitValue === true}">允许</button><button class="loom-policy-choice is-deny" type="button" data-account-model-deny="${escapeHtml(item.id)}" aria-pressed="${item.explicit && item.explicitValue === false}">禁止</button></div></div>
        </article>`;
      }).join('');
      return `<section class="loom-policy-provider-section" data-provider-section="${escapeHtml(provider.id)}"><header class="loom-policy-provider-head"><div><strong>${escapeHtml(provider.name)}</strong><small>${providerAllowed} / ${provider.models.length} 最终允许 · ${provider.models.filter(item => item.explicit).length} 个账号覆盖</small></div><div class="loom-policy-provider-actions"><button class="loom-policy-choice" type="button" data-account-provider-action="inherit" data-provider-id="${escapeHtml(provider.id)}">全部继承</button><button class="loom-policy-choice" type="button" data-account-provider-action="allow" data-provider-id="${escapeHtml(provider.id)}">全部允许</button><button class="loom-policy-choice is-deny" type="button" data-account-provider-action="deny" data-provider-id="${escapeHtml(provider.id)}">全部禁止</button></div></header><div class="loom-policy-account-grid" data-provider-grid="${escapeHtml(provider.id)}" data-view="${escapeHtml(policyState.accountFilters.view || 'comfortable')}">${cards}</div></section>`;
    }).join('');

    root.innerHTML = `
      <div class="loom-policy-account-head"><div><p class="kicker">ACCOUNT MODEL CONSOLE</p><h3>${escapeHtml(user.email)}</h3><div class="loom-policy-account-meta"><span>${escapeHtml(user.role)}</span><span>${escapeHtml(user.status)}</span><span>${groupSummary}</span>${pausedMemberships.length ? `<span>${pausedMemberships.length} 个已暂停分组成员关系</span>` : ''}</div></div><button id="policyResetAccountRules" class="loom-admin-action" type="button">全部恢复继承</button></div>
      <div class="loom-policy-account-master"><div><strong>账号模型总开关</strong><small>${accountAccess.source === 'override' ? '这个账号已有单独的账号级设置' : '当前使用账号默认设置'} · 关闭后所有内置模型都会被硬禁用</small></div><label class="loom-policy-switch"><input id="policyAccountEnabled" type="checkbox" ${accountAccess.enabled !== false ? 'checked' : ''}><span></span></label></div>
      <div class="loom-policy-membership-panel"><div class="loom-policy-membership-head"><strong>Access Group 成员关系</strong><span>点击标签即可加入 / 移出，不必切换页面</span></div><div class="loom-policy-membership-chips">${membershipMarkup}</div></div>
      <div class="loom-policy-account-stats"><div class="loom-policy-account-stat" data-tone="allow"><span>FINAL ALLOW</span><strong>${allowedCount}</strong><small>最终可使用</small></div><div class="loom-policy-account-stat" data-tone="deny"><span>FINAL BLOCK</span><strong>${blockedCount}</strong><small>最终被禁止</small></div><div class="loom-policy-account-stat" data-tone="override"><span>OVERRIDES</span><strong>${explicitCount}</strong><small>账号单独规则</small></div><div class="loom-policy-account-stat" data-tone="hard"><span>HARD DENY</span><strong>${hardCount}</strong><small>全局 / 账号总开关硬限制</small></div></div>
      <div class="loom-policy-subsection">
        <div class="loom-policy-account-controls"><div class="loom-policy-account-filters"><input id="policyAccountModelSearch" class="loom-policy-search" type="search" placeholder="搜索模型 / Provider / Model ID" value="${escapeHtml(policyState.accountFilters.query || '')}"><select id="policyAccountGroupFilter" class="loom-policy-select"><option value="all">全部 Provider</option>${providerOptions}</select><select id="policyAccountStatusFilter" class="loom-policy-select"><option value="all">全部状态</option><option value="allowed">最终允许</option><option value="blocked">最终禁止</option><option value="override">仅账号覆盖</option><option value="inherit">仅继承</option><option value="hard">仅硬限制</option></select><button class="loom-admin-action" type="button" data-account-filter-reset>清除筛选</button></div><div class="loom-policy-view-toggle" aria-label="视图密度"><button type="button" data-account-view="comfortable" aria-pressed="${policyState.accountFilters.view !== 'compact'}">详细</button><button type="button" data-account-view="compact" aria-pressed="${policyState.accountFilters.view === 'compact'}">紧凑</button></div><span id="policyAccountVisibleCount" class="loom-policy-note">${descriptors.length} / ${descriptors.length} 个模型</span></div>
        <div class="loom-policy-bulkbar"><span>批量操作只作用于当前筛选结果，操作前会确认影响数量。</span><div class="loom-policy-toolbar"><button class="loom-admin-action" type="button" data-account-bulk="inherit">筛选结果继承</button><button class="loom-admin-action" type="button" data-account-bulk="allow">筛选结果允许</button><button class="loom-admin-action is-danger" type="button" data-account-bulk="deny">筛选结果禁止</button></div></div>
        <div id="policyAccountProviderStack" class="loom-policy-provider-stack">${providerMarkup}</div><div id="policyAccountVisibleEmpty" class="loom-policy-visible-empty">当前筛选条件下没有模型。</div>
      </div>`;
    const groupSelect = $('policyAccountGroupFilter'); if (groupSelect && [...groupSelect.options].some(option => option.value === policyState.accountFilters.group)) groupSelect.value = policyState.accountFilters.group;
    const statusSelect = $('policyAccountStatusFilter'); if (statusSelect && [...statusSelect.options].some(option => option.value === policyState.accountFilters.status)) statusSelect.value = policyState.accountFilters.status;
    renderAccountModelFilter();
  }

  function syncAccountFilterState() {
    const search = $('policyAccountModelSearch'), group = $('policyAccountGroupFilter'), status = $('policyAccountStatusFilter');
    if (search) policyState.accountFilters.query = search.value.trim().toLowerCase();
    if (group) policyState.accountFilters.group = group.value || 'all';
    if (status) policyState.accountFilters.status = status.value || 'all';
  }
  function renderAccountModelFilter() {
    const root = $('policyAccountDetail'); if (!root) return;
    syncAccountFilterState();
    const {query, group, status, view} = policyState.accountFilters;
    let visible = 0;
    root.querySelectorAll('[data-account-model-id]').forEach(card => {
      const matchesQuery = !query || String(card.dataset.accountModelSearch || '').includes(query);
      const matchesGroup = group === 'all' || String(card.dataset.accountModelGroup || '') === group;
      const state = String(card.dataset.accountModelState || '');
      const explicit = card.dataset.accountModelExplicit === 'true';
      const hard = card.dataset.accountModelHard === 'true';
      let matchesStatus = true;
      if (status === 'allowed') matchesStatus = state === 'allowed'; else if (status === 'blocked') matchesStatus = state === 'blocked'; else if (status === 'override') matchesStatus = explicit; else if (status === 'inherit') matchesStatus = !explicit; else if (status === 'hard') matchesStatus = hard;
      card.hidden = !(matchesQuery && matchesGroup && matchesStatus); if (!card.hidden) visible += 1;
    });
    root.querySelectorAll('[data-provider-section]').forEach(section => { section.hidden = ![...section.querySelectorAll('[data-account-model-id]')].some(card => !card.hidden); });
    root.querySelectorAll('[data-provider-grid]').forEach(grid => { grid.dataset.view = view || 'comfortable'; });
    root.querySelectorAll('[data-account-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.accountView === (view || 'comfortable'))));
    const count = $('policyAccountVisibleCount'); if (count) count.textContent = `${visible} / ${policyState.models.length} 个模型`;
    const empty = $('policyAccountVisibleEmpty'); if (empty) empty.dataset.visible = String(visible === 0);
  }
  function onAccountFilterInput(event) { if (event.target.closest('#policyAccountModelSearch')) renderAccountModelFilter(); }
  function onAccountFilterChange(event) { if (event.target.closest('#policyAccountGroupFilter,#policyAccountStatusFilter')) renderAccountModelFilter(); }
  function visibleAccountModelIds() { const root = $('policyAccountDetail'); if (!root) return []; return [...root.querySelectorAll('[data-account-model-id]')].filter(card => !card.hidden).map(card => String(card.dataset.accountModelId || '')).filter(Boolean); }
  function providerModelIds(providerId) { return policyState.models.filter(model => String(model.group_id || 'other') === String(providerId)).map(model => String(model.model_id)); }
  async function applyAccountBulk(action, modelIds, label = '当前筛选') {
    if (!policyState.selectedAccountId) return;
    const ids = [...new Set((modelIds || []).map(String).filter(Boolean))]; if (!ids.length) return;
    const actionLabel = action === 'inherit' ? '恢复继承' : action === 'allow' ? '设为允许' : '设为禁止';
    if (!confirm(`${actionLabel} ${label}中的 ${ids.length} 个模型？`)) return;
    showError(null);
    try { if (action === 'inherit') await policyRequest(`/admin/users/${policyState.selectedAccountId}/models/reset`, {method:'POST', body:JSON.stringify({model_ids:ids})}); else await policyRequest(`/admin/users/${policyState.selectedAccountId}/models/bulk`, {method:'POST', body:JSON.stringify({model_ids:ids, enabled:action === 'allow'})}); await selectAccount(policyState.selectedAccountId); } catch (error) { showError(error); }
  }

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
    const reset = event.target.closest('#policyResetAccountRules');
    const filterReset = event.target.closest('[data-account-filter-reset]');
    const view = event.target.closest('[data-account-view]');
    const membership = event.target.closest('[data-account-group-toggle]');
    const bulk = event.target.closest('[data-account-bulk]');
    const providerBulk = event.target.closest('[data-account-provider-action]');
    const allow = event.target.closest('[data-account-model-allow]');
    const deny = event.target.closest('[data-account-model-deny]');
    const inherit = event.target.closest('[data-account-model-inherit]');
    try {
      if (filterReset) {
        policyState.accountFilters = {...policyState.accountFilters, query:'', group:'all', status:'all'};
        const search = $('policyAccountModelSearch'), group = $('policyAccountGroupFilter'), status = $('policyAccountStatusFilter');
        if (search) search.value = ''; if (group) group.value = 'all'; if (status) status.value = 'all';
        renderAccountModelFilter(); return;
      }
      if (view) { policyState.accountFilters.view = view.dataset.accountView === 'compact' ? 'compact' : 'comfortable'; renderAccountModelFilter(); return; }
      if (membership) {
        membership.disabled = true;
        const groupId = Number(membership.dataset.accountGroupToggle), enabled = membership.getAttribute('aria-pressed') !== 'true';
        await policyRequest(`/admin/groups/${groupId}/members`, {method:'POST', body:JSON.stringify({user_id:policyState.selectedAccountId, enabled})});
        return await selectAccount(policyState.selectedAccountId);
      }
      if (bulk) return await applyAccountBulk(bulk.dataset.accountBulk, visibleAccountModelIds(), '当前筛选');
      if (providerBulk) {
        const providerId = providerBulk.dataset.providerId;
        const provider = policyState.modelGroups.find(item => String(item.id) === String(providerId));
        return await applyAccountBulk(providerBulk.dataset.accountProviderAction, providerModelIds(providerId), provider?.name || providerId);
      }
      if (reset) {
        if (!confirm('清除这个账号全部单独模型规则，恢复继承全局 / Access Group？')) return;
        await policyRequest(`/admin/users/${policyState.selectedAccountId}/models/reset`, {method:'POST', body:'{}'});
        return await selectAccount(policyState.selectedAccountId);
      }
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