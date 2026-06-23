import React, { useEffect, useState, useRef } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../api';

function StatCard({ label, value, color = 'text-indigo-600', icon }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3.5 shadow-sm">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <p className={`text-2xl font-black ${color}`}>{value}</p>
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [userActivity, setUserActivity] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  
  const subChartRef = useRef(null);
  const engagementChartRef = useRef(null);
  const subChartInstance = useRef(null);
  const engagementChartInstance = useRef(null);

  useEffect(() => {
    fetchAllData();
    const interval = setInterval(() => fetchAllData(false), 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchAllData = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const [statsData, activityData] = await Promise.all([
        api.stats(),
        api.userActivity()
      ]);
      setStats(statsData);
      setUserActivity(activityData.users || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (stats && subChartRef.current) {
      if (subChartInstance.current) subChartInstance.current.destroy();
      
      const ctx = subChartRef.current.getContext('2d');
      subChartInstance.current = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: ['Pending Trials', 'Active Trials', 'Basic Plans', 'Pro Plans'],
          datasets: [{
            data: [stats.pending_trials, stats.active_trials, stats.basic_count, stats.pro_count],
            backgroundColor: ['#f59e0b', '#3b82f6', '#10b981', '#8b5cf6'],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } },
          cutout: '70%'
        }
      });
    }

    if (userActivity.length > 0 && engagementChartRef.current) {
      if (engagementChartInstance.current) engagementChartInstance.current.destroy();

      // Calculate Active vs Inactive (last seen < 24h)
      const now = new Date();
      const active = userActivity.filter(u => u.last_seen && (now - new Date(u.last_seen)) < 86400000).length;
      const inactive = userActivity.length - active;

      const ctx = engagementChartRef.current.getContext('2d');
      engagementChartInstance.current = new Chart(ctx, {
        type: 'pie',
        data: {
          labels: ['Active (24h)', 'Inactive'],
          datasets: [{
            data: [active, inactive],
            backgroundColor: ['#10b981', '#cbd5e1'],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } }
        }
      });
    }
    
    return () => {
      if (subChartInstance.current) subChartInstance.current.destroy();
      if (engagementChartInstance.current) engagementChartInstance.current.destroy();
    };
  }, [stats, userActivity]);

  if (loading && !stats) return <div className="text-slate-500 p-8">Loading dashboard analytics...</div>;
  if (error) return <div className="text-red-500 p-8 border border-red-200 bg-red-50 rounded-xl">Error: {error}</div>;

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">Executive Overview</h1>
        <p className="text-slate-500 text-xs mt-0.5">Platform health, user engagement, and growth metrics.</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard label="Total Stores" value={stats.total_stores} icon="🏪" />
        <StatCard label="Active Trials" value={stats.active_trials} color="text-blue-600" icon="⏳" />
        <StatCard label="Pending Trials" value={stats.pending_trials} color="text-amber-600" icon="📋" />
        <StatCard label="Pro Plans" value={stats.pro_count} color="text-purple-600" icon="⭐" />
        <StatCard label="Basic Plans" value={stats.basic_count} color="text-emerald-600" icon="✅" />
        <StatCard label="Total Users" value={stats.total_users} icon="👤" />
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        {/* Subscription Distribution */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col h-[260px]">
          <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
            <span className="text-indigo-600 font-serif">💳</span> Subscription Distribution
          </h3>
          <div className="flex-1 relative min-h-0">
            <canvas ref={subChartRef}></canvas>
          </div>
        </div>

        {/* User Engagement */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col h-[260px]">
          <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
            <span className="text-emerald-600">⚡</span> Active vs Inactive Users
          </h3>
          <div className="flex-1 relative min-h-0">
            <canvas ref={engagementChartRef}></canvas>
          </div>
          <p className="mt-4 text-[11px] text-slate-400 text-center uppercase tracking-widest font-bold">Based on last 24h activity</p>
        </div>

        {/* Daily Performance Heatmap / Table summary */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col h-[260px]">
          <h3 className="text-sm font-bold text-slate-900 mb-4 flex items-center gap-2">
            <span className="text-indigo-600">🔥</span> Top Performers (Today)
          </h3>
          <div className="flex-1 min-h-0 overflow-auto custom-scrollbar">
            <table className="w-full text-xs text-left">
              <thead className="text-slate-400 font-bold uppercase tracking-wider sticky top-0 bg-white pb-2">
                <tr>
                  <th className="py-2">Owner</th>
                  <th className="py-2 text-right">Sales</th>
                  <th className="py-2 text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {userActivity
                  .filter(u => u.sales_today > 0 || u.foreground_sec_today > 0)
                  .sort((a, b) => b.sales_today - a.sales_today)
                  .slice(0, 8)
                  .map(u => (
                    <tr key={u.user_id} className="hover:bg-slate-50">
                      <td className="py-3 font-semibold text-slate-700">{u.full_name}</td>
                      <td className="py-3 text-right font-black text-emerald-600">{u.sales_today}</td>
                      <td className="py-3 text-right text-slate-500">{Math.round(u.foreground_sec_today / 60)}m</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {userActivity.filter(u => u.sales_today > 0).length === 0 && (
              <div className="h-full flex items-center justify-center text-slate-300 italic">No sales recorded today.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
