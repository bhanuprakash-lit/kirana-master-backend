import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';
import CallSheet from './CallSheet';

export default function Queue() {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [openStore, setOpenStore] = useState(null);

  const load = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.ccQueue();
      setItems(data.items || []);
    } catch (e) {
      ui.toast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">My Call Queue</h1>
        <p className="text-slate-500 text-xs mt-0.5">Stores to call, most important first. Click a store to log a call.</p>
      </div>

      {loading ? (
        <div className="text-slate-500 p-8">Loading your queue…</div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-400 italic">
          No stores assigned to you yet.
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm text-left">
            <thead className="text-slate-400 font-bold uppercase tracking-wider text-[11px] bg-slate-50">
              <tr>
                <th className="py-2.5 px-4">Store</th>
                <th className="py-2.5 px-3">Owner</th>
                <th className="py-2.5 px-3">Why call</th>
                <th className="py-2.5 px-3 text-center">Plan</th>
                <th className="py-2.5 px-3 text-right">Last call</th>
                <th className="py-2.5 px-4"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((s) => (
                <tr key={s.store_id} className="hover:bg-slate-50 cursor-pointer" onClick={() => setOpenStore(s.store_id)}>
                  <td className="py-2.5 px-4 font-semibold text-slate-800">
                    {s.store_name}
                    {s.callback_due && <span className="ml-2 text-[10px] bg-red-100 text-red-600 font-bold px-1.5 py-0.5 rounded-full">CALLBACK</span>}
                  </td>
                  <td className="py-2.5 px-3 text-slate-500">{s.owner_name || '—'}</td>
                  <td className="py-2.5 px-3">
                    <span className="text-amber-700 bg-amber-50 text-xs font-semibold px-2 py-0.5 rounded-full">{s.reason}</span>
                  </td>
                  <td className="py-2.5 px-3 text-center text-slate-500">{s.tier || '—'}</td>
                  <td className="py-2.5 px-3 text-right text-slate-400 text-xs">
                    {s.never_called ? 'Never' : new Date(s.last_call_at).toLocaleDateString()}
                  </td>
                  <td className="py-2.5 px-4 text-right">
                    <span className="text-indigo-600 font-semibold text-xs">Call →</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openStore && (
        <CallSheet storeId={openStore} onClose={() => setOpenStore(null)} onLogged={() => load(false)} />
      )}
    </div>
  );
}
