import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { configure, onUnauthorized } from './api';
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
import Issues from './pages/Issues';
import Settings from './pages/Settings';
import WhatsApp from './pages/whatsapp/WhatsApp';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  const handleLogout = React.useCallback(() => {
    sessionStorage.removeItem('kirana_url');
    sessionStorage.removeItem('kirana_key');
    setIsAuthenticated(false);
  }, []);

  useEffect(() => {
    // Any 401/403 from the API → drop the session and bounce to login.
    onUnauthorized(handleLogout);
    // Check session storage on initial load
    const url = sessionStorage.getItem('kirana_url');
    const key = sessionStorage.getItem('kirana_key');
    if (url && key) {
      configure(url, key);
      setIsAuthenticated(true);
    }
  }, [handleLogout]);

  const handleLogin = (url, key) => {
    sessionStorage.setItem('kirana_url', url);
    sessionStorage.setItem('kirana_key', key);
    setIsAuthenticated(true);
  };

  if (!isAuthenticated) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <UIProvider>
      <div className="min-h-screen bg-slate-50 text-slate-900 font-sans antialiased flex text-[13px]">
        <Sidebar onLogout={handleLogout} />

        <div className="flex-1 flex flex-col h-screen overflow-hidden">
          <header className="bg-white border-b border-slate-200 px-6 py-2.5 flex items-center justify-between z-10">
            <h2 className="text-sm font-bold text-slate-700">Admin Control Center</h2>
            <div className="text-xs font-medium text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">
              {sessionStorage.getItem('kirana_url')}
            </div>
          </header>

          <main className="flex-1 overflow-auto p-6 relative">
            <ErrorBoundary>
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
                <Route path="/issues" element={<Issues />} />
                <Route path="/whatsapp" element={<WhatsApp />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </UIProvider>
  );
}

export default App;
