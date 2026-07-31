import { useState } from "react";
import { api, ApiError } from "../api/client";
import { CutPlan, Marker, Style } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";

const TABS = ["Cut plans", "Markers"] as const;
type Tab = (typeof TABS)[number];

export default function Markers() {
  const [tab, setTab] = useState<Tab>("Cut plans");
  const styles = useAsync(() => api.get<Style[]>("/styles"), [], "/styles");
  const [styleId, setStyleId] = useState("");

  return (
    <div>
      <PageHeader
        eyebrow="Module 14"
        title="Marker & cut planning"
        subtitle="Marker efficiency is derived from pattern geometry; lay plans are solved to cover the order with the least fabric."
        actions={
          <select className="select" style={{ width: "auto" }} value={styleId}
            onChange={(e) => setStyleId(e.target.value)}>
            <option value="">Select a style…</option>
            {styles.data?.map((s) => <option key={s.id} value={s.id}>{s.style_number} — {s.description}</option>)}
          </select>
        }
      />
      <div className="inline-actions" style={{ marginBottom: 18 }}>
        {TABS.map((t) => (
          <button key={t} className={`btn sm ${tab === t ? "primary" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {!styleId ? (
        <Card><div className="empty"><div className="big">Choose a style</div>Markers and cut plans are per style.</div></Card>
      ) : tab === "Markers" ? <MarkerPanel styleId={Number(styleId)} />
        : <CutPlanPanel styleId={Number(styleId)} />}
    </div>
  );
}

function MarkerPanel({ styleId }: { styleId: number }) {
  const { can } = useAuthorization();
  const editable = can("cutting_supervisor", "planner", "merchandiser");
  const toast = useToast();
  const markers = useAsync(() => api.get<Marker[]>(`/marker/markers?style_id=${styleId}`), [styleId]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    marker_code: "", width_cm: "150", length_cm: "", pattern_area_cm2: "", max_plies: "100",
  });
  const [sizes, setSizes] = useState([{ size_label: "M", quantity: "1" }]);

  const rect = parseFloat(form.length_cm) * parseFloat(form.width_cm);
  const preview = rect > 0 && parseFloat(form.pattern_area_cm2) > 0
    ? (parseFloat(form.pattern_area_cm2) / rect) * 100 : null;

  async function create() {
    setError(null);
    try {
      await api.post("/marker/markers", {
        ...form, style_id: styleId, max_plies: Number(form.max_plies),
        sizes: sizes.map((s) => ({ size_label: s.size_label, quantity: Number(s.quantity) })),
      });
      toast.push("Marker created");
      setForm({ ...form, marker_code: "", length_cm: "", pattern_area_cm2: "" });
      markers.reload();
    } catch (e) { setError((e as ApiError).message); }
  }

  return (
    <>
      {editable && (
        <Card title="New marker" hint="Efficiency = pattern area ÷ (length × width). Above 100% is geometrically impossible and is rejected.">
          <div className="card-pad">
            <div className="form-row three">
              <Field label="Code"><input className="input mono" value={form.marker_code} onChange={(e) => setForm({ ...form, marker_code: e.target.value })} placeholder="MK-TS100-A" /></Field>
              <Field label="Fabric width (cm)"><input className="input mono" value={form.width_cm} onChange={(e) => setForm({ ...form, width_cm: e.target.value })} /></Field>
              <Field label="Marker length (cm)"><input className="input mono" value={form.length_cm} onChange={(e) => setForm({ ...form, length_cm: e.target.value })} /></Field>
            </div>
            <div className="form-row two">
              <Field label="Pattern area (cm²)" hint={preview !== null ? `→ ${preview.toFixed(1)}% efficiency` : "sum of all piece areas"}>
                <input className="input mono" value={form.pattern_area_cm2} onChange={(e) => setForm({ ...form, pattern_area_cm2: e.target.value })} />
              </Field>
              <Field label="Max plies"><input className="input mono" value={form.max_plies} onChange={(e) => setForm({ ...form, max_plies: e.target.value })} /></Field>
            </div>

            <div className="flex-between" style={{ margin: "6px 0 10px" }}>
              <div className="section-title mt-0" style={{ margin: 0 }}>Sizes per ply</div>
              <button className="btn sm" onClick={() => setSizes([...sizes, { size_label: "", quantity: "1" }])}>+ Add size</button>
            </div>
            {sizes.map((s, i) => (
              <div className="form-row two" key={i}>
                <Field label="Size"><input className="input" value={s.size_label} onChange={(e) => setSizes(sizes.map((x, idx) => idx === i ? { ...x, size_label: e.target.value } : x))} /></Field>
                <Field label="Garments"><input className="input mono" value={s.quantity} onChange={(e) => setSizes(sizes.map((x, idx) => idx === i ? { ...x, quantity: e.target.value } : x))} /></Field>
              </div>
            ))}
            {error && <ErrorBox message={error} />}
            <button className="btn primary" disabled={!form.marker_code || !form.length_cm} onClick={create}>Create marker</button>
          </div>
        </Card>
      )}

      <div className="section-title">Markers</div>
      {markers.loading ? <Spinner /> : !markers.data?.length ? (
        <Card><div className="empty"><div className="big">No markers</div>Define one before planning a lay.</div></Card>
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr>
                <th>Code</th><th>Sizes / ply</th><th className="num">Width</th><th className="num">Length</th>
                <th className="num">Efficiency</th><th className="num">Max plies</th><th>Status</th><th></th>
              </tr></thead>
              <tbody>
                {markers.data.map((m) => {
                  const eff = parseFloat(m.efficiency_pct);
                  return (
                    <tr key={m.id}>
                      <td className="mono">{m.marker_code}</td>
                      <td>{m.sizes.map((s) => `${s.size_label}×${s.quantity}`).join(" · ")} <span className="muted">({m.pieces_per_ply}/ply)</span></td>
                      <td className="num">{m.width_cm}</td>
                      <td className="num">{m.length_cm}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        <Chip tone={eff >= 85 ? "ok" : eff >= 75 ? "warn" : "bad"} label={`${eff.toFixed(1)}%`} />
                      </td>
                      <td className="num">{m.max_plies}</td>
                      <td><Chip status={m.status} /></td>
                      <td>{editable && m.status === "draft" && (
                        <button className="btn sm" onClick={async () => {
                          await api.post(`/marker/markers/${m.id}/approve`);
                          toast.push("Marker approved"); markers.reload();
                        }}>Approve</button>
                      )}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

function CutPlanPanel({ styleId }: { styleId: number }) {
  const { can } = useAuthorization();
  const editable = can("cutting_supervisor", "planner");
  const toast = useToast();
  const plans = useAsync(() => api.get<CutPlan[]>(`/marker/cut-plans?style_id=${styleId}`), [styleId]);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState("150");
  const [allowOvercut, setAllowOvercut] = useState(true);
  const [sizes, setSizes] = useState([{ size_label: "M", quantity: "100" }]);

  async function solve() {
    setError(null);
    try {
      await api.post("/marker/cut-plans", {
        style_id: styleId, width_cm: width, allow_overcut: allowOvercut,
        sizes: sizes.filter((s) => s.size_label && s.quantity)
          .map((s) => ({ size_label: s.size_label, quantity: Number(s.quantity) })),
      });
      toast.push("Cut plan solved");
      plans.reload();
    } catch (e) { setError((e as ApiError).message); }
  }

  return (
    <>
      {editable && (
        <Card title="Solve a lay plan" hint="Heuristic: greedy coverage then ply reduction. Overcut is reported, never hidden.">
          <div className="card-pad">
            <div className="form-row two">
              <Field label="Fabric width (cm)"><input className="input mono" value={width} onChange={(e) => setWidth(e.target.value)} /></Field>
              <Field label="Overcut">
                <label className="flex" style={{ gap: 6, fontSize: 13 }}>
                  <input type="checkbox" checked={allowOvercut} onChange={(e) => setAllowOvercut(e.target.checked)} />
                  Allow over-production
                </label>
              </Field>
            </div>
            <div className="flex-between" style={{ margin: "6px 0 10px" }}>
              <div className="section-title mt-0" style={{ margin: 0 }}>Required quantities</div>
              <button className="btn sm" onClick={() => setSizes([...sizes, { size_label: "", quantity: "" }])}>+ Add size</button>
            </div>
            {sizes.map((s, i) => (
              <div className="form-row two" key={i}>
                <Field label="Size"><input className="input" value={s.size_label} onChange={(e) => setSizes(sizes.map((x, idx) => idx === i ? { ...x, size_label: e.target.value } : x))} /></Field>
                <Field label="Quantity"><input className="input mono" value={s.quantity} onChange={(e) => setSizes(sizes.map((x, idx) => idx === i ? { ...x, quantity: e.target.value } : x))} /></Field>
              </div>
            ))}
            {error && <ErrorBox message={error} />}
            <button className="btn primary" onClick={solve}>Solve plan</button>
          </div>
        </Card>
      )}

      <div className="section-title">Cut plans</div>
      {plans.loading ? <Spinner /> : !plans.data?.length ? (
        <Card><div className="empty"><div className="big">No plans yet</div>Solve one from the required quantities.</div></Card>
      ) : plans.data.map((p) => {
        const saving = p.saving_vs_bom_m != null ? parseFloat(p.saving_vs_bom_m) : null;
        return (
          <Card key={p.id} title={`${p.plan_number} · ${p.width_cm} cm`}
            hint={p.algorithm}
            actions={<div className="inline-actions">
              <Chip status={p.status} />
              {editable && p.status === "draft" && (
                <button className="btn sm" onClick={async () => {
                  await api.post(`/marker/cut-plans/${p.id}/approve`);
                  toast.push("Plan approved"); plans.reload();
                }}>Approve</button>
              )}
              <a className="btn sm" href={`/api/marker/cut-plans/${p.id}/export`}>CSV</a>
            </div>}>
            <div className="grid cols-4" style={{ padding: 16 }}>
              <div className="card stat accent"><div className="k">Fabric</div><div className="v" style={{ fontSize: 20 }}>{p.total_fabric_m} m</div></div>
              <div className="card stat"><div className="k">Lays / plies</div><div className="v" style={{ fontSize: 20 }}>{p.lay_count} / {p.total_plies}</div></div>
              <div className={`card stat ${p.overcut_pieces > 0 ? "accent-madder" : ""}`}>
                <div className="k">Overcut</div>
                <div className="v" style={{ fontSize: 20 }}>{p.overcut_pieces} <span style={{ fontSize: 13 }}>({parseFloat(p.overcut_pct).toFixed(1)}%)</span></div>
              </div>
              <div className="card stat"><div className="k">Marker efficiency</div><div className="v" style={{ fontSize: 20 }}>{parseFloat(p.weighted_efficiency_pct).toFixed(1)}%</div></div>
            </div>

            {saving !== null && (
              <div className="card-pad" style={{ paddingTop: 0 }}>
                <div className="muted" style={{ fontSize: 13 }}>
                  BOM estimate {p.bom_fabric_m} m · marker plan {p.total_fabric_m} m ·{" "}
                  <strong style={{ color: saving >= 0 ? "var(--indigo-deep)" : "var(--madder)" }}>
                    {saving >= 0 ? `${saving.toFixed(1)} m saved` : `${Math.abs(saving).toFixed(1)} m over BOM`}
                  </strong>
                </div>
              </div>
            )}

            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Lay</th><th>Marker</th><th className="num">Plies</th><th className="num">Fabric (m)</th><th className="num">Pieces</th><th className="num">Efficiency</th></tr></thead>
                <tbody>
                  {p.lays.map((l) => (
                    <tr key={l.id}>
                      <td>{l.sequence}</td>
                      <td className="mono">{l.marker_code}</td>
                      <td className="num">{l.plies}</td>
                      <td className="num">{l.fabric_m}</td>
                      <td className="num">{l.pieces}</td>
                      <td className="num muted">{parseFloat(l.efficiency_pct).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Size</th><th className="num">Required</th><th className="num">Planned</th><th className="num">Overcut</th></tr></thead>
                <tbody>
                  {p.demands.map((d) => (
                    <tr key={d.size_label}>
                      <td>{d.size_label}</td>
                      <td className="num">{d.required_qty}</td>
                      <td className="num">{d.planned_qty}</td>
                      <td className="num">{d.overcut_qty > 0 ? <Chip tone="warn" label={`+${d.overcut_qty}`} /> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </>
  );
}
