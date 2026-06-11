import React, { useEffect, useState } from 'react';
import { api } from '../api';
import Badge from '../components/Badge';

export default function Sessions() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSessions();
    const interval = setInterval(() => fetchSessions(false), 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchSessions = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.adminSessions();
      setSessions(data.sessions || []);
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
          <h1 className="text-2xl font-bold text-slate-900">Active Sessions</h1>
          <p className="text-slate-500 text-sm mt-1">Monitor live user logins and device telemetry across the platform.</p>
        </div>
        <button onClick={() => fetchSessions()} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          Refresh List
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">User</th>
                <th className="px-6 py-4">Store</th>
                <th className="px-6 py-4">Device / OS</th>
                <th className="px-6 py-4">IP Address</th>
                <th className="px-6 py-4">Login Method</th>
                <th className="px-6 py-4">Started At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && sessions.length === 0 ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">Loading active sessions...</td></tr>
              ) : sessions.length === 0 ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">No active sessions found.</td></tr>
              ) : (
                sessions.map(s => (
                  <tr key={s.session_id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="font-bold text-slate-900">{s.full_name}</div>
                      <div className="text-xs text-slate-400">@{s.username}</div>
                    </td>
                    <td className="px-6 py-4 text-slate-900 font-medium">{s.store_name || '—'}</td>
                    <td className="px-6 py-4">
                      {s.device_brand ? (
                        <div className="space-y-0.5">
                          <div className="font-semibold text-slate-700">{s.device_brand} {s.device_model}</div>
                          <div className="text-[10px] text-slate-400 font-bold uppercase">{s.os_name} {s.os_version}</div>
                        </div>
                      ) : <span className="text-slate-400 italic">No telemetry data</span>}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">{s.ip_address || '—'}</td>
                    <td className="px-6 py-4">
                      <Badge color={s.login_method === 'otp' ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}>
                        {s.login_method}
                      </Badge>
                    </td>
                    <td className="px-6 py-4 text-[11px] font-medium text-slate-500">
                      {formatDateTime(s.created_at)}
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
