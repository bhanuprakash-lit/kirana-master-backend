import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useUI } from '../components/UIProvider';

// F4 — per-vertical KPI visibility. Toggle which KPIs each vertical shows.
// Coming-soon KPIs (data_unavailable) are hidden by default; flip them on here
// and the shopkeeper app reflects it live (no app update needed).
export default function KpiVisibility({ embedded = false }) {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const [verticals, setVerticals] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await api.getKpiVisibility();
      setItems(data.items || []);
      setVerticals(data.verticals || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  // Group flat items -> { kpi_id: {name, status, missing_data, cells: {vertical: item}} }
  const grouped = useMemo(() => {
    const m = {};
    for (const it of items) {
      if (!m[it.kpi_id]) {
        m[it.kpi_id] = { kpi_id: it.kpi_id, name: it.name, category: it.category, status: it.status, missing_data: it.missing_data, cells: {} };
      }
      m[it.kpi_id].cells[it.vertical_code] = it;
    }
    return Object.values(m);
  }, [items]);

  const toggle = async (kpiId, vc, current) => {
    const next = !current;
    setItems(prev => prev.map(it =>
      (it.kpi_id === kpiId && it.vertical_code === vc)
        ? { ...it, visible: next, overridden: true } : it));
    try {
      await api.saveKpiVisibility([{ kpi_id: kpiId, vertical_code: vc, is_visible: next }]);
    } catch (e) {
      ui.toast(`Failed to save: ${e.message}`, 'error');
      fetchData();
    }
  };

  if (loading) return <div className="p-12 text-center text-slate-400">Loading…</div>;

  return (
    <div className="space-y-4">
      {!embedded && (
        <div className="flex justify-between items-end">
          <div>
            <h1 className="text-xl font-bold text-slate-900">KPI Visibility by Vertical</h1>
            <p className="text-slate-500 text-sm mt-1">
              Show / hide each KPI per store vertical. Coming-soon KPIs are off by default; turning one on makes it appear in the shopkeeper app instantly.
            </p>
          </div>
          <button onClick={fetchData} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100">Refresh</button>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              <th className="text-left font-semibold text-slate-600 px-4 py-3">KPI</th>
              <th className="text-left font-semibold text-slate-600 px-4 py-3">Status</th>
              {verticals.map(v => (
                <th key={v} className="text-center font-semibold text-slate-600 px-3 py-3 capitalize">{v}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grouped.map(k => (
              <tr key={k.kpi_id} className="border-b border-slate-100 hover:bg-slate-50/50">
                <td className="px-4 py-3">
                  <div className="font-medium text-slate-800">{k.name}</div>
                  <div className="text-xs text-slate-400">{k.category} · {k.kpi_id}</div>
                  {k.missing_data && <div className="text-xs text-amber-600 mt-0.5 max-w-md">{k.missing_data}</div>}
                </td>
                <td className="px-4 py-3">
                  {k.status === 'ok'
                    ? <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">Live</span>
                    : <span className="text-xs font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded">Coming soon</span>}
                </td>
                {verticals.map(v => {
                  const cell = k.cells[v];
                  if (!cell) return <td key={v} className="text-center text-slate-300 px-3 py-3">—</td>;
                  return (
                    <td key={v} className="text-center px-3 py-3">
                      <button
                        onClick={() => toggle(k.kpi_id, v, cell.visible)}
                        title={cell.overridden ? 'Admin override' : 'Default'}
                        className={`w-11 h-6 rounded-full relative transition-colors ${cell.visible ? 'bg-indigo-600' : 'bg-slate-300'}`}
                      >
                        <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-all ${cell.visible ? 'left-[22px]' : 'left-0.5'}`} />
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
