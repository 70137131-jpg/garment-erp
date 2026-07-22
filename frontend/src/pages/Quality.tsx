import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Final, Roll, SalesOrder } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { num, qty } from "../lib/format";

export default function Quality() {
  const [tab, setTab] = useState("fourpoint");
  return (
    <div>
      <PageHeader
        eyebrow="Module 7"
        title="Quality"
        subtitle="Four-point inspection gates incoming rolls; inline DHU tracks the line; the AQL final inspection authorises shipment."
      />
      <Tabs
        tabs={[
          { key: "fourpoint", label: "Four-Point (Incoming)" },
          { key: "inline", label: "Inline DHU" },
          { key: "final", label: "Final AQL" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "fourpoint" && <FourPoint />}
      {tab === "inline" && <Inline />}
      {tab === "final" && <FinalAql />}
    </div>
  );
}

/* ---------------- Four-point ---------------- */
function FourPoint() {
  const rolls = useAsync(() => api.get<Roll[]>("/inventory/rolls?status=pending_inspection"));
  const [target, setTarget] = useState<Roll | null>(null);
  return (
    <Card title="Rolls pending inspection" hint="four-point system · ≤ 40 pts/100yd² passes">
      {rolls.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Roll №</th><th>Dye lot</th><th>Shade</th><th>Grade</th><th className="num">Length</th><th className="num">Width</th><th></th></tr></thead>
            <tbody>
              {rolls.data?.map((r) => (
                <tr key={r.id}>
                  <td className="code">{r.roll_number}</td>
                  <td className="mono muted">{r.dye_lot || "—"}</td>
                  <td className="mono muted">{r.shade_group || "—"}</td>
                  <td><Chip tone="neutral" label={r.grade} /></td>
                  <td className="num">{qty(r.length)} m</td>
                  <td className="num">{r.width_cm ? qty(r.width_cm) : "—"}</td>
                  <td className="right"><button className="btn primary sm" onClick={() => setTarget(r)}>Inspect</button></td>
                </tr>
              ))}
              {!rolls.data?.length && <tr><td colSpan={7} className="muted">No rolls awaiting inspection.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {target && <FourPointForm roll={target} onClose={() => setTarget(null)} onDone={() => { setTarget(null); rolls.reload(); }} />}
    </Card>
  );
}

function FourPointForm({ roll, onClose, onDone }: { roll: Roll; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [defects, setDefects] = useState<{ penalty_points: number; description: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true); setError(null);
    try {
      const res = await api.post<any>("/quality/four-point-inspections", {
        roll_id: roll.id,
        defects: defects.map((d) => ({ penalty_points: d.penalty_points, description: d.description || null })),
      });
      toast.push(`Roll ${res.result === "passed" ? "approved" : "quarantined"}`, { detail: `${res.points_per_100sqyd} pts/100yd²`, bad: res.result !== "passed" });
      onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  const total = defects.reduce((a, d) => a + d.penalty_points, 0);

  return (
    <Drawer title={`Inspect ${roll.roll_number}`} sub="Module 7.1 · four-point" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving} onClick={save}>Record inspection</button></>}>
      {error && <ErrorBox message={error} />}
      <dl className="kv" style={{ marginBottom: 14 }}>
        <dt>Length</dt><dd>{qty(roll.length)} m</dd>
        <dt>Width</dt><dd>{roll.width_cm ? `${qty(roll.width_cm)} cm` : "—"}</dd>
        <dt>Total points</dt><dd className="mono">{total}</dd>
      </dl>
      <div className="flex-between" style={{ marginBottom: 10 }}>
        <span className="mono muted" style={{ fontSize: 11 }}>DEFECTS (1–4 pts each)</span>
        <button className="btn sm" onClick={() => setDefects([...defects, { penalty_points: 1, description: "" }])}>+ Add defect</button>
      </div>
      {defects.map((d, i) => (
        <div className="form-row two" key={i} style={{ marginBottom: 8, alignItems: "end" }}>
          <Field label="Description"><input className="input" value={d.description} onChange={(e) => setDefects(defects.map((x, idx) => idx === i ? { ...x, description: e.target.value } : x))} placeholder="Slub / hole / stain" /></Field>
          <Field label="Points">
            <select className="select" value={d.penalty_points} onChange={(e) => setDefects(defects.map((x, idx) => idx === i ? { ...x, penalty_points: Number(e.target.value) } : x))}>
              <option value={1}>1</option><option value={2}>2</option><option value={3}>3</option><option value={4}>4</option>
            </select>
          </Field>
        </div>
      ))}
      {!defects.length && <div className="hintline">No defects — a clean roll passes automatically.</div>}
    </Drawer>
  );
}

/* ---------------- Inline DHU ---------------- */
function Inline() {
  const toast = useToast();
  const [f, setF] = useState({ sewing_order_id: "", units_checked: "100", defects_found: "3" });
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setError(null);
    try {
      const r = await api.post<any>("/quality/inline-inspections", {
        sewing_order_id: Number(f.sewing_order_id), units_checked: Number(f.units_checked), defects_found: Number(f.defects_found),
      });
      setResult(r); toast.push(`DHU ${r.dhu} recorded`);
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
  }
  return (
    <div className="grid cols-2">
      <Card title="Record inline inspection" hint="DHU = defects ÷ units × 100">
        <div className="card-pad">
          {error && <ErrorBox message={error} />}
          <Field label="Sewing order id" required><input className="input mono" value={f.sewing_order_id} onChange={(e) => setF({ ...f, sewing_order_id: e.target.value })} placeholder="e.g. 1" /></Field>
          <div className="form-row two">
            <Field label="Units checked"><input className="input mono" value={f.units_checked} onChange={(e) => setF({ ...f, units_checked: e.target.value })} /></Field>
            <Field label="Defects found"><input className="input mono" value={f.defects_found} onChange={(e) => setF({ ...f, defects_found: e.target.value })} /></Field>
          </div>
          <button className="btn primary" disabled={!f.sewing_order_id} onClick={save}>Record DHU</button>
        </div>
      </Card>
      <Card title="Latest result">
        <div className="card-pad">
          {!result ? <div className="empty"><div className="big">No inspection yet</div></div> : (
            <dl className="kv">
              <dt>Inspection</dt><dd className="mono">{result.inspection_number}</dd>
              <dt>Units</dt><dd>{num(result.units_checked)}</dd>
              <dt>Defects</dt><dd>{num(result.defects_found)}</dd>
              <dt>DHU</dt><dd className="mono" style={{ fontWeight: 600 }}>{result.dhu}</dd>
            </dl>
          )}
        </div>
      </Card>
    </div>
  );
}

/* ---------------- Final AQL ---------------- */
function FinalAql() {
  const orders = useAsync(() => api.get<SalesOrder[]>("/sales-orders"));
  const toast = useToast();
  const [orderId, setOrderId] = useState(0);
  const [aql, setAql] = useState("2.5");
  const [defects, setDefects] = useState("0");
  const [result, setResult] = useState<Final | null>(null);
  const [error, setError] = useState<string | null>(null);
  const order = orders.data?.find((o) => o.id === orderId);

  async function save() {
    setError(null);
    try {
      const r = await api.post<Final>("/quality/final-inspections", {
        sales_order_id: orderId, lot_size: order?.total_quantity ?? 0, aql, defects_found: Number(defects),
      });
      setResult(r);
      toast.push(`Final inspection ${r.result}`, { bad: r.result !== "passed" });
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
  }

  return (
    <div className="grid cols-2">
      <Card title="Final inspection" hint="ANSI/ASQ Z1.4 · Level II">
        <div className="card-pad">
          {error && <ErrorBox message={error} />}
          <Field label="Sales order" required>
            <select className="select" value={orderId} onChange={(e) => { setOrderId(Number(e.target.value)); setResult(null); }}>
              <option value={0}>Select…</option>
              {orders.data?.map((o) => <option key={o.id} value={o.id}>{o.order_number} · {o.total_quantity} pcs</option>)}
            </select>
          </Field>
          <div className="form-row two">
            <Field label="AQL">
              <select className="select" value={aql} onChange={(e) => setAql(e.target.value)}>
                <option value="1.0">1.0</option><option value="1.5">1.5</option><option value="2.5">2.5</option><option value="4.0">4.0</option>
              </select>
            </Field>
            <Field label="Defects found"><input className="input mono" value={defects} onChange={(e) => setDefects(e.target.value)} /></Field>
          </div>
          <button className="btn primary" disabled={!orderId} onClick={save}>Run inspection</button>
        </div>
      </Card>
      <Card title="Sampling result">
        <div className="card-pad">
          {!result ? <div className="empty"><div className="big">No result yet</div>Pick a lot and run the AQL plan.</div> : (
            <>
              <div style={{ marginBottom: 12 }}>
                <Chip tone={result.result === "passed" ? "ok" : "bad"} label={result.result === "passed" ? "Accept lot" : "Reject lot"} />
              </div>
              <dl className="kv">
                <dt>Inspection</dt><dd className="mono">{result.inspection_number}</dd>
                <dt>Lot size</dt><dd>{num(result.lot_size)}</dd>
                <dt>Sample size</dt><dd className="mono">{result.sample_size}</dd>
                <dt>Accept ≤</dt><dd className="mono">{result.accept_number}</dd>
                <dt>Defects found</dt><dd className="mono">{result.defects_found}</dd>
              </dl>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
