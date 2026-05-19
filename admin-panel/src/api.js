// ── API client ────────────────────────────────────────────────────────────────

let _baseUrl = '';
let _apiKey  = '';

export function configure(baseUrl, apiKey) {
  _baseUrl = baseUrl.replace(/\/+$/, '');
  _apiKey  = apiKey;
}

export function isConfigured() {
  return !!_baseUrl && !!_apiKey;
}

async function request(method, path, body) {
  const res = await fetch(`${_baseUrl}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': _apiKey,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health:          ()                     => request('GET',  '/kirana/health'),
  // Dashboard
  stats:           ()                     => request('GET',  '/kirana/admin/stats'),
  // Stores
  adminStores:     ()                     => request('GET',  '/kirana/admin/stores'),
  mockPayment:     (storeId, tier)        => request('POST', '/kirana/admin/payment/mock-confirm', { store_id: storeId, tier }),
  // Trials & subscriptions
  pendingTrials:   ()                     => request('GET',  '/kirana/admin/pending-trials'),
  approveTrial:    (storeId)              => request('POST', `/kirana/admin/approve-trial/${storeId}`),
  allSubs:         ()                     => request('GET',  '/kirana/admin/all-subscriptions'),
  cancelSub:       (storeId)              => request('POST', `/kirana/admin/cancel-subscription/${storeId}`),
  // Notifications
  notify:          (storeId, title, body) => request('POST', '/kirana/admin/notify', { store_id: storeId || null, title, body }),
  // KPI config
  getKpiTiers:     ()                     => request('GET',  '/kirana/admin/kpi-tiers'),
  saveKpiTiers:    (configs)              => request('PUT',  '/kirana/admin/kpi-tiers', { configs }),
  userActivity:    ()                     => request('GET',  '/kirana/admin/user-activity'),
};
