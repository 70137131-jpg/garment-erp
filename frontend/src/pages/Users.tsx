import { FormEvent, useState } from "react";
import { api, ApiError, AuthUser } from "../api/client";
import { PasswordInput } from "../components/PasswordInput";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";

const ROLES = ["admin", "merchandiser", "procurement", "stores", "cutting_supervisor", "sewing_supervisor", "quality_inspector", "planner", "finance", "readonly"];

interface AuthSessionRecord {
  id: number; user_id: number; created_at: string; last_seen_at: string;
  expires_at: string; revoked_at?: string; ip_address?: string; user_agent?: string;
}

interface AuditEvent {
  id: number; occurred_at: string; event_type: string; actor_user_id?: number;
  target_user_id?: number; email?: string; ip_address?: string; detail?: string;
}

export default function Users() {
  const [tab, setTab] = useState("users");
  const users = useAsync(() => api.get<AuthUser[]>("/auth/users"));
  return <div>
    <PageHeader eyebrow="Security" title="Access Control" subtitle="Accounts, role assignments, password recovery, active sessions and security audit history." />
    <Tabs tabs={[{ key: "users", label: "Users" }, { key: "sessions", label: "Sessions" }, { key: "audit", label: "Audit Log" }]} active={tab} onChange={setTab} />
    {tab === "users" && <UserManagement users={users.data ?? []} loading={users.loading} reload={users.reload} />}
    {tab === "sessions" && <Sessions users={users.data ?? []} />}
    {tab === "audit" && <AuditLog users={users.data ?? []} />}
  </div>;
}

function UserManagement({ users, loading, reload }: { users: AuthUser[]; loading: boolean; reload: () => void }) {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [roles, setRoles] = useState<string[]>(["readonly"]);
  const [resetTarget, setResetTarget] = useState<AuthUser | null>(null);
  const [saving, setSaving] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault(); setSaving(true);
    try {
      await api.post("/auth/users", { email, display_name: name, password, roles });
      setEmail(""); setName(""); setPassword(""); setRoles(["readonly"]); reload();
      toast.push("User created");
    } catch (e) { toast.push("Unable to create user", { detail: (e as ApiError).message, bad: true }); }
    finally { setSaving(false); }
  }

  async function update(user: AuthUser, body: unknown, message: string) {
    try { await api.patch(`/auth/users/${user.id}`, body); reload(); toast.push(message); }
    catch (e) { toast.push("Unable to update user", { detail: (e as ApiError).message, bad: true }); }
  }

  async function setUserRole(user: AuthUser, role: string, checked: boolean) {
    const next = checked ? [...user.roles, role] : user.roles.filter((item) => item !== role);
    if (!next.length) { toast.push("Every user needs at least one role", { bad: true }); return; }
    await update(user, { roles: next }, "Roles updated and sessions revoked");
  }

  return <div className="grid cols-2">
    <Card title="Create user" hint="temporary password must be changed at first sign-in">
      <form onSubmit={create} className="card-pad">
        <Field label="Display name" required><input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></Field>
        <Field label="Email" required><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Temporary password" required hint="15–256 characters"><input className="input" type="password" minLength={15} value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        <RolePicker roles={roles} onChange={setRoles} />
        <button className="btn primary" disabled={saving || !roles.length}>{saving ? "Creating…" : "Create user"}</button>
      </form>
    </Card>
    <Card title="Users" hint="deactivate instead of deleting">
      {loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
        <thead><tr><th>User</th><th>Roles</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>{users.map((user) => <tr key={user.id}>
          <td><b>{user.display_name}</b><br /><span className="mono muted">{user.email}</span></td>
          <td><div className="compact-roles">{ROLES.map((role) => <label key={role}><input type="checkbox" checked={user.roles.includes(role)} onChange={(e) => setUserRole(user, role, e.target.checked)} /> {role.replace(/_/g, " ")}</label>)}</div></td>
          <td><Chip tone={user.is_active ? "ok" : "bad"} label={user.is_active ? (user.must_change_password ? "Password change due" : "Active") : "Disabled"} /></td>
          <td><div className="inline-actions"><button className="btn sm" onClick={() => setResetTarget(user)}>Reset password</button><button className="btn ghost sm" onClick={() => update(user, { is_active: !user.is_active }, user.is_active ? "User disabled and sessions revoked" : "User enabled")}>{user.is_active ? "Disable" : "Enable"}</button></div></td>
        </tr>)}{!users.length && <tr><td colSpan={4} className="muted">No users.</td></tr>}</tbody>
      </table></div>}
    </Card>
    {resetTarget && <ResetPassword user={resetTarget} onClose={() => setResetTarget(null)} onDone={() => { setResetTarget(null); reload(); }} />}
  </div>;
}

function RolePicker({ roles, onChange }: { roles: string[]; onChange: (roles: string[]) => void }) {
  return <Field label="Roles"><div className="role-grid">{ROLES.map((role) => <label key={role}>
    <input type="checkbox" checked={roles.includes(role)} onChange={(e) => onChange(e.target.checked ? [...roles, role] : roles.filter((item) => item !== role))} /> {role.replace(/_/g, " ")}
  </label>)}</div></Field>;
}

function ResetPassword({ user, onClose, onDone }: { user: AuthUser; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    if (password !== confirmation) { setError("Passwords do not match"); return; }
    setSaving(true); setError(null);
    try { await api.post(`/auth/users/${user.id}/reset-password`, { temporary_password: password }); toast.push("Temporary password set; sessions revoked"); onDone(); }
    catch (e) { setError((e as ApiError).message); }
    finally { setSaving(false); }
  }
  return <Drawer title={`Reset ${user.display_name}'s password`} sub="All existing sessions will be revoked" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={saving || password.length < 15} onClick={save}>Reset password</button></>}>
    {error && <ErrorBox message={error} />}
    <Field label="Temporary password" required><PasswordInput className="input" minLength={15} value={password} onChange={(e) => setPassword(e.target.value)} showStrength /></Field>
    <Field label="Confirm password" required><PasswordInput className="input" minLength={15} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></Field>
  </Drawer>;
}

function Sessions({ users }: { users: AuthUser[] }) {
  const sessions = useAsync(() => api.get<AuthSessionRecord[]>("/auth/sessions"));
  const toast = useToast();
  const name = (id: number) => users.find((user) => user.id === id)?.email ?? `User #${id}`;
  async function revoke(id: number) {
    try { await api.post(`/auth/sessions/${id}/revoke`); sessions.reload(); toast.push("Session revoked"); }
    catch (e) { toast.push("Unable to revoke session", { detail: (e as ApiError).message, bad: true }); }
  }
  return <Card title="Sessions" hint="server-side sessions can be revoked immediately">
    {sessions.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
      <thead><tr><th>User</th><th>Created</th><th>Last seen</th><th>IP</th><th>Client</th><th>Status</th><th></th></tr></thead>
      <tbody>{sessions.data?.map((item) => {
        const active = !item.revoked_at && new Date(item.expires_at) > new Date();
        return <tr key={item.id}><td>{name(item.user_id)}</td><td>{new Date(item.created_at).toLocaleString()}</td><td>{new Date(item.last_seen_at).toLocaleString()}</td><td className="mono">{item.ip_address || "—"}</td><td title={item.user_agent}>{item.user_agent?.slice(0, 35) || "—"}</td><td><Chip tone={active ? "ok" : "neutral"} label={active ? "Active" : item.revoked_at ? "Revoked" : "Expired"} /></td><td>{active && <button className="btn sm" onClick={() => revoke(item.id)}>Revoke</button>}</td></tr>;
      })}{!sessions.data?.length && <tr><td colSpan={7} className="muted">No sessions.</td></tr>}</tbody>
    </table></div>}
  </Card>;
}

function AuditLog({ users }: { users: AuthUser[] }) {
  const events = useAsync(() => api.get<AuditEvent[]>("/auth/audit-events?limit=200"));
  const identity = (id?: number) => id ? users.find((user) => user.id === id)?.email ?? `User #${id}` : "System";
  return <Card title="Security audit log" hint="latest 200 events">
    {events.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
      <thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Target</th><th>IP</th><th>Detail</th></tr></thead>
      <tbody>{events.data?.map((event) => <tr key={event.id}><td>{new Date(event.occurred_at).toLocaleString()}</td><td className="code">{event.event_type}</td><td>{identity(event.actor_user_id)}</td><td>{event.email || identity(event.target_user_id)}</td><td className="mono">{event.ip_address || "—"}</td><td>{event.detail || "—"}</td></tr>)}{!events.data?.length && <tr><td colSpan={6} className="muted">No audit events.</td></tr>}</tbody>
    </table></div>}
  </Card>;
}
