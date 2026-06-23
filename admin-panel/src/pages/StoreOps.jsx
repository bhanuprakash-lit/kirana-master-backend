import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { useUI } from '../components/UIProvider';

// M5 / M7 — per-store back-office: onboard staff and register serial/IMEI stock.
// Professional forms: typed staff fields + a searchable product picker (no raw ids).
export default function StoreOps() {
  const ui = useUI();
  const [stores, setStores] = useState([]);
  const [storeId, setStoreId] = useState('');
  const [staff, setStaff] = useState([]);
  const [serials, setSerials] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  // Staff form
  const [sName, setSName] = useState('');
  const [sPhone, setSPhone] = useState('');
  const [sRole, setSRole] = useState('cashier');

  // Serial form
  const [prodQuery, setProdQuery] = useState('');
  const [prodResults, setProdResults] = useState([]);
  const [selProduct, setSelProduct] = useState(null); // {product_id, name}
  const [serialText, setSerialText] = useState('');

  useEffect(() => { api.adminStores().then(d => setStores(d.stores || [])).catch(() => {}); }, []);

  const load = async (sid) => {
    if (!sid) { setStaff([]); setSerials([]); return; }
    setLoading(true);
    try {
      const [st, se] = await Promise.all([api.adminStaff(sid), api.adminSerials(sid, {})]);
      setStaff(st.staff || []); setSerials(se.serials || []);
    } catch (e) { ui.toast(e.message, 'error'); } finally { setLoading(false); }
  };

  const onStore = (sid) => { setStoreId(sid); setSelProduct(null); setProdResults([]); load(sid); };

  const addStaff = async () => {
    if (!storeId || !sName.trim()) { ui.toast('Enter a staff name', 'error'); return; }
    setBusy(true);
    try {
      await api.adminBulkStaff(storeId, [{ name: sName.trim(), phone: sPhone.trim() || null, role: sRole }]);
      ui.toast('Staff added', 'success');
      setSName(''); setSPhone(''); setSRole('cashier');
      load(storeId);
    } catch (e) { ui.toast(`Failed: ${e.message}`, 'error'); } finally { setBusy(false); }
  };

  const searchProducts = async (q) => {
    setProdQuery(q);
    if (q.trim().length < 2) { setProdResults([]); return; }
    try {
      const d = await api.adminProducts({ q: q.trim(), limit: 8 });
      setProdResults(d.products || []);
    } catch { setProdResults([]); }
  };

  const registerSerials = async () => {
    const list = serialText.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
    if (!storeId || !selProduct) { ui.toast('Pick a product first', 'error'); return; }
    if (!list.length) { ui.toast('Enter at least one serial', 'error'); return; }
    setBusy(true);
    try {
      const r = await api.adminBulkSerials(storeId, { product_id: selProduct.product_id, serials: list });
      ui.toast(`Registered ${r.added}, skipped ${r.skipped}`, 'success');
      setSerialText('');
      load(storeId);
    } catch (e) { ui.toast(`Failed: ${e.message}`, 'error'); } finally { setBusy(false); }
  };

  const field = "w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500";

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-slate-900">Store Ops — Staff &amp; Serials</h1>
        <p className="text-slate-500 text-xs mt-0.5">Back-office: onboard staff and register IMEI/serial stock for a store.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 max-w-md">
        <label className="text-xs font-semibold text-slate-500">Store</label>
        <select value={storeId} onChange={e => onStore(e.target.value)} className={`mt-1 ${field}`}>
          <option value="">— select a store —</option>
          {stores.map(s => <option key={s.store_id} value={s.store_id}>{s.store_name} (#{s.store_id})</option>)}
        </select>
      </div>

      {!storeId ? null : loading ? <div className="p-12 text-center text-slate-400">Loading…</div> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Staff */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 space-y-4">
            <div className="font-bold text-slate-800 text-sm">Staff <span className="text-slate-400 font-normal">({staff.length})</span></div>
            <ul className="text-sm divide-y divide-slate-100 max-h-48 overflow-y-auto">
              {staff.map(s => (
                <li key={s.staff_id} className="py-1.5 flex justify-between">
                  <span>{s.name} {s.role && <span className="text-slate-400 capitalize">· {s.role}</span>}</span>
                  <span className="text-slate-400">{s.phone || ''}</span>
                </li>
              ))}
              {staff.length === 0 && <li className="py-2 text-slate-400">No staff yet.</li>}
            </ul>
            <div className="border-t border-slate-100 pt-3 space-y-2">
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Add staff</div>
              <input value={sName} onChange={e => setSName(e.target.value)} placeholder="Full name" className={field} />
              <div className="flex gap-2">
                <input value={sPhone} onChange={e => setSPhone(e.target.value)} placeholder="Phone (optional)" className={field} />
                <select value={sRole} onChange={e => setSRole(e.target.value)} className={`${field} capitalize`}>
                  {['cashier', 'sales', 'manager', 'helper'].map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <button onClick={addStaff} disabled={busy}
                className="bg-indigo-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                Add staff
              </button>
            </div>
          </div>

          {/* Serials */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 space-y-4">
            <div className="font-bold text-slate-800 text-sm">Serial register <span className="text-slate-400 font-normal">({serials.length})</span></div>
            <ul className="text-sm divide-y divide-slate-100 max-h-48 overflow-y-auto">
              {serials.map(s => (
                <li key={s.serial_id} className="py-1.5 flex justify-between">
                  <span className="font-mono">{s.serial_no}</span>
                  <span className="text-slate-400">{s.status}</span>
                </li>
              ))}
              {serials.length === 0 && <li className="py-2 text-slate-400">No serials yet.</li>}
            </ul>
            <div className="border-t border-slate-100 pt-3 space-y-2">
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Register serials</div>
              {/* Product picker (search → pick; no raw ids) */}
              {selProduct ? (
                <div className="flex items-center justify-between bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2">
                  <span className="text-sm font-medium text-indigo-800">{selProduct.name}</span>
                  <button onClick={() => setSelProduct(null)} className="text-xs text-indigo-600 hover:underline">change</button>
                </div>
              ) : (
                <div className="relative">
                  <input value={prodQuery} onChange={e => searchProducts(e.target.value)}
                    placeholder="Search product to register against…" className={field} />
                  {prodResults.length > 0 && (
                    <div className="absolute z-10 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-52 overflow-y-auto">
                      {prodResults.map(p => (
                        <button key={p.product_id}
                          onClick={() => { setSelProduct(p); setProdResults([]); setProdQuery(''); }}
                          className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex justify-between">
                          <span>{p.name}</span>
                          <span className="text-slate-400 text-xs capitalize">{p.vertical_code}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <textarea value={serialText} onChange={e => setSerialText(e.target.value)} rows={3}
                placeholder="One serial / IMEI per line or comma-separated"
                className={`${field} font-mono`} />
              <button onClick={registerSerials} disabled={busy || !selProduct}
                className="bg-indigo-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                Register serials
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
