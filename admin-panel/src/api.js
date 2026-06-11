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

async function request(method, path, body, params) {
  let url = `${_baseUrl}${path}`;
  if (params) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') qs.append(k, v); });
    if (qs.toString()) url += `?${qs.toString()}`;
  }

  const options = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': _apiKey,
    },
  };
  if (body !== undefined && method !== 'GET' && method !== 'HEAD') {
    options.body = JSON.stringify(body);
  }

  const res = await fetch(url, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
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
