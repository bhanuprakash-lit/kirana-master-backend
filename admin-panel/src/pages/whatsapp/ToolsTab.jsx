import React, { useState, useEffect } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';

export default function ToolsTab({ health }) {
  const ui = useUI();
  const [sendPhone, setSendPhone] = useState('');
  const [sendMsg, setSendMsg] = useState('');
  const [sendLoading, setSendLoading] = useState(false);

  const [lookupPhone, setLookupPhone] = useState('');
  const [lookupData, setLookupData] = useState(null);

  const [linkPhone, setLinkPhone] = useState('');
  const [linkStore, setLinkStore] = useState('');

  const handleSend = async () => {
    if (!sendPhone || !sendMsg) { ui.toast('Phone and message required', 'error'); return; }
    setSendLoading(true);
    try {
      await api.waSend(sendPhone, sendMsg);
      ui.toast('Message sent', 'success');
      setSendMsg('');
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    } finally {
      setSendLoading(false);
    }
  };

  const handleLookup = async () => {
    if (!lookupPhone) { ui.toast('Phone required', 'error'); return; }
    try {
      const data = await api.waSession(lookupPhone);
      setLookupData(data);
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  const handleReset = async () => {
    if (!lookupPhone) { ui.toast('Phone required', 'error'); return; }
    if (!(await ui.confirm({ title: 'Reset session?', message: `Reset WhatsApp session for ${lookupPhone}.`, danger: true, confirmLabel: 'Reset' }))) return;
    try {
      await api.waResetSession(lookupPhone);
      ui.toast('Session reset', 'success');
      setLookupData(null);
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  const handleLink = async () => {
    if (!linkPhone || !linkStore) { ui.toast('Phone and Store ID required', 'error'); return; }
    try {
      await api.waLinkStore(linkPhone, linkStore);
      ui.toast(`Linked ${linkPhone} to store #${linkStore}`, 'success');
      setLinkPhone(''); setLinkStore('');
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-bold text-slate-900 mb-4">Send Test Message</h3>
        <div className="space-y-3">
          <input type="tel" placeholder="Phone (with country code)" value={sendPhone} onChange={e => setSendPhone(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
          <textarea placeholder="Message" value={sendMsg} onChange={e => setSendMsg(e.target.value)} rows="3" className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
          <button onClick={handleSend} disabled={sendLoading} className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-medium py-2 rounded-lg text-sm">
            {sendLoading ? 'Sending...' : 'Send Message'}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-bold text-slate-900 mb-4">Session Lookup</h3>
        <div className="space-y-3">
          <input type="tel" placeholder="Phone" value={lookupPhone} onChange={e => setLookupPhone(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
          <div className="flex gap-2">
            <button onClick={handleLookup} className="flex-1 border border-indigo-200 text-indigo-700 font-medium py-2 rounded-lg text-sm">Lookup</button>
            <button onClick={handleReset} className="flex-1 border border-red-200 text-red-600 font-medium py-2 rounded-lg text-sm">Reset Session</button>
          </div>
          {lookupData && (
            <pre className="bg-slate-50 p-3 rounded-lg text-xs overflow-auto">
              {JSON.stringify(lookupData, null, 2)}
            </pre>
          )}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-bold text-slate-900 mb-4">Link Phone to Store</h3>
        <div className="space-y-3">
          <input type="tel" placeholder="Phone" value={linkPhone} onChange={e => setLinkPhone(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
          <input type="number" placeholder="Store ID" value={linkStore} onChange={e => setLinkStore(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
          <button onClick={handleLink} className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 rounded-lg text-sm">Link Store</button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-bold text-slate-900 mb-4">Service Status</h3>
        <div className="space-y-2 text-sm">
          {health ? Object.entries(health).map(([k, v]) => (
            <div key={k} className="flex justify-between py-1.5 border-b border-slate-100 last:border-0">
              <span className="text-slate-500">{k}</span>
              <span className={`font-medium ${v === true ? 'text-emerald-600' : v === false ? 'text-red-500' : 'text-slate-700'}`}>{String(v)}</span>
            </div>
          )) : <div className="text-slate-400">Health check failed.</div>}
        </div>
      </div>
    </div>
  );
}
