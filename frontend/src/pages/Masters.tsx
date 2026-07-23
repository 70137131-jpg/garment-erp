import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Colour, Customer, Material, Season, SizeRange, Supplier } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, qty, titled } from "../lib/format";

type Kind = "materials" | "customers" | "suppliers" | "colours" | "seasons" | "size-ranges";
type MasterRecord = Material | Customer | Supplier | Colour | Season | SizeRange;

const MATERIAL_TYPES = ["fabric", "trims", "thread", "labels", "hangtags", "packaging", "chemicals", "consumables", "service"];
const CONFIG: Record<Kind, { title: string; singular: string; hint: string; roles: string[] }> = {
  materials: { title: "Material master", singular: "material", hint: "dual UoM — buy by roll, stock by metre", roles: ["procurement", "merchandiser"] },
  customers: { title: "Customers", singular: "customer", hint: "deactivate, never delete", roles: ["merchandiser"] },
  suppliers: { title: "Suppliers", singular: "supplier", hint: "approved per material type", roles: ["procurement", "merchandiser"] },
  colours: { title: "Colour library", singular: "colour", hint: "Pantone and digital swatch", roles: ["merchandiser"] },
  seasons: { title: "Seasons", singular: "season", hint: "commercial calendar windows", roles: ["merchandiser"] },
  "size-ranges": { title: "Size ranges", singular: "size range", hint: "labels are immutable after creation", roles: ["merchandiser"] },
};

export default function Masters() {
  const [tab, setTab] = useState<Kind>("materials");
  const tabs = (Object.keys(CONFIG) as Kind[]).map((key) => ({ key, label: CONFIG[key].title }));
  return <div>
    <PageHeader eyebrow="Module 1" title="Master Data" subtitle="Maintain the shared commercial and manufacturing definitions used by every transaction." />
    <Tabs tabs={tabs} active={tab} onChange={(key) => setTab(key as Kind)} />
    <MasterSection kind={tab} />
  </div>;
}

function MasterSection({ kind }: { kind: Kind }) {
  const config = CONFIG[kind];
  const { can } = useAuthorization();
  const editable = can(...config.roles);
  const records = useAsync(() => api.get<MasterRecord[]>(`/masters/${kind}`), [kind]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MasterRecord | null>(null);
  return <Card title={config.title} hint={config.hint} actions={editable ? <button className="btn primary sm" onClick={() => setCreating(true)}>+ New {config.singular}</button> : undefined}>
    {records.loading ? <Spinner /> : <MasterTable kind={kind} records={records.data ?? []} editable={editable} onEdit={setEditing} />}
    {creating && <MasterDrawer kind={kind} onClose={() => setCreating(false)} onDone={() => { setCreating(false); records.reload(); }} />}
    {editing && <MasterDrawer kind={kind} item={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); records.reload(); }} />}
  </Card>;
}

function Active({ value }: { value: boolean }) {
  return <Chip tone={value ? "ok" : "neutral"} label={value ? "Active" : "Inactive"} />;
}

function MasterTable({ kind, records, editable, onEdit }: { kind: Kind; records: MasterRecord[]; editable: boolean; onEdit: (item: MasterRecord) => void }) {
  if (!records.length) return <div className="empty"><div className="big">No records yet</div></div>;
  return <div className="table-wrap"><table className="tbl">
    {kind === "materials" && <><thead><tr><th>Code</th><th>Name</th><th>Type</th><th>UoM</th><th className="num">Conversion</th><th>Status</th><th></th></tr></thead><tbody>{(records as Material[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td>{titled(item.material_type)}</td><td>{item.purchase_uom} → {item.base_uom}</td><td className="num">{qty(item.purchase_to_base_factor)}</td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
    {kind === "customers" && <><thead><tr><th>Code</th><th>Name</th><th>Currency</th><th className="num">Credit limit</th><th>Status</th><th></th></tr></thead><tbody>{(records as Customer[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td className="mono">{item.currency}</td><td className="num">{money(item.credit_limit, item.currency)}</td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
    {kind === "suppliers" && <><thead><tr><th>Code</th><th>Name</th><th>Currency</th><th className="num">Lead time</th><th>Supplies</th><th>Status</th><th></th></tr></thead><tbody>{(records as Supplier[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td className="mono">{item.currency}</td><td className="num">{item.lead_time_days} d</td><td><div className="tag-list">{item.material_types.map((type) => <Chip key={type} tone="neutral" label={titled(type)} />)}</div></td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
    {kind === "colours" && <><thead><tr><th>Code</th><th>Name</th><th>Pantone</th><th>Swatch</th><th>Status</th><th></th></tr></thead><tbody>{(records as Colour[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td>{item.pantone || "—"}</td><td><span className="swatch" style={{ background: item.hex || "#ccc" }} /> {item.hex}</td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
    {kind === "seasons" && <><thead><tr><th>Code</th><th>Name</th><th>Start</th><th>End</th><th>Status</th><th></th></tr></thead><tbody>{(records as Season[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td>{item.start_date || "—"}</td><td>{item.end_date || "—"}</td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
    {kind === "size-ranges" && <><thead><tr><th>Code</th><th>Name</th><th>Sizes</th><th>Status</th><th></th></tr></thead><tbody>{(records as SizeRange[]).map((item) => <tr key={item.id}><td className="code">{item.code}</td><td>{item.name}</td><td><div className="tag-list">{item.sizes.map((size) => <Chip key={size.position} tone="neutral" label={size.label} />)}</div></td><td><Active value={item.active} /></td><Action editable={editable} onClick={() => onEdit(item)} /></tr>)}</tbody></>}
  </table></div>;
}

function Action({ editable, onClick }: { editable: boolean; onClick: () => void }) {
  return <td>{editable && <button className="btn sm" onClick={onClick}>Edit</button>}</td>;
}

function defaults(kind: Kind): Record<string, any> {
  if (kind === "materials") return { name: "", material_type: "fabric", base_uom: "metre", purchase_uom: "roll", purchase_to_base_factor: "50", lot_tracked: true, lead_time_days: 0, min_order_qty: "0", valuation_method: "weighted_average", width_cm: "", gsm: "", composition: "", construction: "", weave: "", active: true };
  if (kind === "customers") return { name: "", currency: "USD", payment_terms: "", credit_limit: "0", billing_address: "", active: true };
  if (kind === "suppliers") return { name: "", currency: "USD", payment_terms: "", lead_time_days: 0, restricted_substance_certified: false, material_types: ["fabric"], active: true };
  if (kind === "colours") return { code: "", name: "", pantone: "", hex: "#2b3a7c", active: true };
  if (kind === "seasons") return { code: "", name: "", start_date: "", end_date: "", active: true };
  return { code: "", name: "", labels: "S, M, L, XL", active: true };
}

function MasterDrawer({ kind, item, onClose, onDone }: { kind: Kind; item?: MasterRecord; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const config = CONFIG[kind];
  const [form, setForm] = useState<Record<string, any>>(() => {
    const initial: Record<string, any> = { ...defaults(kind), ...(item ?? {}) };
    if (kind === "size-ranges" && item) initial.labels = (item as SizeRange).sizes.map((size) => size.label).join(", ");
    return initial;
  });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const needsCode = kind === "colours" || kind === "seasons" || kind === "size-ranges";
  const set = (key: string, value: any) => setForm((current) => ({ ...current, [key]: value }));

  function payload() {
    if (kind === "materials") return item ? pick(form, ["name", "lot_tracked", "lead_time_days", "min_order_qty", "valuation_method", "composition", "construction", "weave", "gsm", "width_cm", "active"]) : pick(form, ["name", "material_type", "base_uom", "purchase_uom", "purchase_to_base_factor", "lot_tracked", "lead_time_days", "min_order_qty", "valuation_method", "composition", "construction", "weave", "gsm", "width_cm", "active"]);
    if (kind === "customers") return pick(form, ["name", "currency", "payment_terms", "credit_limit", "billing_address", "active"]);
    if (kind === "suppliers") return pick(form, ["name", "currency", "payment_terms", "lead_time_days", "restricted_substance_certified", "material_types", "active"]);
    if (kind === "colours") return pick(form, ["code", "name", "pantone", "hex", "active"]);
    if (kind === "seasons") return pick(form, ["code", "name", "start_date", "end_date", "active"]);
    if (item) return pick(form, ["name", "active"]);
    return { code: form.code, name: form.name, active: form.active, sizes: String(form.labels).split(",").map((label, index) => ({ position: index + 1, label: label.trim() })).filter((size) => size.label) };
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      if (item) await api.patch(`/masters/${kind}/${item.id}`, payload());
      else await api.post(`/masters/${kind}`, payload());
      toast.push(`${titled(config.singular)} ${item ? "updated" : "created"}`); onDone();
    } catch (e) { const message = (e as ApiError).message; setError(message); toast.push("Could not save", { detail: message, bad: true }); }
    finally { setSaving(false); }
  }

  return <Drawer title={`${item ? "Edit" : "New"} ${config.singular}`} sub={item ? `Code ${(item as any).code}` : "Master data"} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={saving || !form.name || (!item && needsCode && !form.code)} onClick={save}>{saving ? "Saving…" : "Save"}</button></>}>
    {error && <ErrorBox message={error} />}
    {kind === "materials" && <MaterialFields form={form} set={set} editing={!!item} />}
    {kind === "customers" && <CustomerFields form={form} set={set} />}
    {kind === "suppliers" && <SupplierFields form={form} set={set} />}
    {kind === "colours" && <ColourFields form={form} set={set} />}
    {kind === "seasons" && <SeasonFields form={form} set={set} />}
    {kind === "size-ranges" && <SizeFields form={form} set={set} editing={!!item} />}
    {item && <ActiveField value={form.active} onChange={(value) => set("active", value)} />}
  </Drawer>;
}

function pick(source: Record<string, any>, keys: string[]) {
  return Object.fromEntries(keys.map((key) => [key, source[key] === "" ? null : source[key]]));
}
type Setter = (key: string, value: any) => void;
function ActiveField({ value, onChange }: { value: boolean; onChange: (value: boolean) => void }) { return <Field label="Status"><select className="select" value={String(value)} onChange={(e) => onChange(e.target.value === "true")}><option value="true">Active</option><option value="false">Inactive</option></select></Field>; }
function MaterialFields({ form, set, editing }: { form: Record<string, any>; set: Setter; editing: boolean }) { return <><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field>{!editing && <><Field label="Material type"><select className="select" value={form.material_type} onChange={(e) => set("material_type", e.target.value)}>{MATERIAL_TYPES.map((type) => <option key={type}>{type}</option>)}</select></Field><div className="form-row three"><Field label="Base UoM"><input className="input" value={form.base_uom} onChange={(e) => set("base_uom", e.target.value)} /></Field><Field label="Purchase UoM"><input className="input" value={form.purchase_uom} onChange={(e) => set("purchase_uom", e.target.value)} /></Field><Field label="Conversion"><input className="input mono" value={form.purchase_to_base_factor} onChange={(e) => set("purchase_to_base_factor", e.target.value)} /></Field></div></>}<div className="form-row three"><Field label="Lead time"><input className="input mono" type="number" value={form.lead_time_days} onChange={(e) => set("lead_time_days", Number(e.target.value))} /></Field><Field label="Minimum order"><input className="input mono" value={form.min_order_qty} onChange={(e) => set("min_order_qty", e.target.value)} /></Field><Field label="Valuation"><select className="select" value={form.valuation_method ?? "weighted_average"} onChange={(e) => set("valuation_method", e.target.value)}><option value="weighted_average">Weighted average</option><option value="fifo">FIFO</option></select></Field></div><div className="form-row three"><Field label="Lot tracked"><select className="select" value={String(form.lot_tracked)} onChange={(e) => set("lot_tracked", e.target.value === "true")}><option value="true">Yes</option><option value="false">No</option></select></Field><Field label="Width cm"><input className="input mono" value={form.width_cm ?? ""} onChange={(e) => set("width_cm", e.target.value)} /></Field><Field label="GSM"><input className="input mono" value={form.gsm ?? ""} onChange={(e) => set("gsm", e.target.value)} /></Field></div><Field label="Composition"><input className="input" value={form.composition ?? ""} onChange={(e) => set("composition", e.target.value)} /></Field></>; }
function CustomerFields({ form, set }: { form: Record<string, any>; set: Setter }) { return <><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field><div className="form-row two"><Field label="Currency"><input className="input mono" value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} /></Field><Field label="Credit limit"><input className="input mono" value={form.credit_limit} onChange={(e) => set("credit_limit", e.target.value)} /></Field></div><Field label="Payment terms"><input className="input" value={form.payment_terms ?? ""} onChange={(e) => set("payment_terms", e.target.value)} /></Field><Field label="Billing address"><input className="input" value={form.billing_address ?? ""} onChange={(e) => set("billing_address", e.target.value)} /></Field></>; }
function SupplierFields({ form, set }: { form: Record<string, any>; set: Setter }) { const types: string[] = form.material_types ?? []; return <><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field><div className="form-row two"><Field label="Currency"><input className="input mono" value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} /></Field><Field label="Lead time days"><input className="input mono" type="number" value={form.lead_time_days} onChange={(e) => set("lead_time_days", Number(e.target.value))} /></Field></div><Field label="Payment terms"><input className="input" value={form.payment_terms ?? ""} onChange={(e) => set("payment_terms", e.target.value)} /></Field><Field label="Approved material types"><div className="role-grid">{MATERIAL_TYPES.map((type) => <label key={type}><input type="checkbox" checked={types.includes(type)} onChange={(e) => set("material_types", e.target.checked ? [...types, type] : types.filter((item) => item !== type))} /> {type}</label>)}</div></Field></>; }
function ColourFields({ form, set }: { form: Record<string, any>; set: Setter }) { return <><div className="form-row two"><Field label="Code" required><input className="input mono" value={form.code} onChange={(e) => set("code", e.target.value.toUpperCase())} /></Field><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field></div><div className="form-row two"><Field label="Pantone"><input className="input" value={form.pantone ?? ""} onChange={(e) => set("pantone", e.target.value)} /></Field><Field label="Hex"><input className="input" type="color" value={form.hex || "#000000"} onChange={(e) => set("hex", e.target.value)} /></Field></div></>; }
function SeasonFields({ form, set }: { form: Record<string, any>; set: Setter }) { return <><div className="form-row two"><Field label="Code" required><input className="input mono" value={form.code} onChange={(e) => set("code", e.target.value.toUpperCase())} /></Field><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field></div><div className="form-row two"><Field label="Start date"><input className="input" type="date" value={form.start_date ?? ""} onChange={(e) => set("start_date", e.target.value)} /></Field><Field label="End date"><input className="input" type="date" value={form.end_date ?? ""} onChange={(e) => set("end_date", e.target.value)} /></Field></div></>; }
function SizeFields({ form, set, editing }: { form: Record<string, any>; set: Setter; editing: boolean }) { return <><div className="form-row two"><Field label="Code" required><input className="input mono" disabled={editing} value={form.code} onChange={(e) => set("code", e.target.value.toUpperCase())} /></Field><Field label="Name" required><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field></div><Field label="Ordered sizes" hint={editing ? "Immutable because BOMs and orders reference these labels" : "Comma-separated, smallest first"}><input className="input mono" disabled={editing} value={form.labels} onChange={(e) => set("labels", e.target.value)} /></Field></>; }
