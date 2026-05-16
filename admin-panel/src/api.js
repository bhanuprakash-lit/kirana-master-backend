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
  health:          ()           => request('GET',  '/kirana/health'),
  pendingTrials:   ()           => request('GET',  '/kirana/admin/pending-trials'),
  approveTrial:    (storeId)    => request('POST', `/kirana/admin/approve-trial/${storeId}`),
  allStores:       ()           => request('GET',  '/kirana/stores'),
  allSubs:         ()           => request('GET',  '/kirana/admin/all-subscriptions'),
  cancelSub:       (storeId)    => request('POST', `/kirana/admin/cancel-subscription/${storeId}`),
  getKpiTiers:     ()           => request('GET',  '/kirana/admin/kpi-tiers'),
  saveKpiTiers:    (configs)    => request('PUT',  '/kirana/admin/kpi-tiers', { configs }),
};
