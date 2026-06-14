import React, { useEffect, useState, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import Chart from 'chart.js/auto';
import { api } from '../api';
import Badge from '../components/Badge';

export default function StoreDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState('');
  const [extendDays, setExtendDays] = useState(7);
  const chartRef = useRef(null);
  const chartInstance = useRef(null);

  useEffect(() => {
    fetchData();
  }, [id]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await api.storeDeepDive(id);
      setData(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const runAction = async (label, fn) => {
    setActionLoading(label);
    try {
      await fn();
      await fetchData();
    } catch (e) {
      alert(`Action failed: ${e.message}`);
    } finally {
      setActionLoading('');
    }
  };

  useEffect(() => {
    if (data && data.sales_history && chartRef.current) {
      if (chartInstance.current) chartInstance.current.destroy();
      const ctx = chartRef.current.getContext('2d');
      chartInstance.current = new Chart(ctx, {
        type: 'line',
        data: {
          labels: data.sales_history.map(s => s.date),
          datasets: [{
            label: 'Revenue (₹)',
            data: data.sales_history.map(s => s.revenue),
            borderColor: '#4f46e5',
            backgroundColor: '#4f46e520',
            fill: true,
            tension: 0.4
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true } }
        }
      });
    }
  }, [data]);

  if (loading) return <div className="p-8 text-slate-500">Loading store deep-dive...</div>;
  if (!data || !data.store) return <div className="p-8 text-red-500">Store not found.</div>;

  const { store, inventory, udhaar, top_customers, ai_status, expiring_batches } = data;

  const STATUS_META = {
    none:          { label: 'No Subscription', cls: 'bg-slate-100 text-slate-600' },
    pending_trial: { label: 'Trial Requested', cls: 'bg-amber-100 text-amber-800' },
    trial:         { label: 'On Trial',        cls: 'bg-blue-100 text-blue-800' },
    trial_expired: { label: 'Trial Expired',   cls: 'bg-red-100 text-red-700' },
    active:        { label: 'Subscribed',      cls: 'bg-emerald-100 text-emerald-800' },
    cancelled:     { label: 'Cancelled',       cls: 'bg-red-100 text-red-700' },
  };
  const subStatus = store.sub_status || 'none';
  const statusMeta = STATUS_META[subStatus] || STATUS_META.none;
  const fmtDate = (d) => d ? new Date(d).toLocaleDateString() : '—';

  const recoveryRate = udhaar.total_given > 0
    ? Math.round((udhaar.total_recovered / udhaar.total_given) * 100) 
    : 0;

  return (
    <div className="space-y-8 pb-12">
      <div className="flex items-center gap-4">
        <Link to="/stores" className="p-2 hover:bg-slate-100 rounded-lg transition-colors">
          <span className="text-xl">⬅️</span>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{store.name}</h1>
          <p className="text-slate-500 text-sm flex items-center gap-2">
            <span>📍 {store.location || 'No location'}</span>
            <span className="text-slate-300">•</span>
            <span>📞 {store.phone_number || 'No phone'}</span>
            <span className="text-slate-300">•</span>
            <span>📅 Registered {new Date(store.created_at).toLocaleDateString()}</span>
            <span className="text-slate-300">•</span>
            <span className="font-mono">ID #{store.store_id}</span>
          </p>
        </div>
        <div className="ml-auto">
          <Badge color={store.tier === 'pro' ? 'bg-purple-100 text-purple-800' : 'bg-emerald-100 text-emerald-800'}>
            {store.tier || 'No Plan'}
          </Badge>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Total SKUs</div>
          <div className="text-3xl font-black text-slate-900">{inventory.total_skus}</div>
        </div>
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Stock on Hand</div>
          <div className="text-3xl font-black text-indigo-600">{inventory.total_stock_units}</div>
        </div>
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Stockout Risks</div>
          <div className="text-3xl font-black text-red-500">{inventory.out_of_stock_count}</div>
        </div>
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">Expiring Soon</div>
          <div className="text-3xl font-black text-amber-500">{expiring_batches.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Left Column (Wider) */}
        <div className="lg:col-span-2 space-y-8">
          
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col h-[300px]">
            <h3 className="font-bold text-slate-900 mb-6">Revenue Trend (Last 7 Days)</h3>
            <div className="flex-1 relative flex items-center justify-center">
              {data.sales_history.length === 0 && (
                <div className="absolute inset-0 flex items-center justify-center text-slate-400 italic bg-white/80 z-10">
                  No sales recorded in the last 7 days.
                </div>
              )}
              <div className="absolute inset-0">
                <canvas ref={chartRef}></canvas>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="p-6 border-b border-slate-50 bg-slate-50/50">
              <h3 className="font-bold text-slate-900 flex items-center gap-2">
                <span>🏆</span> Top Customers (Last 30 Days)
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-slate-600">
                <thead className="bg-slate-50 text-slate-400 font-semibold uppercase tracking-wider text-[10px]">
                  <tr>
                    <th className="px-6 py-3">Customer</th>
                    <th className="px-6 py-3 text-right">Orders</th>
                    <th className="px-6 py-3 text-right">Total Spent</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {top_customers.length === 0 ? (
                    <tr><td colSpan="3" className="px-6 py-8 text-center text-slate-400 italic">No recent customers.</td></tr>
                  ) : top_customers.map((c, i) => (
                    <tr key={i} className="hover:bg-slate-50/50">
                      <td className="px-6 py-3">
                        <div className="font-bold text-slate-900">{c.name}</div>
                        <div className="text-xs text-slate-400">{c.phone}</div>
                      </td>
                      <td className="px-6 py-3 text-right font-medium">{c.total_orders}</td>
                      <td className="px-6 py-3 text-right font-black text-emerald-600">₹{c.total_spent}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Right Column (Narrower) */}
        <div className="space-y-8">
          
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
            <h3 className="font-bold text-slate-900 mb-4 flex items-center gap-2">
              <span>💳</span> Khata / Udhaar
            </h3>
            <div className="space-y-4 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Total Given</span>
                <span className="font-medium">₹{udhaar.total_given}</span>
              </div>
              <div className="flex justify-between items-center text-emerald-600">
                <span className="font-medium">Total Recovered</span>
                <span className="font-black">₹{udhaar.total_recovered}</span>
              </div>
              <div className="flex justify-between items-center text-red-500 pt-3 border-t border-slate-100">
                <span className="font-bold">Pending Debt</span>
                <span className="font-black text-lg">₹{udhaar.total_pending}</span>
              </div>
              
              <div className="pt-2">
                <div className="flex justify-between text-[10px] font-bold text-slate-400 uppercase mb-1">
                  <span>Recovery Rate</span>
                  <span>{recoveryRate}%</span>
                </div>
                <div className="h-2 w-full bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${recoveryRate}%` }}></div>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-slate-900 p-6 rounded-2xl shadow-lg text-white">
             <h3 className="font-bold text-slate-100 mb-4 flex items-center gap-2">
              <span>🤖</span> AI Feature Usage
            </h3>
            <div className="space-y-4">
              {Object.entries(ai_status).length === 0 ? (
                <div className="text-slate-500 text-sm italic">No AI activity recorded.</div>
              ) : Object.entries(ai_status).map(([feature, status]) => (
                <div key={feature} className="bg-slate-800/50 p-3 rounded-xl border border-slate-700/50">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-sm font-semibold capitalize text-indigo-400">{feature}</span>
                    <span className="text-xs text-slate-400">{status.used} / {status.limit} today</span>
                  </div>
                  <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden mt-2">
                    <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${Math.min((status.used / status.limit) * 100, 100)}%` }}></div>
                  </div>
                  {status.credits > 0 && (
                    <div className="text-[10px] text-emerald-400 font-bold uppercase mt-2">
                      +{status.credits} Rollover Credits
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm overflow-hidden flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-bold text-slate-900">Subscription</h3>
              <Badge color={statusMeta.cls}>{statusMeta.label}</Badge>
            </div>

            <div className="space-y-3 text-sm">
              <div className="flex justify-between py-2 border-b border-slate-50">
                <span className="text-slate-500">Plan Tier</span>
                <span className="font-bold text-slate-900 uppercase">{store.tier || '—'}</span>
              </div>

              {subStatus === 'pending_trial' && (
                <div className="flex justify-between py-2 border-b border-slate-50">
                  <span className="text-slate-500">Requested Tier</span>
                  <span className="font-bold text-amber-600 uppercase">{store.requested_tier || 'basic'}</span>
                </div>
              )}

              {subStatus === 'trial' && (
                <>
                  <div className="flex justify-between py-2 border-b border-slate-50">
                    <span className="text-slate-500">Trial Tier</span>
                    <span className="font-bold text-blue-700 uppercase">{store.trial_tier || store.tier}</span>
                  </div>
                  <div className="flex justify-between py-2 border-b border-slate-50">
                    <span className="text-slate-500">Days Left</span>
                    <span className={`font-black ${store.trial_days_left <= 3 ? 'text-red-600' : 'text-blue-700'}`}>
                      {store.trial_days_left ?? 0} day{store.trial_days_left === 1 ? '' : 's'}
                    </span>
                  </div>
                </>
              )}

              {store.trial_ends_at && (
                <div className="flex justify-between py-2 border-b border-slate-50">
                  <span className="text-slate-500">Trial Ends</span>
                  <span className="font-bold text-amber-600">{fmtDate(store.trial_ends_at)}</span>
                </div>
              )}

              {subStatus === 'active' && (
                <div className="flex justify-between py-2 border-b border-slate-50">
                  <span className="text-slate-500">Monthly Price</span>
                  <span className="font-bold text-emerald-600">₹{store.monthly_price ?? 0}</span>
                </div>
              )}

              <div className="flex justify-between py-2 border-b border-slate-50">
                <span className="text-slate-500">Plan Started</span>
                <span className="font-medium text-slate-900">{fmtDate(store.sub_started)}</span>
              </div>
              {store.sub_ended && (
                <div className="flex justify-between py-2 border-b border-slate-50">
                  <span className="text-slate-500">Ended</span>
                  <span className="font-medium text-red-600">{fmtDate(store.sub_ended)}</span>
                </div>
              )}
              <div className="flex justify-between py-2 border-b border-slate-50">
                <span className="text-slate-500">Member Since</span>
                <span className="font-medium text-slate-900">{fmtDate(store.created_at)}</span>
              </div>
            </div>

            {/* ── Admin actions ── */}
            <div className="mt-5 pt-4 border-t border-slate-100 space-y-3">
              {subStatus === 'pending_trial' && (
                <button
                  onClick={() => runAction('approve', () => api.approveTrial(store.store_id))}
                  disabled={!!actionLoading}
                  className="w-full py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-sm disabled:opacity-50 transition-colors"
                >
                  {actionLoading === 'approve' ? 'Approving…' : 'Approve Trial'}
                </button>
              )}

              {(subStatus === 'trial' || subStatus === 'trial_expired') && (
                <div>
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Extend Trial</div>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min="1"
                      value={extendDays}
                      onChange={(e) => setExtendDays(Math.max(1, parseInt(e.target.value) || 1))}
                      className="w-20 px-3 py-2 border border-slate-200 rounded-xl text-sm font-medium focus:outline-none focus:ring-2 focus:ring-indigo-300"
                    />
                    <button
                      onClick={() => runAction('extend', () => api.extendTrial(store.store_id, extendDays))}
                      disabled={!!actionLoading}
                      className="flex-1 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm disabled:opacity-50 transition-colors"
                    >
                      {actionLoading === 'extend' ? 'Extending…' : `Add ${extendDays} day${extendDays === 1 ? '' : 's'}`}
                    </button>
                  </div>
                  <div className="flex gap-2 mt-2">
                    {[7, 15, 30].map((d) => (
                      <button
                        key={d}
                        onClick={() => setExtendDays(d)}
                        className={`flex-1 py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                          extendDays === d
                            ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                            : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                        }`}
                      >
                        +{d}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {(subStatus === 'trial' || subStatus === 'active') && (
                <button
                  onClick={() => {
                    if (window.confirm('Cancel this subscription? The store will lose access immediately.')) {
                      runAction('cancel', () => api.cancelSub(store.store_id));
                    }
                  }}
                  disabled={!!actionLoading}
                  className="w-full py-2.5 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 font-bold text-sm disabled:opacity-50 transition-colors"
                >
                  {actionLoading === 'cancel' ? 'Cancelling…' : 'Cancel Subscription'}
                </button>
              )}

              {subStatus === 'none' && (
                <p className="text-xs text-slate-400 italic text-center">
                  This store has never started a trial or subscription.
                </p>
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
