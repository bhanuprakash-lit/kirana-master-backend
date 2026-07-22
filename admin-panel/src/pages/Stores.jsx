import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useUI } from '../components/UIProvider';

export default function Stores() {
  const ui = useUI();
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [autoApprove, setAutoApprove] = useState(false);
  // PAI-19 — who opted in to "let LohiyaAI market my store".
  const [marketingOnly, setMarketingOnly] = useState(false);
  const [togglingAuto, setTogglingAuto] = useState(false);

  useEffect(() => {
    fetchStores();
    api.getAdminSettings().then(s => setAutoApprove(s.auto_approve_trial)).catch(() => { });
    const interval = setInterval(() => fetchStores(false), 30000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleAutoApprove = async () => {
    setTogglingAuto(true);
    try {
      const next = !autoApprove;
      const result = await api.setAdminSettings({ auto_approve_trial: next });
      setAutoApprove(result.auto_approve_trial);
      ui.toast(result.auto_approve_trial ? 'Auto-approve ON — new trial requests will be approved instantly' : 'Auto-approve OFF — requests need manual approval', 'success');
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    } finally {
      setTogglingAuto(false);
    }
  };

  const fetchStores = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.adminStores();
      setStores(data.stores || []);
    } catch (e) {
      ui.toast(`Could not load stores: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = marketingOnly ? stores.filter(s => s.allow_social_marketing) : stores;
    if (!q) return base;
    return base.filter(s =>
      (s.store_name || '').toLowerCase().includes(q) ||
      (s.owner_name || '').toLowerCase().includes(q) ||
      (s.location || '').toLowerCase().includes(q) ||
      (s.vertical_code || '').toLowerCase().includes(q) ||
      String(s.store_id).includes(q)
    );
  }, [stores, query, marketingOnly]);

  const handleApproveTrial = async (storeId) => {
    try {
      await api.approveTrial(storeId);
      ui.toast('Trial approved', 'success');
      fetchStores();
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  const handleCancelSub = async (storeId) => {
    if (!(await ui.confirm({ title: 'Cancel subscription?', message: 'This store will lose its plan.', danger: true, confirmLabel: 'Cancel plan' }))) return;
    try {
      await api.cancelSub(storeId);
      ui.toast('Subscription cancelled', 'success');
      fetchStores();
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end gap-4">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Stores Management</h1>
          <p className="text-slate-500 text-xs mt-0.5">All stores with their true owner, vertical, and subscription. {stores.length} total.</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Auto-approve trial toggle */}
          {/* Auto Approve Toggle */}
          <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2">
            <span className="text-sm font-medium text-slate-700">
              Auto-approve trials
            </span>

            <button
              type="button"
              onClick={handleToggleAutoApprove}
              disabled={togglingAuto}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200 ${autoApprove
                  ? "bg-emerald-500"
                  : "bg-slate-300"
                } ${togglingAuto ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
              aria-pressed={autoApprove}
            >
              <span
                className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 ${autoApprove ? "translate-x-5" : "translate-x-1"
                  }`}
              />
            </button>
          </div>
          <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={marketingOnly}
              onChange={(e) => setMarketingOnly(e.target.checked)}
              className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            />
            Marketing opt-in only
          </label>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search store, owner, vertical…"
            className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <button onClick={() => fetchStores()} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-3 py-1.5 rounded-lg hover:bg-indigo-100 transition-colors">
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-4 py-3">ID</th>
                <th className="px-4 py-3">Store Name & Location</th>
                <th className="px-4 py-3">Vertical</th>
                <th className="px-4 py-3">Owner</th>
                <th className="px-4 py-3">Plan / Tier</th>
                <th className="px-4 py-3">Marketing</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="7" className="px-4 py-4 text-center text-slate-400">Loading stores...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan="7" className="px-4 py-4 text-center text-slate-400">No stores found.</td></tr>
              ) : (
                filtered.map(store => (
                  <tr key={store.store_id} className="hover:bg-slate-50/50">
                    <td className="px-4 py-2.5 font-mono text-slate-400">#{store.store_id}</td>
                    <td className="px-4 py-2.5">
                      <div className="font-bold text-slate-900">
                        <Link to={`/stores/${store.store_id}`} className="hover:text-indigo-600 hover:underline">
                          {store.store_name}
                        </Link>
                      </div>
                      <div className="text-xs text-slate-500 flex items-center gap-1">
                        <span>📍</span> {store.location || 'No location'}
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-slate-100 text-slate-700 capitalize">
                        {store.vertical_code || 'grocery'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-slate-800">{store.owner_name || '—'}</span>
                        {store.owner_store_count > 1 && (
                          <span className="text-[10px] font-bold text-indigo-700 bg-indigo-50 px-1.5 py-0.5 rounded-full" title="Stores this owner runs">
                            {store.owner_store_count} stores
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-slate-400">{store.username ? `@${store.username}` : ''}</div>
                      <div className="text-xs text-indigo-500 font-medium">{store.phone_number || ''}</div>
                    </td>
                    <td className="px-4 py-2.5">
                      {store.tier === 'pending_trial' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800">Pending Trial</span>
                      ) : store.tier === 'pro' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">Pro</span>
                      ) : store.tier === 'basic' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">Basic</span>
                      ) : (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-600">{store.tier || 'None'}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {store.allow_social_marketing ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800" title="Owner allows LohiyaAI to market this store">
                          📣 Opted in
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-500">Off</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right space-x-3">
                      {store.tier === 'pending_trial' && (
                        <button onClick={() => handleApproveTrial(store.store_id)} className="text-emerald-600 font-semibold hover:text-emerald-800">Approve</button>
                      )}
                      {(store.tier === 'basic' || store.tier === 'pro' || store.tier === 'trial') && (
                        <button onClick={() => handleCancelSub(store.store_id)} className="text-red-500 font-medium hover:text-red-700">Cancel Plan</button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
