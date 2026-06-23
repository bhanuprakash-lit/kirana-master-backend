import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';

const navItems = [
  { path: '/', label: 'Dashboard', icon: '📊' },
  { path: '/stores', label: 'Stores', icon: '🏬' },
  { path: '/users', label: 'User Activity', icon: '👥' },
  { path: '/sessions', label: 'Security monitor', icon: '🔐' },
  { path: '/products', label: 'Products', icon: '📦' },
  { path: '/cashflow', label: 'Cashflow', icon: '💸' },
  { path: '/vouchers', label: 'Loyalty / Vouchers', icon: '🎟️' },
  { path: '/loyalty-overview', label: 'Loyalty Overview', icon: '💳' },
  { path: '/store-groups', label: 'Store Groups', icon: '🔗' },
  { path: '/store-ops', label: 'Store Ops', icon: '🧰' },
  { path: '/intelligence', label: 'AI Intelligence', icon: '🧠' },
  { path: '/kpis', label: 'KPI Settings', icon: '🎯' },
  { path: '/issues', label: 'Support', icon: '🐛' },
  { path: '/whatsapp', label: 'WhatsApp', icon: '💬' },
  { path: '/settings', label: 'Settings', icon: '⚙️' },
];

export default function Sidebar({ onLogout }) {
  const navigate = useNavigate();

  const handleLogout = () => {
    onLogout();
    navigate('/');
  };

  return (
    <div className="w-56 bg-slate-900 text-slate-300 flex flex-col h-screen shrink-0">
      <div className="px-5 py-4">
        <h1 className="text-base font-bold text-white flex items-center gap-2">
          <span>🏪</span> Kirana Admin
        </h1>
      </div>

      <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto custom-scrollbar">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-colors ${
                isActive ? 'bg-indigo-600/20 text-indigo-400' : 'hover:bg-slate-800 hover:text-white'
              }`
            }
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="p-3 border-t border-slate-800">
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
        >
          <span className="text-base">🚪</span> Logout
        </button>
      </div>
    </div>
  );
}
