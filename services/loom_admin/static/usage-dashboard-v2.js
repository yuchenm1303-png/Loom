(() => {
  const $ = (id) => document.getElementById(id);
  const compact = (value) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value || 0));
  const number = (value) => new Intl.NumberFormat('en-US').format(Number(value || 0));
  const percent = (value) => `${Number.isFinite(value) ? value.toFixed(value >= 10 ? 1 : 2) : '0.0'}%`;
  const fmtDuration = (seconds) => {
    const s = Math.max(0, Math.round(Number(seconds || 0)));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));

  let usageData = null;
  let requestInFlight = null;
  let refreshRequested = false;

  function currentRange() {
    return document.querySelector('#usageRangeControl [data-range][aria-pressed="true"]')?.dataset.range || '24h';
  }

  function rangeLabel(range) {
    if (range === '7d') return 'LAST 7 DAYS';
    if (range === '30d') return 'LAST 30 DAYS';
    return 'LAST 24 HOURS';
  }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function setWidth(id, value) {
    const el = $(id);
    if (el) el.style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
  }

  function renderRanks(rootId, items, kind) {
    const root = $(rootId);
    if (!root) return;
    if (!items.length) {
      root.innerHTML = '<div class="loom-admin-usage-empty">当前 30 天还没有可展示的用量数据。</div>';
      return;
    }
    const total = Math.max(1, items.reduce((sum, item) => sum + Number(item.total_tokens || 0), 0));
    const max = Math.max(1, ...items.map((item) => Number(item.total_tokens || 0)));
    root.innerHTML = items.slice(0, 10).map((item) => {
      const tokens = Number(item.total_tokens || 0);
      const share = (tokens / total) * 100;
      const metric = Math.max(tokens > 0 ? 3 : 0, (tokens / max) * 100);
      const identity = kind === 'model' ? (item.model || 'unknown') : (item.email || 'unknown');
      const provider = kind === 'model' ? `<span class="loom-admin-usage-provider">${escapeHtml(item.provider || 'local')}</span>` : '';
      const detail = `${provider}<span>${number(item.runs)} runs</span>`;
      return `<div class="loom-admin-usage-rank-row">
        <span class="loom-admin-usage-rank" aria-hidden="true"></span>
        <div class="loom-admin-usage-rank-copy"><strong title="${escapeHtml(identity)}">${escapeHtml(identity)}</strong><small>${detail}</small></div>
        <div class="loom-admin-usage-rank-value"><strong>${compact(tokens)}</strong><small>${percent(share)} share</small></div>
        <i class="loom-admin-usage-rank-bar" style="--metric:${metric.toFixed(2)}%"></i>
      </div>`;
    }).join('');
  }

  function renderTrend(daily) {
    const root = $('usageDailyChart');
    if (!root) return;
    if (!daily.length) {
      root.innerHTML = '<div class="loom-admin-usage-empty">暂无 30 天趋势数据。</div>';
      setText('usageTrendTotal', '0');
      setText('usageTrendRuns', '0 runs');
      setText('usagePeakDay', '—');
      return;
    }
    const max = Math.max(1, ...daily.map((item) => Number(item.total_tokens || 0)));
    const total = daily.reduce((sum, item) => sum + Number(item.total_tokens || 0), 0);
    const runs = daily.reduce((sum, item) => sum + Number(item.runs || 0), 0);
    const peak = daily.reduce((best, item) => Number(item.total_tokens || 0) > Number(best?.total_tokens || -1) ? item : best, null);
    setText('usageTrendTotal', compact(total));
    setText('usageTrendRuns', `${number(runs)} runs`);
    setText('usagePeakDay', peak ? `${String(peak.day || '').slice(5)} · ${compact(peak.total_tokens)}` : '—');
    root.innerHTML = daily.map((item, index) => {
      const tokens = Number(item.total_tokens || 0);
      const height = Math.max(tokens > 0 ? 3 : 0, (tokens / max) * 100);
      const date = String(item.day || '');
      const shortDate = date.slice(5);
      const label = index === daily.length - 1 || index % 5 === 0 ? shortDate : '';
      const tip = `${date}\n${number(tokens)} tokens · ${number(item.runs)} runs`;
      return `<div class="loom-admin-usage-bar" data-tip="${escapeHtml(tip)}" style="--bar-height:${height.toFixed(2)}%"><i style="height:${height.toFixed(2)}%"></i><span>${escapeHtml(label)}</span></div>`;
    }).join('');
  }

  function renderUsage(data) {
    if (!data || !$('usage')) return;
    const range = currentRange();
    const summary = data.ranges?.[range] || {};
    const total = Number(summary.total_tokens || 0);
    const input = Number(summary.input_tokens || 0);
    const output = Number(summary.output_tokens || 0);
    const runs = Number(summary.runs || 0);
    const tokenSum = input + output;
    const inputShare = tokenSum > 0 ? (input / tokenSum) * 100 : 0;
    const outputShare = tokenSum > 0 ? (output / tokenSum) * 100 : 0;
    const tokensPerRun = runs > 0 ? total / runs : 0;
    const ratio = output > 0 ? input / output : 0;
    const models = Array.isArray(data.models) ? data.models : [];
    const users = Array.isArray(data.users) ? data.users : [];
    const daily = Array.isArray(data.daily) ? data.daily : [];

    setText('usageSelectedRange', rangeLabel(range));
    setText('usageExactTokens', `${number(total)} exact`);
    setText('usageTokensPerRun', runs ? compact(tokensPerRun) : '—');
    setText('usageOutputShare', tokenSum ? percent(outputShare) : '—');
    setText('usageInputOutputRatio', output ? `${ratio.toFixed(ratio >= 10 ? 1 : 2)} : 1` : '—');
    setText('usageModelCount', number(models.length));
    setText('usageAccountCount', number(users.length));
    setText('usageInputShare', percent(inputShare));
    setText('usageOutputShareDetail', percent(outputShare));
    setText('usageInputExact', number(input));
    setText('usageOutputExact', number(output));
    setWidth('usageInputBar', inputShare);
    setWidth('usageOutputBar', outputShare);
    setText('usageCompositionTotal', compact(tokenSum));
    setText('usageCompositionRange', rangeLabel(range));
    setText('usageRangeRunsMeta', runs ? `${compact(tokensPerRun)} tokens / run` : 'No completed runs');
    setText('usageDurationMeta', `${number(runs)} completed runs`);

    const donut = $('usageCompositionDonut');
    if (donut) donut.style.setProperty('--output-share', `${(outputShare / 100) * 360}deg`);

    if (data.generated_at) {
      const time = new Date(Number(data.generated_at) * 1000).toLocaleString('zh-CN', { hour12: false });
      setText('usageUpdatedAt', `UPDATED ${time}`);
    }

    renderTrend(daily);
    renderRanks('modelsList', models, 'model');
    renderRanks('usageUsersList', users, 'user');
  }

  async function loadUsage(force = false) {
    if (!force && usageData) {
      renderUsage(usageData);
      return usageData;
    }
    if (requestInFlight) return requestInFlight;
    const access = sessionStorage.getItem('loom_admin_access') || '';
    if (!access) return null;
    requestInFlight = fetch('/api/v1/admin/usage', {
      headers: { Authorization: `Bearer ${access}` },
      credentials: 'same-origin',
      cache: 'no-store',
    }).then(async (response) => {
      if (!response.ok) throw new Error(`usage ${response.status}`);
      usageData = await response.json();
      renderUsage(usageData);
      return usageData;
    }).catch(() => null).finally(() => {
      requestInFlight = null;
      if (refreshRequested) {
        refreshRequested = false;
        void loadUsage(true);
      }
    });
    return requestInFlight;
  }

  function scheduleRender() {
    queueMicrotask(() => {
      if (usageData) renderUsage(usageData);
      else void loadUsage(false);
    });
  }

  function init() {
    const section = $('usage');
    if (!section) return;

    $('usageRangeControl')?.addEventListener('click', (event) => {
      if (!event.target.closest('[data-range]')) return;
      setTimeout(scheduleRender, 0);
    });

    $('refreshButton')?.addEventListener('click', () => {
      if (location.hash !== '#usage') return;
      usageData = null;
      if (requestInFlight) refreshRequested = true;
      else setTimeout(() => void loadUsage(true), 80);
    });

    window.addEventListener('loom-admin:pagechange', (event) => {
      if (event.detail?.page === 'usage') setTimeout(() => void loadUsage(false), 0);
    });

    const mutationTarget = $('usageTokens');
    if (mutationTarget) {
      new MutationObserver(() => scheduleRender()).observe(mutationTarget, { childList: true, subtree: true, characterData: true });
    }

    if (location.hash === '#usage') void loadUsage(false);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
