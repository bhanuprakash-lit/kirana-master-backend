import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { useUI } from '../components/UIProvider';

export default function Issues() {
  const ui = useUI();
  const [issues, setIssues] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchIssues();
    const interval = setInterval(() => {
      fetchIssues(false);
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchIssues = async (showLoader = true) => {
    if (showLoader) setLoading(true);
    try {
      const data = await api.listIssues();
      setIssues(data.items || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleResolve = async (reportId) => {
    try {
      await api.updateIssue(reportId, { status: 'resolved' });
      ui.toast('Issue resolved', 'success');
      fetchIssues();
    } catch (e) {
      ui.toast(`Error: ${e.message}`, 'error');
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Support Issues</h1>
          <p className="text-slate-500 text-sm mt-1">Manage user bug reports and support requests.</p>
        </div>
        <button onClick={fetchIssues} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          Refresh Data
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">ID</th>
                <th className="px-6 py-4">Store ID</th>
                <th className="px-6 py-4">Issue Type</th>
                <th className="px-6 py-4">Description</th>
                <th className="px-6 py-4">Status</th>
                <th className="px-6 py-4">Reported At</th>
                <th className="px-6 py-4">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="7" className="px-6 py-4 text-center text-slate-400">Loading issues...</td></tr>
              ) : issues.length === 0 ? (
                <tr><td colSpan="7" className="px-6 py-4 text-center text-slate-400">No issues found.</td></tr>
              ) : (
                issues.map(issue => (
                  <tr key={issue.report_id} className="hover:bg-slate-50/50">
                    <td className="px-6 py-4 font-mono text-slate-400">#{issue.report_id}</td>
                    <td className="px-6 py-4 font-medium text-slate-900">{issue.store_id}</td>
                    <td className="px-6 py-4 font-semibold text-slate-700 uppercase text-xs tracking-wider">{issue.issue_type}</td>
                    <td className="px-6 py-4">
                      <div className="max-w-xs truncate" title={issue.description}>
                        {issue.description}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${
                        issue.status === 'open' ? 'bg-red-100 text-red-800' :
                        issue.status === 'resolved' ? 'bg-emerald-100 text-emerald-800' :
                        'bg-slate-100 text-slate-800'
                      }`}>
                        {issue.status}
                      </span>
                    </td>
                    <td className="px-6 py-4">{formatDate(issue.created_at)}</td>
                    <td className="px-6 py-4">
                      {issue.status === 'open' && (
                        <button onClick={() => handleResolve(issue.report_id)} className="text-indigo-600 font-medium hover:text-indigo-800">Resolve</button>
                      )}
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
