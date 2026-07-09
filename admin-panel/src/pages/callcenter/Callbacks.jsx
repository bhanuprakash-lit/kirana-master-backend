import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';
import CallSheet from './CallSheet';

export default function Callbacks() {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [openStore, setOpenStore] = useState(null);

  const load = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.ccCallbacks();
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
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">My Callbacks</h1>
        <p className="text-slate-500 text-xs mt-0.5">Scheduled callbacks — overdue ones first.</p>
      </div>

      {loading ? (
        <div className="text-slate-500 p-8">Loading callbacks…</div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-400 italic">
          No scheduled callbacks. 🎉
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm text-left">
            <thead className="text-slate-400 font-bold uppercase tracking-wider text-[11px] bg-slate-50">
              <tr>
                <th className="py-2.5 px-4">Store</th>
                <th className="py-2.5 px-3">Phone</th>
                <th className="py-2.5 px-3">Due</th>
                <th className="py-2.5 px-4"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {items.map((c) => (
                <tr key={c.call_id} className="hover:bg-slate-50 cursor-pointer" onClick={() => setOpenStore(c.store_id)}>
                  <td className="py-2.5 px-4 font-semibold text-slate-800">
                    {c.store_name}
                    {c.overdue && <span className="ml-2 text-[10px] bg-red-100 text-red-600 font-bold px-1.5 py-0.5 rounded-full">OVERDUE</span>}
                  </td>
                  <td className="py-2.5 px-3 text-slate-500">{c.phone_number || '—'}</td>
                  <td className={`py-2.5 px-3 text-xs font-medium ${c.overdue ? 'text-red-600' : 'text-slate-500'}`}>
                    {new Date(c.callback_at).toLocaleString()}
                  </td>
                  <td className="py-2.5 px-4 text-right"><span className="text-indigo-600 font-semibold text-xs">Call →</span></td>
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
