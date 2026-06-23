import React, { useEffect, useState } from 'react';
import { api } from '../api';
import Badge from '../components/Badge';

export default function Users() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUsers();
    const interval = setInterval(() => fetchUsers(false), 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchUsers = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.userActivity();
      setUsers(data.users || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const formatDateTime = (dateStr) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString('en-IN', {
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit',
      hour12: true
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight">User Analytics</h1>
          <p className="text-slate-500 text-xs mt-0.5">Store-owner engagement, app interaction, and how many stores each owner runs.</p>
        </div>
        <div className="flex items-center gap-3">
          {loading && <span className="text-[10px] font-bold text-indigo-500 animate-pulse uppercase">Syncing...</span>}
          <button onClick={() => fetchUsers()} className="text-xs font-bold text-slate-600 bg-white border border-slate-200 px-3 py-1.5 rounded-lg hover:bg-slate-50 transition-colors shadow-sm uppercase tracking-wider">
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600 border-collapse">
            <thead>
              <tr className="bg-slate-50/80 border-b border-slate-200">
                <th className="px-6 py-4 text-[11px] font-bold text-slate-500 uppercase tracking-widest">Store Owner</th>
                <th className="px-6 py-4 text-[11px] font-bold text-slate-500 uppercase tracking-widest">Stores Handled</th>
                <th className="px-6 py-4 text-[11px] font-bold text-slate-500 uppercase tracking-widest">Last Seen / Login</th>
                <th className="px-6 py-4 text-[11px] font-bold text-slate-500 uppercase tracking-widest text-center">App Engagement (Today)</th>
                <th className="px-6 py-4 text-[11px] font-bold text-slate-500 uppercase tracking-widest text-right">Lifetime</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && users.length === 0 ? (
                <tr><td colSpan="5" className="px-6 py-12 text-center text-slate-400">Loading user activity data...</td></tr>
              ) : users.length === 0 ? (
                <tr><td colSpan="5" className="px-6 py-12 text-center text-slate-400">No active users found.</td></tr>
              ) : (
                users.map(u => (
                  <tr key={u.user_id} className="group hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="font-black text-slate-900">{u.full_name || u.username}</div>
                      <div className="text-xs text-slate-400 flex items-center gap-1 mt-0.5">
                        <span className="font-mono bg-slate-100 px-1 rounded">#{u.user_id}</span>
                        <span>@{u.username}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 rounded-md bg-indigo-50 text-indigo-700 text-sm font-black">
                          {u.stores_owned ?? 1}
                        </span>
                        <div className="text-xs text-slate-500">
                          active: <span className="font-semibold text-slate-700">{u.store_name || '—'}</span>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="text-xs font-medium text-slate-900">{formatDateTime(u.last_seen)}</div>
                      <div className="text-[10px] text-slate-400 flex items-center gap-2 mt-1">
                        <Badge color="bg-indigo-50 text-indigo-600">{u.last_login_method || 'N/A'}</Badge>
                        <span className="opacity-50">Last Login: {formatDateTime(u.last_login)}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex justify-center gap-6">
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-400 uppercase">Opens</div>
                          <div className={`text-lg font-black ${u.opens_today > 0 ? 'text-indigo-600' : 'text-slate-300'}`}>{u.opens_today}</div>
                        </div>
                        <div className="text-center border-x border-slate-100 px-6">
                          <div className="text-[10px] font-bold text-slate-400 uppercase">Time</div>
                          <div className={`text-lg font-black ${u.foreground_sec_today > 0 ? 'text-indigo-600' : 'text-slate-300'}`}>
                            {Math.round(u.foreground_sec_today / 60)}m
                          </div>
                        </div>
                        <div className="text-center">
                          <div className="text-[10px] font-bold text-slate-400 uppercase">Sales</div>
                          <div className={`text-lg font-black ${u.sales_today > 0 ? 'text-emerald-600' : 'text-slate-300'}`}>{u.sales_today}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right">
                       <div className="text-lg font-black text-slate-900">{u.total_sessions}</div>
                       <div className="text-[10px] text-slate-400 font-bold uppercase">Total Sessions</div>
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
