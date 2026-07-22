import { ReactNode, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { getRole, setRole } from "../api/client";

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

const ROLES = [
  "admin",
  "merchandiser",
  "procurement",
  "stores",
  "cutting_supervisor",
  "sewing_supervisor",
  "quality_inspector",
  "planner",
  "finance",
  "readonly",
];

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
  };
  return map[seg] ?? seg;
}

export function Shell({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const [role, setRoleState] = useState(getRole());

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
              {group.items.map((item) => (
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
        </nav>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="crumbs">
            Atelier / <b>{crumbFor(loc.pathname)}</b>
          </div>
          <div className="topbar-right">
            <div className="role-switch">
              <label>Acting role</label>
              <select
                className="select"
                style={{ width: "auto", padding: "5px 10px" }}
                value={role}
                onChange={(e) => {
                  setRole(e.target.value);
                  setRoleState(e.target.value);
                }}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
