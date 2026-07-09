import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';

export default function Assignments() {
  const ui = useUI();
  const [execs, setExecs] = useState([]);
  const [stores, setStores] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [targetExec, setTargetExec] = useState('');
  const [search, setSearch] = useState('');
  const [unassignedOnly, setUnassignedOnly] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadExecs = async () => {
    try { setExecs((await api.ccLoad()).items || []); } catch (e) { ui.toast(e.message, 'error'); }
  };
  const loadStores = async () => {
    setLoading(true);
    try {
      const data = await api.ccAssignableStores({ q: search || undefined, unassigned_only: unassignedOnly || undefined });
      setStores(data.items || []);
      setSelected(new Set());
    } catch (e) { ui.toast(e.message, 'error'); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadExecs(); }, []);
  useEffect(() => {
    const t = setTimeout(loadStores, 250);   // debounce search
    return () => clearTimeout(t);
  }, [search, unassignedOnly]);

  const toggle = (id) => setSelected((cur) => {
    const n = new Set(cur);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  const assign = async () => {
    if (!targetExec) { ui.toast('Pick an executive to assign to', 'error'); return; }
    if (selected.size === 0) { ui.toast('Select at least one store', 'error'); return; }
    try {
      const res = await api.ccAssign(Number(targetExec), [...selected]);
      ui.toast(`Assigned ${res.assigned} store(s)`, 'success');
      await Promise.all([loadStores(), loadExecs()]);
    } catch (e) { ui.toast(e.message, 'error'); }
  };

  const unassign = async (store) => {
    if (!(await ui.confirm({ title: 'Unassign store?', message: `Remove ${store.store_name} from ${store.assigned_executive_name}.` }))) return;
    try {
      await api.ccUnassign(store.store_id);
      ui.toast('Unassigned', 'success');
      await Promise.all([loadStores(), loadExecs()]);
    } catch (e) { ui.toast(e.message, 'error'); }
  };

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">Store Assignments</h1>
        <p className="text-slate-500 text-xs mt-0.5">Assign kirana stores to call executives and balance the load.</p>
      </div>

      {/* Workload */}
      <div className="flex flex-wrap gap-2">
        {execs.map((e) => (
          <div key={e.executive_id} className="bg-white border border-slate-200 rounded-lg px-3 py-2 text-xs shadow-sm">
            <span className="font-semibold text-slate-700">{e.full_name}</span>
            <span className="ml-2 text-slate-400">{e.active_stores} stores</span>
          </div>
        ))}
        {execs.length === 0 && <p className="text-xs text-slate-400 italic">No executives yet — add one first.</p>}
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 bg-white border border-slate-200 rounded-xl p-3 shadow-sm">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search stores…"
          className="flex-1 min-w-[160px] border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
          <input type="checkbox" checked={unassignedOnly} onChange={(e) => setUnassignedOnly(e.target.checked)} />
          Unassigned only
        </label>
        <div className="flex items-center gap-2 ml-auto">
          <select value={targetExec} onChange={(e) => setTargetExec(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500">
            <option value="">Assign to…</option>
            {execs.map((e) => <option key={e.executive_id} value={e.executive_id}>{e.full_name}</option>)}
          </select>
          <button onClick={assign} disabled={selected.size === 0 || !targetExec}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-bold px-4 py-2 rounded-lg">
            Assign ({selected.size})
          </button>
        </div>
      </div>

      {/* Stores */}
      {loading ? (
        <div className="text-slate-500 p-8">Loading stores…</div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm text-left">
            <thead className="text-slate-400 font-bold uppercase tracking-wider text-[11px] bg-slate-50">
              <tr>
                <th className="py-2.5 px-4 w-10"></th>
                <th className="py-2.5 px-3">Store</th>
                <th className="py-2.5 px-3">Location</th>
                <th className="py-2.5 px-3">Assigned to</th>
                <th className="py-2.5 px-4 text-right"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {stores.map((s) => (
                <tr key={s.store_id} className="hover:bg-slate-50">
                  <td className="py-2.5 px-4">
                    <input type="checkbox" checked={selected.has(s.store_id)} onChange={() => toggle(s.store_id)} />
                  </td>
                  <td className="py-2.5 px-3 font-semibold text-slate-800">{s.store_name}</td>
                  <td className="py-2.5 px-3 text-slate-500">{s.location || '—'}</td>
                  <td className="py-2.5 px-3">
                    {s.assigned_executive_name
                      ? <span className="text-slate-700">{s.assigned_executive_name}</span>
                      : <span className="text-slate-300 italic">Unassigned</span>}
                  </td>
                  <td className="py-2.5 px-4 text-right">
                    {s.assigned_executive_id && (
                      <button onClick={() => unassign(s)} className="text-xs font-semibold text-red-500 hover:text-red-700">Unassign</button>
                    )}
                  </td>
                </tr>
              ))}
              {stores.length === 0 && (
                <tr><td colSpan={5} className="py-10 text-center text-slate-300 italic">No stores match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
