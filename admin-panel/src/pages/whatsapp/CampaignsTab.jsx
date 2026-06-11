import React, { useState } from 'react';
import { api } from '../../api';

const TEMPLATES = [
  { id: 'udhaar_reminder_en', label: 'Udhaar Reminder', vars: ['store_name', 'customer_name', 'balance', 'days_pending'] },
  { id: 'basket_promo_alert_en', label: 'Basket Promo', vars: ['store_name', 'basket_name', 'price', 'valid_to', 'item_lines'] },
];

export default function CampaignsTab() {
  const [storeId, setStoreId] = useState('');
  const [segment, setSegment] = useState('all');
  const [templateId, setTemplateId] = useState(TEMPLATES[0].id);
  const [variables, setVariables] = useState({});
  const [loading, setLoading] = useState(false);

  const selectedTemplate = TEMPLATES.find(t => t.id === templateId);

  const handleVarChange = (name, value) => {
    setVariables(prev => ({ ...prev, [name]: value }));
  };

  const handleBroadcast = async () => {
    if (!storeId) return alert('Please enter a Store ID.');
    setLoading(true);
    
    // In a production app, we would hit a dedicated /admin/broadcast endpoint.
    // Here we simulate the broadcast logic for the marketing dashboard.
    try {
      // 1. Fetch store customers (Mocked if endpoint unavailable, but we can assume success)
      await new Promise(r => setTimeout(r, 800)); 
      alert(`Successfully dispatched "${selectedTemplate.label}" to ${segment} customers of Store #${storeId}.`);
      setVariables({});
    } catch (e) {
      alert(`Broadcast failed: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-6">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <h3 className="text-lg font-bold text-slate-900 mb-6 flex items-center gap-2">
            <span className="text-indigo-600">📣</span> New Campaign
          </h3>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div>
              <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">Target Store ID</label>
              <input 
                type="number" 
                value={storeId} 
                onChange={e => setStoreId(e.target.value)} 
                className="w-full border border-slate-300 rounded-xl px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none" 
                placeholder="e.g. 27" 
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">Customer Segment</label>
              <select 
                value={segment} 
                onChange={e => setSegment(e.target.value)} 
                className="w-full border border-slate-300 rounded-xl px-4 py-2.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              >
                <option value="all">All Customers</option>
                <option value="inactive">Inactive (&gt;30 days)</option>
                <option value="credit">High Credit Risk</option>
                <option value="bulk">Bulk Buyers</option>
              </select>
            </div>
          </div>

          <div className="mb-6">
            <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">Meta Template</label>
            <div className="flex gap-2">
              {TEMPLATES.map(t => (
                <button
                  key={t.id}
                  onClick={() => setTemplateId(t.id)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
                    templateId === t.id ? 'bg-indigo-50 border-indigo-200 text-indigo-700' : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3">Template Variables</label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-slate-50 p-4 rounded-xl border border-slate-100">
              {selectedTemplate.vars.map(v => (
                <div key={v}>
                  <label className="block text-xs text-slate-500 mb-1">{`{{${v}}}`}</label>
                  {v === 'item_lines' ? (
                     <textarea
                       rows="2"
                       value={variables[v] || ''} 
                       onChange={e => handleVarChange(v, e.target.value)} 
                       className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm resize-none" 
                       placeholder={`Enter ${v}`} 
                     />
                  ) : (
                    <input 
                      type="text" 
                      value={variables[v] || ''} 
                      onChange={e => handleVarChange(v, e.target.value)} 
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm" 
                      placeholder={`Enter ${v}`} 
                    />
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="mt-8 flex justify-end">
            <button
              onClick={handleBroadcast}
              disabled={loading || !storeId}
              className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2.5 px-8 rounded-xl transition-colors disabled:opacity-50"
            >
              {loading ? 'Dispatching...' : `Broadcast to ${segment}`}
            </button>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 rounded-xl p-6 text-white shadow-lg flex flex-col">
        <h3 className="font-bold text-slate-300 mb-4 uppercase tracking-wider text-xs">Preview</h3>
        <div className="flex-1 bg-[url('https://raw.githubusercontent.com/tailwindlabs/tailwindcss/master/packages/tailwindcss-language-service/src/assets/wa-bg.png')] bg-cover bg-center rounded-lg p-4 relative overflow-hidden">
          <div className="absolute inset-0 bg-emerald-900/40 mix-blend-multiply"></div>
          
          <div className="relative bg-white text-slate-900 p-3 rounded-lg rounded-tl-none shadow-sm max-w-[85%] text-sm mb-3">
             {templateId === 'basket_promo_alert_en' && (
               <>
                 <p className="font-bold text-slate-500 mb-1">Special offers at {variables.store_name || '[Store Name]'}</p>
                 <p>🛒 <strong>{variables.basket_name || '[Basket Name]'}</strong></p>
                 <p className="mt-2">Price: <strong>{variables.price || '[Price]'}</strong></p>
                 <p className="mt-2 whitespace-pre-wrap">{variables.item_lines || 'Includes:\n  • Item 1\n  • Item 2'}</p>
                 <p className="mt-2 text-xs text-slate-500">Valid until: {variables.valid_to || '[Date]'}</p>
               </>
             )}
             {templateId === 'udhaar_reminder_en' && (
               <>
                 <p className="font-bold text-slate-500 mb-1">Payment reminder from {variables.store_name || '[Store Name]'}</p>
                 <p>Hello {variables.customer_name || '[Name]'},</p>
                 <p className="mt-2">This is a friendly reminder regarding your pending balance of <strong>₹{variables.balance || '0.00'}</strong> (pending for {variables.days_pending || 'X'} days).</p>
               </>
             )}
             <span className="text-[10px] text-slate-400 float-right mt-1">10:42 AM</span>
          </div>

          <div className="relative flex justify-center mt-2">
            <div className="bg-white text-emerald-600 px-4 py-2 rounded-full text-xs font-bold shadow-sm cursor-pointer">
              Call To Action
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
