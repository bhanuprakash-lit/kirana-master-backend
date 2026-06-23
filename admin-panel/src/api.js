// ── API client ────────────────────────────────────────────────────────────────

let _baseUrl = '';
let _apiKey  = '';
let _onUnauthorized = null;     // set by App → clears session + redirects to login
const REQUEST_TIMEOUT_MS = 20000;

export function configure(baseUrl, apiKey) {
  _baseUrl = baseUrl.replace(/\/+$/, '');
  _apiKey  = apiKey;
}

export function isConfigured() {
  return !!_baseUrl && !!_apiKey;
}

/** Register a handler invoked when any request returns 401/403 (bad/revoked key). */
export function onUnauthorized(fn) {
  _onUnauthorized = fn;
}

async function request(method, path, body, params) {
  let url = `${_baseUrl}${path}`;
  if (params) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') qs.append(k, v); });
    if (qs.toString()) url += `?${qs.toString()}`;
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  const options = {
    method,
    signal: ctrl.signal,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': _apiKey,
    },
  };
  if (body !== undefined && method !== 'GET' && method !== 'HEAD') {
    options.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(url, options);
  } catch (e) {
    clearTimeout(timer);
    if (e.name === 'AbortError') throw new Error('Request timed out — is the backend reachable?');
    throw new Error('Network error — could not reach the backend.');
  }
  clearTimeout(timer);

  if (res.status === 401 || res.status === 403) {
    if (_onUnauthorized) _onUnauthorized();
    throw new Error('Session expired or unauthorized. Please reconnect.');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Open a live SSE log stream via fetch (supports custom headers, unlike EventSource).
 */
export function startLogStream(tail = 100, onEvent, onClose) {
  const ctrl = new AbortController();
  (async () => {
    try {
      const res = await fetch(`${_baseUrl}/kirana/admin/logs/stream?tail=${tail}`, {
        headers: { 'X-API-Key': _apiKey },
        signal: ctrl.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data:'));
          if (line) {
            try { onEvent(JSON.parse(line.slice(5).trim())); } catch {}
          }
        }
      }
      onClose(null);
    } catch (e) {
      if (e.name !== 'AbortError') onClose(e);
    }
  })();
  return () => ctrl.abort();
}

export const api = {
  health:          ()                     => request('GET',  '/kirana/health'),
  
  // Dashboard
  stats:           ()                     => request('GET',  '/kirana/admin/stats'),
  
  // Admin Management
  adminStores:     ()                     => request('GET',  '/kirana/admin/stores'),
  storeDeepDive:   (id)                   => request('GET',  `/kirana/admin/stores/${id}/deep-dive`),
  adminProducts:   (params)               => request('GET',  '/kirana/admin/products', null, params),
  posCategories:   ()                     => request('GET',  '/kirana/admin/categories'),
  approveTrial:    (storeId)              => request('POST', `/kirana/admin/approve-trial/${storeId}`),
  extendTrial:     (storeId, days)        => request('POST', `/kirana/admin/extend-trial/${storeId}`, { days }),
  cancelSub:       (storeId)              => request('POST', `/kirana/admin/cancel-subscription/${storeId}`),
  
  // User Activity & Sessions
  userActivity:    ()                     => request('GET',  '/kirana/admin/user-activity'),
  adminSessions:   ()                     => request('GET',  '/kirana/admin/sessions'),
  
  // Intelligence
  intelTriggers:   ()                     => request('GET',  '/kirana/admin/intelligence/triggers'),
  fireTrigger:     (name)                 => request('POST', `/kirana/admin/intelligence/fire/${name}`),
  intelLogs:       (limit = 100)          => request('GET',  '/kirana/admin/intelligence/all-logs', null, { limit }),
  
  // Loyalty
  adminVouchers:   ()                     => request('GET',  '/kirana/admin/vouchers'),
  
  // KPI Config
  getKpiTiers:     ()                     => request('GET',  '/kirana/admin/kpi-tiers'),
  saveKpiTiers:    (configs)              => request('PUT',  '/kirana/admin/kpi-tiers', { configs }),
  // F4 — per-vertical KPI visibility (show/hide per vertical, live)
  getKpiVisibility:  ()                   => request('GET',  '/kirana/admin/kpi-visibility'),
  saveKpiVisibility: (configs)            => request('PUT',  '/kirana/admin/kpi-visibility', { configs }),
  
  // M2 — Store groups (multi-store rollup)
  listStoreGroups: ()                     => request('GET',  '/kirana/admin/store-groups'),
  createStoreGroup:(name, storeIds, ownerUserId) => request('POST', '/kirana/admin/store-groups', { name, store_ids: storeIds, owner_user_id: ownerUserId }),
  assignStoreGroup:(storeId, groupId)     => request('POST', `/kirana/admin/stores/${storeId}/group`, { group_id: groupId }),

  // M1 — Loyalty overview
  loyaltyOverview: ()                     => request('GET',  '/kirana/admin/loyalty/overview'),

  // M5 / M7 — per-store staff & serial ops (back-office)
  adminStaff:      (storeId)              => request('GET',  `/kirana/admin/stores/${storeId}/staff`),
  adminBulkStaff:  (storeId, staff)       => request('POST', `/kirana/admin/stores/${storeId}/staff/bulk`, { staff }),
  adminSerials:    (storeId, params)      => request('GET',  `/kirana/admin/stores/${storeId}/serials`, null, params),
  adminBulkSerials:(storeId, body)        => request('POST', `/kirana/admin/stores/${storeId}/serials/bulk`, body),

  // System & ML
  mlStatus:        ()                     => request('GET',  '/kirana/admin/ml/status'),
  mlRetrain:       ()                     => request('POST', '/kirana/admin/ml/retrain'),
  logs:            (lines = 200, level = '') => request('GET',  '/kirana/admin/logs', null, { lines, level }),

  // Support / Issue reports
  listIssues:      (limit=200)            => request('GET',  `/oltp/issue_report?limit=${limit}`),
  updateIssue:     (reportId, data)       => request('PATCH', '/oltp/issue_report/record', { keys: { report_id: reportId }, data }),
  
  // Cashflow requests
  listCashflow:    (limit=200)            => request('GET',  `/oltp/cashflow_requests?limit=${limit}`),

  // WhatsApp
  waHealth:        ()                     => request('GET',  '/whatsapp/health'),
  waSession:       (phone)               => request('GET',  `/whatsapp/session/${encodeURIComponent(phone)}`),
  waResetSession:  (phone)               => request('DELETE', `/whatsapp/session/${encodeURIComponent(phone)}`),
  waSend:          (phone, message)      => request('POST', '/whatsapp/send/text', { phone_number: phone, message }),
  waLinkStore:     (phone, storeId)      => request('POST', '/whatsapp/session/link-store', { phone, store_id: parseInt(storeId) }),
};
