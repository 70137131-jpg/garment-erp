import { createContext, useCallback, useContext, useState, ReactNode } from "react";

interface Toast {
  id: number;
  message: string;
  detail?: string;
  bad?: boolean;
}

interface ToastCtx {
  push: (message: string, opts?: { detail?: string; bad?: boolean }) => void;
}

const Ctx = createContext<ToastCtx>({ push: () => {} });

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, opts?: { detail?: string; bad?: boolean }) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, detail: opts?.detail, bad: opts?.bad }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="true">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>
            <div>{t.message}</div>
            {t.detail && <div className="t-mono">{t.detail}</div>}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
