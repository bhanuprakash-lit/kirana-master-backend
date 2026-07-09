import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';

const ROLES = [
  { value: 'call_executive', label: 'Executive' },
  { value: 'call_manager', label: 'Manager' },
];

export default function Executives() {
  const ui = useUI();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [resetTarget, setResetTarget] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      setRows(await api.ccExecutives());
    } catch (e) {
      ui.toast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const toggleActive = async (r) => {
    try {
      await api.ccUpdateExec(r.executive_id, { is_active: !r.is_active });
      ui.toast(r.is_active ? 'Deactivated' : 'Activated', 'success');
      load();
    } catch (e) { ui.toast(e.message, 'error'); }
  };


  return (
    <div className="space-y-5 pb-10">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight">Call Executives</h1>
          <p className="text-slate-500 text-xs mt-0.5">Manage the tele-calling team and their access.</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold px-4 py-2 rounded-lg shadow-sm">
          + Add Executive
        </button>
      </div>

      {loading ? (
        <div className="text-slate-500 p-8">Loading…</div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm text-left">
            <thead className="text-slate-400 font-bold uppercase tracking-wider text-[11px] bg-slate-50">
              <tr>
                <th className="py-2.5 px-4">Name</th>
                <th className="py-2.5 px-3">Username</th>
                <th className="py-2.5 px-3">Role</th>
                <th className="py-2.5 px-3 text-right">Stores</th>
                <th className="py-2.5 px-3 text-right">Calls Today</th>
                <th className="py-2.5 px-3 text-center">Status</th>
                <th className="py-2.5 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {rows.map((r) => (
                <tr key={r.executive_id} className="hover:bg-slate-50">
                  <td className="py-2.5 px-4 font-semibold text-slate-800">{r.full_name}
                    {r.phone && <span className="block text-[11px] text-slate-400 font-normal">{r.phone}</span>}
                  </td>
                  <td className="py-2.5 px-3 text-slate-500">{r.username}</td>
                  <td className="py-2.5 px-3 text-slate-500">{r.role === 'call_manager' ? 'Manager' : 'Executive'}</td>
                  <td className="py-2.5 px-3 text-right font-semibold text-slate-700">{r.assigned_count}</td>
                  <td className="py-2.5 px-3 text-right text-slate-600">{r.calls_today}</td>
                  <td className="py-2.5 px-3 text-center">
                    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                      r.is_active ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                      {r.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="py-2.5 px-4 text-right whitespace-nowrap">
                    <button onClick={() => setResetTarget(r)} className="text-xs font-semibold text-slate-500 hover:text-indigo-600 mr-3">Reset PW</button>
                    <button onClick={() => toggleActive(r)} className="text-xs font-semibold text-slate-500 hover:text-indigo-600">
                      {r.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="py-10 text-center text-slate-300 italic">No executives yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); load(); }} />}
      {resetTarget && <ResetPwModal exec={resetTarget} onClose={() => setResetTarget(null)} />}
    </div>
  );
}

function ResetPwModal({ exec, onClose }) {
  const ui = useUI();
  const [pw, setPw] = useState('');
  const [saving, setSaving] = useState(false);
  const cls = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

  const submit = async () => {
    if (pw.length < 6) { ui.toast('Password must be at least 6 characters', 'error'); return; }
    setSaving(true);
    try {
      await api.ccUpdateExec(exec.executive_id, { password: pw });
      ui.toast('Password reset', 'success');
      onClose();
    } catch (e) { ui.toast(e.message, 'error'); }
    finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-black text-slate-900">Reset password</h2>
        <p className="text-xs text-slate-500">For {exec.full_name} ({exec.username}).</p>
        <input className={cls} type="password" autoFocus placeholder="New password (min 6)" value={pw} onChange={(e) => setPw(e.target.value)} />
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
          <button onClick={submit} disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60">
            {saving ? 'Saving…' : 'Reset'}
          </button>
        </div>
      </div>
    </div>
  );
}

function CreateModal({ onClose, onCreated }) {
  const ui = useUI();
  const [form, setForm] = useState({ full_name: '', username: '', password: '', phone: '', email: '', role: 'call_executive' });
  const [saving, setSaving] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const cls = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

  const submit = async () => {
    if (!form.full_name || !form.username || form.password.length < 6) {
      ui.toast('Name, username, and a 6+ char password are required', 'error');
      return;
    }
    setSaving(true);
    try {
      await api.ccCreateExec({ ...form, phone: form.phone || null, email: form.email || null });
      ui.toast('Executive created', 'success');
      onCreated();
    } catch (e) { ui.toast(e.message, 'error'); }
    finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-black text-slate-900">Add Executive</h2>
        <input className={cls} placeholder="Full name" value={form.full_name} onChange={set('full_name')} />
        <input className={cls} placeholder="Username" value={form.username} onChange={set('username')} />
        <input className={cls} type="password" placeholder="Password (min 6)" value={form.password} onChange={set('password')} />
        <div className="grid grid-cols-2 gap-3">
          <input className={cls} placeholder="Phone (optional)" value={form.phone} onChange={set('phone')} />
          <select className={cls} value={form.role} onChange={set('role')}>
            {ROLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <input className={cls} placeholder="Email (optional)" value={form.email} onChange={set('email')} />
        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
          <button onClick={submit} disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}
