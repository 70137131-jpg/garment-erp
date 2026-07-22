import { useState } from "react";
import { api, ApiError } from "../api/client";
import { CostSheet, Style } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, pct } from "../lib/format";

const CATEGORIES = ["material", "trim", "sewing", "overhead", "other"];

export default function Costing() {
  const styles = useAsync(() => api.get<Style[]>("/styles"));
  const [styleId, setStyleId] = useState(0);
  const sheets = useAsync(
    () => (styleId ? api.get<CostSheet[]>(`/costing/styles/${styleId}/cost-sheets`) : Promise.resolve([] as CostSheet[])),
    [styleId]
  );
  const [open, setOpen] = useState(false);
  const toast = useToast();

  async function approve(id: number) {
    try {
      await api.post(`/costing/cost-sheets/${id}/approve`);
      toast.push("Cost sheet approved");
      sheets.reload();
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Module 6"
        title="Costing"
        subtitle="Versioned cost sheets build material + SAM sewing + overhead into a margin and price; order profitability reads back against operational value."
        actions={
          <div className="inline-actions">
            <select className="select" style={{ width: "auto" }} value={styleId} onChange={(e) => setStyleId(Number(e.target.value))}>
              <option value={0}>Select a style…</option>
              {styles.data?.map((s) => <option key={s.id} value={s.id}>{s.style_number} — {s.description}</option>)}
            </select>
            {styleId ? <button className="btn primary" onClick={() => setOpen(true)}>+ New cost sheet</button> : null}
          </div>
        }
      />

      {!styleId ? (
        <Card><div className="empty"><div className="big">Choose a style</div>Cost sheets are versioned per style.</div></Card>
      ) : sheets.loading ? <Spinner /> : !sheets.data?.length ? (
        <Card><div className="empty"><div className="big">No cost sheets yet</div>Create the first version for this style.</div></Card>
      ) : (
        <div className="grid" style={{ gap: 16 }}>
          {[...sheets.data].reverse().map((cs) => (
            <Card key={cs.id} title={`Version ${cs.version_no}`} hint={cs.currency}
              actions={<div className="inline-actions"><Chip status={cs.status} />{cs.status === "draft" && <button className="btn sm" onClick={() => approve(cs.id)}>Approve</button>}</div>}>
              <div className="grid cols-2" style={{ padding: 18, gap: 20 }}>
                <div className="table-wrap">
                  <table className="tbl">
                    <thead><tr><th>Line</th><th>Category</th><th className="num">Qty</th><th className="num">Rate</th><th className="num">Amount</th></tr></thead>
                    <tbody>
                      {cs.lines.map((l) => (
                        <tr key={l.id}>
                          <td>{l.description || "—"}</td>
                          <td><Chip tone="neutral" label={l.category} /></td>
                          <td className="num">{l.quantity}</td>
                          <td className="num">{money(l.rate, cs.currency)}</td>
                          <td className="num">{money(l.amount, cs.currency)}</td>
                        </tr>
                      ))}
                      {!cs.lines.length && <tr><td colSpan={5} className="muted">No lines.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <div>
                  <dl className="kv">
                    <dt>Material</dt><dd>{money(cs.material_cost, cs.currency)}</dd>
                    <dt>Sewing (SAM)</dt><dd>{money(cs.sewing_cost, cs.currency)}</dd>
                    <dt>Overhead</dt><dd>{money(cs.overhead_cost, cs.currency)}</dd>
                    <dt style={{ fontWeight: 700 }}>Total cost</dt><dd style={{ fontWeight: 700 }}>{money(cs.total_cost, cs.currency)}</dd>
                    <dt>Margin</dt><dd>{pct((parseFloat(cs.margin_pct) * 100).toString())} · {money(cs.margin_amount, cs.currency)}</dd>
                    <dt style={{ color: "var(--indigo-deep)" }}>Selling price</dt>
                    <dd style={{ fontWeight: 700, color: "var(--indigo-deep)", fontSize: 16 }}>{money(cs.selling_price, cs.currency)}</dd>
                  </dl>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Profitability />

      {open && styleId ? <SheetForm styleId={styleId} onClose={() => setOpen(false)} onDone={() => { setOpen(false); sheets.reload(); }} /> : null}
    </div>
  );
}

function Profitability() {
  const [orderId, setOrderId] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    setError(null);
    try {
      const r = await api.get<any>(`/costing/sales-orders/${Number(orderId)}/profitability`);
      setData(r);
    } catch (e) { setError((e as ApiError).message); setData(null); }
  }
  return (
    <>
      <div className="section-title">Order profitability</div>
      <Card>
        <div className="card-pad">
          <div className="flex" style={{ marginBottom: error || data ? 16 : 0 }}>
            <input className="input mono" style={{ maxWidth: 200 }} value={orderId} onChange={(e) => setOrderId(e.target.value)} placeholder="Sales order id" />
            <button className="btn" disabled={!orderId} onClick={load}>Compute</button>
          </div>
          {error && <ErrorBox message={error} />}
          {data && (
            <div className="grid cols-4">
              <div className="card stat accent"><div className="k">Revenue</div><div className="v" style={{ fontSize: 22 }}>{money(data.revenue)}</div></div>
              <div className="card stat"><div className="k">Total cost</div><div className="v" style={{ fontSize: 22 }}>{money(data.total_cost)}</div></div>
              <div className="card stat accent-madder"><div className="k">Profit</div><div className="v" style={{ fontSize: 22 }}>{money(data.profit)}</div></div>
              <div className="card stat"><div className="k">Margin</div><div className="v" style={{ fontSize: 22 }}>{pct(data.margin_pct)}</div></div>
            </div>
          )}
        </div>
      </Card>
    </>
  );
}

function SheetForm({ styleId, onClose, onDone }: { styleId: number; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ base_size: "M", sam: "12.5", sewing_cost_per_min: "0.08", sewing_efficiency_pct: "80", overhead_pct: "0.15", margin_pct: "0.20" });
  const [lines, setLines] = useState([{ category: "material", description: "Shell fabric", quantity: "1.2", rate: "4.00", wastage_pct: "0.05" }]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: string) => setF({ ...f, [k]: v });

  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post(`/costing/styles/${styleId}/cost-sheets`, { ...f, lines });
      toast.push("Cost sheet created"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <Drawer title="New cost sheet" sub="Module 6.1" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving} onClick={save}>Create draft</button></>}>
      {error && <ErrorBox message={error} />}
      <div className="form-row two">
        <Field label="Base size"><input className="input" value={f.base_size} onChange={(e) => set("base_size", e.target.value)} /></Field>
        <Field label="SAM (min)"><input className="input mono" value={f.sam} onChange={(e) => set("sam", e.target.value)} /></Field>
      </div>
      <div className="form-row two">
        <Field label="Sewing cost/min"><input className="input mono" value={f.sewing_cost_per_min} onChange={(e) => set("sewing_cost_per_min", e.target.value)} /></Field>
        <Field label="Sewing efficiency %"><input className="input mono" value={f.sewing_efficiency_pct} onChange={(e) => set("sewing_efficiency_pct", e.target.value)} /></Field>
      </div>
      <div className="form-row two">
        <Field label="Overhead" hint="fraction e.g. 0.15"><input className="input mono" value={f.overhead_pct} onChange={(e) => set("overhead_pct", e.target.value)} /></Field>
        <Field label="Margin" hint="fraction e.g. 0.20"><input className="input mono" value={f.margin_pct} onChange={(e) => set("margin_pct", e.target.value)} /></Field>
      </div>
      <div className="flex-between" style={{ margin: "6px 0 10px" }}>
        <div className="section-title mt-0" style={{ margin: 0 }}>Cost lines</div>
        <button className="btn sm" onClick={() => setLines([...lines, { category: "material", description: "", quantity: "0", rate: "0", wastage_pct: "0" }])}>+ Add line</button>
      </div>
      {lines.map((l, i) => (
        <div className="card" key={i} style={{ marginBottom: 8, padding: 12 }}>
          <div className="form-row two">
            <Field label="Category">
              <select className="select" value={l.category} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, category: e.target.value } : x))}>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="Description"><input className="input" value={l.description} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, description: e.target.value } : x))} /></Field>
          </div>
          <div className="form-row three">
            <Field label="Qty"><input className="input mono" value={l.quantity} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, quantity: e.target.value } : x))} /></Field>
            <Field label="Rate"><input className="input mono" value={l.rate} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, rate: e.target.value } : x))} /></Field>
            <Field label="Wastage"><input className="input mono" value={l.wastage_pct} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, wastage_pct: e.target.value } : x))} /></Field>
          </div>
        </div>
      ))}
    </Drawer>
  );
}
