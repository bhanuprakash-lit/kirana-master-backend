import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';
import { DISPOSITIONS, USAGE, SENTIMENTS, NEXT_ACTIONS, TAGS, labelOf } from './constants';

// Modal: focused store context + call history + a log-a-call form.
// Props: storeId, onClose(), onLogged() (parent refreshes its list).
export default function CallSheet({ storeId, onClose, onLogged }) {
  const ui = useUI();
  const [sheet, setSheet] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [disposition, setDisposition] = useState('answered');
  const [usage, setUsage] = useState('');
  const [feedback, setFeedback] = useState('');
  const [sentiment, setSentiment] = useState('');
  const [rating, setRating] = useState('');
  const [nextAction, setNextAction] = useState('done');
  const [callbackAt, setCallbackAt] = useState('');
  const [tags, setTags] = useState([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const s = await api.ccCallSheet(storeId);
        if (!cancelled) setSheet(s);
      } catch (e) {
        if (!cancelled) ui.toast(e.message, 'error');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [storeId]);

  const answered = disposition === 'answered';
  const toggleTag = (t) => setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  const submit = async () => {
    if (nextAction === 'callback' && !callbackAt) {
      ui.toast('Pick a callback date/time', 'error');
      return;
    }
    setSaving(true);
    try {
      await api.ccLogCall(storeId, {
        disposition,
        app_usage_status: answered && usage ? usage : null,
        feedback_text: answered && feedback.trim() ? feedback.trim() : null,
        sentiment: answered && sentiment ? sentiment : null,
        rating: answered && rating ? Number(rating) : null,
        next_action: nextAction,
        callback_at: nextAction === 'callback' ? new Date(callbackAt).toISOString() : null,
        tags: answered ? tags : [],
      });
      ui.toast('Call logged', 'success');
      onLogged?.();
      onClose();
    } catch (e) {
      ui.toast(e.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  const Field = ({ label, children }) => (
    <label className="block">
      <span className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">{label}</span>
      {children}
    </label>
  );
  const selectCls = 'w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-start justify-center overflow-auto p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl my-8" onClick={(e) => e.stopPropagation()}>
        {loading ? (
          <div className="p-10 text-center text-slate-500">Loading call sheet…</div>
        ) : !sheet ? (
          <div className="p-10 text-center text-slate-400">Store not found.</div>
        ) : (
          <>
            {/* Header + context */}
            <div className="p-5 border-b border-slate-100">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-black text-slate-900">{sheet.store_name}</h2>
                  <p className="text-xs text-slate-500">{sheet.location || 'No location'} · Store #{sheet.store_id}</p>
                </div>
                <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl leading-none">×</button>
              </div>
              <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <Ctx label="Owner" value={sheet.owner_name || '—'} />
                <Ctx label="Phone" value={sheet.phone_number
                  ? <a href={`tel:${sheet.phone_number}`} className="text-indigo-600 font-semibold">{sheet.phone_number}</a>
                  : '—'} />
                <Ctx label="Plan" value={sheet.tier || '—'} />
                <Ctx label="Trial" value={sheet.trial_days_left != null ? `${sheet.trial_days_left}d left` : '—'} />
                <Ctx label="Sales (7d)" value={sheet.orders_7d} />
                <Ctx label="Last login" value={sheet.days_since_login == null ? 'Never' : `${sheet.days_since_login}d ago`} />
              </div>
              {sheet.reason && (
                <div className="mt-3 inline-block bg-amber-50 text-amber-700 text-xs font-semibold px-2.5 py-1 rounded-full">
                  Why call: {sheet.reason}
                </div>
              )}
            </div>

            {/* Log form */}
            <div className="p-5 space-y-4">
              <h3 className="text-sm font-bold text-slate-900">Log this call</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Call answered?">
                  <select className={selectCls} value={disposition} onChange={(e) => setDisposition(e.target.value)}>
                    {DISPOSITIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </Field>
                {answered && (
                  <Field label="App usage">
                    <select className={selectCls} value={usage} onChange={(e) => setUsage(e.target.value)}>
                      <option value="">— select —</option>
                      {USAGE.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </Field>
                )}
              </div>

              {answered && (
                <>
                  <Field label="Feedback">
                    <textarea className={selectCls} rows={2} value={feedback}
                      onChange={(e) => setFeedback(e.target.value)} placeholder="What did the owner say?" />
                  </Field>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <Field label="Sentiment">
                      <select className={selectCls} value={sentiment} onChange={(e) => setSentiment(e.target.value)}>
                        <option value="">— select —</option>
                        {SENTIMENTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </Field>
                    <Field label="Rating">
                      <select className={selectCls} value={rating} onChange={(e) => setRating(e.target.value)}>
                        <option value="">— select —</option>
                        {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{'⭐'.repeat(n)}</option>)}
                      </select>
                    </Field>
                  </div>
                  <Field label="Tags">
                    <div className="flex flex-wrap gap-2">
                      {TAGS.map((t) => (
                        <button key={t.value} type="button" onClick={() => toggleTag(t.value)}
                          className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-colors ${
                            tags.includes(t.value)
                              ? 'bg-indigo-600 text-white border-indigo-600'
                              : 'bg-white text-slate-600 border-slate-300 hover:border-indigo-400'
                          }`}>
                          {t.label}
                        </button>
                      ))}
                    </div>
                  </Field>
                </>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Next action">
                  <select className={selectCls} value={nextAction} onChange={(e) => setNextAction(e.target.value)}>
                    {NEXT_ACTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </Field>
                {nextAction === 'callback' && (
                  <Field label="Callback at">
                    <input type="datetime-local" className={selectCls} value={callbackAt}
                      onChange={(e) => setCallbackAt(e.target.value)} />
                  </Field>
                )}
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
                <button onClick={submit} disabled={saving}
                  className="px-4 py-2 rounded-lg text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60">
                  {saving ? 'Saving…' : 'Save call'}
                </button>
              </div>
            </div>

            {/* History */}
            {sheet.history.length > 0 && (
              <div className="p-5 border-t border-slate-100">
                <h3 className="text-sm font-bold text-slate-900 mb-2">Call history</h3>
                <div className="space-y-2 max-h-52 overflow-auto custom-scrollbar">
                  {sheet.history.map((h) => (
                    <div key={h.call_id} className="text-xs border border-slate-100 rounded-lg p-2.5">
                      <div className="flex justify-between text-slate-500">
                        <span className="font-semibold text-slate-700">{labelOf(DISPOSITIONS, h.disposition)}</span>
                        <span>{new Date(h.called_at).toLocaleString()}</span>
                      </div>
                      {h.feedback_text && <p className="mt-1 text-slate-600">{h.feedback_text}</p>}
                      <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-slate-400">
                        {h.app_usage_status && <span>· {labelOf(USAGE, h.app_usage_status)}</span>}
                        {h.rating && <span>· {'⭐'.repeat(h.rating)}</span>}
                        {(h.tags || []).map((t) => <span key={t} className="bg-slate-100 px-1.5 rounded">{labelOf(TAGS, t)}</span>)}
                        <span className="ml-auto">{h.executive_name}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Ctx({ label, value }) {
  return (
    <div>
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="text-slate-800 font-medium">{value}</p>
    </div>
  );
}
