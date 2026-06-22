import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api';

// M2 — Store groups. Create a chain group and assign stores to it.
// The shopkeeper app's Store Comparison screen reads the resulting rollup.
export default function StoreGroups() {
  const [groups, setGroups] = useState([]);
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [g, s] = await Promise.all([api.listStoreGroups(), api.adminStores()]);
      setGroups(g.groups || []);
      setStores(s.stores || []);
    } catch (e) { console.error(e); } finally { setLoading(false); }
  };

  // store_id -> group_id (for the assign dropdown current value)
  const storeGroup = useMemo(() => {
    const m = {};
    for (const g of groups) for (const st of (g.stores || [])) m[st.store_id] = g.group_id;
    return m;
  }, [groups]);

  const createGroup = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    try { await api.createStoreGroup(newName.trim(), [], null); setNewName(''); await fetchData(); }
    catch (e) { alert(`Failed: ${e.message}`); } finally { setBusy(false); }
  };

  const assign = async (storeId, groupId) => {
    try { await api.assignStoreGroup(storeId, groupId ? Number(groupId) : null); await fetchData(); }
    catch (e) { alert(`Failed: ${e.message}`); fetchData(); }
  };

  if (loading) return <div className="p-12 text-center text-slate-400">Loading…</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Store Groups (chains)</h1>
        <p className="text-slate-500 text-sm mt-1">Link an owner's outlets into a group so they get the multi-store rollup (zone/city comparison) in the app.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 flex items-end gap-3">
        <div className="flex-1">
          <label className="text-xs font-semibold text-slate-500">New group name</label>
          <input value={newName} onChange={e => setNewName(e.target.value)}
            placeholder="e.g. Sharma Supermart (all outlets)"
            className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        <button onClick={createGroup} disabled={busy}
          className="bg-indigo-600 text-white text-sm font-medium px-5 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
          Create group
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {groups.map(g => (
          <div key={g.group_id} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <div className="font-bold text-slate-800">{g.name}</div>
            <div className="text-xs text-slate-400 mb-3">
              #{g.group_id}{g.owner_name ? ` · owner ${g.owner_name}` : ''} · {(g.stores || []).length} stores
            </div>
            {(g.stores || []).length === 0
              ? <div className="text-sm text-slate-400">No stores yet — assign below.</div>
              : <ul className="text-sm text-slate-700 space-y-1">
                  {g.stores.map(st => (
                    <li key={st.store_id} className="flex justify-between items-center">
                      <span>{st.store_name} <span className="text-slate-400">· {st.area}</span></span>
                      <button onClick={() => assign(st.store_id, null)}
                        className="text-xs text-rose-600 hover:underline">remove</button>
                    </li>
                  ))}
                </ul>}
          </div>
        ))}
        {groups.length === 0 && <div className="text-slate-400 text-sm">No groups yet.</div>}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 font-semibold text-slate-700">Assign stores to a group</div>
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 border-b border-slate-200">
            <th className="text-left font-semibold text-slate-600 px-4 py-2">Store</th>
            <th className="text-left font-semibold text-slate-600 px-4 py-2">Owner</th>
            <th className="text-left font-semibold text-slate-600 px-4 py-2">Group</th>
          </tr></thead>
          <tbody>
            {stores.map(s => (
              <tr key={s.store_id} className="border-b border-slate-100">
                <td className="px-4 py-2 font-medium text-slate-800">{s.store_name}</td>
                <td className="px-4 py-2 text-slate-500">{s.owner_name || '—'}</td>
                <td className="px-4 py-2">
                  <select value={storeGroup[s.store_id] || ''} onChange={e => assign(s.store_id, e.target.value)}
                    className="border border-slate-300 rounded-lg px-2 py-1 text-sm">
                    <option value="">— ungrouped —</option>
                    {groups.map(g => <option key={g.group_id} value={g.group_id}>{g.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
