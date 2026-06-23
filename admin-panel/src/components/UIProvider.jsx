import React, { createContext, useCallback, useContext, useState } from 'react';

// Lightweight toast + confirm system so pages stop using window.alert/confirm.
// Usage:  const ui = useUI();  ui.toast('Saved', 'success');
//         if (await ui.confirm({ title, message, danger:true })) { ... }

const UICtx = createContext(null);
export const useUI = () => useContext(UICtx);

let _id = 0;

export function UIProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [dialog, setDialog] = useState(null); // { title, message, confirmLabel, danger, resolve }

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback((message, type = 'info') => {
    const id = ++_id;
    setToasts((t) => [...t, { id, message, type }]);
    setTimeout(() => dismiss(id), 3500);
  }, [dismiss]);

  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      setDialog({
        title: opts.title || 'Are you sure?',
        message: opts.message || '',
        confirmLabel: opts.confirmLabel || 'Confirm',
        danger: !!opts.danger,
        resolve,
      });
    });
  }, []);

  const close = (val) => {
    dialog?.resolve(val);
    setDialog(null);
  };

  const tone = {
    success: 'bg-emerald-600',
    error: 'bg-rose-600',
    info: 'bg-slate-800',
  };

  return (
    <UICtx.Provider value={{ toast, confirm }}>
      {children}

      {/* Toast stack */}
      <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={`${tone[t.type] || tone.info} text-white text-sm font-medium px-4 py-2.5 rounded-lg shadow-lg cursor-pointer animate-[fadeIn_.15s_ease-out] max-w-sm`}
          >
            {t.message}
          </div>
        ))}
      </div>

      {/* Confirm modal */}
      {dialog && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm p-5">
            <h3 className="text-base font-bold text-slate-900">{dialog.title}</h3>
            {dialog.message && (
              <p className="text-sm text-slate-500 mt-1.5">{dialog.message}</p>
            )}
            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => close(false)}
                className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={() => close(true)}
                className={`px-4 py-2 text-sm font-bold text-white rounded-lg ${
                  dialog.danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700'
                }`}
              >
                {dialog.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </UICtx.Provider>
  );
}
