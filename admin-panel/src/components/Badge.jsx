import React from 'react';

export default function Badge({ children, color = 'bg-slate-100 text-slate-600' }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${color}`}>
      {children}
    </span>
  );
}
