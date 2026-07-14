import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { configure, configureExecutive, onUnauthorized, api } from './api';
import { UIProvider } from './components/UIProvider';
import ErrorBoundary from './components/ErrorBoundary';
import Sidebar from './components/Sidebar';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Stores from './pages/Stores';
import StoreDetail from './pages/StoreDetail';
import Users from './pages/Users';
import Sessions from './pages/Sessions';
import Products from './pages/Products';
import Cashflow from './pages/Cashflow';
import Vouchers from './pages/Vouchers';
import KpiSettings from './pages/KpiSettings';
import StoreGroups from './pages/StoreGroups';
import LoyaltyOverview from './pages/LoyaltyOverview';
import StoreOps from './pages/StoreOps';
import Intelligence from './pages/Intelligence';
import Vision from './pages/Vision';
import DirectorAccess from './pages/DirectorAccess';
import Issues from './pages/Issues';
import Settings from './pages/Settings';
import WhatsApp from './pages/whatsapp/WhatsApp';
import CcExecutives from './pages/callcenter/Executives';
import CcAssignments from './pages/callcenter/Assignments';
import CcFeedback from './pages/callcenter/Feedback';
import CcQueue from './pages/callcenter/Queue';
import CcCallbacks from './pages/callcenter/Callbacks';
import CcStats from './pages/callcenter/Stats';

// Call-center manager pages (admin key OR call_manager token).
function ManagerRoutes() {
  return (
    <>
      <Route path="/callcenter/executives" element={<CcExecutives />} />
      <Route path="/callcenter/assignments" element={<CcAssignments />} />
      <Route path="/callcenter/feedback" element={<CcFeedback />} />
    </>
  );
}

function App() {
  const [auth, setAuth] = useState(null);   // { mode, role, name } | null

  const handleLogout = React.useCallback(() => {
    if (sessionStorage.getItem('kirana_cc_token')) {
      api.ccLogout().catch(() => {});   // best-effort token revoke
    }
    ['kirana_url', 'kirana_key', 'kirana_cc_token', 'kirana_cc_role', 'kirana_cc_name']
      .forEach((k) => sessionStorage.removeItem(k));
    setAuth(null);
  }, []);

  useEffect(() => {
    onUnauthorized(handleLogout);
    const url = sessionStorage.getItem('kirana_url');
    const key = sessionStorage.getItem('kirana_key');
    const token = sessionStorage.getItem('kirana_cc_token');
    if (url && key) {
      configure(url, key);
      setAuth({ mode: 'admin' });
    } else if (url && token) {
      configureExecutive(url, token);
      setAuth({
        mode: 'executive',
        role: sessionStorage.getItem('kirana_cc_role'),
        name: sessionStorage.getItem('kirana_cc_name'),
      });
    }
  }, [handleLogout]);

  const handleLogin = (payload) => {
    sessionStorage.setItem('kirana_url', payload.url);
    if (payload.mode === 'admin') {
      sessionStorage.setItem('kirana_key', payload.key);
      setAuth({ mode: 'admin' });
    } else {
      sessionStorage.setItem('kirana_cc_token', payload.token);
      sessionStorage.setItem('kirana_cc_role', payload.role);
      sessionStorage.setItem('kirana_cc_name', payload.name);
      setAuth({ mode: 'executive', role: payload.role, name: payload.name });
    }
  };

  if (!auth) return <Login onLogin={handleLogin} />;

  const isExecutiveOnly = auth.mode === 'executive' && auth.role === 'call_executive';
  const isManager = auth.mode === 'admin' || auth.role === 'call_manager';

  return (
    <UIProvider>
      <div className="min-h-screen bg-slate-50 text-slate-900 font-sans antialiased flex text-[13px]">
        <Sidebar onLogout={handleLogout} auth={auth} />

        <div className="flex-1 flex flex-col h-screen overflow-hidden">
          <header className="bg-white border-b border-slate-200 px-6 py-2.5 flex items-center justify-between z-10">
            <h2 className="text-sm font-bold text-slate-700">
              {auth.mode === 'executive' ? `Call Center · ${auth.name || ''}` : 'Admin Control Center'}
            </h2>
            <div className="text-xs font-medium text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">
              {sessionStorage.getItem('kirana_url')}
            </div>
          </header>

          <main className="flex-1 overflow-auto p-6 relative">
            <ErrorBoundary>
              {auth.mode === 'admin' ? (
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/stores" element={<Stores />} />
                  <Route path="/stores/:id" element={<StoreDetail />} />
                  <Route path="/users" element={<Users />} />
                  <Route path="/sessions" element={<Sessions />} />
                  <Route path="/products" element={<Products />} />
                  <Route path="/cashflow" element={<Cashflow />} />
                  <Route path="/vouchers" element={<Vouchers />} />
                  <Route path="/kpis" element={<KpiSettings />} />
                  <Route path="/kpi-visibility" element={<Navigate to="/kpis" replace />} />
                  <Route path="/store-groups" element={<StoreGroups />} />
                  <Route path="/loyalty-overview" element={<LoyaltyOverview />} />
                  <Route path="/store-ops" element={<StoreOps />} />
                  <Route path="/intelligence" element={<Intelligence />} />
                  <Route path="/vision" element={<Vision />} />
                  <Route path="/director-access" element={<DirectorAccess />} />
                  <Route path="/callcenter/executives" element={<CcExecutives />} />
                  <Route path="/callcenter/assignments" element={<CcAssignments />} />
                  <Route path="/callcenter/feedback" element={<CcFeedback />} />
                  <Route path="/issues" element={<Issues />} />
                  <Route path="/whatsapp" element={<WhatsApp />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              ) : isManager ? (
                <Routes>
                  <Route path="/callcenter/executives" element={<CcExecutives />} />
                  <Route path="/callcenter/assignments" element={<CcAssignments />} />
                  <Route path="/callcenter/feedback" element={<CcFeedback />} />
                  <Route path="*" element={<Navigate to="/callcenter/executives" replace />} />
                </Routes>
              ) : (
                <Routes>
                  <Route path="/callcenter/queue" element={<CcQueue />} />
                  <Route path="/callcenter/callbacks" element={<CcCallbacks />} />
                  <Route path="/callcenter/stats" element={<CcStats />} />
                  <Route path="*" element={<Navigate to="/callcenter/queue" replace />} />
                </Routes>
              )}
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </UIProvider>
  );
}

export default App;
