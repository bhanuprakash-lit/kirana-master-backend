import React, { useState } from 'react';
import { configure, configureExecutive } from '../api';

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('admin');   // 'admin' | 'executive'
  const [url, setUrl] = useState('http://localhost:9000');
  const [apiKey, setApiKey] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const handleAdmin = async () => {
    configure(url, apiKey);
    const { api } = await import('../api');
    await api.stats();                       // validate the key
    onLogin({ mode: 'admin', url, key: apiKey });
  };

  const handleExecutive = async () => {
    configureExecutive(url, '');             // set base URL for the login call
    const { api } = await import('../api');
    const res = await api.ccLogin(username.trim(), password);
    configureExecutive(url, res.token);
    onLogin({ mode: 'executive', url, token: res.token, role: res.role, name: res.full_name });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'admin') await handleAdmin();
      else await handleExecutive();
    } catch (err) {
      setError(err.message || 'Failed to connect. Check your details.');
    } finally {
      setBusy(false);
    }
  };

  const Tab = ({ id, label }) => (
    <button
      type="button"
      onClick={() => { setMode(id); setError(''); }}
      className={`flex-1 py-2 rounded-lg text-sm font-bold transition-colors ${
        mode === id ? 'bg-white text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white p-8 rounded-2xl border border-slate-200 shadow-xl w-full max-w-md">
        <div className="text-center mb-6">
          <span className="text-4xl mb-4 block">🏪</span>
          <h1 className="text-2xl font-black text-slate-900">Kirana Master</h1>
          <p className="text-slate-500 text-sm mt-1">Admin Panel Login</p>
        </div>

        <div className="flex gap-1 bg-slate-100 p-1 rounded-xl mb-6">
          <Tab id="admin" label="Admin" />
          <Tab id="executive" label="Call Executive" />
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
              type="url" value={url} onChange={(e) => setUrl(e.target.value)}
              className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          {mode === 'admin' ? (
            <div>
              <label className="block text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Admin API Key</label>
              <input
                type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                required
              />
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Username</label>
                <input
                  type="text" value={username} onChange={(e) => setUsername(e.target.value)}
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-bold text-slate-600 uppercase tracking-wider mb-2">Password</label>
                <input
                  type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  required
                />
              </div>
            </>
          )}

          <button
            type="submit" disabled={busy}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-bold py-3 rounded-xl transition-colors shadow-sm"
          >
            {busy ? 'Connecting…' : mode === 'admin' ? 'Connect to Master' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}
