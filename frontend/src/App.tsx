import { FormEvent, ReactNode, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ApiError, AuthUser, auth } from "./api/client";
import { AuthorizationProvider } from "./auth/Authorization";
import { Shell } from "./components/Shell";
import { Spinner } from "./components/ui";
import Costing from "./pages/Costing";
import Dashboard from "./pages/Dashboard";
import Finance from "./pages/Finance";
import Inventory from "./pages/Inventory";
import Masters from "./pages/Masters";
import Procurement from "./pages/Procurement";
import Production from "./pages/Production";
import Quality from "./pages/Quality";
import Sales from "./pages/Sales";
import SalesDetail from "./pages/SalesDetail";
import StyleDetail from "./pages/StyleDetail";
import Styles from "./pages/Styles";
import Users from "./pages/Users";

function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <section className="auth-brand" aria-label="Atelier Garment ERP">
        <div className="auth-brand-top">
          <div className="brand-symbol large" aria-hidden="true"><span /></div>
          <div>
            <div className="auth-wordmark">Atelier</div>
            <div className="auth-product">Garment ERP</div>
          </div>
        </div>
        <div className="auth-brand-copy">
          <div className="eyebrow light">Built for garment operations</div>
          <h2>One operating system for your factory.</h2>
          <p>Connect demand, materials, production, quality, and finance in a single controlled workspace.</p>
          <div className="auth-capabilities">
            <span>Order to cash</span>
            <span>Plan to produce</span>
            <span>Procure to pay</span>
          </div>
        </div>
        <div className="auth-brand-foot">Secure operations workspace</div>
      </section>
      <main className="auth-main">{children}</main>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await auth.login(email, password));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <form className="login-card" onSubmit={submit}>
        <div className="auth-mobile-brand">
          <div className="brand-symbol" aria-hidden="true"><span /></div>
          <strong>Atelier</strong>
        </div>
        <div className="eyebrow">Welcome back</div>
        <h1>Sign in to Atelier</h1>
        <p className="muted">Use the account issued by your ERP administrator.</p>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label htmlFor="login-email">Email address</label>
        <input id="login-email" className="input" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" required autoFocus />
        <label htmlFor="login-password">Password</label>
        <input id="login-password" className="input" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" required />
        <button className="btn primary auth-submit" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
        <div className="auth-security"><span aria-hidden="true" /> Your session is protected and access is role controlled.</div>
      </form>
    </AuthLayout>
  );
}

function PasswordChange({ user, onChanged, onLogout }: { user: AuthUser; onChanged: (user: AuthUser) => void; onLogout: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmation) {
      setError("New passwords do not match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await auth.changePassword(currentPassword, newPassword);
      onChanged(await auth.me());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <form className="login-card" onSubmit={submit}>
        <div className="eyebrow">Account security</div>
        <h1>Choose a new password</h1>
        <p className="muted">Your temporary password must be replaced before accessing the workspace.</p>
        <div className="account-context">Signed in as <strong>{user.email}</strong></div>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label htmlFor="current-password">Current password</label>
        <input id="current-password" className="input" type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
        <label htmlFor="new-password">New password</label>
        <input id="new-password" className="input" type="password" autoComplete="new-password" minLength={15} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
        <div className="password-hint">Use at least 15 characters.</div>
        <label htmlFor="confirm-password">Confirm new password</label>
        <input id="confirm-password" className="input" type="password" autoComplete="new-password" minLength={15} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required />
        <div className="auth-actions">
          <button className="btn primary" disabled={busy}>{busy ? "Updating..." : "Update password"}</button>
          <button className="btn ghost" type="button" onClick={onLogout}>Sign out</button>
        </div>
      </form>
    </AuthLayout>
  );
}

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    auth.me().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
    const unauthorize = () => setUser(null);
    window.addEventListener("erp:unauthorized", unauthorize);
    return () => window.removeEventListener("erp:unauthorized", unauthorize);
  }, []);

  if (loading) return <div className="app-loading"><div className="loading-brand"><div className="brand-symbol large"><span /></div><strong>Atelier</strong></div><Spinner /></div>;
  if (!user) return <Login onLogin={setUser} />;
  if (user.must_change_password) return <PasswordChange user={user} onChanged={setUser} onLogout={async () => { try { await auth.logout(); } finally { setUser(null); } }} />;
  const permit = (permission: string, element: JSX.Element) =>
    user.permissions.includes(permission) ? element : <Navigate to="/" replace />;

  return (
    <AuthorizationProvider user={user}>
      <Shell user={user} onLogout={async () => { try { await auth.logout(); } finally { setUser(null); } }}>
        <Routes>
          <Route path="/" element={<Dashboard permissions={user.permissions} />} />
          <Route path="/masters" element={permit("masters:read", <Masters />)} />
          <Route path="/styles" element={permit("styles:read", <Styles />)} />
          <Route path="/styles/:id" element={permit("styles:read", <StyleDetail />)} />
          <Route path="/sales" element={permit("sales:read", <Sales />)} />
          <Route path="/sales/:id" element={permit("sales:read", <SalesDetail />)} />
          <Route path="/procurement" element={permit("procurement:read", <Procurement />)} />
          <Route path="/inventory" element={permit("inventory:read", <Inventory />)} />
          <Route path="/production" element={permit("production:read", <Production />)} />
          <Route path="/quality" element={permit("quality:read", <Quality />)} />
          <Route path="/costing" element={permit("costing:read", <Costing />)} />
          <Route path="/finance" element={permit("finance:read", <Finance />)} />
          {user.permissions.includes("users:manage") && <Route path="/users" element={<Users />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </AuthorizationProvider>
  );
}
