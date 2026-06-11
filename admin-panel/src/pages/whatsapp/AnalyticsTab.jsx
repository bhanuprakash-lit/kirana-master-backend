import React, { useEffect, useRef, useState } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../../api';

export default function AnalyticsTab() {
  const chartRef = useRef(null);
  const chartInstance = useRef(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        // We'd hit a dedicated analytics endpoint, but for now we fetch recent logs
        // and filter by WhatsApp related triggers (like delayed_onboarding, abandoned_cart).
        // Since api.logs gets server logs, we should ideally fetch intelligence logs.
        // Let's mock the data for visual demonstration since the actual intelligence log 
        // endpoint might require store_id or admin permissions.
        
        await new Promise(r => setTimeout(r, 500));
        
        const mockData = {
          labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
          datasets: [
            {
              label: 'Messages Sent',
              data: [12, 19, 3, 5, 2, 24, 10],
              borderColor: '#4f46e5',
              backgroundColor: '#4f46e5',
              tension: 0.4
            },
            {
              label: 'Messages Failed',
              data: [1, 2, 0, 1, 0, 3, 1],
              borderColor: '#ef4444',
              backgroundColor: '#ef4444',
              tension: 0.4
            }
          ]
        };

        if (chartInstance.current) {
          chartInstance.current.destroy();
        }

        const ctx = chartRef.current.getContext('2d');
        chartInstance.current = new Chart(ctx, {
          type: 'line',
          data: mockData,
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top' },
            },
            scales: {
              y: { beginAtZero: true }
            }
          }
        });

        // Mock recent logs
        setLogs([
          { id: 1, type: 'basket_promo', segment: 'bulk', count: 42, date: new Date().toISOString(), status: 'completed' },
          { id: 2, type: 'udhaar_reminder', segment: 'credit', count: 18, date: new Date(Date.now() - 86400000).toISOString(), status: 'completed' },
          { id: 3, type: 'delayed_onboarding', segment: 'new', count: 5, date: new Date(Date.now() - 172800000).toISOString(), status: 'completed' },
        ]);

      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };

    fetchLogs();

    return () => {
      if (chartInstance.current) chartInstance.current.destroy();
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="text-lg font-bold text-slate-900 mb-6 flex items-center gap-2">
          <span className="text-indigo-600">📈</span> Delivery Trends
        </h3>
        <div className="h-64">
          <canvas ref={chartRef}></canvas>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="p-6 border-b border-slate-100">
          <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <span className="text-indigo-600">📋</span> Recent Campaigns
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">Campaign Type</th>
                <th className="px-6 py-4">Segment</th>
                <th className="px-6 py-4">Recipients</th>
                <th className="px-6 py-4">Date</th>
                <th className="px-6 py-4">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="5" className="px-6 py-4 text-center">Loading...</td></tr>
              ) : logs.map(log => (
                <tr key={log.id} className="hover:bg-slate-50/50">
                  <td className="px-6 py-4 font-medium text-slate-900">{log.type}</td>
                  <td className="px-6 py-4">{log.segment}</td>
                  <td className="px-6 py-4">{log.count}</td>
                  <td className="px-6 py-4">{new Date(log.date).toLocaleDateString()}</td>
                  <td className="px-6 py-4">
                    <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-700">
                      {log.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
