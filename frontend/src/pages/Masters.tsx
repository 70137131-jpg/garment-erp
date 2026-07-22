import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Colour, Customer, Material, SizeRange, Supplier } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, qty, titled } from "../lib/format";

const TABS = [
  { key: "materials", label: "Materials" },
  { key: "customers", label: "Customers" },
  { key: "suppliers", label: "Suppliers" },
  { key: "colours", label: "Colours" },
  { key: "sizes", label: "Size Ranges" },
];

const MATERIAL_TYPES = ["fabric", "trims", "thread", "labels", "hangtags", "packaging", "chemicals", "consumables", "service"];

export default function Masters() {
  const [tab, setTab] = useState("materials");
  return (
    <div>
      <PageHeader
        eyebrow="Module 1"
        title="Master Data"
        subtitle="The foundation everything builds on — what things are before any transaction happens."
      />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "materials" && <Materials />}
      {tab === "customers" && <Customers />}
      {tab === "suppliers" && <Suppliers />}
      {tab === "colours" && <Colours />}
      {tab === "sizes" && <Sizes />}
    </div>
  );
}

function useCreate<T>(path: string, onDone: () => void) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(body: unknown, label: string) {
    setSaving(true);
    setError(null);
    try {
      await api.post<T>(path, body);
      toast.push(`${label} created`);
      onDone();
      return true;
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Failed";
      setError(msg);
      toast.push("Could not save", { detail: msg, bad: true });
      return false;
    } finally {
      setSaving(false);
    }
  }
  return { submit, saving, error, setError };
}

/* ---------------- Materials ---------------- */
function Materials() {
  const { data, loading, reload } = useAsync(() => api.get<Material[]>("/masters/materials"));
  const [open, setOpen] = useState(false);
  return (
    <Card
      title="Material master"
      hint="dual UoM — buy by roll, stock by metre"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New material</button>}
    >
      {loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Code</th><th>Name</th><th>Type</th><th>Base UoM</th><th className="num">Conv.</th><th>Lot</th><th>Width</th></tr></thead>
            <tbody>
              {data?.map((m) => (
                <tr key={m.id}>
                  <td className="code">{m.code}</td>
                  <td>{m.name}</td>
                  <td><Chip tone="neutral" label={titled(m.material_type)} /></td>
                  <td className="mono">{m.base_uom}</td>
                  <td className="num">{qty(m.purchase_to_base_factor)}</td>
                  <td>{m.lot_tracked ? <Chip tone="info" label="Lot" /> : <span className="muted">—</span>}</td>
                  <td className="num">{m.width_cm ? `${qty(m.width_cm)} cm` : "—"}</td>
                </tr>
              ))}
              {!data?.length && <tr><td colSpan={7} className="muted">No materials yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && <MaterialForm onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
    </Card>
  );
}

function MaterialForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { submit, saving, error } = useCreate("/masters/materials", onDone);
  const [f, setF] = useState({ name: "", material_type: "fabric", base_uom: "metre", purchase_uom: "roll", purchase_to_base_factor: "50", lot_tracked: true, width_cm: "", gsm: "", composition: "" });
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  return (
    <Drawer title="New material" sub="Module 1.3" onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !f.name} onClick={() => submit({
          name: f.name, material_type: f.material_type, base_uom: f.base_uom, purchase_uom: f.purchase_uom,
          purchase_to_base_factor: f.purchase_to_base_factor, lot_tracked: f.lot_tracked,
          width_cm: f.width_cm || null, gsm: f.gsm || null, composition: f.composition || null,
        }, "Material")}>Save material</button>
      </>}>
      {error && <ErrorBox message={error} />}
      <Field label="Name" required><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Single Jersey 180gsm" /></Field>
      <Field label="Material type" required>
        <select className="select" value={f.material_type} onChange={(e) => set("material_type", e.target.value)}>
          {MATERIAL_TYPES.map((t) => <option key={t} value={t}>{titled(t)}</option>)}
        </select>
      </Field>
      <div className="form-row three">
        <Field label="Base UoM"><input className="input" value={f.base_uom} onChange={(e) => set("base_uom", e.target.value)} /></Field>
        <Field label="Purchase UoM"><input className="input" value={f.purchase_uom} onChange={(e) => set("purchase_uom", e.target.value)} /></Field>
        <Field label="Conversion" hint="base units per purchase unit"><input className="input mono" value={f.purchase_to_base_factor} onChange={(e) => set("purchase_to_base_factor", e.target.value)} /></Field>
      </div>
      <div className="form-row three">
        <Field label="Width (cm)"><input className="input mono" value={f.width_cm} onChange={(e) => set("width_cm", e.target.value)} /></Field>
        <Field label="GSM"><input className="input mono" value={f.gsm} onChange={(e) => set("gsm", e.target.value)} /></Field>
        <Field label="Lot tracked">
          <select className="select" value={String(f.lot_tracked)} onChange={(e) => set("lot_tracked", e.target.value === "true")}>
            <option value="true">Yes</option><option value="false">No</option>
          </select>
        </Field>
      </div>
      <Field label="Composition"><input className="input" value={f.composition} onChange={(e) => set("composition", e.target.value)} placeholder="100% Cotton" /></Field>
    </Drawer>
  );
}

/* ---------------- Customers ---------------- */
function Customers() {
  const { data, loading, reload } = useAsync(() => api.get<Customer[]>("/masters/customers"));
  const [open, setOpen] = useState(false);
  const { submit, saving, error } = useCreate("/masters/customers", () => { setOpen(false); reload(); });
  const [f, setF] = useState({ name: "", currency: "USD", credit_limit: "0" });
  return (
    <Card title="Customers" hint="deactivate, never delete"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New customer</button>}>
      {loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Code</th><th>Name</th><th>Currency</th><th className="num">Credit limit</th></tr></thead>
            <tbody>
              {data?.map((c) => (
                <tr key={c.id}><td className="code">{c.code}</td><td>{c.name}</td><td className="mono">{c.currency}</td><td className="num">{money(c.credit_limit, c.currency)}</td></tr>
              ))}
              {!data?.length && <tr><td colSpan={4} className="muted">No customers yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <Drawer title="New customer" sub="Module 1.1" onClose={() => setOpen(false)}
          footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={saving || !f.name} onClick={() => submit(f, "Customer")}>Save</button></>}>
          {error && <ErrorBox message={error} />}
          <Field label="Name" required><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <div className="form-row two">
            <Field label="Currency"><input className="input mono" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} /></Field>
            <Field label="Credit limit"><input className="input mono" value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} /></Field>
          </div>
        </Drawer>
      )}
    </Card>
  );
}

/* ---------------- Suppliers ---------------- */
function Suppliers() {
  const { data, loading, reload } = useAsync(() => api.get<Supplier[]>("/masters/suppliers"));
  const [open, setOpen] = useState(false);
  const { submit, saving, error } = useCreate("/masters/suppliers", () => { setOpen(false); reload(); });
  const [f, setF] = useState({ name: "", currency: "USD", lead_time_days: 0, material_types: ["fabric"] as string[] });
  const toggle = (t: string) => setF({ ...f, material_types: f.material_types.includes(t) ? f.material_types.filter((x) => x !== t) : [...f.material_types, t] });
  return (
    <Card title="Suppliers" hint="approved per material type"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New supplier</button>}>
      {loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Code</th><th>Name</th><th>Currency</th><th className="num">Lead time</th><th>Supplies</th></tr></thead>
            <tbody>
              {data?.map((s) => (
                <tr key={s.id}><td className="code">{s.code}</td><td>{s.name}</td><td className="mono">{s.currency}</td>
                  <td className="num">{s.lead_time_days} d</td>
                  <td><div className="tag-list">{s.material_types.map((t) => <Chip key={t} tone="neutral" label={titled(t)} />)}</div></td></tr>
              ))}
              {!data?.length && <tr><td colSpan={5} className="muted">No suppliers yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <Drawer title="New supplier" sub="Module 1.2" onClose={() => setOpen(false)}
          footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={saving || !f.name} onClick={() => submit(f, "Supplier")}>Save</button></>}>
          {error && <ErrorBox message={error} />}
          <Field label="Name" required><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <div className="form-row two">
            <Field label="Currency"><input className="input mono" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} /></Field>
            <Field label="Lead time (days)"><input className="input mono" type="number" value={f.lead_time_days} onChange={(e) => setF({ ...f, lead_time_days: Number(e.target.value) })} /></Field>
          </div>
          <Field label="Approved material types">
            <div className="tag-list">
              {MATERIAL_TYPES.map((t) => (
                <button key={t} type="button" onClick={() => toggle(t)}
                  className={`chip ${f.material_types.includes(t) ? "info" : "neutral"}`} style={{ cursor: "pointer", border: "1px solid var(--line-strong)" }}>
                  {titled(t)}
                </button>
              ))}
            </div>
          </Field>
        </Drawer>
      )}
    </Card>
  );
}

/* ---------------- Colours ---------------- */
function Colours() {
  const { data, loading, reload } = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const [open, setOpen] = useState(false);
  const { submit, saving, error } = useCreate("/masters/colours", () => { setOpen(false); reload(); });
  const [f, setF] = useState({ code: "", name: "", pantone: "", hex: "#2b3a7c" });
  return (
    <Card title="Colour library" hint="Module 1.4"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New colour</button>}>
      {loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Code</th><th>Name</th><th>Pantone</th><th>Swatch</th></tr></thead>
            <tbody>
              {data?.map((c) => (
                <tr key={c.id}><td className="code">{c.code}</td><td>{c.name}</td><td className="mono muted">{c.pantone || "—"}</td>
                  <td><span className="swatch" style={{ background: c.hex || "#ccc" }} /> <span className="mono muted">{c.hex}</span></td></tr>
              ))}
              {!data?.length && <tr><td colSpan={4} className="muted">No colours yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <Drawer title="New colour" sub="Module 1.4" onClose={() => setOpen(false)}
          footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={saving || !f.code || !f.name} onClick={() => submit({ code: f.code, name: f.name, pantone: f.pantone || null, hex: f.hex }, "Colour")}>Save</button></>}>
          {error && <ErrorBox message={error} />}
          <div className="form-row two">
            <Field label="Code" required><input className="input mono" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} placeholder="NAVY" /></Field>
            <Field label="Name" required><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Navy" /></Field>
          </div>
          <div className="form-row two">
            <Field label="Pantone"><input className="input mono" value={f.pantone} onChange={(e) => setF({ ...f, pantone: e.target.value })} placeholder="19-3920 TCX" /></Field>
            <Field label="Hex"><input className="input mono" type="color" value={f.hex} onChange={(e) => setF({ ...f, hex: e.target.value })} style={{ height: 38 }} /></Field>
          </div>
        </Drawer>
      )}
    </Card>
  );
}

/* ---------------- Size ranges ---------------- */
function Sizes() {
  const { data, loading, reload } = useAsync(() => api.get<SizeRange[]>("/masters/size-ranges"));
  const [open, setOpen] = useState(false);
  const { submit, saving, error } = useCreate("/masters/size-ranges", () => { setOpen(false); reload(); });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [labels, setLabels] = useState("S, M, L, XL");
  return (
    <Card title="Size ranges" hint="ordered — order is load-bearing downstream"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New size range</button>}>
      {loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Code</th><th>Name</th><th>Sizes</th></tr></thead>
            <tbody>
              {data?.map((s) => (
                <tr key={s.id}><td className="code">{s.code}</td><td>{s.name}</td>
                  <td><div className="tag-list">{s.sizes.map((z) => <Chip key={z.position} tone="neutral" label={z.label} />)}</div></td></tr>
              ))}
              {!data?.length && <tr><td colSpan={3} className="muted">No size ranges yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && (
        <Drawer title="New size range" sub="Module 1.6" onClose={() => setOpen(false)}
          footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={saving || !code || !name} onClick={() => {
              const sizes = labels.split(",").map((l, i) => ({ position: i + 1, label: l.trim() })).filter((s) => s.label);
              submit({ code, name, sizes }, "Size range");
            }}>Save</button></>}>
          {error && <ErrorBox message={error} />}
          <div className="form-row two">
            <Field label="Code" required><input className="input mono" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="MENS-STD" /></Field>
            <Field label="Name" required><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Mens Standard" /></Field>
          </div>
          <Field label="Sizes (ordered)" required hint="comma-separated, smallest first"><input className="input mono" value={labels} onChange={(e) => setLabels(e.target.value)} /></Field>
        </Drawer>
      )}
    </Card>
  );
}
