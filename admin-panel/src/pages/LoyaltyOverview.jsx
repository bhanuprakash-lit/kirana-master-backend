import React, { useEffect, useState } from 'react';
import { api } from '../api';

// M1 — Loyalty adoption overview (read-only). Which stores enabled loyalty,
// their earn/redeem rates, member counts, outstanding points + ₹ liability.
export default function LoyaltyOverview() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => { (async () => {
    try { const d = await api.loyaltyOverview(); setRows(d.stores || []); }
    catch (e) { console.error(e); } finally { setLoading(false); }
  })(); }, []);

  if (loading) return <div className="p-12 text-center text-slate-400">Loading…</div>;

  const active = rows.filter(r => r.is_active).length;
  const totalLiability = rows.reduce((a, r) => a + Number(r.liability || 0), 0);
  const totalMembers = rows.reduce((a, r) => a + Number(r.members || 0), 0);

  const Stat = ({ label, value }) => (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
      <div className="text-xs font-semibold text-slate-400 uppercase">{label}</div>
      <div className="text-2xl font-bold text-slate-900 mt-1">{value}</div>
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Loyalty Overview</h1>
        <p className="text-slate-500 text-sm mt-1">Adoption and points liability across all stores that turned loyalty on.</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Stores w/ loyalty" value={rows.length} />
        <Stat label="Active" value={active} />
        <Stat label="Total members" value={totalMembers} />
        <Stat label="Points liability" value={`₹${totalLiability.toLocaleString('en-IN')}`} />
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 border-b border-slate-200">
            {['Store','Status','Earn (pts/₹100)','Redeem (paise/pt)','Members','Points out','Liability','Coupons'].map(h =>
              <th key={h} className="text-left font-semibold text-slate-600 px-4 py-3 whitespace-nowrap">{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.store_id} className="border-b border-slate-100 hover:bg-slate-50/50">
                <td className="px-4 py-3 font-medium text-slate-800">{r.store_name}</td>
                <td className="px-4 py-3">
                  {r.is_active
                    ? <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">Active</span>
                    : <span className="text-xs font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded">Off</span>}
                </td>
                <td className="px-4 py-3">{r.points_per_100}</td>
                <td className="px-4 py-3">{r.redeem_paise_per_point}</td>
                <td className="px-4 py-3">{r.members}</td>
                <td className="px-4 py-3">{Number(r.points_outstanding).toLocaleString('en-IN')}</td>
                <td className="px-4 py-3 font-medium">₹{Number(r.liability).toLocaleString('en-IN')}</td>
                <td className="px-4 py-3">{r.coupons_active}/{r.coupons_total}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-400">No store has enabled loyalty yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
