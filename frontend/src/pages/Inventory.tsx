import { useState } from "react";
import { api } from "../api/client";
import { LedgerEntry, Material, Reservation, Roll } from "../api/types";
import { Card, Chip, PageHeader, Spinner, Tabs } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { qty, titled } from "../lib/format";

export default function Inventory() {
  const [tab, setTab] = useState("rolls");
  return (
    <div>
      <PageHeader
        eyebrow="Module 4"
        title="Inventory & Warehouse"
        subtitle="An immutable stock ledger where balance is the sum of movements, and a roll register queryable by shade group, width, grade and status."
      />
      <Tabs tabs={[{ key: "rolls", label: "Roll Register" }, { key: "ledger", label: "Stock Ledger" }, { key: "reservations", label: "Reservations" }]} active={tab} onChange={setTab} />
      {tab === "rolls" && <Rolls />}
      {tab === "ledger" && <Ledger />}
      {tab === "reservations" && <Reservations />}
    </div>
  );
}

const ROLL_STATUSES = ["", "pending_inspection", "available", "reserved", "quarantined", "consumed", "rejected"];

function Rolls() {
  const [status, setStatus] = useState("");
  const rolls = useAsync(() => api.get<Roll[]>(`/inventory/rolls${status ? `?status=${status}` : ""}`), [status]);
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"));
  const matName = (id: number) => materials.data?.find((m) => m.id === id)?.name ?? `#${id}`;
  return (
    <Card title="Roll register" hint="every roll a uniquely identified unit"
      actions={
        <select className="select" style={{ width: "auto", padding: "5px 10px" }} value={status} onChange={(e) => setStatus(e.target.value)}>
          {ROLL_STATUSES.map((s) => <option key={s} value={s}>{s ? titled(s) : "All statuses"}</option>)}
        </select>
      }>
      {rolls.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Roll №</th><th>Material</th><th>Dye lot</th><th>Shade</th><th>Grade</th><th className="num">Length</th><th className="num">Width</th><th>Status</th></tr></thead>
            <tbody>
              {rolls.data?.map((r) => (
                <tr key={r.id}>
                  <td className="code">{r.roll_number}</td>
                  <td>{matName(r.material_id)}</td>
                  <td className="mono muted">{r.dye_lot || "—"}</td>
                  <td className="mono muted">{r.shade_group || "—"}</td>
                  <td><Chip tone="neutral" label={r.grade} /></td>
                  <td className="num">{qty(r.length)} m</td>
                  <td className="num">{r.width_cm ? `${qty(r.width_cm)}` : "—"}</td>
                  <td><Chip status={r.status} /></td>
                </tr>
              ))}
              {!rolls.data?.length && <tr><td colSpan={8} className="muted">No rolls match.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Ledger() {
  const ledger = useAsync(() => api.get<LedgerEntry[]>("/inventory/ledger"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"));
  const matName = (id: number) => materials.data?.find((m) => m.id === id)?.name ?? `#${id}`;
  return (
    <Card title="Stock ledger" hint="append-only · balance = Σ lines">
      {ledger.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>#</th><th>Material</th><th>Movement</th><th className="num">Qty</th><th>Reference</th><th>Note</th></tr></thead>
            <tbody>
              {[...(ledger.data ?? [])].reverse().map((e) => {
                const q = parseFloat(e.quantity);
                return (
                  <tr key={e.id}>
                    <td className="mono muted">{e.id}</td>
                    <td>{matName(e.material_id)}</td>
                    <td><Chip tone={q >= 0 ? "ok" : "warn"} label={titled(e.movement_type)} /></td>
                    <td className="num" style={{ color: q >= 0 ? "var(--ok)" : "var(--madder)" }}>{q >= 0 ? "+" : ""}{qty(e.quantity)} {e.uom}</td>
                    <td className="mono muted">{e.reference_type || "—"}</td>
                    <td className="muted">{e.note || "—"}</td>
                  </tr>
                );
              })}
              {!ledger.data?.length && <tr><td colSpan={6} className="muted">No movements yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Reservations() {
  const res = useAsync(() => api.get<Reservation[]>("/inventory/reservations"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"));
  const matName = (id: number) => materials.data?.find((m) => m.id === id)?.name ?? `#${id}`;
  return (
    <Card title="Reservations" hint="Active → Released → Consumed">
      {res.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>#</th><th>Material</th><th className="num">Roll</th><th className="num">Reserved</th><th>For</th><th>Status</th></tr></thead>
            <tbody>
              {res.data?.map((r) => (
                <tr key={r.id}>
                  <td className="mono muted">{r.id}</td>
                  <td>{matName(r.material_id)}</td>
                  <td className="num mono">{r.roll_id ?? "—"}</td>
                  <td className="num">{qty(r.reserved_qty)} m</td>
                  <td className="mono muted">{r.reference_type ? `${r.reference_type} #${r.reference_id}` : "—"}</td>
                  <td><Chip status={r.status} /></td>
                </tr>
              ))}
              {!res.data?.length && <tr><td colSpan={6} className="muted">No reservations yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
