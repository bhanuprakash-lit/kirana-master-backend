import './style.css';
import { configure, isConfigured, api } from './api.js';

// ── Toast ─────────────────────────────────────────────────────────────────────

function toast(msg, type = 'success') {
  const c = document.getElementById('toast-container');
  const el = document.createElement('div');
  const colors = { success: 'bg-emerald-600', error: 'bg-red-600', info: 'bg-slate-700' };
  el.className = `toast pointer-events-auto px-4 py-3 rounded-lg text-white text-sm font-medium shadow-lg ${colors[type] ?? colors.info}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── Session ───────────────────────────────────────────────────────────────────

function loadSession() {
  const url = sessionStorage.getItem('kirana_url');
  const key = sessionStorage.getItem('kirana_key');
  if (url && key) configure(url, key);
}

function saveSession(url, key) {
  sessionStorage.setItem('kirana_url', url);
  sessionStorage.setItem('kirana_key', key);
  configure(url, key);
}

function clearSession() {
  sessionStorage.removeItem('kirana_url');
  sessionStorage.removeItem('kirana_key');
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

function el(tag, cls, inner) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (inner) e.innerHTML = inner;
  return e;
}

function formatDate(val) {
  if (!val) return '—';
  return new Date(val).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function tierBadge(tier) {
  const map = {
    pending_trial: ['Pending',  'bg-amber-100 text-amber-800'],
    trial:         ['Trial',    'bg-blue-100 text-blue-800'],
    basic:         ['Basic',    'bg-indigo-100 text-indigo-800'],
    pro:           ['Pro',      'bg-purple-100 text-purple-800'],
    none:          ['None',     'bg-slate-100 text-slate-600'],
  };
  const [label, cls] = map[tier] ?? ['Unknown', 'bg-slate-100 text-slate-600'];
  return `<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${cls}">${label}</span>`;
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Login screen ──────────────────────────────────────────────────────────────

function renderLogin() {
  const app = document.getElementById('app');
  app.innerHTML = '';

  const wrap = el('div', 'min-h-screen flex items-center justify-center p-4');
  const card = el('div', 'bg-white rounded-2xl shadow-xl p-8 w-full max-w-md');
  card.innerHTML = `
    <div class="flex items-center gap-3 mb-8">
      <span class="text-4xl">🏪</span>
      <div>
        <h1 class="text-2xl font-bold text-slate-900">Kirana AI</h1>
        <p class="text-sm text-slate-500">Admin Panel</p>
      </div>
    </div>
    <form id="login-form" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">Backend URL</label>
        <input id="f-url" type="url" required placeholder="http://localhost:9000"
          class="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
      </div>
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-1">Admin API Key</label>
        <input id="f-key" type="password" required placeholder="kirana-dev-key"
          class="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
      </div>
      <button type="submit" id="login-btn"
        class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2.5 rounded-lg text-sm transition-colors">
        Connect
      </button>
    </form>
  `;
  wrap.appendChild(card);
  app.appendChild(wrap);

  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = document.getElementById('f-url').value.trim();
    const key = document.getElementById('f-key').value.trim();
    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Connecting…';
    try {
      saveSession(url, key);
      await api.health();
      toast('Connected successfully!');
      renderApp();
    } catch (err) {
      clearSession();
      toast(`Connection failed: ${err.message}`, 'error');
      btn.disabled = false;
      btn.textContent = 'Connect';
    }
  });
}

// ── App shell ─────────────────────────────────────────────────────────────────

let _activeTab = 'pending';

function renderApp() {
  const app = document.getElementById('app');
  app.innerHTML = '';

  const layout = el('div', 'min-h-screen flex flex-col');

  const header = el('header', 'bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between');
  header.innerHTML = `
    <div class="flex items-center gap-2">
      <span class="text-2xl">🏪</span>
      <span class="text-lg font-bold text-slate-900">Kirana AI Admin</span>
    </div>
    <button id="logout-btn" class="text-sm text-slate-500 hover:text-red-600 font-medium transition-colors">
      Logout
    </button>
  `;
  layout.appendChild(header);

  const tabs = el('nav', 'bg-white border-b border-slate-200 px-6 flex gap-1');
  const tabDefs = [
    { id: 'pending',       label: 'Pending Trials' },
    { id: 'subscriptions', label: 'All Subscriptions' },
    { id: 'kpi-packages',  label: 'KPI Packages' },
  ];
  tabDefs.forEach(({ id, label }) => {
    const active = _activeTab === id;
    const btn = el('button',
      `tab-btn px-4 py-3 text-sm font-medium border-b-2 transition-colors ${active ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-600 hover:text-slate-900'}`,
      label,
    );
    btn.dataset.tab = id;
    tabs.appendChild(btn);
  });
  layout.appendChild(tabs);

  const content = el('main', 'flex-1 p-6');
  content.id = 'tab-content';
  layout.appendChild(content);

  app.appendChild(layout);

  tabs.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _activeTab = btn.dataset.tab;
      renderApp();
    });
  });

  document.getElementById('logout-btn').addEventListener('click', () => {
    clearSession();
    renderLogin();
  });

  if (_activeTab === 'pending') loadPendingTrials();
  else if (_activeTab === 'subscriptions') loadAllSubscriptions();
  else loadKpiPackages();
}

// ── Pending Trials ────────────────────────────────────────────────────────────

async function loadPendingTrials() {
  const content = document.getElementById('tab-content');
  content.innerHTML = `
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-slate-900">Pending Trial Requests</h2>
      <button id="refresh-pending" class="text-sm text-indigo-600 hover:text-indigo-800 font-medium">↻ Refresh</button>
    </div>
    <div id="pending-list"><div class="text-slate-500 text-sm">Loading…</div></div>
  `;
  document.getElementById('refresh-pending').addEventListener('click', loadPendingTrials);
  await fetchAndRenderPending();
}

async function fetchAndRenderPending() {
  const list = document.getElementById('pending-list');
  try {
    const data = await api.pendingTrials();
    const rows = data.pending ?? [];

    if (rows.length === 0) {
      list.innerHTML = `
        <div class="bg-white rounded-xl border border-slate-200 p-12 text-center">
          <p class="text-4xl mb-3">✅</p>
          <p class="text-slate-600 font-medium">No pending trial requests</p>
          <p class="text-slate-400 text-sm mt-1">All requests have been processed.</p>
        </div>`;
      return;
    }

    const wrap = el('div', 'bg-white rounded-xl border border-slate-200 overflow-hidden');
    wrap.innerHTML = `
      <table class="w-full text-sm">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Store</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Store ID</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Requested</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody id="pending-tbody"></tbody>
      </table>
    `;
    list.innerHTML = '';
    list.appendChild(wrap);

    const tbody = document.getElementById('pending-tbody');
    rows.forEach(row => {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
      tr.innerHTML = `
        <td class="px-4 py-3 font-medium text-slate-900">${escHtml(row.store_name)}</td>
        <td class="px-4 py-3 text-slate-500">#${row.store_id}</td>
        <td class="px-4 py-3 text-slate-500">${formatDate(row.started_at)}</td>
        <td class="px-4 py-3 text-right">
          <button data-store="${row.store_id}" data-name="${escHtml(row.store_name)}"
            class="approve-btn bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors">
            Approve Trial
          </button>
        </td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.approve-btn').forEach(btn => {
      btn.addEventListener('click', () => approveTrial(btn.dataset.store, btn.dataset.name, btn));
    });

  } catch (err) {
    list.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

async function approveTrial(storeId, storeName, btn) {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    await api.approveTrial(storeId);
    toast(`Trial approved for ${storeName || `Store #${storeId}`}!`);
    await fetchAndRenderPending();
  } catch (err) {
    toast(`Failed: ${err.message}`, 'error');
    btn.disabled = false;
    btn.textContent = 'Approve Trial';
  }
}

// ── All Subscriptions ─────────────────────────────────────────────────────────

async function loadAllSubscriptions() {
  const content = document.getElementById('tab-content');
  content.innerHTML = `
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-slate-900">All Subscriptions</h2>
      <button id="refresh-subs" class="text-sm text-indigo-600 hover:text-indigo-800 font-medium">↻ Refresh</button>
    </div>
    <div id="subs-list"><div class="text-slate-500 text-sm">Loading…</div></div>
  `;
  document.getElementById('refresh-subs').addEventListener('click', loadAllSubscriptions);
  await fetchAndRenderSubs();
}

async function fetchAndRenderSubs() {
  const list = document.getElementById('subs-list');
  try {
    const data = await api.allSubs();
    const rows = data.subscriptions ?? [];

    if (rows.length === 0) {
      list.innerHTML = `
        <div class="bg-white rounded-xl border border-slate-200 p-12 text-center">
          <p class="text-4xl mb-3">📋</p>
          <p class="text-slate-600 font-medium">No subscriptions yet</p>
        </div>`;
      return;
    }

    const wrap = el('div', 'bg-white rounded-xl border border-slate-200 overflow-hidden');
    wrap.innerHTML = `
      <table class="w-full text-sm">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Store</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Tier</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Started</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Expires</th>
            <th class="text-left px-4 py-3 font-semibold text-slate-700">Trial Ends</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody id="subs-tbody"></tbody>
      </table>
    `;
    list.innerHTML = '';
    list.appendChild(wrap);

    const tbody = document.getElementById('subs-tbody');
    rows.forEach(row => {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
      const isActive = !row.ended_at || new Date(row.ended_at) > new Date();
      const canCancel = isActive && row.tier !== 'pending_trial' && row.tier !== 'none';
      tr.innerHTML = `
        <td class="px-4 py-3">
          <div class="font-medium text-slate-900">${escHtml(row.store_name)}</div>
          <div class="text-slate-400 text-xs">#${row.store_id}</div>
        </td>
        <td class="px-4 py-3">${tierBadge(row.tier)}</td>
        <td class="px-4 py-3 text-slate-500">${formatDate(row.started_at)}</td>
        <td class="px-4 py-3 text-slate-500">${formatDate(row.ended_at)}</td>
        <td class="px-4 py-3 text-slate-500">${formatDate(row.trial_ends_at)}</td>
        <td class="px-4 py-3 text-right">
          ${canCancel ? `
            <button data-store="${row.store_id}" data-name="${escHtml(row.store_name)}"
              class="cancel-btn text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-300 text-red-600 hover:bg-red-50 transition-colors">
              Cancel
            </button>` : ''}
        </td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.cancel-btn').forEach(btn => {
      btn.addEventListener('click', () => cancelSub(btn.dataset.store, btn.dataset.name, btn));
    });

  } catch (err) {
    list.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

async function cancelSub(storeId, storeName, btn) {
  const label = storeName || `Store #${storeId}`;
  if (!confirm(`Cancel subscription for ${label}?\n\nThis will revoke their access immediately.`)) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="border-top-color:#dc2626;border-color:rgba(220,38,38,0.3)"></span>';
  try {
    await api.cancelSub(storeId);
    toast(`Subscription cancelled for ${label}.`, 'info');
    await fetchAndRenderSubs();
  } catch (err) {
    toast(`Failed: ${err.message}`, 'error');
    btn.disabled = false;
    btn.textContent = 'Cancel';
  }
}

// ── KPI Packages tab ──────────────────────────────────────────────────────────

// Pending edits: kpi_id → 'basic'|'pro'
let _kpiEdits = {};

async function loadKpiPackages() {
  const content = document.getElementById('tab-content');
  content.innerHTML = `
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="text-xl font-bold text-slate-900">KPI Packages</h2>
        <p class="text-sm text-slate-500 mt-0.5">Control which KPIs are available on Basic vs Pro plans.</p>
      </div>
      <div class="flex gap-2">
        <button id="reset-kpi" class="text-sm text-slate-500 hover:text-slate-800 font-medium px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors">Reset</button>
        <button id="save-kpi" class="text-sm bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-4 py-1.5 rounded-lg transition-colors">Save Changes</button>
      </div>
    </div>
    <div class="flex gap-3 mb-5">
      <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">
        <span class="w-2 h-2 rounded-full bg-indigo-500"></span>Basic — included in Basic & Pro plans
      </span>
      <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">
        <span class="w-2 h-2 rounded-full bg-purple-500"></span>Pro — Pro plan only
      </span>
    </div>
    <div id="kpi-list"><div class="text-slate-500 text-sm">Loading…</div></div>
  `;
  _kpiEdits = {};
  document.getElementById('save-kpi').addEventListener('click', saveKpiPackages);
  document.getElementById('reset-kpi').addEventListener('click', loadKpiPackages);
  await fetchAndRenderKpis();
}

async function fetchAndRenderKpis() {
  const list = document.getElementById('kpi-list');
  try {
    const data = await api.getKpiTiers();
    const kpis = data.kpis ?? [];

    if (kpis.length === 0) {
      list.innerHTML = `<div class="bg-white rounded-xl border border-slate-200 p-12 text-center"><p class="text-slate-500">No KPIs found in registry.</p></div>`;
      return;
    }

    // Group by category
    const grouped = {};
    kpis.forEach(k => {
      const cat = k.category || 'Uncategorized';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(k);
    });

    list.innerHTML = '';
    Object.entries(grouped).forEach(([category, items]) => {
      const section = el('div', 'mb-4');
      section.innerHTML = `
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-xs font-bold text-slate-500 uppercase tracking-wider">${escHtml(category)}</h3>
          <div class="flex gap-1">
            <button data-cat="${escHtml(category)}" data-tier="basic" class="cat-btn text-xs px-2 py-0.5 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50 transition-colors">All Basic</button>
            <button data-cat="${escHtml(category)}" data-tier="pro" class="cat-btn text-xs px-2 py-0.5 rounded border border-purple-200 text-purple-700 hover:bg-purple-50 transition-colors">All Pro</button>
          </div>
        </div>
        <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table class="w-full text-sm">
            <tbody id="cat-${escHtml(category).replace(/\s+/g, '-')}"></tbody>
          </table>
        </div>
      `;
      list.appendChild(section);

      const tbodyId = `cat-${category.replace(/\s+/g, '-')}`;
      const tbody = document.getElementById(tbodyId);
      items.forEach(kpi => {
        const tr = document.createElement('tr');
        tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
        tr.dataset.kpiId = kpi.kpi_id;
        const currentTier = _kpiEdits[kpi.kpi_id] ?? kpi.tier;
        tr.innerHTML = `
          <td class="px-4 py-3">
            <div class="font-medium text-slate-900">${escHtml(kpi.name)}</div>
            <div class="text-slate-400 text-xs">${escHtml(kpi.kpi_id)}${kpi.is_custom ? ' <span class="text-indigo-500">· custom</span>' : ''}</div>
          </td>
          <td class="px-4 py-3 text-right">
            <div class="inline-flex rounded-lg border border-slate-200 overflow-hidden">
              <button data-kpi="${kpi.kpi_id}" data-tier="basic"
                class="tier-btn px-3 py-1.5 text-xs font-semibold transition-colors ${currentTier === 'basic' ? 'bg-indigo-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'}">
                Basic
              </button>
              <button data-kpi="${kpi.kpi_id}" data-tier="pro"
                class="tier-btn px-3 py-1.5 text-xs font-semibold border-l border-slate-200 transition-colors ${currentTier === 'pro' ? 'bg-purple-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'}">
                Pro
              </button>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      });

      // Category bulk buttons
      section.querySelectorAll('.cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const cat = btn.dataset.cat;
          const tier = btn.dataset.tier;
          grouped[cat].forEach(k => { _kpiEdits[k.kpi_id] = tier; });
          // Re-render all tier buttons in this category
          const tb = document.getElementById(`cat-${cat.replace(/\s+/g, '-')}`);
          if (!tb) return;
          tb.querySelectorAll('.tier-btn').forEach(b => {
            const isSel = b.dataset.tier === tier;
            b.className = `tier-btn px-3 py-1.5 text-xs font-semibold transition-colors ${
              b.dataset.tier === 'pro'
                ? (isSel ? 'bg-purple-600 text-white border-l border-slate-200' : 'bg-white text-slate-500 hover:bg-slate-50 border-l border-slate-200')
                : (isSel ? 'bg-indigo-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50')
            }`;
          });
        });
      });
    });

    // Individual tier toggle
    list.querySelectorAll('.tier-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const kpiId = btn.dataset.kpi;
        const tier  = btn.dataset.tier;
        _kpiEdits[kpiId] = tier;
        // Update sibling buttons in the same toggle group
        const row = btn.closest('tr');
        row.querySelectorAll('.tier-btn').forEach(b => {
          const isSel = b.dataset.tier === tier;
          b.className = `tier-btn px-3 py-1.5 text-xs font-semibold transition-colors ${
            b.dataset.tier === 'pro'
              ? (isSel ? 'bg-purple-600 text-white border-l border-slate-200' : 'bg-white text-slate-500 hover:bg-slate-50 border-l border-slate-200')
              : (isSel ? 'bg-indigo-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50')
          }`;
        });
      });
    });

  } catch (err) {
    list.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

async function saveKpiPackages() {
  const btn = document.getElementById('save-kpi');
  if (Object.keys(_kpiEdits).length === 0) {
    toast('No changes to save.', 'info');
    return;
  }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Saving…';
  try {
    const configs = Object.entries(_kpiEdits).map(([kpi_id, tier]) => ({ kpi_id, tier }));
    const res = await api.saveKpiTiers(configs);
    toast(`Saved ${res.saved} KPI tier assignment${res.saved !== 1 ? 's' : ''}.`);
    _kpiEdits = {};
    // Reload to reflect server state
    await fetchAndRenderKpis();
  } catch (err) {
    toast(`Save failed: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Changes';
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

loadSession();
if (isConfigured()) renderApp();
else renderLogin();
