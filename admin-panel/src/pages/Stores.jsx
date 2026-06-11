import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function Stores() {
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
useEffect(() => {
  fetchStores();
  // Auto-refresh every 30 seconds for new trials
  const interval = setInterval(() => {
    fetchStores(false); // pass false to avoid triggering full loading spinner
  }, 30000);
  return () => clearInterval(interval);
}, []);

const fetchStores = async (showLoader = true) => {
  if (showLoader) setLoading(true);
  try {
    const data = await api.adminStores();
    setStores(data.stores || []);
  } catch (e) {
    console.error(e);
  } finally {
    setLoading(false);
  }
};

  const handleApproveTrial = async (storeId) => {
    try {
      await api.approveTrial(storeId);
      alert('Trial approved!');
      fetchStores();
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  };

  const handleCancelSub = async (storeId) => {
    if (!window.confirm('Cancel subscription for this store?')) return;
    try {
      await api.cancelSub(storeId);
      alert('Subscription cancelled.');
      fetchStores();
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Stores Management</h1>
          <p className="text-slate-500 text-sm mt-1">View all stores, manage subscriptions and trials.</p>
        </div>
        <button onClick={fetchStores} className="text-sm font-medium text-indigo-600 bg-indigo-50 px-4 py-2 rounded-lg hover:bg-indigo-100 transition-colors">
          Refresh Data
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-600">
            <thead className="bg-slate-50 text-slate-500 font-semibold uppercase tracking-wider text-xs">
              <tr>
                <th className="px-6 py-4">ID</th>
                <th className="px-6 py-4">Store Name & Location</th>
                <th className="px-6 py-4">Registered</th>
                <th className="px-6 py-4">Owner</th>
                <th className="px-6 py-4">Plan / Tier</th>
                <th className="px-6 py-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">Loading stores...</td></tr>
              ) : stores.length === 0 ? (
                <tr><td colSpan="6" className="px-6 py-4 text-center text-slate-400">No stores found.</td></tr>
              ) : (
                stores.map(store => (
                  <tr key={store.store_id} className="hover:bg-slate-50/50">
                    <td className="px-6 py-4 font-mono text-slate-400">#{store.store_id}</td>
                    <td className="px-6 py-4">
                      <div className="font-bold text-slate-900">
                        <Link to={`/stores/${store.store_id}`} className="hover:text-indigo-600 hover:underline">
                          {store.store_name}
                        </Link>
                      </div>
                      <div className="text-xs text-slate-500 flex items-center gap-1">
                        <span>📍</span> {store.location || 'No location'}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-slate-500">
                      {new Date(store.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4">
                      {store.owner_name}
                      <div className="text-xs text-slate-400">@{store.username}</div>
                      <div className="text-xs text-indigo-500 font-medium">{store.phone_number || 'No phone'}</div>
                    </td>
                    <td className="px-6 py-4">
                      {store.tier === 'pending_trial' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800">Pending Trial</span>
                      ) : store.tier === 'pro' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-100 text-purple-800">Pro</span>
                      ) : store.tier === 'basic' ? (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">Basic</span>
                      ) : (
                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-600">{store.tier || 'None'}</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right space-x-3">
                      {store.tier === 'pending_trial' && (
                        <button onClick={() => handleApproveTrial(store.store_id)} className="text-emerald-600 font-semibold hover:text-emerald-800">Approve</button>
                      )}
                      {(store.tier === 'basic' || store.tier === 'pro' || store.tier === 'trial') && (
                        <button onClick={() => handleCancelSub(store.store_id)} className="text-red-500 font-medium hover:text-red-700">Cancel Plan</button>
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
