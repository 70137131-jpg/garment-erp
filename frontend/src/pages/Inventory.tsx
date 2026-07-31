import { useState } from "react";
import { api, ApiError } from "../api/client";
import { LedgerEntry, Material, Reservation, Roll, WarehouseOperation } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { ListToolbar, useListView } from "../components/ListTools";
import { useToast } from "../components/Toast";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { money, qty, titled } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export default function Inventory() {
  const { can } = useAuthorization();
  const [tab, setTab] = useState("rolls");
  return (
    <div>
      <PageHeader eyebrow="Module 4" title="Inventory & Warehouse" subtitle="Immutable stock control with roll-level traceability, physical warehouse execution, reservations, and count variance audit." />
      <Tabs tabs={[
        { key: "rolls", label: "Roll Register" },
        { key: "operations", label: "Warehouse Operations" },
        { key: "ledger", label: "Stock Ledger" },
        { key: "valuation", label: "Valuation" },
        { key: "reservations", label: "Reservations" },
      ]} active={tab} onChange={setTab} />
      {tab === "rolls" && <Rolls />}
      {tab === "operations" && <Operations editable={can("stores", "quality_inspector")} />}
      {tab === "ledger" && <Ledger />}
      {tab === "valuation" && <Valuation />}
      {tab === "reservations" && <Reservations />}
    </div>
  );
}

const ROLL_STATUSES = ["", "pending_inspection", "available", "reserved", "quarantined", "consumed", "rejected"];

function Rolls() {
  const [status, setStatus] = useState("");
  const rolls = useAsync(() => api.get<Roll[]>(`/inventory/rolls${status ? `?status=${status}` : ""}`), [status]);
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"), [], "/masters/materials");
  const view = useListView(rolls.data ?? [], (roll) => `${roll.roll_number} ${roll.dye_lot ?? ""} ${roll.shade_group ?? ""}`, (a, b) => a.id - b.id);
  const materialName = (id: number) => materials.data?.find((material) => material.id === id)?.name ?? `#${id}`;
  return (
    <Card title="Roll register" hint="Every roll is a uniquely identified unit">
      <ListToolbar {...toolbarProps(view)} exportPath="/inventory/rolls/export" placeholder="Search roll, dye lot, shade..." filters={
        <select className="select" style={{ width: "auto", minHeight: 34 }} value={status} onChange={(event) => setStatus(event.target.value)}>
          {ROLL_STATUSES.map((value) => <option key={value} value={value}>{value ? titled(value) : "All statuses"}</option>)}
        </select>
      } />
      {rolls.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
        <thead><tr><th>Roll</th><th>Material</th><th>Warehouse / Bin</th><th>Dye lot</th><th>Shade</th><th>Grade</th><th className="num">Length</th><th>Status</th></tr></thead>
        <tbody>{view.rows.map((roll) => <tr key={roll.id}>
          <td className="code">{roll.roll_number}</td><td>{materialName(roll.material_id)}</td>
          <td className="mono muted">{roll.warehouse ?? "MAIN"} / {roll.location ?? "Unbinned"}</td>
          <td className="mono muted">{roll.dye_lot || "-"}</td><td className="mono muted">{roll.shade_group || "-"}</td>
          <td><Chip tone="neutral" label={roll.grade} /></td><td className="num">{qty(roll.length)} m</td><td><Chip status={roll.status} /></td>
        </tr>)}{!view.rows.length && <tr><td colSpan={8} className="muted">No rolls match.</td></tr>}</tbody>
      </table></div>}
    </Card>
  );
}

function toolbarProps(view: ReturnType<typeof useListView<any>>) {
  return {
    query: view.query, onQuery: view.setQuery, descending: view.descending,
    onDirection: view.toggleDirection, page: view.page, pages: view.pages,
    total: view.total, previous: view.previous, next: view.next,
  };
}

function Operations({ editable }: { editable: boolean }) {
  const operations = useAsync(() => api.get<WarehouseOperation[]>("/inventory/operations?limit=500"));
  const rolls = useAsync(() => api.get<Roll[]>("/inventory/rolls"));
  const [open, setOpen] = useState(false);
  const view = useListView(operations.data ?? [], (item) => `${item.operation_number} ${item.operation_type} ${item.reason ?? ""}`, (a, b) => a.id - b.id);
  return <Card title="Warehouse operations" hint="Posted physical actions and count variances" actions={editable ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ Post operation</button> : undefined}>
    <ListToolbar {...toolbarProps(view)} placeholder="Search operation, type, reason..." />
    {operations.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
      <thead><tr><th>Operation</th><th>Type</th><th>Lines</th><th>Reason</th><th>Posted by</th><th>Posted at</th></tr></thead>
      <tbody>{view.rows.map((operation) => <tr key={operation.id}>
        <td className="code">{operation.operation_number}</td><td><Chip tone="info" label={titled(operation.operation_type)} /></td>
        <td>{operation.lines.length}</td><td>{operation.reason || "-"}</td><td className="muted">{operation.created_by || "-"}</td>
        <td className="mono muted">{new Date(operation.created_at).toLocaleString()}</td>
      </tr>)}{!view.rows.length && <tr><td colSpan={6} className="muted">No warehouse operations yet.</td></tr>}</tbody>
    </table></div>}
    {open && <OperationForm rolls={rolls.data ?? []} onClose={() => setOpen(false)} onDone={() => { setOpen(false); operations.reload(); rolls.reload(); }} />}
  </Card>;
}

function OperationForm({ rolls, onClose, onDone }: { rolls: Roll[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [type, setType] = useState("transfer");
  const [rollId, setRollId] = useState(rolls.find((roll) => roll.status === "available")?.id ?? 0);
  const roll = rolls.find((item) => item.id === rollId);
  const [quantity, setQuantity] = useState(roll?.length ?? "0");
  const [warehouse, setWarehouse] = useState("MAIN");
  const [location, setLocation] = useState("");
  const [reason, setReason] = useState("");
  const [extra, setExtra] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!roll) { setError("Select a roll"); return; }
    setSaving(true); setError("");
    try {
      if (type === "transfer" || type === "put_away") {
        await api.post(type === "transfer" ? "/inventory/transfers" : "/inventory/put-away", {
          material_id: roll.material_id, roll_id: roll.id, quantity,
          from_warehouse: roll.warehouse ?? "MAIN", from_location: roll.location,
          to_warehouse: type === "put_away" ? (roll.warehouse ?? "MAIN") : warehouse,
          to_location: location || null, reason,
        });
      } else if (type === "return") {
        await api.post("/inventory/returns", { material_id: roll.material_id, roll_id: roll.id, quantity, warehouse: roll.warehouse ?? "MAIN", location: roll.location, direction: extra || "supplier", reason });
      } else if (type === "cycle_count") {
        await api.post("/inventory/cycle-counts", { reason, lines: [{ material_id: roll.material_id, roll_id: roll.id, counted_qty: quantity, warehouse: roll.warehouse ?? "MAIN", location: roll.location }] });
      } else if (type === "regrade") {
        await api.post(`/inventory/rolls/${roll.id}/regrade`, { grade: extra || "B", reason });
      } else if (type === "split") {
        const parts = extra.split(",").map((value) => ({ quantity: value.trim() })).filter((part) => part.quantity);
        await api.post(`/inventory/rolls/${roll.id}/split`, { parts, reason });
      } else if (type === "join") {
        const rollIds = extra.split(",").map((value) => Number(value.trim())).filter(Boolean);
        await api.post("/inventory/rolls/join", { roll_ids: rollIds, reason });
      }
      toast.push("Warehouse operation posted"); onDone();
    } catch (caught) {
      const message = (caught as ApiError).message; setError(message); toast.push("Operation failed", { detail: message, bad: true });
    } finally { setSaving(false); }
  }

  return <Drawer title="Post warehouse operation" sub="Ledger-backed execution" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={saving || !reason.trim()} onClick={save}>Post operation</button></>}>
    {error && <ErrorBox message={error} />}
    <Field label="Operation" required><select className="select" value={type} onChange={(event) => setType(event.target.value)}>
      <option value="transfer">Warehouse transfer</option><option value="put_away">Put-away</option><option value="return">Return</option>
      <option value="cycle_count">Cycle count</option><option value="regrade">Regrade roll</option><option value="split">Split roll</option><option value="join">Join rolls</option>
    </select></Field>
    <Field label="Roll" required><select className="select" value={rollId} onChange={(event) => { const id = Number(event.target.value); setRollId(id); setQuantity(rolls.find((item) => item.id === id)?.length ?? "0"); }}>
      <option value={0}>Select roll...</option>{rolls.map((item) => <option key={item.id} value={item.id}>{item.roll_number} - {item.status}</option>)}
    </select></Field>
    {!['regrade','split','join'].includes(type) && <Field label={type === "cycle_count" ? "Counted quantity" : "Quantity"} required><input className="input mono" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></Field>}
    {(type === "transfer" || type === "put_away") && <div className="form-row two"><Field label="Destination warehouse"><input className="input" value={warehouse} onChange={(event) => setWarehouse(event.target.value)} /></Field><Field label="Destination bin"><input className="input" value={location} onChange={(event) => setLocation(event.target.value)} /></Field></div>}
    {type === "return" && <Field label="Return direction"><select className="select" value={extra || "supplier"} onChange={(event) => setExtra(event.target.value)}><option value="supplier">To supplier</option><option value="customer">From customer</option><option value="stock">From production to stock</option></select></Field>}
    {type === "regrade" && <Field label="New grade"><select className="select" value={extra || "B"} onChange={(event) => setExtra(event.target.value)}><option>A</option><option>B</option><option>C</option></select></Field>}
    {type === "split" && <Field label="Child quantities" hint="Comma-separated quantities; they must equal on-hand"><input className="input mono" value={extra} onChange={(event) => setExtra(event.target.value)} placeholder="40, 60" /></Field>}
    {type === "join" && <Field label="Roll IDs" hint="Comma-separated compatible source roll IDs"><input className="input mono" value={extra} onChange={(event) => setExtra(event.target.value)} placeholder="12, 13" /></Field>}
    <Field label="Reason" required><textarea className="input" value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
  </Drawer>;
}

function Ledger() {
  const ledger = useAsync(() => api.get<LedgerEntry[]>("/inventory/ledger"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"), [], "/masters/materials");
  const view = useListView(ledger.data ?? [], (entry) => `${entry.id} ${entry.movement_type} ${entry.reference_type ?? ""} ${entry.note ?? ""}`, (a, b) => a.id - b.id);
  const materialName = (id: number) => materials.data?.find((material) => material.id === id)?.name ?? `#${id}`;
  return <Card title="Stock ledger" hint="Append-only; balance equals sum of lines"><ListToolbar {...toolbarProps(view)} exportPath="/inventory/ledger/export" placeholder="Search movement, reference, note..." />
    {ledger.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl"><thead><tr><th>#</th><th>Material</th><th>Movement</th><th className="num">Qty</th><th>Reference</th><th>Note</th></tr></thead>
      <tbody>{view.rows.map((entry) => { const amount = parseFloat(entry.quantity); return <tr key={entry.id}><td className="mono muted">{entry.id}</td><td>{materialName(entry.material_id)}</td><td><Chip tone={amount >= 0 ? "ok" : "warn"} label={titled(entry.movement_type)} /></td><td className="num" style={{ color: amount >= 0 ? "var(--ok)" : "var(--madder)" }}>{amount >= 0 ? "+" : ""}{qty(entry.quantity)} {entry.uom}</td><td className="mono muted">{entry.reference_type || "-"}</td><td className="muted">{entry.note || "-"}</td></tr>; })}{!view.rows.length && <tr><td colSpan={6} className="muted">No movements yet.</td></tr>}</tbody>
    </table></div>}
  </Card>;
}

interface Valuation { material_id: number; warehouse?: string; location?: string; quantity: string; value: string; unit_cost: string; }
function Valuation() {
  const values = useAsync(() => api.get<Valuation[]>("/inventory/valuation"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"), [], "/masters/materials");
  const name = (id: number) => materials.data?.find((material) => material.id === id)?.name ?? `#${id}`;
  return <Card title="Stock valuation" hint="Ledger-derived book value; FIFO or weighted-average by material">
    {values.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl"><thead><tr><th>Material</th><th>Method</th><th className="num">On hand</th><th className="num">Unit cost</th><th className="num">Value</th></tr></thead><tbody>
      {values.data?.map((value) => { const material = materials.data?.find((item) => item.id === value.material_id); return <tr key={value.material_id}><td>{name(value.material_id)}</td><td><Chip tone="neutral" label={titled(material?.valuation_method ?? "weighted_average")} /></td><td className="num">{qty(value.quantity)}</td><td className="num">{money(value.unit_cost)}</td><td className="num">{money(value.value)}</td></tr>; })}
      {!values.data?.length && <tr><td colSpan={5} className="muted">No valued stock yet.</td></tr>}
    </tbody></table></div>}
  </Card>;
}

function Reservations() {
  const reservations = useAsync(() => api.get<Reservation[]>("/inventory/reservations"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"), [], "/masters/materials");
  const view = useListView(reservations.data ?? [], (item) => `${item.id} ${item.status} ${item.reference_type ?? ""}`, (a, b) => a.id - b.id);
  const materialName = (id: number) => materials.data?.find((material) => material.id === id)?.name ?? `#${id}`;
  return <Card title="Reservations" hint="Active to released or consumed"><ListToolbar {...toolbarProps(view)} placeholder="Search reservation or status..." />
    {reservations.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl"><thead><tr><th>#</th><th>Material</th><th className="num">Roll</th><th className="num">Reserved</th><th>For</th><th>Status</th></tr></thead>
      <tbody>{view.rows.map((item) => <tr key={item.id}><td className="mono muted">{item.id}</td><td>{materialName(item.material_id)}</td><td className="num mono">{item.roll_id ?? "-"}</td><td className="num">{qty(item.reserved_qty)} m</td><td className="mono muted">{item.reference_type ? `${item.reference_type} #${item.reference_id}` : "-"}</td><td><Chip status={item.status} /></td></tr>)}{!view.rows.length && <tr><td colSpan={6} className="muted">No reservations yet.</td></tr>}</tbody></table></div>}
  </Card>;
}
