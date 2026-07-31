import { useState } from "react";
import { api, ApiError } from "../api/client";
import {
  AndonBoard, DowntimeReason, OeeSummary, ShiftLog, WorkCentre,
} from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";

const TABS = ["Andon", "Shift logs", "OEE"] as const;
type Tab = (typeof TABS)[number];

function today() { return new Date().toISOString().slice(0, 10); }
function daysAgo(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}
/** OEE is conventionally read against 85% world-class / 60% typical. */
function oeeTone(pct: number): "ok" | "warn" | "bad" {
  if (pct >= 85) return "ok";
  if (pct >= 60) return "warn";
  return "bad";
}

export default function ShopFloor() {
  const [tab, setTab] = useState<Tab>("Andon");
  return (
    <div>
      <PageHeader
        eyebrow="Module 12"
        title="Shop floor"
        subtitle="Shift execution, coded downtime, and OEE — availability × performance × quality, with planned stoppages out of the denominator."
      />
      <div className="inline-actions" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn sm ${tab === t ? "primary" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === "Andon" && <AndonPanel />}
      {tab === "Shift logs" && <ShiftLogPanel />}
      {tab === "OEE" && <OeePanel />}
    </div>
  );
}

function AndonPanel() {
  const board = useAsync(() => api.get<AndonBoard>("/mes/andon"));
  if (board.loading) return <Spinner />;
  if (!board.data?.lines.length) {
    return <Card><div className="empty"><div className="big">No work centres</div>Add them under Planning.</div></Card>;
  }
  return (
    <div className="grid cols-3" style={{ gap: 14 }}>
      {board.data.lines.map((l) => {
        const pct = parseFloat(l.today_oee_pct);
        return (
          <Card key={l.work_centre_id}>
            <div className="card-pad">
              <div className="flex-between" style={{ marginBottom: 10 }}>
                <strong className="mono">{l.code}</strong>
                <Chip
                  tone={l.status === "running" ? "ok" : l.status === "stopped" ? "bad" : "neutral"}
                  label={l.status}
                />
              </div>
              <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{l.name}</div>
              <div style={{ fontSize: 30, fontWeight: 600 }}>
                {pct.toFixed(1)}<span style={{ fontSize: 16 }}>%</span>
              </div>
              <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>OEE today</div>
              <div style={{ background: "var(--rule)", borderRadius: 3, height: 6, overflow: "hidden" }}>
                <div style={{
                  width: `${Math.min(100, pct)}%`, height: "100%",
                  background: pct >= 85 ? "var(--indigo-deep, #35c)" : pct >= 60 ? "#c90" : "var(--madder, #a33)",
                }} />
              </div>
              <dl className="kv" style={{ marginTop: 12 }}>
                <dt>Open logs</dt><dd>{l.open_logs}</dd>
                <dt>Downtime</dt><dd>{l.today_downtime_minutes} min</dd>
                <dt>Top stop</dt><dd>{l.top_downtime_reason || "—"}</dd>
              </dl>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function ShiftLogPanel() {
  const { can } = useAuthorization();
  const editable = can("sewing_supervisor", "cutting_supervisor", "planner");
  const toast = useToast();
  const centres = useAsync(() => api.get<WorkCentre[]>("/planning/work-centres"), [], "/planning/work-centres");
  const reasons = useAsync(() => api.get<DowntimeReason[]>("/mes/downtime-reasons"), [], "/mes/downtime-reasons");
  const logs = useAsync(() => api.get<ShiftLog[]>("/mes/shift-logs?limit=50"));
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    work_centre_id: "", log_date: today(), shift: "A",
    operators: "25", planned_minutes: "480", ideal_cycle_seconds: "42",
  });
  const [counts, setCounts] = useState<Record<number, { total: string; good: string; reject: string }>>({});
  const [downtime, setDowntime] = useState<Record<number, { reason: string; minutes: string }>>({});

  async function act(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    try { await fn(); toast.push(ok); logs.reload(); }
    catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
  }

  return (
    <>
      {editable && (
        <Card title="Open a shift log">
          <div className="card-pad">
            <div className="form-row three">
              <Field label="Work centre">
                <select className="select" value={form.work_centre_id} onChange={(e) => setForm({ ...form, work_centre_id: e.target.value })}>
                  <option value="">Select…</option>
                  {centres.data?.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                </select>
              </Field>
              <Field label="Date"><input className="input mono" type="date" value={form.log_date} onChange={(e) => setForm({ ...form, log_date: e.target.value })} /></Field>
              <Field label="Shift"><input className="input mono" value={form.shift} onChange={(e) => setForm({ ...form, shift: e.target.value })} /></Field>
            </div>
            <div className="form-row three">
              <Field label="Operators"><input className="input mono" value={form.operators} onChange={(e) => setForm({ ...form, operators: e.target.value })} /></Field>
              <Field label="Planned minutes"><input className="input mono" value={form.planned_minutes} onChange={(e) => setForm({ ...form, planned_minutes: e.target.value })} /></Field>
              <Field label="Ideal cycle (s/pc)" hint="rated seconds per piece"><input className="input mono" value={form.ideal_cycle_seconds} onChange={(e) => setForm({ ...form, ideal_cycle_seconds: e.target.value })} /></Field>
            </div>
            {error && <ErrorBox message={error} />}
            <button className="btn primary" disabled={!form.work_centre_id}
              onClick={() => act(() => api.post("/mes/shift-logs", {
                ...form, work_centre_id: Number(form.work_centre_id), operators: Number(form.operators),
              }), "Shift log opened")}>Open shift</button>
          </div>
        </Card>
      )}

      <div className="section-title">Shift logs</div>
      {logs.loading ? <Spinner /> : !logs.data?.length ? (
        <Card><div className="empty"><div className="big">No shift logs</div>Open one to start recording.</div></Card>
      ) : logs.data.map((log) => {
        const c = counts[log.id] || { total: String(log.total_count), good: String(log.good_count), reject: String(log.reject_count) };
        const d = downtime[log.id] || { reason: "", minutes: "" };
        const pct = parseFloat(log.oee_pct);
        return (
          <Card key={log.id} title={`${log.log_number} · ${log.work_centre_code} · ${log.log_date} shift ${log.shift}`}
            actions={<div className="inline-actions">
              {log.performance_capped && <Chip tone="warn" label="cycle time suspect" />}
              <Chip tone={oeeTone(pct)} label={`OEE ${pct.toFixed(1)}%`} />
              <Chip status={log.status} />
            </div>}>
            <div className="grid cols-4" style={{ padding: 16 }}>
              <div className="card stat"><div className="k">Availability</div><div className="v" style={{ fontSize: 19 }}>{parseFloat(log.availability_pct).toFixed(1)}%</div></div>
              <div className="card stat"><div className="k">Performance</div><div className="v" style={{ fontSize: 19 }}>{parseFloat(log.performance_pct).toFixed(1)}%</div></div>
              <div className="card stat"><div className="k">Quality</div><div className="v" style={{ fontSize: 19 }}>{parseFloat(log.quality_pct).toFixed(1)}%</div></div>
              <div className="card stat accent"><div className="k">Run / planned</div><div className="v" style={{ fontSize: 19 }}>{log.run_minutes} / {log.planned_minutes}</div></div>
            </div>

            {log.downtime.length > 0 && (
              <div className="table-wrap">
                <table className="tbl">
                  <thead><tr><th>Stoppage</th><th>Category</th><th className="num">Minutes</th><th>Type</th><th>Note</th></tr></thead>
                  <tbody>
                    {log.downtime.map((e) => (
                      <tr key={e.id}>
                        <td>{e.reason_code} · <span className="muted">{e.reason_description}</span></td>
                        <td><Chip tone="neutral" label={e.category || "—"} /></td>
                        <td className="num">{e.minutes}</td>
                        <td>{e.planned ? <Chip tone="neutral" label="planned" /> : <Chip tone="bad" label="unplanned" />}</td>
                        <td className="muted">{e.note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {editable && log.status === "open" && (
              <div className="card-pad">
                <div className="form-row three">
                  <Field label="Total"><input className="input mono" value={c.total} onChange={(e) => setCounts({ ...counts, [log.id]: { ...c, total: e.target.value } })} /></Field>
                  <Field label="Good"><input className="input mono" value={c.good} onChange={(e) => setCounts({ ...counts, [log.id]: { ...c, good: e.target.value } })} /></Field>
                  <Field label="Reject"><input className="input mono" value={c.reject} onChange={(e) => setCounts({ ...counts, [log.id]: { ...c, reject: e.target.value } })} /></Field>
                </div>
                <div className="inline-actions" style={{ marginBottom: 12 }}>
                  <button className="btn sm" onClick={() => act(() => api.patch(`/mes/shift-logs/${log.id}/counts`, {
                    total_count: Number(c.total), good_count: Number(c.good), reject_count: Number(c.reject),
                  }), "Counts saved")}>Save counts</button>
                </div>
                <div className="form-row three">
                  <Field label="Downtime reason">
                    <select className="select" value={d.reason} onChange={(e) => setDowntime({ ...downtime, [log.id]: { ...d, reason: e.target.value } })}>
                      <option value="">Select…</option>
                      {reasons.data?.map((r) => <option key={r.id} value={r.id}>{r.code} — {r.description}{r.planned ? " (planned)" : ""}</option>)}
                    </select>
                  </Field>
                  <Field label="Minutes"><input className="input mono" value={d.minutes} onChange={(e) => setDowntime({ ...downtime, [log.id]: { ...d, minutes: e.target.value } })} /></Field>
                  <Field label="&nbsp;">
                    <button className="btn sm" disabled={!d.reason || !d.minutes}
                      onClick={() => act(() => api.post(`/mes/shift-logs/${log.id}/downtime`, {
                        reason_id: Number(d.reason), minutes: d.minutes,
                      }), "Downtime recorded")}>Add stoppage</button>
                  </Field>
                </div>
                <button className="btn primary" onClick={() => act(() => api.post(`/mes/shift-logs/${log.id}/close`), "Shift closed")}>Close shift</button>
              </div>
            )}
          </Card>
        );
      })}
    </>
  );
}

function OeePanel() {
  const [range, setRange] = useState({ date_from: daysAgo(29), date_to: today() });
  const oee = useAsync(
    () => api.get<OeeSummary>(`/mes/oee?date_from=${range.date_from}&date_to=${range.date_to}`),
    [range.date_from, range.date_to]
  );

  return (
    <>
      <Card title="OEE over a period" hint="Factors are re-derived from summed minutes and counts, not averaged across shifts.">
        <div className="card-pad">
          <div className="form-row two">
            <Field label="From"><input className="input mono" type="date" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} /></Field>
            <Field label="To"><input className="input mono" type="date" value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></Field>
          </div>
          <a className="btn sm" href={`/api/mes/oee/export?date_from=${range.date_from}&date_to=${range.date_to}`}>Export CSV</a>
        </div>
      </Card>

      {oee.loading ? <Spinner /> : !oee.data ? null : (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div className="card stat"><div className="k">Availability</div><div className="v" style={{ fontSize: 22 }}>{parseFloat(oee.data.availability_pct).toFixed(1)}%</div></div>
            <div className="card stat"><div className="k">Performance</div><div className="v" style={{ fontSize: 22 }}>{parseFloat(oee.data.performance_pct).toFixed(1)}%</div></div>
            <div className="card stat"><div className="k">Quality</div><div className="v" style={{ fontSize: 22 }}>{parseFloat(oee.data.quality_pct).toFixed(1)}%</div></div>
            <div className={`card stat ${oeeTone(parseFloat(oee.data.oee_pct)) === "ok" ? "accent" : "accent-madder"}`}>
              <div className="k">OEE · {oee.data.shifts} shifts</div>
              <div className="v" style={{ fontSize: 22 }}>{parseFloat(oee.data.oee_pct).toFixed(1)}%</div>
            </div>
          </div>

          <div className="section-title">Downtime Pareto</div>
          <Card>
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr>
                  <th>Reason</th><th>Category</th><th>Type</th><th className="num">Minutes</th>
                  <th className="num">Events</th><th className="num">Share</th><th className="num">Cumulative</th>
                </tr></thead>
                <tbody>
                  {oee.data.pareto.map((p) => (
                    <tr key={p.reason_id}>
                      <td>{p.reason_code} · <span className="muted">{p.description}</span></td>
                      <td><Chip tone="neutral" label={p.category} /></td>
                      <td>{p.planned ? "planned" : <Chip tone="bad" label="unplanned" />}</td>
                      <td className="num">{p.minutes}</td>
                      <td className="num">{p.events}</td>
                      <td className="num">{parseFloat(p.share_pct).toFixed(1)}%</td>
                      <td className="num muted">{parseFloat(p.cumulative_pct).toFixed(1)}%</td>
                    </tr>
                  ))}
                  {!oee.data.pareto.length && <tr><td colSpan={7} className="muted">No downtime recorded in this period.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
