import { ReactNode, useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { AuthUser } from "../api/client";
import { prefetchRoute } from "../lib/prefetch";
import { AccountMenu } from "./AccountMenu";

interface NavItem {
  to: string;
  ic: string;
  name: string;
  end?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    label: "Overview",
    items: [{ to: "/", ic: "DB", name: "Dashboard", end: true }],
  },
  {
    label: "Commercial",
    items: [
      { to: "/sales", ic: "SO", name: "Sales Orders" },
      { to: "/styles", ic: "ST", name: "Styles & BOM" },
      { to: "/costing", ic: "CO", name: "Costing" },
    ],
  },
  {
    label: "Supply & Make",
    items: [
      { to: "/planning", ic: "PL", name: "Planning" },
      { to: "/procurement", ic: "PO", name: "Procurement" },
      { to: "/inventory", ic: "IN", name: "Inventory" },
      { to: "/production", ic: "PR", name: "Production" },
      { to: "/markers", ic: "MK", name: "Marker & Cut" },
      { to: "/shop-floor", ic: "SF", name: "Shop Floor" },
      { to: "/warehouse", ic: "WH", name: "Warehouse" },
      { to: "/quality", ic: "QC", name: "Quality" },
    ],
  },
  {
    label: "Back office",
    items: [
      { to: "/masters", ic: "MD", name: "Master Data" },
      { to: "/finance", ic: "FN", name: "Finance" },
    ],
  },
];

const PAGE_PERMISSION: Record<string, string> = {
  "/sales": "sales:read",
  "/styles": "styles:read",
  "/costing": "costing:read",
  "/planning": "planning:read",
  "/procurement": "procurement:read",
  "/inventory": "inventory:read",
  "/production": "production:read",
  "/markers": "production:read",
  "/shop-floor": "production:read",
  "/warehouse": "inventory:read",
  "/quality": "quality:read",
  "/masters": "masters:read",
  "/finance": "finance:read",
};

function crumbFor(path: string): string {
  if (path === "/") return "Dashboard";
  const segment = path.split("/")[1];
  const labels: Record<string, string> = {
    sales: "Sales Orders",
    styles: "Styles & BOM",
    costing: "Costing",
    planning: "Planning",
    procurement: "Procurement",
    inventory: "Inventory",
    production: "Production",
    markers: "Marker & Cut",
    "shop-floor": "Shop Floor",
    warehouse: "Warehouse",
    quality: "Quality",
    masters: "Master Data",
    finance: "Finance",
    users: "Access Control",
    settings: "Settings",
  };
  return labels[segment] ?? segment;
}

export function Shell({ children, user, onLogout }: { children: ReactNode; user: AuthUser; onLogout: () => void }) {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  return (
    <div className={`shell ${menuOpen ? "nav-open" : ""}`}>
      <button
        className="sidebar-scrim"
        type="button"
        aria-label="Close navigation"
        onClick={() => setMenuOpen(false)}
      />

      <aside className="sidebar" aria-label="Primary navigation">
        <div className="brand">
          <Link className="mark" to="/" aria-label="Atelier Garment ERP — go to dashboard">
            <div className="brand-symbol" aria-hidden="true"><span /></div>
            <div>
              <div className="name">Atelier</div>
              <div className="sub">Garment ERP</div>
            </div>
          </Link>
          <button className="sidebar-close" type="button" aria-label="Close navigation" onClick={() => setMenuOpen(false)}>
            Close
          </button>
        </div>

        <div className="workspace-label">
          <span className="workspace-dot" />
          Manufacturing workspace
        </div>

        <nav className="nav">
          {NAV.map((group) => {
            const visibleItems = group.items.filter(
              (item) => !PAGE_PERMISSION[item.to] || user.permissions.includes(PAGE_PERMISSION[item.to])
            );
            if (visibleItems.length === 0) return null;
            return (
              <div className="nav-group" key={group.label}>
                <div className="nav-label">{group.label}</div>
                {visibleItems.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    // Start the chunk download while the pointer is still
                    // travelling, so the click only waits on data.
                    onMouseEnter={() => prefetchRoute(item.to)}
                    onFocus={() => prefetchRoute(item.to)}
                    onTouchStart={() => prefetchRoute(item.to)}
                    className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                  >
                    <span className="ic" aria-hidden="true">{item.ic}</span>
                    <span>{item.name}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
          {user.permissions.includes("users:manage") && (
            <div className="nav-group">
              <div className="nav-label">Administration</div>
              <NavLink to="/users" onMouseEnter={() => prefetchRoute("/users")} onFocus={() => prefetchRoute("/users")} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
                <span className="ic" aria-hidden="true">AC</span>
                <span>Access Control</span>
              </NavLink>
            </div>
          )}
        </nav>

        <div className="sidebar-user">
          <AccountMenu user={user} onLogout={onLogout} placement="up" />
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="menu-button"
              type="button"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <span /><span /><span />
            </button>
            <div className="crumbs">
              <span>Operations</span>
              <span className="crumb-separator" aria-hidden="true">/</span>
              <b>{crumbFor(location.pathname)}</b>
            </div>
          </div>
          <div className="topbar-right">
            <div className="system-state"><span /> Workspace online</div>
            <div className="user-chip">
              <AccountMenu user={user} onLogout={onLogout} placement="down" compact />
            </div>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
