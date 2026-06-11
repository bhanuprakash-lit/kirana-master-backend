import React, { useState } from 'react';
import { configure } from '../api';

export default function Login({ onLogin }) {
  const [url, setUrl] = useState('http://localhost:9000');
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    
    // Temporarily configure API
    configure(url, apiKey);
    
    try {
      // Validate by calling a health endpoint or admin endpoint
      // Using dynamic import of api object since it's already configured
      const { api } = await import('../api');
      await api.stats();
      
      onLogin(url, apiKey);
    } catch (err) {
      setError(err.message || 'Failed to connect. Check URL and Admin API Key.');
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white p-8 rounded-2xl border border-slate-200 shadow-xl w-full max-w-md">
        <div className="text-center mb-8">
          <span className="text-4xl mb-4 block">🏪</span>
          <h1 className="text-2xl font-black text-slate-900">Kirana Master</h1>
          <p className="text-slate-500 text-sm mt-1">Admin Panel Login</p>
        </div>
        
        {error && (
          <div className="mb-6 p-3 bg-red-50 text-red-700 text-sm font-medium rounded-lg border border-red-200 text-center">
            {error}
          </div>
        )}
        
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Backend URL</label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Admin API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>
          <button
            type="submit"
            className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3 rounded-xl transition-colors shadow-sm"
          >
            Connect to Master
          </button>
        </form>
      </div>
    </div>
  );
}
