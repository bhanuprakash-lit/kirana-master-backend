import React, { useState } from 'react';
import KpiTiers from './KpiTiers';
import KpiVisibility from './KpiVisibility';

// One page, two subtabs (like WhatsApp): tier gating + per-vertical visibility.
export default function KpiSettings() {
  const [tab, setTab] = useState('tiers');

  const TabBtn = ({ id, children }) => (
    <button
      onClick={() => setTab(id)}
      className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
        tab === id
          ? 'border-indigo-600 text-indigo-600'
          : 'border-transparent text-slate-500 hover:text-slate-700'
      }`}
    >
      {children}
    </button>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-slate-900">KPI Settings</h1>
        <p className="text-slate-500 text-sm mt-0.5">
          Tier access (Basic vs Pro) and per-vertical visibility in one place.
        </p>
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        <TabBtn id="tiers">Tier Access</TabBtn>
        <TabBtn id="visibility">Vertical Visibility</TabBtn>
      </div>

      {tab === 'tiers' ? <KpiTiers embedded /> : <KpiVisibility embedded />}
    </div>
  );
}
