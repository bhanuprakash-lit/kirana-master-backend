import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';

const adminNav = [
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
  { path: '/vision', label: 'Vision AI', icon: '👁️' },
  { path: '/director-access', label: 'Director Access', icon: '📈' },
  { path: '/kpis', label: 'KPI Settings', icon: '🎯' },
  { path: '/issues', label: 'Support', icon: '🐛' },
  { path: '/whatsapp', label: 'WhatsApp', icon: '💬' },
  { path: '/settings', label: 'Settings', icon: '⚙️' },
];

// Call-center manager pages (shown to admins and call managers).
const managerCallCenterNav = [
  { path: '/callcenter/executives', label: 'Call Execs', icon: '📞' },
  { path: '/callcenter/assignments', label: 'Assignments', icon: '🗂️' },
  { path: '/callcenter/feedback', label: 'Call Feedback', icon: '📝' },
];

// Call-center executive pages (shown to call executives).
const executiveNav = [
  { path: '/callcenter/queue', label: 'My Queue', icon: '📞' },
  { path: '/callcenter/callbacks', label: 'Callbacks', icon: '⏰' },
  { path: '/callcenter/stats', label: 'My Stats', icon: '📊' },
];

export default function Sidebar({ onLogout, auth }) {
  const navigate = useNavigate();

  const handleLogout = () => {
    onLogout();
    navigate('/');
  };

  // Build the nav + optional section heading per role.
  let sections;
  if (auth?.mode === 'admin') {
    sections = [
      { items: adminNav },
      { heading: 'Call Center', items: managerCallCenterNav },
    ];
  } else if (auth?.role === 'call_manager') {
    sections = [{ heading: 'Call Center', items: managerCallCenterNav }];
  } else {
    sections = [{ heading: 'My Work', items: executiveNav }];
  }

  return (
    <div className="w-56 bg-slate-900 text-slate-300 flex flex-col h-screen shrink-0">
      <div className="px-5 py-4">
        <h1 className="text-base font-bold text-white flex items-center gap-2">
          <span>🏪</span> Kirana Admin
        </h1>
      </div>

      <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto custom-scrollbar">
        {sections.map((section, si) => (
          <div key={si} className={si > 0 ? 'pt-3' : ''}>
            {section.heading && (
              <p className="px-3 pb-1 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                {section.heading}
              </p>
            )}
            {section.items.map((item) => (
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
          </div>
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
