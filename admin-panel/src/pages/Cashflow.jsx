import React, { useEffect, useState } from 'react';
import { api } from '../api';

export default function Cashflow() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchRequests();
    const interval = setInterval(() => {
      fetchRequests(false);
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchRequests = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.listCashflow();
      setRequests(data.items || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Cashflow Requests</h1>
          <p className="text-slate-500 text-sm mt-1">Manage credit and cashflow loan requests from stores.</p>
        </div>
        <button onClick={fetchRequests} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          Refresh Data
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">ID</th>
                <th className="px-6 py-4">Store ID</th>
                <th className="px-6 py-4">Amount</th>
                <th className="px-6 py-4">Bank</th>
                <th className="px-6 py-4">Status</th>
                <th className="px-6 py-4">Requested At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">Loading requests...</td></tr>
              ) : requests.length === 0 ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">No requests found.</td></tr>
              ) : (
                requests.map(req => (
                  <tr key={req.request_id} className="hover:bg-slate-50/50">
                    <td className="px-6 py-4 font-mono text-slate-400">#{req.request_id}</td>
                    <td className="px-6 py-4 font-medium text-slate-900">{req.store_id}</td>
                    <td className="px-6 py-4 font-bold text-emerald-600">₹{req.amount_requested}</td>
                    <td className="px-6 py-4">{req.selected_bank}</td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${
                        req.status === 'pending' ? 'bg-amber-100 text-amber-800' :
                        req.status === 'approved' ? 'bg-emerald-100 text-emerald-800' :
                        'bg-slate-100 text-slate-800'
                      }`}>
                        {req.status}
                      </span>
                    </td>
                    <td className="px-6 py-4">{formatDate(req.created_at)}</td>
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
