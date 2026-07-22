import { ReactNode } from "react";
import { statusTone, titled, Tone } from "../lib/format";

export function Chip({ status, tone, label }: { status?: string; tone?: Tone; label?: string }) {
  const t = tone ?? statusTone(status);
  return (
    <span className={`chip ${t}`}>
      <span className="dot" />
      {label ?? titled(status)}
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  hint,
  actions,
  children,
  pad,
}: {
  title?: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
  pad?: boolean;
}) {
  return (
    <div className="card">
      {(title || actions) && (
        <div className="card-head">
          <h3>{title}</h3>
          <div className="flex">
            {hint && <span className="hint">{hint}</span>}
            {actions}
          </div>
        </div>
      )}
      {pad ? <div className="card-pad">{children}</div> : children}
    </div>
  );
}

export function Stat({
  k,
  v,
  foot,
  accent,
}: {
  k: string;
  v: ReactNode;
  foot?: ReactNode;
  accent?: "indigo" | "madder";
}) {
  return (
    <div
      className={`card stat ${accent === "indigo" ? "accent" : ""} ${
        accent === "madder" ? "accent-madder" : ""
      }`}
    >
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="big">{title}</div>
      {hint && <div>{hint}</div>}
    </div>
  );
}

export function Spinner() {
  return <div className="spinner">LOADING…</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="err">{message}</div>;
}

export function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label>
        {label} {required && <span className="req">*</span>}
      </label>
      {children}
      {hint && <div className="hintline mt-0">{hint}</div>}
    </div>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (k: string) => void;
}) {
  return (
    <div className="flex" style={{ gap: 4, marginBottom: 18, borderBottom: "1px solid var(--line)" }}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className="btn ghost"
          style={{
            borderRadius: 0,
            borderBottom: active === t.key ? "2px solid var(--indigo)" : "2px solid transparent",
            color: active === t.key ? "var(--indigo-deep)" : "var(--ink-soft)",
            marginBottom: -1,
            fontFamily: "var(--mono)",
            fontSize: 11,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Drawer({
  title,
  sub,
  onClose,
  children,
  footer,
}: {
  title: string;
  sub?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {sub && <div className="sub">{sub}</div>}
          </div>
          <button className="x-btn" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-foot">{footer}</div>}
      </div>
    </div>
  );
}
