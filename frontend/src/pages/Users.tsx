import { FormEvent, useEffect, useState } from "react";
import { api, AuthUser } from "../api/client";
import { useToast } from "../components/Toast";

const ROLES = ["admin", "merchandiser", "procurement", "stores", "cutting_supervisor", "sewing_supervisor", "quality_inspector", "planner", "finance", "readonly"];

export default function Users() {
  const toast = useToast();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [roles, setRoles] = useState<string[]>(["readonly"]);

  async function load() { setUsers(await api.get<AuthUser[]>("/auth/users")); }
  useEffect(() => { load().catch((e) => toast.push("Unable to load users", { detail: e.message, bad: true })); }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      await api.post("/auth/users", { email, display_name: name, password, roles });
      setEmail(""); setName(""); setPassword(""); setRoles(["readonly"]);
      await load();
      toast.push("User created");
    } catch (e: any) { toast.push("Unable to create user", { detail: e.message, bad: true }); }
  }

  async function toggleActive(user: AuthUser) {
    try {
      await api.patch(`/auth/users/${user.id}`, { is_active: !user.is_active });
      await load();
      toast.push(user.is_active ? "User disabled and sessions revoked" : "User enabled");
    } catch (e: any) { toast.push("Unable to update user", { detail: e.message, bad: true }); }
  }

  async function setUserRole(user: AuthUser, role: string, checked: boolean) {
    const next = checked ? [...user.roles, role] : user.roles.filter((item) => item !== role);
    try {
      await api.patch(`/auth/users/${user.id}`, { roles: next });
      await load();
      toast.push("Role assignments updated; existing sessions were revoked");
    } catch (e: any) { toast.push("Unable to update roles", { detail: e.message, bad: true }); }
  }

  return <div>
    <div className="page-head"><div><div className="eyebrow">Security</div><h1>Access Control</h1><p>Accounts and centrally assigned ERP roles.</p></div></div>
    <div className="grid two">
      <section className="card">
        <h2>Create user</h2>
        <form onSubmit={create} className="form-grid">
          <label>Display name<input className="input" value={name} onChange={(e) => setName(e.target.value)} required /></label>
          <label>Email<input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label className="full">Temporary password<input className="input" type="password" minLength={15} value={password} onChange={(e) => setPassword(e.target.value)} required /><small>Minimum 15 characters. The user must replace it at first sign-in; share it through a secure channel.</small></label>
          <fieldset className="full role-grid"><legend>Roles</legend>{ROLES.map((role) => <label key={role}><input type="checkbox" checked={roles.includes(role)} onChange={(e) => setRoles(e.target.checked ? [...roles, role] : roles.filter((r) => r !== role))} /> {role.replace(/_/g, " ")}</label>)}</fieldset>
          <button className="btn primary" disabled={!roles.length}>Create user</button>
        </form>
      </section>
      <section className="card">
        <h2>Users</h2>
        <div className="table-wrap"><table><thead><tr><th>User</th><th>Roles</th><th>Status</th><th /></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td><b>{user.display_name}</b><br /><small>{user.email}</small></td><td><div className="compact-roles">{ROLES.map((role) => <label key={role}><input type="checkbox" checked={user.roles.includes(role)} onChange={(e) => setUserRole(user, role, e.target.checked)} /> {role.replace(/_/g, " ")}</label>)}</div></td><td>{user.is_active ? "Active" : "Disabled"}</td><td><button className="btn ghost" onClick={() => toggleActive(user)}>{user.is_active ? "Disable" : "Enable"}</button></td></tr>)}</tbody></table></div>
      </section>
    </div>
  </div>;
}
