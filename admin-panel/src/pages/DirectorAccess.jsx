import React, { useEffect, useState } from 'react';
import { api, getBaseUrl } from '../api';
import { useUI } from '../components/UIProvider';

// Curate which stores' analytics appear in the director dashboard.
// Toggling a store OFF hides it from every fleet-wide metric + the store filter.
export default function DirectorAccess() {
  const ui = useUI();
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(null);   // store_id currently saving
  const [q, setQ] = useState('');

  useEffect(() => { fetchStores(); }, []);

  const fetchStores = async () => {
    setLoading(true);
    try {
      const d = await api.directorStores();
      setStores(d.stores || []);
    } catch (e) {
      ui.toast(`Couldn't load stores: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const toggle = async (store) => {
    const next = !store.include_in_director;
    setSaving(store.store_id);
    // Optimistic update.
    setStores(prev => prev.map(s =>
      s.store_id === store.store_id ? { ...s, include_in_director: next } : s));
    try {
      await api.setDirectorStore(store.store_id, next);
      ui.toast(`${store.name} ${next ? 'added to' : 'hidden from'} director view`, 'success');
    } catch (e) {
      // Roll back on failure.
      setStores(prev => prev.map(s =>
        s.store_id === store.store_id ? { ...s, include_in_director: !next } : s));
      ui.toast(`Update failed: ${e.message}`, 'error');
    } finally {
      setSaving(null);
    }
  };

  const included = stores.filter(s => s.include_in_director).length;
  const excluded = stores.length - included;
  const filtered = stores.filter(s => {
    const t = q.trim().toLowerCase();
    if (!t) return true;
    return (s.name || '').toLowerCase().includes(t)
      || (s.location || '').toLowerCase().includes(t)
      || String(s.store_id).includes(t);
  });

  const dashboardUrl = `${getBaseUrl()}/director`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Director Access</h1>
        <p className="text-slate-500 text-sm mt-1">
          Choose which stores' analytics the director sees. Switch OFF dev, test, or internal
          stores — they're removed from every fleet-wide metric and the store filter.
        </p>
      </div>

      {/* Dashboard link + counts */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Included</div>
          <div className="text-3xl font-black text-emerald-600">{included}</div>
        </div>
        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Hidden</div>
          <div className="text-3xl font-black text-slate-400">{excluded}</div>
        </div>
        <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Dashboard</div>
          <a href={dashboardUrl} target="_blank" rel="noreferrer"
             className="text-sm font-semibold text-indigo-600 hover:text-indigo-800 hover:underline break-all">
            {dashboardUrl} ↗
          </a>
          <p className="text-[11px] text-slate-400 mt-1">Share with the director's access token appended.</p>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search stores by name, location, or ID…"
          className="flex-1 max-w-md px-4 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
        />
        <button onClick={fetchStores}
          className="text-sm font-medium text-indigo-600 bg-indigo-50 px-3 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          ↻ Refresh
        </button>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-4 py-3">ID</th>
                <th className="px-4 py-3">Store Name &amp; Location</th>
                <th className="px-4 py-3">Vertical</th>
                <th className="px-4 py-3 text-right">In Director View</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="4" className="px-4 py-6 text-center text-slate-400">Loading stores…</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan="4" className="px-4 py-6 text-center text-slate-400">No stores found.</td></tr>
              ) : filtered.map(store => (
                <tr key={store.store_id} className={`hover:bg-slate-50/50 ${!store.include_in_director ? 'bg-slate-50/40' : ''}`}>
                  <td className="px-4 py-2.5 font-mono text-slate-400">#{store.store_id}</td>
                  <td className="px-4 py-2.5">
                    <div className="font-semibold text-slate-900">{store.name}</div>
                    <div className="text-xs text-slate-400">{store.location || 'No location'}</div>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="text-xs font-medium text-slate-500 bg-slate-100 px-2 py-1 rounded-full capitalize">
                      {store.vertical_code}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => toggle(store)}
                      disabled={saving === store.store_id}
                      role="switch"
                      aria-checked={store.include_in_director}
                      title={store.include_in_director ? 'Included — click to hide' : 'Hidden — click to include'}
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50
                        ${store.include_in_director ? 'bg-emerald-500' : 'bg-slate-300'}`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform
                        ${store.include_in_director ? 'translate-x-6' : 'translate-x-1'}`} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
