import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Colour, CutOrder, SewingOrder, SizeRange, Style, Subcontract, Supplier } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
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
      <Tabs tabs={[{ key: "cut", label: "Cut Orders" }, { key: "sew", label: "Sewing" }, { key: "sub", label: "Subcontract" }]} active={tab} onChange={setTab} />
      {tab === "cut" && <CutOrders />}
      {tab === "sew" && <Sewing />}
      {tab === "sub" && <Subcontracts />}
    </div>
  );
}

/* ---------------- Cut orders ---------------- */
function CutOrders() {
  const cuts = useAsync(() => api.get<CutOrder[]>("/production/cut-orders"));
  const styles = useAsync(() => api.get<Style[]>("/styles"));
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
    <Card title="Cut orders" actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New cut order</button>}>
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
                      {c.status === "planned" && <button className="btn sm" onClick={() => act(c, "reserve")}>Reserve</button>}
                      {(c.status === "fabric_reserved" || c.status === "planned") && <button className="btn sm" onClick={() => act(c, "issue")}>Issue</button>}
                      {c.status === "in_cutting" && <button className="btn sm" onClick={() => act(c, "complete")}>Complete</button>}
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
  const styles = useAsync(() => api.get<Style[]>("/styles"));
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
  // The backend exposes create + single-GET (no list); we hold the session's
  // orders locally and re-fetch a single order after recording output.
  const [items, setItems] = useState<SewingOrder[]>([]);
  const styles = useAsync(() => api.get<Style[]>("/styles"));
  const [open, setOpen] = useState(false);
  const [output, setOutput] = useState<SewingOrder | null>(null);
  const styleNo = (id: number) => styles.data?.find((s) => s.id === id)?.style_number ?? `#${id}`;

  async function refresh(id: number) {
    try {
      const fresh = await api.get<SewingOrder>(`/production/sewing-orders/${id}`);
      setItems((xs) => xs.map((x) => (x.id === id ? fresh : x)));
    } catch { /* ignore */ }
  }

  return (
    <Card title="Sewing orders" hint="SAM-based line efficiency"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New sewing order</button>}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Sew №</th><th>Style</th><th>Line</th><th>Status</th><th className="num">Planned</th><th className="num">Produced</th><th className="num">Avg eff.</th><th></th></tr></thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id}>
                <td className="code">{s.order_number}</td>
                <td>{styleNo(s.style_id)}</td>
                <td>{s.line || "—"}</td>
                <td><Chip status={s.status} /></td>
                <td className="num">{num(s.planned_qty)}</td>
                <td className="num">{num(s.produced_qty)}</td>
                <td className="num">{pct(s.average_efficiency_pct)}</td>
                <td className="right"><button className="btn sm" onClick={() => setOutput(s)}>+ Output</button></td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan={8} className="muted">No sewing orders in view. Create one to begin.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <SewForm styles={styles.data ?? []} onClose={() => setOpen(false)} onDone={(o) => { setOpen(false); if (o) setItems([o, ...items]); }} />}
      {output && <OutputForm sewing={output} onClose={() => setOutput(null)} onDone={() => { const id = output.id; setOutput(null); refresh(id); }} />}
    </Card>
  );
}

function SewForm({ styles, onClose, onDone }: { styles: Style[]; onClose: () => void; onDone: (o?: SewingOrder) => void }) {
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
      toast.push(`Sewing order ${o.order_number} created`); onDone(o);
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

/* ---------------- Subcontract ---------------- */
function Subcontracts() {
  const [items, setItems] = useState<Subcontract[]>([]);
  const suppliers = useAsync(() => api.get<Supplier[]>("/masters/suppliers"));
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const supName = (id: number) => suppliers.data?.find((s) => s.id === id)?.name ?? `#${id}`;

  async function receive(sc: Subcontract) {
    const val = prompt(`Receive how many from ${sc.order_number}? (outstanding ${sc.outstanding_qty})`);
    if (!val) return;
    try {
      const upd = await api.post<Subcontract>(`/production/subcontract-orders/${sc.id}/receive`, { received_qty: Number(val) });
      setItems(items.map((x) => (x.id === upd.id ? upd : x)));
      toast.push("Receipt reconciled");
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <Card title="Subcontract orders" hint="outstanding balance tracked"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New subcontract</button>}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Order</th><th>Process</th><th>Subcontractor</th><th className="num">Sent</th><th className="num">Received</th><th className="num">Outstanding</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id}>
                <td className="code">{s.order_number}</td><td>{s.process}</td><td>{supName(s.subcontractor_id)}</td>
                <td className="num">{num(s.sent_qty)}</td><td className="num">{num(s.received_qty)}</td>
                <td className="num" style={{ color: s.outstanding_qty > 0 ? "var(--madder)" : "var(--ok)" }}>{num(s.outstanding_qty)}</td>
                <td><Chip status={s.status} /></td>
                <td className="right">{s.outstanding_qty > 0 && <button className="btn sm" onClick={() => receive(s)}>Receive</button>}</td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan={8} className="muted">No subcontract orders in view. Create one to begin.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <SubForm suppliers={suppliers.data ?? []} onClose={() => setOpen(false)} onDone={(sc) => { setOpen(false); setItems([sc, ...items]); }} />}
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
