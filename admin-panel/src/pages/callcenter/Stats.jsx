import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';

function Card({ label, value, sub, color = 'text-indigo-600', icon }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <p className={`text-2xl font-black ${color}`}>{value}</p>
      {sub && <p className="text-[11px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function Stats() {
  const ui = useUI();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setStats(await api.ccStats());
      } catch (e) {
        ui.toast(e.message, 'error');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="text-slate-500 p-8">Loading your stats…</div>;
  if (!stats) return null;

  const pct = (r) => `${Math.round((r || 0) * 100)}%`;

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">My Stats</h1>
        <p className="text-slate-500 text-xs mt-0.5">Your calling activity over the last 30 days.</p>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        <Card label="Assigned Stores" value={stats.assigned_stores} icon="🏬" />
        <Card label="Calls Today" value={stats.calls_today} color="text-emerald-600" icon="📞" />
        <Card label="Calls (30d)" value={stats.calls_30d} icon="🗓️" />
        <Card label="Connect Rate" value={pct(stats.connect_rate)} sub={`${stats.answered_30d} answered`} color="text-blue-600" icon="✅" />
        <Card label="Now Using App" value={stats.using_30d} sub="stores reached, 30d" color="text-purple-600" icon="📲" />
        <Card label="Avg Rating" value={stats.avg_rating != null ? stats.avg_rating.toFixed(1) : '—'} icon="⭐" />
        <Card label="Pending Callbacks" value={stats.pending_callbacks} color={stats.pending_callbacks > 0 ? 'text-amber-600' : 'text-slate-700'} icon="⏰" />
      </div>
    </div>
  );
}
