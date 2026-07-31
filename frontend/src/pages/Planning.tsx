import { useState } from "react";
import { api, ApiError } from "../api/client";
import {
  AtpResponse, CapacityBoard, MrpRun, MrpRunDetail, Style, WorkCentre,
} from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { invalidate, useAsync } from "../lib/useAsync";

const TABS = ["MRP", "Capacity board", "Available to promise", "Work centres"] as const;
type Tab = (typeof TABS)[number];

function iso(daysFromToday: number) {
  const d = new Date();
  d.setDate(d.getDate() + daysFromToday);
  return d.toISOString().slice(0, 10);
}

export default function Planning() {
  const [tab, setTab] = useState<Tab>("MRP");
  return (
    <div>
      <PageHeader
        eyebrow="Module 11"
        title="Planning"
        subtitle="Time-phased material netting, finite capacity, and an honest answer to “can we promise this date?”."
      />
      <div className="inline-actions" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn sm ${tab === t ? "primary" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === "MRP" && <MrpPanel />}
      {tab === "Capacity board" && <CapacityPanel />}
      {tab === "Available to promise" && <AtpPanel />}
      {tab === "Work centres" && <WorkCentrePanel />}
    </div>
  );
}

/* ------------------------------------------------------------------ MRP -- */
function MrpPanel() {
  const { can } = useAuthorization();
  const editable = can("planner", "procurement");
  const toast = useToast();
  const runs = useAsync(() => api.get<MrpRun[]>("/planning/mrp-runs"));
  const [detail, setDetail] = useState<MrpRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ horizon_start: iso(0), horizon_end: iso(90), bucket_days: "7" });
  const [shortagesOnly, setShortagesOnly] = useState(true);

  async function runMrp() {
    setBusy(true); setError(null);
    try {
      await api.post("/planning/mrp-runs", { ...form, bucket_days: Number(form.bucket_days) });
      toast.push("MRP run complete");
      runs.reload();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setBusy(false); }
  }

  async function open(id: number, only = shortagesOnly) {
    setError(null);
    try {
      setDetail(await api.get<MrpRunDetail>(`/planning/mrp-runs/${id}?shortages_only=${only}`));
    } catch (e) { setError((e as ApiError).message); }
  }

  async function firm(id: number) {
    try {
      const r = await api.post<{ requisition_number: string }>(`/planning/mrp-runs/${id}/firm`, { planned_order_ids: [] });
      toast.push(`Requisition ${r.requisition_number} raised`);
      runs.reload(); open(id);
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <>
      <Card title="Run MRP" hint="Nets confirmed demand against stock and open purchase orders, bucket by bucket.">
        <div className="card-pad">
          <div className="form-row three">
            <Field label="Horizon start"><input className="input mono" type="date" value={form.horizon_start} onChange={(e) => setForm({ ...form, horizon_start: e.target.value })} /></Field>
            <Field label="Horizon end"><input className="input mono" type="date" value={form.horizon_end} onChange={(e) => setForm({ ...form, horizon_end: e.target.value })} /></Field>
            <Field label="Bucket (days)"><input className="input mono" value={form.bucket_days} onChange={(e) => setForm({ ...form, bucket_days: e.target.value })} /></Field>
          </div>
          {error && <ErrorBox message={error} />}
          {editable && <button className="btn primary" disabled={busy} onClick={runMrp}>{busy ? "Running…" : "Run MRP"}</button>}
        </div>
      </Card>

      <div className="section-title">Runs</div>
      {runs.loading ? <Spinner /> : !runs.data?.length ? (
        <Card><div className="empty"><div className="big">No MRP runs yet</div>Run the planner to see planned orders.</div></Card>
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr>
                <th>Run</th><th>Horizon</th><th className="num">Materials</th>
                <th className="num">Planned orders</th><th className="num">Past due</th><th>Status</th><th></th>
              </tr></thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.run_number}</td>
                    <td className="muted">{r.horizon_start} → {r.horizon_end} · {r.bucket_days}d</td>
                    <td className="num">{r.material_count}</td>
                    <td className="num">{r.planned_order_count}</td>
                    <td className="num">{r.past_due_count > 0 ? <Chip tone="bad" label={String(r.past_due_count)} /> : 0}</td>
                    <td><Chip status={r.status} /></td>
                    <td className="inline-actions">
                      <button className="btn sm" onClick={() => open(r.id)}>Open</button>
                      <a className="btn sm" href={`/api/planning/mrp-runs/${r.id}/export`}>CSV</a>
                      {editable && r.status === "draft" && <button className="btn sm" onClick={() => firm(r.id)}>Firm all</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {detail && (
        <>
          <div className="section-title">{detail.run_number} — planned orders</div>
          <Card>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr>
                  <th>Material</th><th className="num">Quantity</th><th>Release by</th>
                  <th>Needed</th><th className="num">Lead time</th><th>Status</th>
                </tr></thead>
                <tbody>
                  {detail.planned_orders.map((p) => (
                    <tr key={p.id}>
                      <td>{p.material_code} · <span className="muted">{p.material_name}</span></td>
                      <td className="num">{p.quantity}</td>
                      <td className={p.past_due ? "" : "muted"}>
                        {p.release_date} {p.past_due && <Chip tone="bad" label="past due" />}
                      </td>
                      <td className="muted">{p.need_date}</td>
                      <td className="num">{p.lead_time_days}d</td>
                      <td><Chip status={p.status} /></td>
                    </tr>
                  ))}
                  {!detail.planned_orders.length && <tr><td colSpan={6} className="muted">Demand is fully covered — nothing to buy.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="flex-between" style={{ margin: "18px 0 8px" }}>
            <div className="section-title mt-0" style={{ margin: 0 }}>Time-phased netting</div>
            <label className="flex" style={{ gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={shortagesOnly} onChange={(e) => { setShortagesOnly(e.target.checked); open(detail.id, e.target.checked); }} />
              Shortages only
            </label>
          </div>
          <Card>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr>
                  <th>Material</th><th>Bucket</th><th className="num">Opening</th><th className="num">Gross</th>
                  <th className="num">Receipts</th><th className="num">Net</th><th className="num">Planned</th><th className="num">Projected</th>
                </tr></thead>
                <tbody>
                  {detail.buckets.map((b) => (
                    <tr key={b.id}>
                      <td>{b.material_code}</td>
                      <td className="muted mono">{b.bucket_start}</td>
                      <td className="num">{b.opening_balance}</td>
                      <td className="num">{b.gross_requirement}</td>
                      <td className="num">{b.scheduled_receipts}</td>
                      <td className="num" style={{ fontWeight: parseFloat(b.net_requirement) > 0 ? 600 : 400 }}>{b.net_requirement}</td>
                      <td className="num">{b.planned_order_qty}</td>
                      <td className="num">{b.projected_available}</td>
                    </tr>
                  ))}
                  {!detail.buckets.length && <tr><td colSpan={8} className="muted">No buckets match this filter.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------- capacity -- */
function CapacityPanel() {
  const [range, setRange] = useState({ horizon_start: iso(0), horizon_end: iso(56), bucket_days: "7" });
  const board = useAsync(
    () => api.get<CapacityBoard>(
      `/planning/capacity-board?horizon_start=${range.horizon_start}&horizon_end=${range.horizon_end}&bucket_days=${range.bucket_days}`
    ),
    [range.horizon_start, range.horizon_end, range.bucket_days]
  );

  return (
    <>
      <Card title="Load versus capacity" hint="Capacity is operators × shift minutes × efficiency, less any exception for the day.">
        <div className="card-pad">
          <div className="form-row three">
            <Field label="From"><input className="input mono" type="date" value={range.horizon_start} onChange={(e) => setRange({ ...range, horizon_start: e.target.value })} /></Field>
            <Field label="To"><input className="input mono" type="date" value={range.horizon_end} onChange={(e) => setRange({ ...range, horizon_end: e.target.value })} /></Field>
            <Field label="Bucket (days)"><input className="input mono" value={range.bucket_days} onChange={(e) => setRange({ ...range, bucket_days: e.target.value })} /></Field>
          </div>
        </div>
      </Card>

      {board.loading ? <Spinner /> : !board.data?.centres.length ? (
        <Card><div className="empty"><div className="big">No work centres</div>Add one under “Work centres”.</div></Card>
      ) : board.data.centres.map((c) => (
        <div key={c.work_centre_id} style={{ marginBottom: 16 }}>
          <div className="section-title">{c.code} — {c.name}</div>
          <Card actions={c.overloaded_buckets > 0
            ? <Chip tone="bad" label={`${c.overloaded_buckets} overloaded`} />
            : <Chip tone="ok" label="within capacity" />}>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr>
                  <th>Bucket</th><th className="num">Capacity (min)</th><th className="num">Loaded</th>
                  <th className="num">Free</th><th className="num">Utilisation</th><th>Load</th>
                </tr></thead>
                <tbody>
                  {c.buckets.map((b) => {
                    const pct = Math.min(200, parseFloat(b.utilisation_pct));
                    return (
                      <tr key={b.bucket_start}>
                        <td className="mono muted">{b.bucket_start} → {b.bucket_end}</td>
                        <td className="num">{b.capacity_minutes}</td>
                        <td className="num">{b.loaded_minutes}</td>
                        <td className="num">{b.available_minutes}</td>
                        <td className="num">{b.utilisation_pct}%</td>
                        <td style={{ minWidth: 160 }}>
                          <div style={{ background: "var(--rule)", borderRadius: 3, height: 8, overflow: "hidden" }}>
                            <div style={{
                              width: `${Math.min(100, pct)}%`, height: "100%",
                              background: b.overloaded ? "var(--madder, #a33)" : "var(--indigo-deep, #35c)",
                            }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ ATP -- */
function AtpPanel() {
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const [form, setForm] = useState({ style_id: "", quantity: "1000", wanted_date: iso(45) });
  const [result, setResult] = useState<AtpResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setError(null);
    try {
      setResult(await api.post<AtpResponse>("/planning/atp", {
        style_id: Number(form.style_id), quantity: Number(form.quantity), wanted_date: form.wanted_date,
      }));
    } catch (e) { setError((e as ApiError).message); setResult(null); }
  }

  return (
    <>
      <Card title="Can we promise it?" hint="Checks material availability and finite capacity, and names whichever one binds.">
        <div className="card-pad">
          <div className="form-row three">
            <Field label="Style">
              <select className="select" value={form.style_id} onChange={(e) => setForm({ ...form, style_id: e.target.value })}>
                <option value="">Select…</option>
                {styles.data?.map((s) => <option key={s.id} value={s.id}>{s.style_number} — {s.description}</option>)}
              </select>
            </Field>
            <Field label="Quantity"><input className="input mono" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>
            <Field label="Wanted by"><input className="input mono" type="date" value={form.wanted_date} onChange={(e) => setForm({ ...form, wanted_date: e.target.value })} /></Field>
          </div>
          {error && <ErrorBox message={error} />}
          <button className="btn primary" disabled={!form.style_id} onClick={check}>Check</button>
        </div>
      </Card>

      {result && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div className={`card stat ${result.can_promise ? "accent" : "accent-madder"}`}>
              <div className="k">Verdict</div>
              <div className="v" style={{ fontSize: 20 }}>{result.can_promise ? "Can promise" : "Cannot promise"}</div>
            </div>
            <div className="card stat"><div className="k">Earliest date</div><div className="v" style={{ fontSize: 20 }}>{result.promise_date || "—"}</div></div>
            <div className="card stat"><div className="k">Limiting factor</div><div className="v" style={{ fontSize: 20 }}>{result.limiting_factor}</div></div>
            <div className="card stat"><div className="k">Minutes needed</div><div className="v" style={{ fontSize: 20 }}>{result.required_minutes}</div></div>
          </div>

          <Card title="Material position">
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr>
                  <th>Material</th><th className="num">Required</th><th className="num">Available</th>
                  <th className="num">Shortfall</th><th className="num">Lead time</th><th>Earliest</th>
                </tr></thead>
                <tbody>
                  {result.materials.map((m) => (
                    <tr key={m.material_id}>
                      <td>{m.material_code} · <span className="muted">{m.material_name}</span></td>
                      <td className="num">{m.required_qty}</td>
                      <td className="num">{m.available_qty}</td>
                      <td className="num">{parseFloat(m.shortfall_qty) > 0
                        ? <Chip tone="bad" label={m.shortfall_qty} /> : m.shortfall_qty}</td>
                      <td className="num">{m.lead_time_days}d</td>
                      <td className="muted">{m.earliest_available || "in stock"}</td>
                    </tr>
                  ))}
                  {!result.materials.length && <tr><td colSpan={6} className="muted">No BOM to explode.</td></tr>}
                </tbody>
              </table>
            </div>
            {result.notes.length > 0 && (
              <div className="card-pad">
                {result.notes.map((n, i) => <div key={i} className="muted" style={{ fontSize: 13 }}>· {n}</div>)}
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}

/* --------------------------------------------------------- work centres -- */
function WorkCentrePanel() {
  const { can } = useAuthorization();
  const editable = can("planner");
  const toast = useToast();
  const centres = useAsync(() => api.get<WorkCentre[]>("/planning/work-centres?active_only=false"));
  const [form, setForm] = useState({
    code: "", name: "", centre_type: "sewing", operators: "25",
    shift_minutes: "480", shifts_per_day: "1", efficiency_pct: "80",
  });
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setError(null);
    try {
      await api.post("/planning/work-centres", {
        ...form,
        operators: Number(form.operators),
        shift_minutes: Number(form.shift_minutes),
        shifts_per_day: Number(form.shifts_per_day),
      });
      toast.push("Work centre created");
      // Other pages (shop floor) read the active-only list under a different
      // key; clear the whole prefix so they do not serve a stale roster.
      invalidate("/planning/work-centres");
      setForm({ ...form, code: "", name: "" });
      centres.reload();
    } catch (e) { const m = (e as ApiError).message; setError(m); }
  }

  return (
    <>
      {editable && (
        <Card title="New work centre" hint="Daily capacity = operators × shift minutes × shifts × efficiency.">
          <div className="card-pad">
            <div className="form-row three">
              <Field label="Code"><input className="input mono" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="SEW-1" /></Field>
              <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Type">
                <select className="select" value={form.centre_type} onChange={(e) => setForm({ ...form, centre_type: e.target.value })}>
                  {["cutting", "sewing", "finishing", "packing", "embroidery", "washing"].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </Field>
            </div>
            <div className="form-row three">
              <Field label="Operators"><input className="input mono" value={form.operators} onChange={(e) => setForm({ ...form, operators: e.target.value })} /></Field>
              <Field label="Shift minutes"><input className="input mono" value={form.shift_minutes} onChange={(e) => setForm({ ...form, shift_minutes: e.target.value })} /></Field>
              <Field label="Efficiency %"><input className="input mono" value={form.efficiency_pct} onChange={(e) => setForm({ ...form, efficiency_pct: e.target.value })} /></Field>
            </div>
            {error && <ErrorBox message={error} />}
            <button className="btn primary" disabled={!form.code || !form.name} onClick={create}>Create</button>
          </div>
        </Card>
      )}

      <div className="section-title">Work centres</div>
      {centres.loading ? <Spinner /> : !centres.data?.length ? (
        <Card><div className="empty"><div className="big">None yet</div>Capacity planning needs at least one.</div></Card>
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr>
                <th>Code</th><th>Name</th><th>Type</th><th className="num">Operators</th>
                <th className="num">Shift</th><th className="num">Efficiency</th><th className="num">Daily minutes</th><th></th>
              </tr></thead>
              <tbody>
                {centres.data.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">{c.code}</td>
                    <td>{c.name}</td>
                    <td><Chip tone="neutral" label={c.centre_type} /></td>
                    <td className="num">{c.operators}</td>
                    <td className="num">{c.shift_minutes} × {c.shifts_per_day}</td>
                    <td className="num">{c.efficiency_pct}%</td>
                    <td className="num" style={{ fontWeight: 600 }}>{c.daily_minutes}</td>
                    <td>{c.active ? <Chip tone="ok" label="active" /> : <Chip tone="neutral" label="inactive" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
