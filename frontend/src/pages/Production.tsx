import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Colour, CutOrder, ProductionRoute, SewingOrder, SizeRange, Style, Subcontract, Supplier, WipSummary } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { PdfLink } from "../components/ListTools";
import { num, pct, qty } from "../lib/format";

export default function Production() {
  const [tab, setTab] = useState("cut");
  return (
    <div>
      <PageHeader
        eyebrow="Module 5"
        title="Production"
        subtitle="Cut orders compute fabric from the size-BOM, issue rolls as ledger consumption, then sewing runs with SAM-based efficiency. Subcontracting tracks the outstanding balance."
      />
      <Tabs tabs={[{ key: "cut", label: "Cut Orders" }, { key: "sew", label: "Sewing" }, { key: "routing", label: "Routing & WIP" }, { key: "sub", label: "Subcontract" }]} active={tab} onChange={setTab} />
      {tab === "cut" && <CutOrders />}
      {tab === "sew" && <Sewing />}
      {tab === "routing" && <RoutingWip />}
      {tab === "sub" && <Subcontracts />}
    </div>
  );
}

/* ---------------- Cut orders ---------------- */
function CutOrders() {
  const { can } = useAuthorization();
  const cuts = useAsync(() => api.get<CutOrder[]>("/production/cut-orders"));
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const styleNo = (id: number) => styles.data?.find((s) => s.id === id)?.style_number ?? `#${id}`;

  async function act(cut: CutOrder, kind: "reserve" | "issue" | "complete") {
    try {
      if (kind === "reserve") await api.post(`/production/cut-orders/${cut.id}/reserve-fabric`, {});
      if (kind === "issue") await api.post(`/production/cut-orders/${cut.id}/issue-fabric`, {});
      if (kind === "complete") await api.post(`/production/cut-orders/${cut.id}/complete`, { cut_qty: cut.sizes.map((s) => ({ size_label: s.size_label, planned_qty: s.planned_qty })) });
      toast.push("Cut order updated");
      cuts.reload();
    } catch (e) { toast.push("Action failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <Card title="Cut orders" actions={can("planner", "cutting_supervisor") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New cut order</button> : undefined}>
      {cuts.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Cut №</th><th>Style</th><th>Status</th><th className="num">Fabric req.</th><th className="num">Issued</th><th className="num">Pieces</th><th>Actions</th></tr></thead>
            <tbody>
              {[...(cuts.data ?? [])].reverse().map((c) => (
                <tr key={c.id}>
                  <td className="code">{c.order_number}</td>
                  <td>{styleNo(c.style_id)}</td>
                  <td><Chip status={c.status} /></td>
                  <td className="num">{qty(c.fabric_required)} m</td>
                  <td className="num">{qty(c.fabric_issued)} m</td>
                  <td className="num">{num(c.pieces_cut)}</td>
                  <td>
                    <div className="inline-actions">
                      <PdfLink path={`/documents/cut-orders/${c.id}/pdf`} />
                      {can("planner", "stores") && c.status === "planned" && <button className="btn sm" onClick={() => act(c, "reserve")}>Reserve</button>}
                      {can("stores", "cutting_supervisor") && (c.status === "fabric_reserved" || c.status === "planned") && <button className="btn sm" onClick={() => act(c, "issue")}>Issue</button>}
                      {can("cutting_supervisor") && c.status === "in_cutting" && <button className="btn sm" onClick={() => act(c, "complete")}>Complete</button>}
                    </div>
                  </td>
                </tr>
              ))}
              {!cuts.data?.length && <tr><td colSpan={7} className="muted">No cut orders yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && <CutForm onClose={() => setOpen(false)} onDone={() => { setOpen(false); cuts.reload(); }} />}
    </Card>
  );
}

function CutForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const colours = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const ranges = useAsync(() => api.get<SizeRange[]>("/masters/size-ranges"));
  const [styleId, setStyleId] = useState(0);
  const [colourId, setColourId] = useState(0);
  const [sizes, setSizes] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const range = ranges.data?.find((r) => r.id === styles.data?.find((s) => s.id === styleId)?.size_range_id);

  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post("/production/cut-orders", {
        style_id: Number(styleId), colour_id: Number(colourId),
        sizes: Object.entries(sizes).filter(([, v]) => Number(v) > 0).map(([size_label, v]) => ({ size_label, planned_qty: Number(v) })),
      });
      toast.push("Cut order created"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <Drawer title="New cut order" sub="Module 5.1 · fabric from size-BOM" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !styleId || !colourId} onClick={save}>Create</button></>}>
      {error && <ErrorBox message={error} />}
      <div className="form-row two">
        <Field label="Style" required>
          <select className="select" value={styleId} onChange={(e) => { setStyleId(Number(e.target.value)); setSizes({}); }}>
            <option value={0}>Select…</option>
            {styles.data?.map((s) => <option key={s.id} value={s.id}>{s.style_number}</option>)}
          </select>
        </Field>
        <Field label="Colour" required>
          <select className="select" value={colourId} onChange={(e) => setColourId(Number(e.target.value))}>
            <option value={0}>Select…</option>
            {colours.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
      </div>
      {range && (
        <Field label="Size breakdown to cut">
          <div className="matrix">
            <table>
              <thead><tr>{range.sizes.map((z) => <th key={z.label}>{z.label}</th>)}</tr></thead>
              <tbody><tr>{range.sizes.map((z) => <td key={z.label}><input value={sizes[z.label] ?? ""} placeholder="0" onChange={(e) => setSizes({ ...sizes, [z.label]: e.target.value })} /></td>)}</tr></tbody>
            </table>
          </div>
        </Field>
      )}
      <div className="hintline">Fabric requirement is calculated from the style's approved BOM once created.</div>
    </Drawer>
  );
}

/* ---------------- Sewing ---------------- */
function Sewing() {
  const { can } = useAuthorization();
  const orders = useAsync(() => api.get<SewingOrder[]>("/production/sewing-orders"));
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const [open, setOpen] = useState(false);
  const [output, setOutput] = useState<SewingOrder | null>(null);
  const styleNo = (id: number) => styles.data?.find((s) => s.id === id)?.style_number ?? `#${id}`;

  return (
    <Card title="Sewing orders" hint="SAM-based line efficiency"
      actions={can("planner", "sewing_supervisor") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New sewing order</button> : undefined}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Sew №</th><th>Style</th><th>Line</th><th>Status</th><th className="num">Planned</th><th className="num">Produced</th><th className="num">Avg eff.</th><th></th></tr></thead>
          <tbody>
            {(orders.data ?? []).map((s) => (
              <tr key={s.id}>
                <td className="code">{s.order_number}</td>
                <td>{styleNo(s.style_id)}</td>
                <td>{s.line || "—"}</td>
                <td><Chip status={s.status} /></td>
                <td className="num">{num(s.planned_qty)}</td>
                <td className="num">{num(s.produced_qty)}</td>
                <td className="num">{pct(s.average_efficiency_pct)}</td>
                <td className="right"><div className="inline-actions"><PdfLink path={`/documents/sewing-orders/${s.id}/pdf`} />{can("sewing_supervisor") && <button className="btn sm" onClick={() => setOutput(s)}>+ Output</button>}</div></td>
              </tr>
            ))}
            {!orders.data?.length && <tr><td colSpan={8} className="muted">No sewing orders yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <SewForm styles={styles.data ?? []} onClose={() => setOpen(false)} onDone={() => { setOpen(false); orders.reload(); }} />}
      {output && <OutputForm sewing={output} onClose={() => setOutput(null)} onDone={() => { setOutput(null); orders.reload(); }} />}
    </Card>
  );
}

function SewForm({ styles, onClose, onDone }: { styles: Style[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [styleId, setStyleId] = useState(0);
  const [line, setLine] = useState("Line 1");
  const [planned, setPlanned] = useState("0");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError(null);
    try {
      const o = await api.post<SewingOrder>("/production/sewing-orders", { style_id: Number(styleId), line, planned_qty: Number(planned) });
      toast.push(`Sewing order ${o.order_number} created`); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }
  return (
    <Drawer title="New sewing order" sub="Module 5.4" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !styleId} onClick={save}>Create</button></>}>
      {error && <ErrorBox message={error} />}
      <Field label="Style" required>
        <select className="select" value={styleId} onChange={(e) => setStyleId(Number(e.target.value))}>
          <option value={0}>Select…</option>
          {styles.map((s) => <option key={s.id} value={s.id}>{s.style_number}</option>)}
        </select>
      </Field>
      <div className="form-row two">
        <Field label="Line"><input className="input" value={line} onChange={(e) => setLine(e.target.value)} /></Field>
        <Field label="Planned qty"><input className="input mono" value={planned} onChange={(e) => setPlanned(e.target.value)} /></Field>
      </div>
    </Drawer>
  );
}

function OutputForm({ sewing, onClose, onDone }: { sewing: SewingOrder; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ output_date: new Date().toISOString().slice(0, 10), produced_qty: "0", operators: "25", working_minutes: "480" });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post(`/production/sewing-orders/${sewing.id}/daily-output`, {
        output_date: f.output_date, produced_qty: Number(f.produced_qty), operators: Number(f.operators), working_minutes: Number(f.working_minutes),
      });
      toast.push("Daily output recorded"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }
  return (
    <Drawer title="Record daily output" sub={`${sewing.order_number} · Module 5.5`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving} onClick={save}>Record</button></>}>
      {error && <ErrorBox message={error} />}
      <Field label="Date"><input className="input mono" type="date" value={f.output_date} onChange={(e) => setF({ ...f, output_date: e.target.value })} /></Field>
      <div className="form-row three">
        <Field label="Produced"><input className="input mono" value={f.produced_qty} onChange={(e) => setF({ ...f, produced_qty: e.target.value })} /></Field>
        <Field label="Operators"><input className="input mono" value={f.operators} onChange={(e) => setF({ ...f, operators: e.target.value })} /></Field>
        <Field label="Minutes"><input className="input mono" value={f.working_minutes} onChange={(e) => setF({ ...f, working_minutes: e.target.value })} /></Field>
      </div>
      <div className="hintline">Efficiency % = produced × SAM ÷ (operators × minutes) × 100.</div>
    </Drawer>
  );
}

/* ---------------- Routing and WIP ---------------- */
function RoutingWip() {
  const { can } = useAuthorization();
  const routes = useAsync(() => api.get<ProductionRoute[]>("/production/routes"));
  const sewing = useAsync(() => api.get<SewingOrder[]>("/production/sewing-orders"));
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const [sewingId, setSewingId] = useState(0);
  const selected = sewing.data?.find((order) => order.id === sewingId);
  const activeRoute = routes.data?.find((route) => route.style_id === selected?.style_id && route.active);
  const [stepId, setStepId] = useState(0);
  const [quantityIn, setQuantityIn] = useState("0");
  const [quantityOut, setQuantityOut] = useState("0");
  const [rejected, setRejected] = useState("0");
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const summary = useAsync(() => sewingId ? api.get<WipSummary[]>(`/production/sewing-orders/${sewingId}/wip`) : Promise.resolve([]), [sewingId]);
  const styleName = (id: number) => styles.data?.find((style) => style.id === id)?.style_number ?? `#${id}`;
  async function postWip() {
    try {
      await api.post(`/production/sewing-orders/${sewingId}/wip`, { route_step_id: stepId, quantity_in: Number(quantityIn), quantity_out: Number(quantityOut), rejected_qty: Number(rejected) });
      toast.push("WIP movement posted"); summary.reload(); setQuantityIn("0"); setQuantityOut("0"); setRejected("0");
    } catch (error) { toast.push("WIP posting failed", { detail: (error as ApiError).message, bad: true }); }
  }
  return <div className="grid cols-2">
    <Card title="Production routes" hint="Versioned operation sequences" actions={can("planner") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New route</button> : undefined}>
      <div className="table-wrap"><table className="tbl"><thead><tr><th>Route</th><th>Style</th><th>Version</th><th>Steps</th><th>Status</th></tr></thead><tbody>
        {routes.data?.map((route) => <tr key={route.id}><td className="code">{route.route_number}</td><td>{styleName(route.style_id)}</td><td className="num">{route.version_no}</td><td>{route.steps.map((step) => step.operation).join(" / ")}</td><td><Chip tone={route.active ? "ok" : "neutral"} label={route.active ? "Active" : "Superseded"} /></td></tr>)}
        {!routes.data?.length && <tr><td colSpan={5} className="muted">No production routes defined.</td></tr>}
      </tbody></table></div>
    </Card>
    <Card title="WIP workbench" hint="Quantity at each routing step" pad>
      <Field label="Sewing order"><select className="select" value={sewingId} onChange={(event) => { setSewingId(Number(event.target.value)); setStepId(0); }}><option value={0}>Select...</option>{sewing.data?.map((order) => <option key={order.id} value={order.id}>{order.order_number} - {styleName(order.style_id)}</option>)}</select></Field>
      {activeRoute && <><Field label="Route step"><select className="select" value={stepId} onChange={(event) => setStepId(Number(event.target.value))}><option value={0}>Select...</option>{activeRoute.steps.map((step) => <option key={step.id} value={step.id}>{step.sequence}. {step.operation}</option>)}</select></Field>
      <div className="form-row three"><Field label="Quantity in"><input className="input mono" value={quantityIn} onChange={(event) => setQuantityIn(event.target.value)} /></Field><Field label="Quantity out"><input className="input mono" value={quantityOut} onChange={(event) => setQuantityOut(event.target.value)} /></Field><Field label="Rejected"><input className="input mono" value={rejected} onChange={(event) => setRejected(event.target.value)} /></Field></div>
      {can("planner", "sewing_supervisor") && <button className="btn primary" disabled={!stepId} onClick={postWip}>Post WIP movement</button>}
      <div className="divider" /><div className="table-wrap"><table className="tbl"><thead><tr><th>Step</th><th className="num">In</th><th className="num">Out</th><th className="num">Reject</th><th className="num">WIP</th></tr></thead><tbody>{summary.data?.map((row) => <tr key={row.route_step_id}><td>{row.sequence}. {row.operation}</td><td className="num">{row.quantity_in}</td><td className="num">{row.quantity_out}</td><td className="num">{row.rejected_qty}</td><td className="num"><b>{row.wip_qty}</b></td></tr>)}</tbody></table></div></>}
      {selected && !activeRoute && <div className="err">This style has no active production route.</div>}
    </Card>
    {open && <RouteForm styles={styles.data ?? []} onClose={() => setOpen(false)} onDone={() => { setOpen(false); routes.reload(); }} />}
  </div>;
}

function RouteForm({ styles, onClose, onDone }: { styles: Style[]; onClose: () => void; onDone: () => void }) {
  const [styleId, setStyleId] = useState(0);
  const [name, setName] = useState("Main production route");
  const [steps, setSteps] = useState("Cut handover, Join shoulder, Attach neck, Sleeve set, Side seam, Finishing");
  const [error, setError] = useState("");
  const toast = useToast();
  async function save() {
    try {
      await api.post("/production/routes", { style_id: styleId, name, steps: steps.split(",").map((operation, index) => ({ sequence: (index + 1) * 10, operation: operation.trim() })).filter((step) => step.operation) });
      toast.push("Production route created"); onDone();
    } catch (caught) { const message = (caught as ApiError).message; setError(message); toast.push("Route failed", { detail: message, bad: true }); }
  }
  return <Drawer title="New production route" sub="Versioned operation sequence" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!styleId || !name.trim()} onClick={save}>Create route</button></>}>
    {error && <ErrorBox message={error} />}<Field label="Style" required><select className="select" value={styleId} onChange={(event) => setStyleId(Number(event.target.value))}><option value={0}>Select...</option>{styles.map((style) => <option key={style.id} value={style.id}>{style.style_number}</option>)}</select></Field>
    <Field label="Route name"><input className="input" value={name} onChange={(event) => setName(event.target.value)} /></Field><Field label="Operations" hint="Comma-separated, in production sequence"><textarea className="input" value={steps} onChange={(event) => setSteps(event.target.value)} /></Field>
  </Drawer>;
}

/* ---------------- Subcontract ---------------- */
function Subcontracts() {
  const { can } = useAuthorization();
  const orders = useAsync(() => api.get<Subcontract[]>("/production/subcontract-orders"));
  const suppliers = useAsync(() => api.get<Supplier[]>("/masters/suppliers"));
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const supName = (id: number) => suppliers.data?.find((s) => s.id === id)?.name ?? `#${id}`;

  async function receive(sc: Subcontract) {
    const val = prompt(`Receive how many from ${sc.order_number}? (outstanding ${sc.outstanding_qty})`);
    if (!val) return;
    try {
      await api.post<Subcontract>(`/production/subcontract-orders/${sc.id}/receive`, { received_qty: Number(val) });
      orders.reload();
      toast.push("Receipt reconciled");
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <Card title="Subcontract orders" hint="outstanding balance tracked"
      actions={can("planner", "procurement") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New subcontract</button> : undefined}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Order</th><th>Process</th><th>Subcontractor</th><th className="num">Sent</th><th className="num">Received</th><th className="num">Outstanding</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {(orders.data ?? []).map((s) => (
              <tr key={s.id}>
                <td className="code">{s.order_number}</td><td>{s.process}</td><td>{supName(s.subcontractor_id)}</td>
                <td className="num">{num(s.sent_qty)}</td><td className="num">{num(s.received_qty)}</td>
                <td className="num" style={{ color: s.outstanding_qty > 0 ? "var(--madder)" : "var(--ok)" }}>{num(s.outstanding_qty)}</td>
                <td><Chip status={s.status} /></td>
                <td className="right">{can("stores", "planner") && s.outstanding_qty > 0 && <button className="btn sm" onClick={() => receive(s)}>Receive</button>}</td>
              </tr>
            ))}
            {!orders.data?.length && <tr><td colSpan={8} className="muted">No subcontract orders yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <SubForm suppliers={suppliers.data ?? []} onClose={() => setOpen(false)} onDone={() => { setOpen(false); orders.reload(); }} />}
    </Card>
  );
}

function SubForm({ suppliers, onClose, onDone }: { suppliers: Supplier[]; onClose: () => void; onDone: (s: Subcontract) => void }) {
  const toast = useToast();
  const [f, setF] = useState({ subcontractor_id: 0, process: "Embroidery", sent_qty: "0", rate: "0.25" });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true); setError(null);
    try {
      const s = await api.post<Subcontract>("/production/subcontract-orders", {
        subcontractor_id: Number(f.subcontractor_id), process: f.process, sent_qty: Number(f.sent_qty), rate: f.rate,
      });
      toast.push(`Subcontract ${s.order_number} created`); onDone(s);
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }
  return (
    <Drawer title="New subcontract order" sub="Module 5.6" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !f.subcontractor_id} onClick={save}>Create</button></>}>
      {error && <ErrorBox message={error} />}
      <Field label="Subcontractor" required>
        <select className="select" value={f.subcontractor_id} onChange={(e) => setF({ ...f, subcontractor_id: Number(e.target.value) })}>
          <option value={0}>Select…</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </Field>
      <div className="form-row three">
        <Field label="Process"><input className="input" value={f.process} onChange={(e) => setF({ ...f, process: e.target.value })} /></Field>
        <Field label="Sent qty"><input className="input mono" value={f.sent_qty} onChange={(e) => setF({ ...f, sent_qty: e.target.value })} /></Field>
        <Field label="Rate"><input className="input mono" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} /></Field>
      </div>
    </Drawer>
  );
}
