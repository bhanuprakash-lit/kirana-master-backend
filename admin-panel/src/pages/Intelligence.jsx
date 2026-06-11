import React, { useEffect, useState } from 'react';
import { api } from '../api';
import Badge from '../components/Badge';

export default function Intelligence() {
  const [triggers, setTriggers] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [firing, setFiring] = useState(null);

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => fetchLogs(false), 15000);
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [tData, lData] = await Promise.all([
        api.intelTriggers(),
        api.intelLogs(50)
      ]);
      setTriggers(tData.triggers || []);
      setLogs(lData.logs || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchLogs = async (showLoader = true) => {
    try {
      const data = await api.intelLogs(50);
      setLogs(data.logs || []);
    } catch (e) { console.error(e); }
  };

  const handleFire = async (name) => {
    if (!window.confirm(`Manually fire trigger "${name}" now?`)) return;
    setFiring(name);
    try {
      await api.fireTrigger(name);
      alert(`Trigger ${name} fired successfully!`);
      fetchLogs();
    } catch (e) {
      alert(`Failed to fire: ${e.message}`);
    } finally {
      setFiring(null);
    }
  };

  return (
    <div className="space-y-8 pb-12">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Intelligence Engine</h1>
          <p className="text-slate-500 text-sm mt-1">Manage automated triggers and monitor notification delivery.</p>
        </div>
        <button onClick={fetchData} className="text-xs font-bold text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors uppercase tracking-wider">
          Sync Engine
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left: Triggers List */}
        <div className="lg:col-span-1 space-y-4">
          <h2 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em]">Available Triggers</h2>
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden divide-y divide-slate-100">
            {triggers.map(t => (
              <div key={t} className="p-4 flex items-center justify-between group hover:bg-slate-50 transition-colors">
                <div>
                  <div className="font-bold text-slate-900 text-sm">{t}</div>
                  <div className="text-[10px] text-slate-400 font-medium uppercase mt-0.5 tracking-wider">Background Job</div>
                </div>
                <button 
                  onClick={() => handleFire(t)}
                  disabled={firing === t}
                  className="opacity-0 group-hover:opacity-100 bg-indigo-600 text-white text-[10px] font-black px-3 py-1.5 rounded-lg hover:bg-indigo-700 transition-all uppercase disabled:opacity-50"
                >
                  {firing === t ? 'Firing...' : 'Fire Now'}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Right: Audit Trail */}
        <div className="lg:col-span-2 space-y-4">
          <h2 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em]">Notification Audit Trail</h2>
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50/80 border-b border-slate-200 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                  <tr>
                    <th className="px-6 py-4">Trigger</th>
                    <th className="px-6 py-4">Recipient</th>
                    <th className="px-6 py-4">Status</th>
                    <th className="px-6 py-4">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {logs.map(log => (
                    <tr key={log.id} className="hover:bg-slate-50/50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-bold text-slate-900 text-xs">{log.trigger_type}</div>
                        <div className="text-[10px] text-slate-400 font-medium">{log.title}</div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="text-xs text-slate-700">Store #{log.store_id}</div>
                        <div className="text-[10px] text-slate-400 font-mono">User #{log.user_id}</div>
                      </td>
                      <td className="px-6 py-4">
                        <Badge color={log.status === 'sent' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}>
                          {log.status}
                        </Badge>
                      </td>
                      <td className="px-6 py-4 text-[10px] font-medium text-slate-500">
                        {log.sent_at ? new Date(log.sent_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
