import React, { useEffect, useState } from 'react';
import { api } from '../api';

export default function KpiTiers() {
  const [kpis, setKpis] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchKpis();
  }, []);

  const fetchKpis = async () => {
    try {
      const data = await api.getKpiTiers();
      setKpis(data.kpis || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const toggleTier = async (kpiId, currentTier) => {
    const newTier = currentTier === 'basic' ? 'pro' : 'basic';
    // Optimistic update
    setKpis(prev => prev.map(k => k.kpi_id === kpiId ? { ...k, tier: newTier, is_custom: true } : k));
    
    try {
      await api.saveKpiTiers([{ kpi_id: kpiId, tier: newTier }]);
    } catch (e) {
      alert(`Failed to save KPI config: ${e.message}`);
      // Revert on failure
      fetchKpis();
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">KPI Configurations</h1>
          <p className="text-slate-500 text-sm mt-1">Manage feature gates: which analytics are available on Basic vs. Pro tiers.</p>
        </div>
        <button onClick={fetchKpis} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          Refresh Data
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">KPI ID</th>
                <th className="px-6 py-4">Name</th>
                <th className="px-6 py-4">Category</th>
                <th className="px-6 py-4">Tier Access</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="4" className="px-6 py-4 text-center text-slate-400">Loading KPIs...</td></tr>
              ) : kpis.length === 0 ? (
                <tr><td colSpan="4" className="px-6 py-4 text-center text-slate-400">No KPIs found.</td></tr>
              ) : (
                kpis.map(kpi => (
                  <tr key={kpi.kpi_id} className="hover:bg-slate-50/50">
                    <td className="px-6 py-4 font-mono text-slate-500 text-xs">{kpi.kpi_id}</td>
                    <td className="px-6 py-4 font-bold text-slate-900">{kpi.name}</td>
                    <td className="px-6 py-4">{kpi.category}</td>
                    <td className="px-6 py-4">
                      <button 
                        onClick={() => toggleTier(kpi.kpi_id, kpi.tier)}
                        className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-bold uppercase transition-colors ${
                          kpi.tier === 'pro' ? 'bg-purple-100 text-purple-800 hover:bg-purple-200' : 'bg-emerald-100 text-emerald-800 hover:bg-emerald-200'
                        }`}
                      >
                        {kpi.tier}
                        {kpi.is_custom && <span className="ml-1 opacity-50" title="Custom Override">*</span>}
                      </button>
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
