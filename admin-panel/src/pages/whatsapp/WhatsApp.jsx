import React, { useState, useEffect } from 'react';
import ToolsTab from './ToolsTab';
import CampaignsTab from './CampaignsTab';
import AnalyticsTab from './AnalyticsTab';
import { api } from '../../api';

export default function WhatsApp() {
  const [activeTab, setActiveTab] = useState('campaigns');
  const [health, setHealth] = useState(null);

  useEffect(() => {
    api.waHealth().then(setHealth).catch(console.error);
  }, []);

  const tabs = [
    { id: 'campaigns', label: 'Campaigns', icon: '📣' },
    { id: 'analytics', label: 'Analytics', icon: '📈' },
    { id: 'tools', label: 'Developer Tools', icon: '🛠️' },
  ];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-xl font-bold text-slate-900">WhatsApp Suite</h1>
          <p className="text-slate-500 text-sm mt-1">Manage marketing campaigns, track delivery, and debug sessions.</p>
        </div>
        
        {health && (
           <div className={`px-4 py-2 rounded-full text-sm font-semibold flex items-center gap-2 ${health.is_configured ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
             <span className={`w-2 h-2 rounded-full ${health.is_configured ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
             {health.is_configured ? 'API Connected' : 'API Unconfigured'}
           </div>
        )}
      </div>

      <div className="border-b border-slate-200">
        <nav className="-mb-px flex gap-6">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`pb-4 px-1 border-b-2 font-medium text-sm transition-colors flex items-center gap-2 ${
                activeTab === tab.id
                  ? 'border-indigo-600 text-indigo-600'
                  : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
              }`}
            >
              <span>{tab.icon}</span> {tab.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="pt-2">
        {activeTab === 'campaigns' && <CampaignsTab />}
        {activeTab === 'analytics' && <AnalyticsTab />}
        {activeTab === 'tools'   && <ToolsTab health={health} />}
      </div>
    </div>
  );
}
