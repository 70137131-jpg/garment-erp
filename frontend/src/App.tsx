import { FormEvent, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ApiError, AuthUser, auth } from "./api/client";
import { Shell } from "./components/Shell";
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
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark"><span className="glyph" /></div>
        <div className="eyebrow">Atelier · Garment ERP</div>
        <h1>Sign in</h1>
        <p className="muted">Use the account issued by your ERP administrator.</p>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label>Email</label>
        <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        <label>Password</label>
        <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
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
    if (newPassword !== confirmation) { setError("New passwords do not match"); return; }
    setBusy(true); setError("");
    try {
      await auth.changePassword(currentPassword, newPassword);
      onChanged(await auth.me());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to change password");
    } finally { setBusy(false); }
  }

  return <div className="login-page"><form className="login-card" onSubmit={submit}>
    <div className="eyebrow">Security · {user.email}</div><h1>Choose a new password</h1>
    <p className="muted">Your temporary password must be replaced before accessing the ERP.</p>
    {error && <div className="auth-error" role="alert">{error}</div>}
    <label>Current password<input className="input" type="password" autoComplete="current-password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required /></label>
    <label>New password<input className="input" type="password" autoComplete="new-password" minLength={15} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required /></label>
    <label>Confirm new password<input className="input" type="password" autoComplete="new-password" minLength={15} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required /></label>
    <button className="btn primary" disabled={busy}>{busy ? "Updating…" : "Update password"}</button>
    <button className="btn ghost" type="button" onClick={onLogout}>Sign out</button>
  </form></div>;
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

  if (loading) return <div className="app-loading">Loading Atelier…</div>;
  if (!user) return <Login onLogin={setUser} />;
  if (user.must_change_password) return <PasswordChange user={user} onChanged={setUser} onLogout={async () => { try { await auth.logout(); } finally { setUser(null); } }} />;
  const permit = (permission: string, element: JSX.Element) =>
    user.permissions.includes(permission) ? element : <Navigate to="/" replace />;

  return (
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
      </Routes>
    </Shell>
  );
}
