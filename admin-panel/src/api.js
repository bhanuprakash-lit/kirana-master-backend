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

/**
 * Open a live SSE log stream via fetch (supports custom headers, unlike EventSource).
 * @param {number} tail   - initial lines to backfill
 * @param {function} onEvent  - called with each parsed {raw, level} object
 * @param {function} onClose  - called with Error|null when stream ends
 * @returns {function} cancel - call to abort the stream
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
  // Stores
  adminStores:     ()                     => request('GET',  '/kirana/admin/stores'),
  mockPayment:     (storeId, tier)        => request('POST', '/kirana/admin/payment/mock-confirm', { store_id: storeId, tier }),
  // Trials & subscriptions
  pendingTrials:   ()                     => request('GET',  '/kirana/admin/pending-trials'),
  approveTrial:    (storeId, tier='basic') => request('POST', `/kirana/admin/approve-trial/${storeId}?tier=${tier}`),
  allSubs:         ()                     => request('GET',  '/kirana/admin/all-subscriptions'),
  cancelSub:       (storeId)              => request('POST', `/kirana/admin/cancel-subscription/${storeId}`),
  // Notifications
  notify:          (storeId, title, body) => request('POST', '/kirana/admin/notify', { store_id: storeId || null, title, body }),
  // KPI config
  getKpiTiers:     ()                     => request('GET',  '/kirana/admin/kpi-tiers'),
  saveKpiTiers:    (configs)              => request('PUT',  '/kirana/admin/kpi-tiers', { configs }),
  userActivity:    ()                     => request('GET',  '/kirana/admin/user-activity'),
  // ML model freshness + retraining
  mlStatus:        (refresh = false)      => request('GET',  `/kirana/admin/ml/status${refresh ? '?refresh=true' : ''}`),
  mlRetrain:       ()                     => request('POST', '/kirana/admin/ml/retrain'),
  // Support / Issue reports
  listIssues:      (limit=200)            => request('GET',  `/oltp/issue_report?limit=${limit}`),
  updateIssue:     (reportId, data)       => request('PATCH', '/oltp/issue_report/record', { keys: { report_id: reportId }, data }),
  // Cashflow requests
  listCashflow:    (limit=200)            => request('GET',  `/oltp/cashflow_requests?limit=${limit}`),
  // Inventory / Product catalog
  posCategories:  ()                      => request('GET',  '/kirana/admin/categories'),
  adminProducts:  (params)                => request('GET',  `/kirana/admin/products?${new URLSearchParams(params)}`),
  updateProduct:  (id, data)              => request('PATCH', `/kirana/admin/products/${id}`, data),
  // Server logs (snapshot)
  logs: (lines = 200, level = '') => request('GET', `/kirana/admin/logs?lines=${lines}${level ? `&level=${level}` : ''}`),
  // WhatsApp
  waHealth:        ()                     => request('GET',  '/whatsapp/health'),
  waSession:       (phone)               => request('GET',  `/whatsapp/session/${encodeURIComponent(phone)}`),
  waResetSession:  (phone)               => request('DELETE', `/whatsapp/session/${encodeURIComponent(phone)}`),
  waSend:          (phone, message)      => request('POST', '/whatsapp/send/text', { phone_number: phone, message }),
  waLinkStore:     (phone, storeId)      => request('POST', '/whatsapp/session/link-store', { phone, store_id: parseInt(storeId) }),
};
