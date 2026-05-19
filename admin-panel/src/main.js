import './style.css';
import { configure, isConfigured, api } from './api.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

function toast(msg, type = 'success') {
  const c = document.getElementById('toast-container');
  const el = document.createElement('div');
  const colors = { success: 'bg-emerald-600', error: 'bg-red-600', info: 'bg-slate-700' };
  el.className = `toast pointer-events-auto px-4 py-3 rounded-lg text-white text-sm font-medium shadow-lg ${colors[type] ?? colors.info}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function el(tag, cls, inner) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (inner !== undefined) e.innerHTML = inner;
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
  if (!tier) return '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-400">No Plan</span>';
  const [label, cls] = map[tier] ?? [tier, 'bg-slate-100 text-slate-600'];
  return `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ${cls}">${label}</span>`;
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function statCard(label, value, color, icon) {
  return `
    <div class="bg-white rounded-xl border border-slate-200 p-5">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">${label}</span>
        <span class="text-lg">${icon}</span>
      </div>
      <p class="text-3xl font-black ${color}">${value}</p>
    </div>`;
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
  sessionStorage.removeItem('kirana_tab');
  _activeTab = 'dashboard';
}

// ── Login ─────────────────────────────────────────────────────────────────────

function renderLogin() {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="min-h-screen flex items-center justify-center p-4 bg-slate-50">
      <div class="bg-white rounded-2xl shadow-xl p-8 w-full max-w-md">
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
      </div>
    </div>`;

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
      toast('Connected!');
      renderApp();
    } catch (err) {
      clearSession();
      toast(`Connection failed: ${err.message}`, 'error');
      btn.disabled = false;
      btn.textContent = 'Connect';
    }
  });
}

// ── App shell (sidebar layout) ────────────────────────────────────────────────

// Persist active tab across page refreshes
let _activeTab = sessionStorage.getItem('kirana_tab') || 'dashboard';

function setActiveTab(id) {
  _activeTab = id;
  sessionStorage.setItem('kirana_tab', id);
}

const NAV_ITEMS = [
  { id: 'dashboard',     icon: '📊', label: 'Dashboard' },
  { id: 'stores',        icon: '🏪', label: 'Stores' },
  { id: 'pending',       icon: '⏳', label: 'Pending Trials' },
  { id: 'subscriptions', icon: '💳', label: 'Subscriptions' },
  { id: 'notifications', icon: '🔔', label: 'Notifications' },
  { id: 'kpi-packages',  icon: '📈', label: 'KPI Config' },
  { id: 'user-activity', icon: '👁️', label: 'User Activity' },
];

function renderApp() {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="min-h-screen flex bg-slate-50">

      <!-- Sidebar -->
      <aside class="w-56 flex-shrink-0 bg-white border-r border-slate-200 flex flex-col">
        <div class="px-5 py-5 border-b border-slate-100">
          <div class="flex items-center gap-2">
            <span class="text-2xl">🏪</span>
            <div>
              <p class="text-sm font-bold text-slate-900 leading-none">Kirana AI</p>
              <p class="text-xs text-slate-400 mt-0.5">Admin</p>
            </div>
          </div>
        </div>
        <nav class="flex-1 py-3 space-y-0.5 px-2" id="sidebar-nav"></nav>
        <div class="p-3 border-t border-slate-100">
          <button id="logout-btn"
            class="w-full text-left px-3 py-2 text-sm text-slate-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors font-medium">
            ← Logout
          </button>
        </div>
      </aside>

      <!-- Main content -->
      <div class="flex-1 min-w-0 flex flex-col">
        <header class="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
          <h2 id="page-title" class="text-lg font-bold text-slate-900"></h2>
          <button id="refresh-btn"
            class="text-sm text-indigo-600 hover:text-indigo-800 font-medium px-3 py-1.5 rounded-lg border border-indigo-200 hover:bg-indigo-50 transition-colors">
            ↻ Refresh
          </button>
        </header>
        <main class="flex-1 p-6 overflow-auto" id="tab-content"></main>
      </div>

    </div>`;

  // Build sidebar nav
  const nav = document.getElementById('sidebar-nav');
  NAV_ITEMS.forEach(({ id, icon, label }) => {
    const active = _activeTab === id;
    const btn = el('button',
      `w-full flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-lg transition-colors ${
        active ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
      }`,
      `<span>${icon}</span><span>${label}</span>`
    );
    btn.dataset.tab = id;
    nav.appendChild(btn);
  });

  nav.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      setActiveTab(btn.dataset.tab);
      renderApp();
    });
  });

  document.getElementById('logout-btn').addEventListener('click', () => {
    clearSession();
    renderLogin();
  });

  // Page title + refresh target
  const current = NAV_ITEMS.find(n => n.id === _activeTab);
  document.getElementById('page-title').textContent = current?.label ?? '';

  // Route to tab
  const loaders = {
    dashboard:     loadDashboard,
    stores:        loadStores,
    pending:       loadPendingTrials,
    subscriptions: loadAllSubscriptions,
    notifications: loadNotifications,
    'kpi-packages': loadKpiPackages,
    'user-activity': loadUserActivity,
  };
  const loader = loaders[_activeTab] ?? loadDashboard;
  document.getElementById('refresh-btn').addEventListener('click', loader);
  loader();
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

async function loadDashboard() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  try {
    const [stats, pending] = await Promise.all([api.stats(), api.pendingTrials()]);
    const pendingRows = (pending.pending ?? []).slice(0, 5);

    content.innerHTML = `
      <!-- Stat cards -->
      <div class="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        ${statCard('Total Stores',   stats.total_stores,   'text-slate-900', '🏪')}
        ${statCard('Store Owners',   stats.total_users,    'text-slate-900', '👤')}
        ${statCard('Pending Trials', stats.pending_trials, stats.pending_trials > 0 ? 'text-amber-600' : 'text-slate-900', '⏳')}
        ${statCard('Active Trials',  stats.active_trials,  'text-blue-600', '🎯')}
        ${statCard('Basic Plan',     stats.basic_count,    'text-indigo-600', '⭐')}
        ${statCard('Pro Plan',       stats.pro_count,      'text-purple-600', '💎')}
      </div>

      <!-- Pending trials preview -->
      <div class="bg-white rounded-xl border border-slate-200">
        <div class="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 class="font-bold text-slate-900">Pending Trial Requests</h3>
          <button id="goto-pending" class="text-xs text-indigo-600 hover:text-indigo-800 font-semibold">View all →</button>
        </div>
        ${pendingRows.length === 0
          ? '<div class="p-8 text-center text-slate-400 text-sm">No pending requests ✅</div>'
          : `<table class="w-full text-sm">
              <thead class="bg-slate-50 border-b border-slate-100">
                <tr>
                  <th class="text-left px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Store</th>
                  <th class="text-left px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Plan</th>
                  <th class="text-left px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Date</th>
                  <th class="px-5 py-3"></th>
                </tr>
              </thead>
              <tbody>${pendingRows.map(r => {
                const tierPill = (r.requested_tier || 'basic') === 'pro'
                  ? '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">Pro Trial</span>'
                  : '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">Basic Trial</span>';
                return `<tr class="border-b border-slate-50 last:border-0">
                  <td class="px-5 py-3 font-medium text-slate-900">${escHtml(r.store_name)} <span class="text-slate-400 text-xs">#${r.store_id}</span></td>
                  <td class="px-5 py-3">${tierPill}</td>
                  <td class="px-5 py-3 text-slate-400">${formatDate(r.started_at)}</td>
                  <td class="px-5 py-3 text-right">
                    <button data-store="${r.store_id}" data-name="${escHtml(r.store_name)}"
                      class="quick-approve bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors">
                      Approve
                    </button>
                  </td>
                </tr>`;
              }).join('')}</tbody>
            </table>`
        }
      </div>`;

    document.getElementById('goto-pending')?.addEventListener('click', () => {
      setActiveTab('pending'); renderApp();
    });
    content.querySelectorAll('.quick-approve').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
        try {
          await api.approveTrial(btn.dataset.store);
          toast(`Trial approved for ${btn.dataset.name}!`);
          loadDashboard();
        } catch (err) {
          toast(`Failed: ${err.message}`, 'error');
          btn.disabled = false; btn.textContent = 'Approve';
        }
      });
    });
  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

// ── Stores ────────────────────────────────────────────────────────────────────

async function loadStores() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  try {
    const data = await api.adminStores();
    const stores = data.stores ?? [];

    if (stores.length === 0) {
      content.innerHTML = '<div class="bg-white rounded-xl border border-slate-200 p-12 text-center text-slate-400">No stores yet.</div>';
      return;
    }

    content.innerHTML = `
      <div class="mb-4 flex items-center gap-3">
        <input id="store-search" type="text" placeholder="Search store or owner…"
          class="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-72" />
        <span class="text-sm text-slate-400">${stores.length} stores total</span>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 border-b border-slate-200">
            <tr>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Store</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Owner</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Plan</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Registered</th>
              <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="stores-tbody"></tbody>
        </table>
      </div>`;

    function renderStoreRows(filter = '') {
      const q = filter.toLowerCase();
      const tbody = document.getElementById('stores-tbody');
      tbody.innerHTML = '';
      const filtered = q
        ? stores.filter(s =>
            (s.store_name ?? '').toLowerCase().includes(q) ||
            (s.name ?? '').toLowerCase().includes(q) ||
            (s.username ?? '').toLowerCase().includes(q))
        : stores;

      filtered.forEach(s => {
        const tr = document.createElement('tr');
        tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
        const trialEnds = s.trial_ends_at ? `<div class="text-xs text-slate-400">Trial ends ${formatDate(s.trial_ends_at)}</div>` : '';
        tr.innerHTML = `
          <td class="px-4 py-3">
            <div class="font-semibold text-slate-900">${escHtml(s.store_name ?? '—')}</div>
            <div class="text-xs text-slate-400">#${s.store_id} · ${escHtml(s.location ?? '—')}</div>
          </td>
          <td class="px-4 py-3">
            <div class="text-slate-800">${escHtml(s.username ?? '—')}</div>
            ${s.owner_name && s.owner_name !== s.username ? `<div class="text-xs text-slate-400">${escHtml(s.owner_name)}</div>` : ''}
          </td>
          <td class="px-4 py-3">${tierBadge(s.tier)}${trialEnds}</td>
          <td class="px-4 py-3 text-slate-400">${formatDate(s.created_at)}</td>
          <td class="px-4 py-3 text-right">
            <div class="flex items-center justify-end gap-2 flex-wrap">
              <button data-store="${s.store_id}" data-name="${escHtml(s.store_name ?? '')}"
                class="notify-store-btn text-xs font-medium px-2.5 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors">
                🔔 Notify
              </button>
              <div class="flex rounded-lg border border-slate-200 overflow-hidden">
                <button data-store="${s.store_id}" data-tier="basic"
                  class="mock-pay-btn text-xs font-semibold px-2.5 py-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 transition-colors border-r border-slate-200">
                  Basic
                </button>
                <button data-store="${s.store_id}" data-tier="pro"
                  class="mock-pay-btn text-xs font-semibold px-2.5 py-1.5 bg-purple-50 hover:bg-purple-100 text-purple-700 transition-colors">
                  Pro
                </button>
              </div>
            </div>
          </td>`;
        tbody.appendChild(tr);
      });

      // Notify button → switch to notifications tab pre-filled
      tbody.querySelectorAll('.notify-store-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          _notifyPreset = { storeId: btn.dataset.store, storeName: btn.dataset.name };
          setActiveTab('notifications');
          renderApp();
        });
      });

      // Mock payment buttons
      tbody.querySelectorAll('.mock-pay-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const storeId = btn.dataset.store;
          const tier = btn.dataset.tier;
          btn.disabled = true;
          const orig = btn.textContent;
          btn.innerHTML = '<span class="spinner" style="width:10px;height:10px;border-width:2px"></span>';
          try {
            await api.mockPayment(storeId, tier);
            toast(`${tier === 'pro' ? 'Pro' : 'Basic'} plan activated for store #${storeId}!`);
            loadStores();
          } catch (err) {
            toast(`Failed: ${err.message}`, 'error');
            btn.disabled = false;
            btn.textContent = orig;
          }
        });
      });
    }

    renderStoreRows();
    document.getElementById('store-search').addEventListener('input', e => renderStoreRows(e.target.value));
  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

// ── Pending Trials ────────────────────────────────────────────────────────────

async function loadPendingTrials() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  try {
    const data = await api.pendingTrials();
    const rows = data.pending ?? [];

    if (rows.length === 0) {
      content.innerHTML = `
        <div class="bg-white rounded-xl border border-slate-200 p-12 text-center">
          <p class="text-4xl mb-3">✅</p>
          <p class="text-slate-600 font-medium">No pending requests</p>
          <p class="text-slate-400 text-sm mt-1">All trial requests have been processed.</p>
        </div>`;
      return;
    }

    const wrap = el('div', 'bg-white rounded-xl border border-slate-200 overflow-hidden');
    wrap.innerHTML = `
      <table class="w-full text-sm">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Store</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Requested Plan</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Date</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody id="pending-tbody"></tbody>
      </table>`;
    content.innerHTML = '';
    content.appendChild(wrap);

    const tbody = document.getElementById('pending-tbody');
    rows.forEach(row => {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
      const reqTier = row.requested_tier || 'basic';
      const tierPill = reqTier === 'pro'
        ? '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">Pro Trial</span>'
        : '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">Basic Trial</span>';
      tr.innerHTML = `
        <td class="px-4 py-3">
          <div class="font-semibold text-slate-900">${escHtml(row.store_name)}</div>
          <div class="text-xs text-slate-400">#${row.store_id}</div>
        </td>
        <td class="px-4 py-3">${tierPill}</td>
        <td class="px-4 py-3 text-slate-400">${formatDate(row.started_at)}</td>
        <td class="px-4 py-3 text-right">
          <button data-store="${row.store_id}" data-name="${escHtml(row.store_name)}"
            class="approve-btn bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors">
            Approve
          </button>
        </td>`;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.approve-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
        try {
          await api.approveTrial(btn.dataset.store);
          toast(`Trial approved for ${btn.dataset.name}!`);
          loadPendingTrials();
        } catch (err) {
          toast(`Failed: ${err.message}`, 'error');
          btn.disabled = false; btn.textContent = 'Approve';
        }
      });
    });
  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

// ── All Subscriptions ─────────────────────────────────────────────────────────

async function loadAllSubscriptions() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  try {
    const data = await api.allSubs();
    const rows = data.subscriptions ?? [];

    if (rows.length === 0) {
      content.innerHTML = '<div class="bg-white rounded-xl border border-slate-200 p-12 text-center text-slate-400">No subscriptions yet.</div>';
      return;
    }

    const wrap = el('div', 'bg-white rounded-xl border border-slate-200 overflow-hidden');
    wrap.innerHTML = `
      <table class="w-full text-sm">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Store</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Tier</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Started</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Expires</th>
            <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Trial Ends</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody id="subs-tbody"></tbody>
      </table>`;
    content.innerHTML = '';
    content.appendChild(wrap);

    const tbody = document.getElementById('subs-tbody');
    rows.forEach(row => {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
      const isActive = !row.ended_at || new Date(row.ended_at) > new Date();
      const canCancel = isActive && row.tier !== 'pending_trial' && row.tier !== 'none';
      tr.innerHTML = `
        <td class="px-4 py-3">
          <div class="font-semibold text-slate-900">${escHtml(row.store_name)}</div>
          <div class="text-xs text-slate-400">#${row.store_id}</div>
        </td>
        <td class="px-4 py-3">${tierBadge(row.tier)}</td>
        <td class="px-4 py-3 text-slate-400">${formatDate(row.started_at)}</td>
        <td class="px-4 py-3 text-slate-400">${formatDate(row.ended_at)}</td>
        <td class="px-4 py-3 text-slate-400">${formatDate(row.trial_ends_at)}</td>
        <td class="px-4 py-3 text-right">
          ${canCancel ? `
            <button data-store="${row.store_id}" data-name="${escHtml(row.store_name)}"
              class="cancel-btn text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors">
              Cancel
            </button>` : ''}
        </td>`;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.cancel-btn').forEach(btn => {
      btn.addEventListener('click', () => confirmCancelSub(btn.dataset.store, btn.dataset.name, btn));
    });
  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

function confirmCancelSub(storeId, storeName, btn) {
  const td = btn.closest('td');
  td.innerHTML = `
    <div class="flex items-center justify-end gap-2">
      <span class="text-xs text-slate-500">Revoke access?</span>
      <button class="confirm-yes text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white transition-colors">Yes, Cancel</button>
      <button class="confirm-no text-xs font-medium px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors">Keep</button>
    </div>`;
  td.querySelector('.confirm-no').addEventListener('click', loadAllSubscriptions);
  td.querySelector('.confirm-yes').addEventListener('click', async () => {
    td.innerHTML = '<span class="text-xs text-slate-400">Cancelling…</span>';
    try {
      await api.cancelSub(storeId);
      toast(`Subscription cancelled for ${storeName || `Store #${storeId}`}.`, 'info');
      loadAllSubscriptions();
    } catch (err) {
      toast(`Failed: ${err.message}`, 'error');
      loadAllSubscriptions();
    }
  });
}

// ── Notifications ─────────────────────────────────────────────────────────────

let _notifyPreset = null; // set by Stores → Notify button

async function loadNotifications() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading stores…</div>';

  let stores = [];
  try {
    const data = await api.adminStores();
    stores = data.stores ?? [];
  } catch (_) {}

  const presetStore = _notifyPreset?.storeId ?? '';
  const presetName  = _notifyPreset?.storeName ?? '';
  _notifyPreset = null;

  const storeOptions = stores.map(s =>
    `<option value="${s.store_id}" ${String(s.store_id) === String(presetStore) ? 'selected' : ''}>
       #${s.store_id} — ${escHtml(s.store_name ?? '')}
     </option>`
  ).join('');

  content.innerHTML = `
    <div class="max-w-lg">
      <div class="bg-white rounded-xl border border-slate-200 p-6 space-y-5">

        <div>
          <label class="block text-sm font-semibold text-slate-700 mb-2">Target</label>
          <select id="notify-target"
            class="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500">
            <option value="">📢 All stores (broadcast)</option>
            ${storeOptions}
          </select>
          ${presetStore ? `<p class="text-xs text-indigo-600 mt-1">Pre-filled from Stores: ${escHtml(presetName)}</p>` : ''}
        </div>

        <div>
          <label class="block text-sm font-semibold text-slate-700 mb-2">Title</label>
          <input id="notify-title" type="text" placeholder="e.g. New feature available!"
            class="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        </div>

        <div>
          <label class="block text-sm font-semibold text-slate-700 mb-2">Message</label>
          <textarea id="notify-body" rows="3" placeholder="Notification message…"
            class="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"></textarea>
        </div>

        <button id="notify-send"
          class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2.5 rounded-lg text-sm transition-colors">
          Send Notification
        </button>

        <div id="notify-result" class="hidden"></div>
      </div>

      <p class="text-xs text-slate-400 mt-3 text-center">
        Notifications are delivered via Firebase Cloud Messaging. Stores without FCM tokens will be skipped.
      </p>
    </div>`;

  document.getElementById('notify-send').addEventListener('click', async () => {
    const target  = document.getElementById('notify-target').value;
    const title   = document.getElementById('notify-title').value.trim();
    const body    = document.getElementById('notify-body').value.trim();
    const resultEl = document.getElementById('notify-result');

    if (!title || !body) { toast('Title and message are required.', 'error'); return; }

    const btn = document.getElementById('notify-send');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Sending…';

    try {
      const res = await api.notify(target ? parseInt(target) : null, title, body);
      resultEl.className = 'mt-2 p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-700';
      resultEl.textContent = `Sent to ${res.sent} of ${res.total} store(s).`;
      resultEl.classList.remove('hidden');
      toast(`Sent to ${res.sent} store(s)!`);
    } catch (err) {
      toast(`Failed: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Send Notification';
    }
  });
}

// ── KPI Packages ──────────────────────────────────────────────────────────────

let _kpiEdits = {};

async function loadKpiPackages() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  _kpiEdits = {};

  try {
    const data = await api.getKpiTiers();
    const kpis = data.kpis ?? [];

    if (kpis.length === 0) {
      content.innerHTML = '<div class="bg-white rounded-xl border border-slate-200 p-12 text-center text-slate-400">No KPIs in registry.</div>';
      return;
    }

    content.innerHTML = `
      <div class="flex items-center gap-3 mb-5">
        <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">
          <span class="w-2 h-2 rounded-full bg-indigo-500"></span>Basic — included in Basic &amp; Pro
        </span>
        <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">
          <span class="w-2 h-2 rounded-full bg-purple-500"></span>Pro — Pro plan only
        </span>
        <div class="ml-auto flex gap-2">
          <button id="reset-kpi" class="text-sm text-slate-500 hover:text-slate-800 font-medium px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors">Reset</button>
          <button id="save-kpi" class="text-sm bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-4 py-1.5 rounded-lg transition-colors">Save Changes</button>
        </div>
      </div>
      <div id="kpi-list"></div>`;

    document.getElementById('save-kpi').addEventListener('click', saveKpiPackages);
    document.getElementById('reset-kpi').addEventListener('click', loadKpiPackages);

    const grouped = {};
    kpis.forEach(k => {
      const cat = k.category || 'Uncategorized';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(k);
    });

    const list = document.getElementById('kpi-list');
    Object.entries(grouped).forEach(([category, items]) => {
      const section = el('div', 'mb-4');
      const safeCat = category.replace(/\s+/g, '-');
      section.innerHTML = `
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-xs font-bold text-slate-500 uppercase tracking-wider">${escHtml(category)}</h3>
          <div class="flex gap-1">
            <button data-cat="${escHtml(category)}" data-tier="basic" class="cat-btn text-xs px-2 py-0.5 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50">All Basic</button>
            <button data-cat="${escHtml(category)}" data-tier="pro"   class="cat-btn text-xs px-2 py-0.5 rounded border border-purple-200 text-purple-700 hover:bg-purple-50">All Pro</button>
          </div>
        </div>
        <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table class="w-full text-sm"><tbody id="cat-${safeCat}"></tbody></table>
        </div>`;
      list.appendChild(section);

      const tbody = document.getElementById(`cat-${safeCat}`);
      items.forEach(kpi => {
        const currentTier = _kpiEdits[kpi.kpi_id] ?? kpi.tier;
        const tr = document.createElement('tr');
        tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50';
        tr.dataset.kpiId = kpi.kpi_id;
        tr.innerHTML = `
          <td class="px-4 py-3">
            <div class="font-medium text-slate-900">${escHtml(kpi.name)}</div>
            <div class="text-slate-400 text-xs">${escHtml(kpi.kpi_id)}${kpi.is_custom ? ' · custom' : ''}</div>
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
          </td>`;
        tbody.appendChild(tr);
      });

      section.querySelectorAll('.cat-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const tier = btn.dataset.tier;
          grouped[category].forEach(k => { _kpiEdits[k.kpi_id] = tier; });
          const tb = document.getElementById(`cat-${safeCat}`);
          tb?.querySelectorAll('.tier-btn').forEach(b => {
            const sel = b.dataset.tier === tier;
            b.className = `tier-btn px-3 py-1.5 text-xs font-semibold transition-colors ${b.dataset.tier === 'pro' ? 'border-l border-slate-200 ' : ''}${sel ? (b.dataset.tier === 'pro' ? 'bg-purple-600 text-white' : 'bg-indigo-600 text-white') : 'bg-white text-slate-500 hover:bg-slate-50'}`;
          });
        });
      });
    });

    list.querySelectorAll('.tier-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        _kpiEdits[btn.dataset.kpi] = btn.dataset.tier;
        const row = btn.closest('tr');
        row.querySelectorAll('.tier-btn').forEach(b => {
          const sel = b.dataset.tier === btn.dataset.tier;
          b.className = `tier-btn px-3 py-1.5 text-xs font-semibold transition-colors ${b.dataset.tier === 'pro' ? 'border-l border-slate-200 ' : ''}${sel ? (b.dataset.tier === 'pro' ? 'bg-purple-600 text-white' : 'bg-indigo-600 text-white') : 'bg-white text-slate-500 hover:bg-slate-50'}`;
        });
      });
    });

  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

async function saveKpiPackages() {
  const btn = document.getElementById('save-kpi');
  if (Object.keys(_kpiEdits).length === 0) { toast('No changes to save.', 'info'); return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Saving…';
  try {
    const configs = Object.entries(_kpiEdits).map(([kpi_id, tier]) => ({ kpi_id, tier }));
    const res = await api.saveKpiTiers(configs);
    toast(`Saved ${res.saved} KPI assignment${res.saved !== 1 ? 's' : ''}.`);
    _kpiEdits = {};
    await loadKpiPackages();
  } catch (err) {
    toast(`Save failed: ${err.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Changes'; }
  }
}

// ── User Activity ─────────────────────────────────────────────────────────────

async function loadUserActivity() {
  const content = document.getElementById('tab-content');
  content.innerHTML = '<div class="text-slate-400 text-sm">Loading…</div>';
  try {
    const data = await api.userActivity();
    const users = data.users ?? [];

    if (users.length === 0) {
      content.innerHTML = '<div class="bg-white rounded-xl border border-slate-200 p-12 text-center text-slate-400">No users found.</div>';
      return;
    }

    content.innerHTML = `
      <div class="mb-4 flex items-center gap-3">
        <input id="ua-search" type="text" placeholder="Search by name or store…"
          class="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-64" />
        <span class="text-xs text-slate-400">${users.length} users · ${users.filter(u => u.opens_today > 0).length} active today</span>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 border-b border-slate-200">
            <tr>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">User / Store</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Last Login</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Last Seen (App)</th>
              <th class="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Opens Today</th>
              <th class="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Time in App</th>
              <th class="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Sales Today</th>
              <th class="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
            </tr>
          </thead>
          <tbody id="ua-tbody"></tbody>
        </table>
      </div>`;

    function relativeTime(iso) {
      if (!iso) return '—';
      const diff = Date.now() - new Date(iso);
      const m = Math.floor(diff / 60000);
      if (m < 1)  return 'Just now';
      if (m < 60) return m + 'm ago';
      const h = Math.floor(m / 60);
      if (h < 24) return h + 'h ago';
      const d = Math.floor(h / 24);
      if (d < 7)  return d + 'd ago';
      return formatDate(iso);
    }

    function renderRows(filter = '') {
      const q = filter.toLowerCase();
      const tbody = document.getElementById('ua-tbody');
      tbody.innerHTML = '';
      const filtered = q
        ? users.filter(u => (u.full_name ?? '').toLowerCase().includes(q) || (u.store_name ?? '').toLowerCase().includes(q))
        : users;

      filtered.forEach(u => {
        const activeToday = u.opens_today > 0;
        const madesSales  = u.sales_today > 0;
        const sec = u.foreground_sec_today ?? 0;
        const timeLabel = sec === 0 ? '—'
          : sec < 60 ? `${sec}s`
          : sec < 3600 ? `${Math.floor(sec/60)}m ${sec%60}s`
          : `${Math.floor(sec/3600)}h ${Math.floor((sec%3600)/60)}m`;
        const methodMap = {
          password: { label: 'Password', cls: 'bg-slate-100 text-slate-600' },
          phone:    { label: 'Phone OTP', cls: 'bg-blue-100 text-blue-700' },
          register: { label: 'Register', cls: 'bg-emerald-100 text-emerald-700' },
        };
        const method = u.last_login_method ?? 'password';
        const { label: methodLabel, cls: methodCls } = methodMap[method] ?? methodMap.password;
        const tr = document.createElement('tr');
        tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors';
        tr.innerHTML = `
          <td class="px-4 py-3">
            <div class="font-semibold text-slate-900">${escHtml(u.full_name ?? u.username ?? '—')}</div>
            <div class="text-xs text-slate-400">${escHtml(u.store_name ?? 'No store')} · @${escHtml(u.username ?? '')}</div>
          </td>
          <td class="px-4 py-3">
            <div class="text-slate-600 text-sm">${relativeTime(u.last_login)}</div>
            <span class="inline-flex mt-0.5 items-center px-1.5 py-0.5 rounded text-xs font-semibold ${methodCls}">${methodLabel}</span>
          </td>
          <td class="px-4 py-3 text-slate-500 text-sm">${relativeTime(u.last_seen)}</td>
          <td class="px-4 py-3 text-center">
            <span class="font-semibold ${activeToday ? 'text-indigo-600' : 'text-slate-400'}">${u.opens_today ?? 0}</span>
          </td>
          <td class="px-4 py-3 text-center">
            <span class="font-semibold ${sec > 0 ? 'text-violet-600' : 'text-slate-400'}">${timeLabel}</span>
          </td>
          <td class="px-4 py-3 text-center">
            <span class="font-semibold ${madesSales ? 'text-emerald-600' : 'text-slate-400'}">${u.sales_today ?? 0}</span>
          </td>
          <td class="px-4 py-3 text-center">
            ${activeToday
              ? '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700">Active</span>'
              : '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-500">Inactive</span>'
            }
          </td>`;
        tbody.appendChild(tr);
      });
    }

    renderRows();
    document.getElementById('ua-search').addEventListener('input', e => renderRows(e.target.value));
  } catch (err) {
    content.innerHTML = `<div class="bg-red-50 text-red-700 rounded-xl p-4 text-sm">Error: ${escHtml(err.message)}</div>`;
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

loadSession();
if (isConfigured()) renderApp();
else renderLogin();
