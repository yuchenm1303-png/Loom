(() => {
  const list = document.getElementById('toolsList');
  if (!list) return;

  const panelHead = list.closest('.usage-ops-panel')?.querySelector('.loom-admin-panel-head');
  if (!panelHead) return;

  let search = document.getElementById('toolSearch');
  let sort = document.getElementById('toolSort');
  let hint = document.getElementById('toolsHint');

  if (!search || !sort || !hint) {
    const toolbar = document.createElement('div');
    toolbar.className = 'loom-admin-tool-toolbar';

    search = document.createElement('input');
    search.id = 'toolSearch';
    search.className = 'loom-admin-tool-search';
    search.type = 'search';
    search.placeholder = '搜索工具名称';
    search.autocomplete = 'off';
    search.setAttribute('aria-label', '搜索工具');

    sort = document.createElement('select');
    sort.id = 'toolSort';
    sort.className = 'loom-admin-tool-sort';
    sort.setAttribute('aria-label', '工具排序');
    sort.innerHTML = '<option value="calls">按调用次数</option><option value="recent">按最近调用</option><option value="name">按名称</option>';

    hint = document.createElement('span');
    hint.id = 'toolsHint';
    hint.textContent = '等待 telemetry';

    toolbar.append(search, sort, hint);
    panelHead.appendChild(toolbar);
  }

  let records = [];
  let syncQueued = false;

  const numberFormatter = new Intl.NumberFormat('en-US');
  const parseCount = (value) => {
    const numeric = String(value || '').replace(/[^0-9.-]/g, '');
    const parsed = Number(numeric);
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const parseRenderedTime = (value) => {
    const parts = String(value || '').match(/\d+/g)?.map(Number) || [];
    if (parts.length < 3) return 0;
    const [year, month, day, hour = 0, minute = 0, second = 0] = parts;
    const timestamp = new Date(year, month - 1, day, hour, minute, second).getTime();
    return Number.isFinite(timestamp) ? timestamp : 0;
  };

  const namespaceFor = (name) => {
    const match = String(name || '').match(/^([^.:/]+)[.:/]/);
    if (match?.[1]) return match[1];
    const underscore = String(name || '').match(/^([a-z0-9]+)_/i);
    return underscore?.[1] || 'tool';
  };

  const totalTelemetryCalls = () => {
    const total = parseCount(document.getElementById('toolCallsTotal')?.textContent);
    if (total > 0) return total;
    return records.reduce((sum, item) => sum + item.calls, 0);
  };

  function readNativeRows() {
    const rows = [...list.querySelectorAll(':scope > .loom-admin-tool-row')];
    if (!rows.length) return false;

    records = rows.map((row) => {
      const name = row.querySelector('strong')?.textContent?.trim() || 'unknown';
      const rawLast = row.querySelector('small')?.textContent?.trim() || 'Last · —';
      const lastLabel = rawLast.replace(/^Last\s*·\s*/i, '') || '—';
      return {
        name,
        namespace: namespaceFor(name),
        calls: parseCount(row.querySelector(':scope > span')?.textContent),
        lastLabel,
        lastAt: lastLabel === '—' ? 0 : parseRenderedTime(lastLabel),
        rank: 0,
      };
    });

    const ranked = [...records].sort((a, b) =>
      b.calls - a.calls || b.lastAt - a.lastAt || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
    );
    ranked.forEach((item, index) => { item.rank = index + 1; });
    render();
    return true;
  }

  function buildCard(item, total) {
    const share = total > 0 ? Math.max(0, Math.min(100, (item.calls / total) * 100)) : 0;
    const card = document.createElement('article');
    card.className = 'loom-admin-tool-card';

    const top = document.createElement('div');
    top.className = 'loom-admin-tool-card-main';

    const rank = document.createElement('span');
    rank.className = 'loom-admin-tool-rank';
    rank.textContent = `#${item.rank}`;
    rank.setAttribute('aria-label', `Usage rank ${item.rank}`);

    const identity = document.createElement('div');
    identity.className = 'loom-admin-tool-identity';
    const title = document.createElement('strong');
    title.textContent = item.name;
    title.title = item.name;
    const meta = document.createElement('div');
    meta.className = 'loom-admin-tool-meta';
    const tag = document.createElement('span');
    tag.className = 'loom-admin-tool-tag';
    tag.textContent = item.namespace;
    tag.title = item.namespace === 'tool' ? 'Tool' : `Namespace · ${item.namespace}`;
    const last = document.createElement('small');
    last.textContent = `Last · ${item.lastLabel}`;
    meta.append(tag, last);
    identity.append(title, meta);

    const usage = document.createElement('div');
    usage.className = 'loom-admin-tool-usage';
    const calls = document.createElement('strong');
    calls.textContent = numberFormatter.format(item.calls);
    const callsLabel = document.createElement('span');
    callsLabel.textContent = 'calls';
    const shareLabel = document.createElement('small');
    shareLabel.textContent = `${share.toFixed(1)}%`;
    shareLabel.title = 'Share of recorded tool calls in the current 30D telemetry window';
    usage.append(calls, callsLabel, shareLabel);

    top.append(rank, identity, usage);

    const track = document.createElement('div');
    track.className = 'loom-admin-tool-share-track';
    track.title = `${item.name} · ${share.toFixed(1)}% of recorded calls`;
    const fill = document.createElement('i');
    fill.style.width = `${share}%`;
    track.appendChild(fill);

    card.append(top, track);
    return card;
  }

  function render() {
    const query = search.value.trim().toLocaleLowerCase();
    let filtered = records.filter((item) => {
      if (!query) return true;
      return `${item.name} ${item.namespace}`.toLocaleLowerCase().includes(query);
    });

    if (sort.value === 'recent') {
      filtered = [...filtered].sort((a, b) => b.lastAt - a.lastAt || b.calls - a.calls || a.name.localeCompare(b.name));
    } else if (sort.value === 'name') {
      filtered = [...filtered].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
    } else {
      filtered = [...filtered].sort((a, b) => b.calls - a.calls || b.lastAt - a.lastAt || a.name.localeCompare(b.name));
    }

    const total = totalTelemetryCalls();
    const shownCalls = filtered.reduce((sum, item) => sum + item.calls, 0);
    hint.textContent = `显示 ${filtered.length} / ${records.length} · ${numberFormatter.format(shownCalls)} 次调用`;
    list.classList.add('is-enhanced');

    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'loom-admin-tool-empty';
      empty.textContent = records.length && query ? `没有匹配“${search.value.trim()}”的工具。` : '还没有工具调用 telemetry。';
      list.replaceChildren(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    filtered.forEach((item) => fragment.appendChild(buildCard(item, total)));
    list.replaceChildren(fragment);
  }

  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(() => {
      syncQueued = false;
      if (readNativeRows()) return;
      const nativeEmpty = list.querySelector(':scope > .loom-admin-empty-block');
      if (nativeEmpty) {
        records = [];
        render();
      }
    });
  }

  search.addEventListener('input', render);
  sort.addEventListener('change', render);

  const observer = new MutationObserver(scheduleSync);
  observer.observe(list, { childList: true });
  scheduleSync();
})();
