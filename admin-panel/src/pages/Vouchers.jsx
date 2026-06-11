import React, { useEffect, useState } from 'react';
import { api } from '../api';
import Badge from '../components/Badge';

export default function Vouchers() {
  const [vouchers, setVouchers] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchVouchers();
    const interval = setInterval(() => fetchVouchers(false), 60000);
    return () => clearInterval(interval);
  }, []);

  const fetchVouchers = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.adminVouchers();
      setVouchers(data.vouchers || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const formatDateTime = (dateStr) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Referral Vouchers</h1>
          <p className="text-slate-500 text-sm mt-1">Manage all earned reward vouchers across referral campaigns.</p>
        </div>
        <button onClick={() => fetchVouchers()} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors uppercase tracking-widest text-[10px]">
          Refresh Vouchers
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs border-b border-slate-200">
              <tr>
                <th className="px-6 py-4">Voucher Code</th>
                <th className="px-6 py-4">Customer</th>
                <th className="px-6 py-4">Store</th>
                <th className="px-6 py-4">Campaign</th>
                <th className="px-6 py-4">Reward</th>
                <th className="px-6 py-4">Status</th>
                <th className="px-6 py-4 text-right">Earned At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && vouchers.length === 0 ? (
                <tr><td colSpan="7" className="px-6 py-4 text-center text-slate-400">Loading reward vouchers...</td></tr>
              ) : vouchers.length === 0 ? (
                <tr><td colSpan="7" className="px-6 py-4 text-center text-slate-400">No vouchers found in the system.</td></tr>
              ) : (
                vouchers.map(v => (
                  <tr key={v.voucher_id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="font-mono font-bold text-indigo-600 bg-indigo-50 px-2 py-1 rounded inline-block">
                        {v.voucher_code}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="font-bold text-slate-900">{v.customer_name}</div>
                      <div className="text-[10px] text-slate-400 uppercase font-black">ID #{v.customer_id}</div>
                    </td>
                    <td className="px-6 py-4 font-medium text-slate-700">{v.store_name}</td>
                    <td className="px-6 py-4 text-xs font-semibold text-slate-500">{v.campaign_name}</td>
                    <td className="px-6 py-4 font-black text-emerald-600">{v.discount_pct}% OFF</td>
                    <td className="px-6 py-4">
                      <Badge color={v.is_used ? 'bg-slate-100 text-slate-400' : 'bg-emerald-100 text-emerald-800'}>
                        {v.is_used ? 'Used' : 'Active'}
                      </Badge>
                      {v.is_used && (
                        <div className="text-[10px] text-slate-400 mt-1 font-medium italic">
                          Used on: {formatDateTime(v.used_at)}
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right text-[11px] font-medium text-slate-500">
                      {formatDateTime(v.earned_at)}
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
