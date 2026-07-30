import { FormEvent, useState } from "react";
import { ApiError, AuthUser, auth } from "../api/client";
import { PasswordInput } from "../components/PasswordInput";
import { ThemeSwitch } from "../components/ThemeSwitch";
import { useToast } from "../components/Toast";
import { Card, Chip, ErrorBox, Field, PageHeader, Tabs } from "../components/ui";
import { useTheme } from "../theme/ThemeProvider";

export default function Settings({ user }: { user: AuthUser }) {
  const [tab, setTab] = useState("appearance");

  return (
    <div>
      <PageHeader
        eyebrow="Account"
        title="Settings"
        subtitle="Personalise how the workspace looks on this device, and manage your own account security."
      />
      <Tabs
        tabs={[
          { key: "appearance", label: "Appearance" },
          { key: "profile", label: "Profile" },
          { key: "security", label: "Security" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "appearance" && <Appearance />}
      {tab === "profile" && <Profile user={user} />}
      {tab === "security" && <Security />}
    </div>
  );
}

function Appearance() {
  const { choice, resolved } = useTheme();

  return (
    <div className="grid cols-2">
      <Card title="Theme" hint="stored on this device">
        <div className="card-pad">
          <Field
            label="Colour scheme"
            hint="System follows your operating system and updates live when it changes."
          >
            <ThemeSwitch />
          </Field>
          <dl className="kv">
            <dt>Preference</dt>
            <dd>{choice === "system" ? "Follow system" : choice === "light" ? "Light" : "Dark"}</dd>
            <dt>Currently</dt>
            <dd><Chip tone={resolved === "light" ? "info" : "neutral"} label={resolved === "light" ? "Light" : "Dark"} /></dd>
          </dl>
          <p className="hintline mt-0">
            The preference is saved per browser, not to your account, so each device you sign in
            from keeps its own setting.
          </p>
        </div>
      </Card>
      <Card title="Preview" hint="how surfaces read in the current theme">
        <div className="card-pad theme-preview">
          <div className="theme-preview-row">
            <Chip tone="ok" label="Confirmed" />
            <Chip tone="warn" label="Pending" />
            <Chip tone="bad" label="Blocked" />
            <Chip tone="info" label="Draft" />
          </div>
          <div className="theme-preview-row">
            <button className="btn primary" type="button">Primary</button>
            <button className="btn" type="button">Secondary</button>
            <button className="btn ghost" type="button">Ghost</button>
          </div>
          <div className="theme-preview-stat">
            <div className="k">Sample metric</div>
            <div className="v">1,248</div>
            <div className="foot">Editorial numerals, tabular figures</div>
          </div>
        </div>
      </Card>
    </div>
  );
}

function Profile({ user }: { user: AuthUser }) {
  return (
    <div className="grid cols-2">
      <Card title="Your account" hint="managed by an administrator">
        <div className="card-pad">
          <dl className="kv">
            <dt>Display name</dt>
            <dd>{user.display_name}</dd>
            <dt>Email</dt>
            <dd className="mono">{user.email}</dd>
            <dt>Status</dt>
            <dd><Chip tone={user.is_active ? "ok" : "bad"} label={user.is_active ? "Active" : "Disabled"} /></dd>
            <dt>Member since</dt>
            <dd>{new Date(user.created_at).toLocaleDateString()}</dd>
          </dl>
          <p className="hintline mt-0">
            Your name and email are part of the audit trail, so they are changed by an
            administrator in Access Control rather than edited here.
          </p>
        </div>
      </Card>
      <Card title="Roles and access" hint={`${user.permissions.length} permissions`}>
        <div className="card-pad">
          <div className="section-title mt-0">Roles</div>
          <div className="tag-list">
            {user.roles.map((role) => <Chip key={role} tone="info" label={role.replace(/_/g, " ")} />)}
            {!user.roles.length && <span className="muted">No roles assigned.</span>}
          </div>
          <div className="section-title">Permissions</div>
          <div className="tag-list">
            {user.permissions.map((permission) => (
              <span className="chip" key={permission}>{permission}</span>
            ))}
            {!user.permissions.length && <span className="muted">No permissions granted.</span>}
          </div>
        </div>
      </Card>
    </div>
  );
}

function Security() {
  const toast = useToast();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmation) {
      setError("New passwords do not match");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await auth.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      toast.push("Password updated");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unable to change password");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid cols-2">
      <Card title="Change password" hint="15–256 characters">
        <form className="card-pad" onSubmit={submit}>
          {error && <ErrorBox message={error} />}
          <Field label="Current password" required>
            <PasswordInput
              className="input"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
            />
          </Field>
          <Field label="New password" required>
            <PasswordInput
              className="input"
              autoComplete="new-password"
              minLength={15}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              showStrength
              required
            />
          </Field>
          <Field label="Confirm new password" required>
            <PasswordInput
              className="input"
              autoComplete="new-password"
              minLength={15}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              required
            />
          </Field>
          <button className="btn primary" disabled={saving || newPassword.length < 15}>
            {saving ? "Updating…" : "Update password"}
          </button>
        </form>
      </Card>
      <Card title="Session security" hint="how this workspace protects you">
        <div className="card-pad">
          <dl className="kv">
            <dt>Session</dt>
            <dd>Server-side, revocable, delivered in an HttpOnly cookie</dd>
            <dt>Idle timeout</dt>
            <dd>You are signed out automatically after a period of inactivity</dd>
            <dt>On password change</dt>
            <dd>Other sessions are invalidated</dd>
          </dl>
          <p className="hintline mt-0">
            An administrator can review and revoke active sessions for any account from Access
            Control.
          </p>
        </div>
      </Card>
    </div>
  );
}
