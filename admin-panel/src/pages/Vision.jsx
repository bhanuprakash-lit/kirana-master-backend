import React, { useEffect, useState, useRef } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../api';

const DAY_OPTIONS = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
];

const pct = (r) => `${(Math.round((r || 0) * 1000) / 10).toFixed(1)}%`;

function StatCard({ label, value, sub, color = 'text-indigo-600', icon }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3.5 shadow-sm">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <p className={`text-2xl font-black ${color}`}>{value}</p>
      {sub && <p className="text-[11px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function Vision() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const dailyRef = useRef(null);
  const detectorRef = useRef(null);
  const dailyChart = useRef(null);
  const detectorChart = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const d = await api.visionAnalytics(days);
        if (!cancelled) setData(d);
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    return () => { cancelled = true; };
  }, [days]);

  useEffect(() => {
    if (!data) return;

    // Daily trend — units counted + items that needed review, per day.
    if (dailyRef.current) {
      if (dailyChart.current) dailyChart.current.destroy();
      const daily = data.daily || [];
      const ctx = dailyRef.current.getContext('2d');
      dailyChart.current = new Chart(ctx, {
        type: 'line',
        data: {
          labels: daily.map((d) => d.date.slice(5)),
          datasets: [
            {
              label: 'Units counted',
              data: daily.map((d) => d.units),
              borderColor: '#6366f1',
              backgroundColor: 'rgba(99,102,241,0.12)',
              fill: true,
              tension: 0.3,
              pointRadius: 2,
            },
            {
              label: 'Unknown items',
              data: daily.map((d) => d.unknown_items),
              borderColor: '#f59e0b',
              backgroundColor: 'transparent',
              tension: 0.3,
              pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } },
          scales: {
            x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 0, autoSkipPadding: 12 } },
            y: { beginAtZero: true, ticks: { font: { size: 9 }, precision: 0 } },
          },
        },
      });
    }

    // Detector split — how much our own YOLO covers vs the Gemini fallback.
    if (detectorRef.current) {
      if (detectorChart.current) detectorChart.current.destroy();
      const det = data.detectors || [];
      const LABELS = { yolo: 'Our YOLO', gemini: 'Gemini fallback' };
      const COLORS = { yolo: '#10b981', gemini: '#8b5cf6' };
      const ctx = detectorRef.current.getContext('2d');
      detectorChart.current = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: det.map((d) => LABELS[d.detector_source] || d.detector_source),
          datasets: [{
            data: det.map((d) => d.items),
            backgroundColor: det.map((d) => COLORS[d.detector_source] || '#94a3b8'),
            borderWidth: 0,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } },
          cutout: '65%',
        },
      });
    }

    return () => {
      if (dailyChart.current) dailyChart.current.destroy();
      if (detectorChart.current) detectorChart.current.destroy();
    };
  }, [data]);

  const s = data?.sessions;
  const d = data?.detections;
  const avgSecs = s?.avg_processing_seconds;

  return (
    <div className="space-y-5 pb-10">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight">Vision AI Analytics</h1>
          <p className="text-slate-500 text-xs mt-0.5">
            Shelf-scan &amp; bulk stock-in usage and accuracy across all stores.
          </p>
        </div>
        <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
          {DAY_OPTIONS.map((o) => (
            <button
              key={o.value}
              onClick={() => setDays(o.value)}
              className={`px-3 py-1 rounded-md text-xs font-semibold transition-colors ${
                days === o.value ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="text-red-500 p-4 border border-red-200 bg-red-50 rounded-xl text-sm">Error: {error}</div>
      )}
      {loading && !data && <div className="text-slate-500 p-8">Loading vision analytics…</div>}

      {data && (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Total Scans" value={s.total} sub={`${s.done} done · ${s.failed} failed`} icon="📸" />
            <StatCard label="Units Counted" value={d.units} sub={`${d.items} detections`} color="text-emerald-600" icon="🔢" />
            <StatCard label="Unknown Rate" value={pct(d.unknown_rate)} sub="need owner review"
              color={d.unknown_rate > 0.3 ? 'text-amber-600' : 'text-slate-700'} icon="❓" />
            <StatCard label="Correction Rate" value={pct(d.correction_rate)} sub="owner had to fix"
              color={d.correction_rate > 0.2 ? 'text-amber-600' : 'text-slate-700'} icon="✏️" />
            <StatCard label="Avg Processing" value={avgSecs != null ? `${avgSecs.toFixed(1)}s` : '—'}
              sub="upload → result" color="text-blue-600" icon="⚡" />
            <StatCard label="Stock-in Committed" value={s.committed} sub={`${s.onboarding} onboarding scans`}
              color="text-purple-600" icon="📦" />
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col h-[280px] lg:col-span-2">
              <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="text-indigo-600">📈</span> Daily Activity
              </h3>
              <div className="flex-1 relative min-h-0">
                {(data.daily || []).length > 0
                  ? <canvas ref={dailyRef}></canvas>
                  : <div className="h-full flex items-center justify-center text-slate-300 italic">No scans in this window.</div>}
              </div>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm flex flex-col h-[280px]">
              <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                <span className="text-emerald-600">🧠</span> Detector Split
              </h3>
              <div className="flex-1 relative min-h-0">
                {(data.detectors || []).length > 0
                  ? <canvas ref={detectorRef}></canvas>
                  : <div className="h-full flex items-center justify-center text-slate-300 italic">No detections yet.</div>}
              </div>
              <p className="mt-2 text-[11px] text-slate-400 text-center">
                Own-model coverage vs Gemini fallback · avg match {d.avg_match_score != null ? d.avg_match_score.toFixed(2) : '—'}
              </p>
            </div>
          </div>

          {/* Per-store breakdown */}
          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
              <span className="text-indigo-600">🏬</span> Usage by Store
            </h3>
            <div className="overflow-auto custom-scrollbar max-h-[360px]">
              <table className="w-full text-xs text-left">
                <thead className="text-slate-400 font-bold uppercase tracking-wider sticky top-0 bg-white">
                  <tr>
                    <th className="py-2 pr-3">Store</th>
                    <th className="py-2 px-3 text-right">Scans</th>
                    <th className="py-2 px-3 text-right">Units</th>
                    <th className="py-2 px-3 text-right">Unknown</th>
                    <th className="py-2 px-3 text-right">Corrections</th>
                    <th className="py-2 px-3 text-right">YOLO share</th>
                    <th className="py-2 pl-3 text-right">Last scan</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {(data.stores || []).map((r) => (
                    <tr key={r.store_id} className="hover:bg-slate-50">
                      <td className="py-2.5 pr-3 font-semibold text-slate-700">
                        {r.store_name} <span className="text-slate-300 font-normal">#{r.store_id}</span>
                      </td>
                      <td className="py-2.5 px-3 text-right font-bold text-slate-700">{r.sessions}</td>
                      <td className="py-2.5 px-3 text-right text-slate-600">{r.units}</td>
                      <td className={`py-2.5 px-3 text-right ${r.unknown_rate > 0.3 ? 'text-amber-600 font-semibold' : 'text-slate-500'}`}>{pct(r.unknown_rate)}</td>
                      <td className={`py-2.5 px-3 text-right ${r.correction_rate > 0.2 ? 'text-amber-600 font-semibold' : 'text-slate-500'}`}>{pct(r.correction_rate)}</td>
                      <td className="py-2.5 px-3 text-right text-emerald-600 font-semibold">{pct(r.yolo_share)}</td>
                      <td className="py-2.5 pl-3 text-right text-slate-400">{r.last_scan || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(data.stores || []).length === 0 && (
                <div className="py-10 text-center text-slate-300 italic">No store has used vision in this window.</div>
              )}
            </div>
          </div>

          {/* Top unknowns — next products to label */}
          <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
            <h3 className="text-sm font-bold text-slate-900 mb-1 flex items-center gap-2">
              <span className="text-amber-500">🏷️</span> Top Unrecognised Products
            </h3>
            <p className="text-[11px] text-slate-400 mb-3">Most-seen items the model couldn't match — the next labels to train / add to the catalog.</p>
            {(data.top_unknowns || []).length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
                {data.top_unknowns.map((u, i) => (
                  <div key={i} className="flex items-center justify-between py-1.5 border-b border-slate-50">
                    <span className="text-xs text-slate-600 truncate pr-2">{u.raw_name}</span>
                    <span className="text-xs text-slate-400 shrink-0">{u.times_seen}× · {u.units} units</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-6 text-center text-slate-300 italic">No unresolved unknowns — everything matched.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
