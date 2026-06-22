import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { isConfigured, configure } from './api';
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
import KpiTiers from './pages/KpiTiers';
import KpiVisibility from './pages/KpiVisibility';
import StoreGroups from './pages/StoreGroups';
import LoyaltyOverview from './pages/LoyaltyOverview';
import StoreOps from './pages/StoreOps';
import Intelligence from './pages/Intelligence';
import Issues from './pages/Issues';
import Settings from './pages/Settings';
import WhatsApp from './pages/whatsapp/WhatsApp';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    // Check session storage on initial load
    const url = sessionStorage.getItem('kirana_url');
    const key = sessionStorage.getItem('kirana_key');
    if (url && key) {
      configure(url, key);
      setIsAuthenticated(true);
    }
  }, []);

  const handleLogin = (url, key) => {
    sessionStorage.setItem('kirana_url', url);
    sessionStorage.setItem('kirana_key', key);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    sessionStorage.removeItem('kirana_url');
    sessionStorage.removeItem('kirana_key');
    setIsAuthenticated(false);
  };

  if (!isAuthenticated) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans antialiased flex">
      <Sidebar onLogout={handleLogout} />

      <div className="flex-1 flex flex-col h-screen overflow-hidden">
        <header className="bg-white border-b border-slate-200 px-8 py-4 flex items-center justify-between shadow-sm z-10">
          <h2 className="text-lg font-bold text-slate-800">Admin Control Center</h2>
          <div className="text-sm font-medium text-slate-500 bg-slate-100 px-3 py-1 rounded-full">
            {sessionStorage.getItem('kirana_url')}
          </div>
        </header>

        <main className="flex-1 overflow-auto p-8 relative">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/stores" element={<Stores />} />
            <Route path="/stores/:id" element={<StoreDetail />} />
            <Route path="/users" element={<Users />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/products" element={<Products />} />
            <Route path="/cashflow" element={<Cashflow />} />
            <Route path="/vouchers" element={<Vouchers />} />
            <Route path="/kpis" element={<KpiTiers />} />
            <Route path="/kpi-visibility" element={<KpiVisibility />} />
            <Route path="/store-groups" element={<StoreGroups />} />
            <Route path="/loyalty-overview" element={<LoyaltyOverview />} />
            <Route path="/store-ops" element={<StoreOps />} />
            <Route path="/intelligence" element={<Intelligence />} />
            <Route path="/issues" element={<Issues />} />
            <Route path="/whatsapp" element={<WhatsApp />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default App;
