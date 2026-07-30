import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AuthUser } from "../api/client";
import { useTheme } from "../theme/ThemeProvider";
import { ThemeSwitch } from "./ThemeSwitch";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

/**
 * The account control: identity, a quick appearance switch, a way through to
 * full settings, and sign out.
 *
 * Rendered twice — in the sidebar footer (the primary placement, opening
 * upward) and in the topbar (opening downward). The topbar copy is what keeps
 * sign-out one tap away on narrow screens, where the sidebar is behind the
 * hamburger.
 *
 * Closes on Escape, on outside pointerdown, and on navigation.
 */
export function AccountMenu({
  user,
  onLogout,
  placement = "up",
  compact = false,
}: {
  user: AuthUser;
  onLogout: () => void;
  placement?: "up" | "down";
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const { resolved } = useTheme();
  const panelId = useId();

  useEffect(() => setOpen(false), [location.pathname]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  // Move focus into the panel on open so keyboard users are not stranded
  // behind the trigger.
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
  }, [open]);

  function go(path: string) {
    setOpen(false);
    navigate(path);
  }

  return (
    <div
      className={`account place-${placement} ${compact ? "compact" : ""} ${open ? "open" : ""}`}
      ref={containerRef}
    >
      {open && (
        <div className="account-menu" role="dialog" aria-label="Account" id={panelId} ref={panelRef}>
          <div className="account-menu-head">
            <div className="avatar" aria-hidden="true">{initials(user.display_name)}</div>
            <div className="account-menu-id">
              <strong>{user.display_name}</strong>
              <span>{user.email}</span>
            </div>
          </div>

          {user.roles.length > 0 && (
            <div className="account-roles" aria-label="Assigned roles">
              {user.roles.map((role) => (
                <span className="chip" key={role}>{role.replace(/_/g, " ")}</span>
              ))}
            </div>
          )}

          <div className="account-section">
            <div className="account-section-label" id={`${panelId}-appearance`}>Appearance</div>
            <ThemeSwitch describedBy={`${panelId}-appearance`} />
            <p className="account-hint">
              Currently {resolved === "light" ? "light" : "dark"}.
            </p>
          </div>

          <div className="account-actions">
            <button className="account-item" type="button" data-autofocus onClick={() => go("/settings")}>
              <span className="ic" aria-hidden="true">ST</span>
              <span>
                <b>Settings</b>
                <small>Personalisation and account</small>
              </span>
            </button>
            <button className="account-item danger" type="button" onClick={onLogout}>
              <span className="ic" aria-hidden="true">SO</span>
              <span>
                <b>Sign out</b>
                <small>End this session</small>
              </span>
            </button>
          </div>
        </div>
      )}

      <button
        className="account-trigger"
        type="button"
        ref={triggerRef}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={`avatar ${compact ? "small" : ""}`} aria-hidden="true">{initials(user.display_name)}</span>
        <span className="account-trigger-copy">
          <strong>{user.display_name}</strong>
          <span>{compact ? user.roles.join(" / ") : user.email}</span>
        </span>
        <span className="account-caret" aria-hidden="true">
          {placement === "up" ? (open ? "▾" : "▴") : open ? "▴" : "▾"}
        </span>
      </button>
    </div>
  );
}
