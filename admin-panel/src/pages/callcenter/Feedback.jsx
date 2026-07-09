import React, { useEffect, useState } from 'react';
import { api } from '../../api';
import { useUI } from '../../components/UIProvider';
import { TAGS, SENTIMENTS, USAGE, labelOf } from './constants';

const DAY_OPTIONS = [7, 30, 90];
const SENTIMENT_COLOR = { positive: 'text-emerald-600', neutral: 'text-slate-500', negative: 'text-red-600' };

export default function Feedback() {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);
  const [tag, setTag] = useState('');
  const [sentiment, setSentiment] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.ccFeedback({ days, tag: tag || undefined, sentiment: sentiment || undefined });
      setItems(data.items || []);
    } catch (e) { ui.toast(e.message, 'error'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [days, tag, sentiment]);

  const cls = 'border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-bold text-slate-900 tracking-tight">Call Feedback</h1>
        <p className="text-slate-500 text-xs mt-0.5">What store owners told the team — filterable for the product roadmap.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3 bg-white border border-slate-200 rounded-xl p-3 shadow-sm">
        <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
          {DAY_OPTIONS.map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={`px-3 py-1 rounded-md text-xs font-semibold ${days === d ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500'}`}>
              {d}d
            </button>
          ))}
        </div>
        <select className={cls} value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">All tags</option>
          {TAGS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <select className={cls} value={sentiment} onChange={(e) => setSentiment(e.target.value)}>
          <option value="">All sentiment</option>
          {SENTIMENTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="text-slate-500 p-8">Loading feedback…</div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-400 italic">No feedback matches.</div>
      ) : (
        <div className="space-y-2">
          {items.map((f) => (
            <div key={f.call_id} className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-800">{f.store_name}</p>
                  <p className="text-[11px] text-slate-400">{f.executive_name} · {new Date(f.called_at).toLocaleString()}</p>
                </div>
                {f.sentiment && (
                  <span className={`text-xs font-bold ${SENTIMENT_COLOR[f.sentiment] || 'text-slate-500'}`}>
                    {labelOf(SENTIMENTS, f.sentiment)}
                  </span>
                )}
              </div>
              {f.feedback_text && <p className="mt-2 text-sm text-slate-700">{f.feedback_text}</p>}
              <div className="mt-2 flex flex-wrap gap-1.5 items-center">
                {(f.tags || []).map((t) => (
                  <span key={t} className="bg-indigo-50 text-indigo-600 text-[11px] font-semibold px-2 py-0.5 rounded-full">{labelOf(TAGS, t)}</span>
                ))}
                {f.app_usage_status && <span className="text-[11px] text-slate-400">· {labelOf(USAGE, f.app_usage_status)}</span>}
                {f.rating && <span className="text-[11px] text-amber-500">· {'⭐'.repeat(f.rating)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
