import { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { AuthUser } from "../api/client";

const NAV = [
  {
    label: "Overview",
    items: [{ to: "/", ic: "▦", name: "Dashboard", end: true }],
  },
  {
    label: "Commercial",
    items: [
      { to: "/sales", ic: "SO", name: "Sales Orders" },
      { to: "/styles", ic: "ST", name: "Styles & BOM" },
      { to: "/costing", ic: "$", name: "Costing" },
    ],
  },
  {
    label: "Supply & Make",
    items: [
      { to: "/procurement", ic: "PO", name: "Procurement" },
      { to: "/inventory", ic: "▤", name: "Inventory" },
      { to: "/production", ic: "✂", name: "Production" },
      { to: "/quality", ic: "QC", name: "Quality" },
    ],
  },
  {
    label: "Back office",
    items: [
      { to: "/masters", ic: "◆", name: "Master Data" },
      { to: "/finance", ic: "₤", name: "Finance" },
    ],
  },
];

const PAGE_PERMISSION: Record<string, string> = {
  "/sales": "sales:read",
  "/styles": "styles:read",
  "/costing": "costing:read",
  "/procurement": "procurement:read",
  "/inventory": "inventory:read",
  "/production": "production:read",
  "/quality": "quality:read",
  "/masters": "masters:read",
  "/finance": "finance:read",
};

function crumbFor(path: string): string {
  if (path === "/") return "Dashboard";
  const seg = path.split("/")[1];
  const map: Record<string, string> = {
    sales: "Sales Orders",
    styles: "Styles & BOM",
    costing: "Costing",
    procurement: "Procurement",
    inventory: "Inventory",
    production: "Production",
    quality: "Quality",
    masters: "Master Data",
    finance: "Finance",
    users: "Access Control",
  };
  return map[seg] ?? seg;
}

export function Shell({ children, user, onLogout }: { children: ReactNode; user: AuthUser; onLogout: () => void }) {
  const loc = useLocation();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="mark">
            <div className="glyph" />
            <div>
              <div className="name">Atelier</div>
              <div className="sub">Garment ERP</div>
            </div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items
                .filter((item) => !PAGE_PERMISSION[item.to] || user.permissions.includes(PAGE_PERMISSION[item.to]))
                .map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={(item as any).end}
                  className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                >
                  <span className="ic">{item.ic}</span>
                  {item.name}
                </NavLink>
              ))}
            </div>
          ))}
          {user.permissions.includes("users:manage") && (
            <div className="nav-group">
              <div className="nav-label">Administration</div>
              <NavLink to="/users" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
                <span className="ic">AC</span>Access Control
              </NavLink>
            </div>
          )}
        </nav>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="crumbs">
            Atelier / <b>{crumbFor(loc.pathname)}</b>
          </div>
          <div className="topbar-right">
            <div className="user-chip">
              <div><b>{user.display_name}</b><small>{user.roles.join(" · ")}</small></div>
              <button className="btn ghost" onClick={onLogout}>Sign out</button>
            </div>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
