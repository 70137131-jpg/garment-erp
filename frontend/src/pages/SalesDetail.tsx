import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Colour, Final, Profitability, SalesOrder, Shipment, Style } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, num } from "../lib/format";

interface CommercialCheck { eligible: boolean; order_value: string; credit_limit: string; current_exposure: string; projected_exposure: string; messages: string[]; }
interface OrderRevision { id: number; revision_no: number; action: string; reason: string; created_at: string; created_by?: string; }

export default function SalesDetail() {
  const { can } = useAuthorization();
  const { id } = useParams();
  const oid = Number(id);
  const order = useAsync(() => api.get<SalesOrder>(`/sales-orders/${oid}`), [oid]);
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const colours = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const prof = useAsync(() => api.get<Profitability>(`/costing/sales-orders/${oid}/profitability`), [oid]);
  const checks = useAsync(() => api.get<CommercialCheck>(`/sales-orders/${oid}/commercial-checks`), [oid]);
  const history = useAsync(() => api.get<OrderRevision[]>(`/sales-orders/${oid}/history`), [oid]);
  const shipments = useAsync(() => api.get<Shipment[]>(`/sales-orders/${oid}/shipments`), [oid]);
  const [change, setChange] = useState<"amend" | "cancel" | null>(null);
  const [shipOpen, setShipOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const toast = useToast();

  if (order.loading) return <Spinner />;
  if (!order.data) return <div className="err">Order not found.</div>;
  const o = order.data;
  const styleNo = (sid: number) => styles.data?.find((s) => s.id === sid)?.style_number ?? `#${sid}`;
  const colourNm = (cid: number) => colours.data?.find((c) => c.id === cid)?.name ?? `#${cid}`;

  async function act(path: string, ok: string) {
    try {
      await api.post(path);
      toast.push(ok);
      order.reload(); prof.reload();
      checks.reload(); history.reload();
    } catch (e) { toast.push("Action failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <div>
      <PageHeader
        eyebrow={<Link to="/sales" style={{ color: "var(--madder)" }}>← Sales Orders</Link>}
        title={o.order_number}
        subtitle={o.customer_po_number ? `Customer PO ${o.customer_po_number}` : undefined}
        actions={
          <div className="inline-actions">
            <Chip status={o.status} />
            {can("merchandiser") && ["draft", "confirmed"].includes(o.status) && <button className="btn" onClick={() => setChange("amend")}>Amend</button>}
            {can("merchandiser") && ["draft", "confirmed", "in_production"].includes(o.status) && <button className="btn danger" onClick={() => setChange("cancel")}>Cancel</button>}
            {can("merchandiser") && o.status === "draft" && <button className="btn primary" onClick={() => act(`/sales-orders/${oid}/confirm`, "Order confirmed")}>Confirm</button>}
            {can("merchandiser") && ["confirmed", "in_production", "partially_shipped"].includes(o.status) && (
              <button className="btn primary" onClick={() => setShipOpen(true)}>Ship</button>
            )}
            {can("merchandiser") && o.status === "shipped" && <button className="btn primary" onClick={() => setCloseOpen(true)}>Close</button>}
          </div>
        }
      />

      {(o.status === "confirmed" || o.status === "in_production") && (
        <div className="hintline" style={{ marginBottom: 16 }}>
          Shipment remains blocked until a quality inspector records a passing final AQL inspection in the Quality module.
        </div>
      )}

      <div className="grid cols-4" style={{ marginBottom: 20 }}>
        <div className="card stat accent"><div className="k">Total qty</div><div className="v">{num(o.total_quantity)}</div></div>
        <div className="card stat"><div className="k">Shipped qty</div><div className="v">{num(o.total_shipped_quantity)}</div><div className="foot">{o.total_quantity ? `${Math.round(o.total_shipped_quantity / o.total_quantity * 100)}% fulfilled` : "â€”"}</div></div>
        <div className="card stat"><div className="k">Order value</div><div className="v" style={{ fontSize: 24 }}>{money(o.total_value, o.currency)}</div></div>
        <div className="card stat accent-madder"><div className="k">Est. profit</div><div className="v" style={{ fontSize: 24 }}>{prof.loading ? "…" : money(prof.data?.profit, o.currency)}</div>
          <div className="foot">{prof.data ? `${parseFloat(prof.data.margin_pct).toFixed(1)}% margin` : "—"}</div></div>
        <div className="card stat"><div className="k">Unit cost</div><div className="v" style={{ fontSize: 24 }}>{prof.loading ? "…" : money(prof.data?.unit_cost, o.currency)}</div><div className="foot">from cost sheet</div></div>
      </div>

      <Card title="Shipment history" hint={`${shipments.data?.length ?? 0} dispatches`}>
        <div className="table-wrap"><table className="tbl"><thead><tr><th>Shipment</th><th>Reference</th><th className="num">Units</th><th className="num">Cartons</th><th className="num">COGS</th></tr></thead><tbody>
          {shipments.data?.map((shipment) => <tr key={shipment.id}><td className="code">{shipment.shipment_number}</td><td>{shipment.shipping_reference || "â€”"}</td><td className="num">{shipment.total_quantity}</td><td className="num">{shipment.total_cartons}</td><td className="num">{money(shipment.cost_of_goods, o.currency)}</td></tr>)}
          {!shipments.data?.length && <tr><td colSpan={5} className="muted">No shipments recorded.</td></tr>}
        </tbody></table></div>
      </Card>

      <div className="grid cols-2" style={{ marginBottom: 20 }}>
        <Card title="Commercial controls" hint={checks.data?.eligible ? "Clear to confirm" : "Action required"} pad>
          {checks.loading ? <Spinner /> : <><dl className="kv"><dt>Credit limit</dt><dd>{money(checks.data?.credit_limit, o.currency)}</dd><dt>Current exposure</dt><dd>{money(checks.data?.current_exposure, o.currency)}</dd><dt>Projected exposure</dt><dd>{money(checks.data?.projected_exposure, o.currency)}</dd></dl>
          <div className="divider" />{checks.data?.eligible ? <Chip tone="ok" label="Checks passed" /> : checks.data?.messages.map((message) => <div className="err" key={message}>{message}</div>)}</>}
        </Card>
        <Card title="Amendment & cancellation history" hint={`${history.data?.length ?? 0} revisions`}>
          <div className="table-wrap"><table className="tbl"><thead><tr><th>Rev</th><th>Action</th><th>Reason</th><th>By</th></tr></thead><tbody>
            {history.data?.map((row) => <tr key={row.id}><td className="mono">{row.revision_no}</td><td><Chip tone={row.action === "cancelled" ? "bad" : "info"} label={row.action} /></td><td>{row.reason}</td><td className="muted">{row.created_by || "-"}</td></tr>)}
            {!history.data?.length && <tr><td colSpan={4} className="muted">No amendments or cancellations.</td></tr>}
          </tbody></table></div>
        </Card>
      </div>

      {o.lines.map((ln) => (
        <Card key={ln.id} title={`${styleNo(ln.style_id)} · ${colourNm(ln.colour_id)}`} hint={`${money(ln.unit_price, o.currency)} / unit · ${money(ln.line_value, o.currency)} line`} >
          <div className="table-wrap" style={{ padding: "0 0 6px" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Size</th>
                  {ln.sizes.map((s) => <th key={s.size_label} className="num">{s.size_label}</th>)}
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                <tr><td className="mono muted">Ordered</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.ordered_qty}</td>)}<td className="num"><b>{ln.sizes.reduce((a, s) => a + s.ordered_qty, 0)}</b></td></tr>
                <tr><td className="mono muted">Confirmed</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.confirmed_qty || "—"}</td>)}<td className="num">{ln.sizes.reduce((a, s) => a + s.confirmed_qty, 0) || "—"}</td></tr>
                <tr><td className="mono muted">Shipped</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.shipped_qty || "—"}</td>)}<td className="num">{ln.sizes.reduce((a, s) => a + s.shipped_qty, 0) || "—"}</td></tr>
              </tbody>
            </table>
          </div>
        </Card>
      ))}
      {change && <OrderChange mode={change} order={o} onClose={() => setChange(null)} onDone={() => { setChange(null); order.reload(); checks.reload(); history.reload(); }} />}
      {shipOpen && <ShipmentDrawer order={o} onClose={() => setShipOpen(false)} onDone={() => { setShipOpen(false); order.reload(); shipments.reload(); history.reload(); prof.reload(); }} />}
      {closeOpen && <CloseOrderDrawer order={o} onClose={() => setCloseOpen(false)} onDone={() => { setCloseOpen(false); order.reload(); history.reload(); }} />}
    </div>
  );
}

function OrderChange({ mode, order, onClose, onDone }: { mode: "amend" | "cancel"; order: SalesOrder; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError("");
    try {
      await api.post(`/sales-orders/${order.id}/${mode}`, mode === "amend" ? { reason, notes } : { reason });
      toast.push(mode === "amend" ? "Order amended" : "Order cancelled"); onDone();
    } catch (caught) { const message = (caught as ApiError).message; setError(message); toast.push("Action failed", { detail: message, bad: true }); }
    finally { setSaving(false); }
  }
  return <Drawer title={mode === "amend" ? "Amend sales order" : "Cancel sales order"} sub={order.order_number} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Close</button><button className={`btn ${mode === "cancel" ? "danger" : "primary"}`} disabled={saving || !reason.trim()} onClick={save}>{mode === "amend" ? "Save amendment" : "Cancel order"}</button></>}>
    {error && <ErrorBox message={error} />}
    {mode === "amend" && <Field label="Revised notes"><textarea className="input" value={notes} onChange={(event) => setNotes(event.target.value)} /></Field>}
    <Field label="Reason" required><textarea className="input" value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
  </Drawer>;
}

function ShipmentDrawer({ order, onClose, onDone }: { order: SalesOrder; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const outstanding = order.lines.flatMap((line) => line.sizes.map((size) => ({ id: size.id, label: size.size_label, outstanding: size.confirmed_qty - size.shipped_qty }))
    .filter((size) => size.outstanding > 0));
  const [lines, setLines] = useState(outstanding.map((line) => ({ ...line, quantity: String(line.outstanding), cartons: "0" })));
  const [reference, setReference] = useState("");
  const [destination, setDestination] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError("");
    try {
      await api.post(`/sales-orders/${order.id}/ship`, {
        shipping_reference: reference || null,
        destination: destination || null,
        lines: lines.filter((line) => Number(line.quantity) > 0).map((line) => ({ sales_order_size_cell_id: line.id, quantity: Number(line.quantity), carton_count: Number(line.cartons || 0) })),
      });
      toast.push("Shipment dispatched"); onDone();
    } catch (caught) { const message = (caught as ApiError).message; setError(message); toast.push("Shipment failed", { detail: message, bad: true }); }
    finally { setSaving(false); }
  }
  return <Drawer title="Dispatch shipment" sub={order.order_number} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={saving || !lines.some((line) => Number(line.quantity) > 0)} onClick={save}>Dispatch</button></>}>
    {error && <ErrorBox message={error} />}
    <div className="form-row two"><Field label="Shipping reference"><input className="input mono" value={reference} onChange={(e) => setReference(e.target.value)} /></Field><Field label="Destination"><input className="input" value={destination} onChange={(e) => setDestination(e.target.value)} /></Field></div>
    <div className="section-title">Outstanding size quantities</div>
    {lines.map((line, index) => <div className="form-row three" key={line.id}><Field label={`Size ${line.label}`} hint={`${line.outstanding} available`}><input className="input mono" value={line.quantity} onChange={(e) => setLines(lines.map((item, i) => i === index ? { ...item, quantity: e.target.value } : item))} /></Field><Field label="Cartons"><input className="input mono" value={line.cartons} onChange={(e) => setLines(lines.map((item, i) => i === index ? { ...item, cartons: e.target.value } : item))} /></Field></div>)}
  </Drawer>;
}

function CloseOrderDrawer({ order, onClose, onDone }: { order: SalesOrder; onClose: () => void; onDone: () => void }) {
  const toast = useToast(); const [reason, setReason] = useState(""); const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  async function save() { setSaving(true); setError(""); try { await api.post(`/sales-orders/${order.id}/close`, { reason }); toast.push("Order closed"); onDone(); } catch (caught) { const message = (caught as ApiError).message; setError(message); } finally { setSaving(false); } }
  return <Drawer title="Close sales order" sub={order.order_number} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!reason.trim() || saving} onClick={save}>Close order</button></>}>
    {error && <ErrorBox message={error} />}<Field label="Closure reason" required><textarea className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
  </Drawer>;
}
