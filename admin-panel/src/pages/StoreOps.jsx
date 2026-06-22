import React, { useEffect, useState } from 'react';
import { api } from '../api';

// M5 / M7 — per-store back-office ops: view staff + bulk-add staff, and view
// serial register + bulk-register serials (warehouse intake) for one product.
export default function StoreOps() {
  const [stores, setStores] = useState([]);
  const [storeId, setStoreId] = useState('');
  const [staff, setStaff] = useState([]);
  const [serials, setSerials] = useState([]);
  const [loading, setLoading] = useState(false);

  // bulk inputs
  const [staffText, setStaffText] = useState('');
  const [serialProduct, setSerialProduct] = useState('');
  const [serialText, setSerialText] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.adminStores().then(d => setStores(d.stores || [])).catch(console.error); }, []);

  const load = async (sid) => {
    if (!sid) { setStaff([]); setSerials([]); return; }
    setLoading(true);
    try {
      const [st, se] = await Promise.all([api.adminStaff(sid), api.adminSerials(sid, {})]);
      setStaff(st.staff || []); setSerials(se.serials || []);
    } catch (e) { console.error(e); } finally { setLoading(false); }
  };

  const onStore = (sid) => { setStoreId(sid); load(sid); };

  const bulkStaff = async () => {
    const lines = staffText.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length || !storeId) return;
    // each line: Name, phone, role
    const rows = lines.map(l => {
      const [name, phone, role] = l.split(',').map(s => (s || '').trim());
      return { name, phone: phone || null, role: role || null };
    });
    setBusy(true);
    try { const r = await api.adminBulkStaff(storeId, rows); alert(`Added ${r.created} staff`); setStaffText(''); load(storeId); }
    catch (e) { alert(`Failed: ${e.message}`); } finally { setBusy(false); }
  };

  const bulkSerials = async () => {
    const list = serialText.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
    if (!list.length || !storeId || !serialProduct) { alert('Need product id + serials'); return; }
    setBusy(true);
    try {
      const r = await api.adminBulkSerials(storeId, { product_id: Number(serialProduct), serials: list });
      alert(`Added ${r.added}, skipped ${r.skipped}`); setSerialText(''); load(storeId);
    } catch (e) { alert(`Failed: ${e.message}`); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Store Ops — Staff &amp; Serials</h1>
        <p className="text-slate-500 text-sm mt-1">Back-office bulk operations: onboard staff and register IMEI/serial stock for a store.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
        <label className="text-xs font-semibold text-slate-500">Store</label>
        <select value={storeId} onChange={e => onStore(e.target.value)}
          className="mt-1 block w-full max-w-md border border-slate-300 rounded-lg px-3 py-2 text-sm">
          <option value="">— select a store —</option>
          {stores.map(s => <option key={s.store_id} value={s.store_id}>{s.store_name} (#{s.store_id})</option>)}
        </select>
      </div>

      {!storeId ? null : loading ? <div className="p-12 text-center text-slate-400">Loading…</div> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Staff */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-4">
            <div className="font-bold text-slate-800">Staff ({staff.length})</div>
            <ul className="text-sm divide-y divide-slate-100 max-h-56 overflow-y-auto">
              {staff.map(s => (
                <li key={s.staff_id} className="py-1.5 flex justify-between">
                  <span>{s.name} {s.role && <span className="text-slate-400">· {s.role}</span>}</span>
                  <span className="text-slate-400">{s.phone || ''}</span>
                </li>
              ))}
              {staff.length === 0 && <li className="py-2 text-slate-400">No staff.</li>}
            </ul>
            <div>
              <label className="text-xs font-semibold text-slate-500">Bulk add (one per line: <code>Name, phone, role</code>)</label>
              <textarea value={staffText} onChange={e => setStaffText(e.target.value)} rows={4}
                placeholder={"Ramesh, 9876543210, cashier\nSita, , sales"}
                className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono" />
              <button onClick={bulkStaff} disabled={busy}
                className="mt-2 bg-indigo-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">Add staff</button>
            </div>
          </div>

          {/* Serials */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-4">
            <div className="font-bold text-slate-800">Serial register ({serials.length})</div>
            <ul className="text-sm divide-y divide-slate-100 max-h-56 overflow-y-auto">
              {serials.map(s => (
                <li key={s.serial_id} className="py-1.5 flex justify-between">
                  <span className="font-mono">{s.serial_no}</span>
                  <span className="text-slate-400">{s.status}</span>
                </li>
              ))}
              {serials.length === 0 && <li className="py-2 text-slate-400">No serials.</li>}
            </ul>
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-500">Bulk register for product</label>
              <input value={serialProduct} onChange={e => setSerialProduct(e.target.value)} placeholder="product_id"
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" />
              <textarea value={serialText} onChange={e => setSerialText(e.target.value)} rows={4}
                placeholder={"serial / IMEI per line or comma-separated"}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono" />
              <button onClick={bulkSerials} disabled={busy}
                className="bg-indigo-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">Register serials</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
