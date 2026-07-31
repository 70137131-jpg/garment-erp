import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Bin, Material, ScanResult, WarehouseTask } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";

const TABS = ["Scan", "Tasks", "Bins"] as const;
type Tab = (typeof TABS)[number];

export default function Warehouse() {
  const [tab, setTab] = useState<Tab>("Scan");
  return (
    <div>
      <PageHeader
        eyebrow="Module 13"
        title="Warehouse"
        subtitle="Directed work for handhelds. Completing a task posts through the stock ledger, and retries with the same key never move stock twice."
      />
      <div className="inline-actions" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn sm ${tab === t ? "primary" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === "Scan" && <ScanPanel />}
      {tab === "Tasks" && <TaskPanel />}
      {tab === "Bins" && <BinPanel />}
    </div>
  );
}

function ScanPanel() {
  const [code, setCode] = useState("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function scan() {
    setError(null);
    try { setResult(await api.post<ScanResult>("/wms/scan", { code })); }
    catch (e) { setError((e as ApiError).message); setResult(null); }
  }

  return (
    <>
      <Card title="Scan" hint="Roll number, bin code, or material code.">
        <div className="card-pad">
          <div className="flex">
            <input className="input mono" style={{ maxWidth: 320, fontSize: 18 }} value={code} autoFocus
              placeholder="Scan or type a code…"
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") scan(); }} />
            <button className="btn primary" disabled={!code.trim()} onClick={scan}>Look up</button>
          </div>
          {error && <ErrorBox message={error} />}
        </div>
      </Card>

      {result && (
        <Card
          title={result.kind === "unknown" ? "Not recognised" : result.label || result.code || ""}
          actions={<Chip tone={result.kind === "unknown" ? "bad" : "ok"} label={result.kind} />}>
          <div className="card-pad">
            {result.kind === "unknown" ? (
              <div className="muted">Nothing in the system matches “{result.code}”.</div>
            ) : (
              <dl className="kv">
                {result.material_code && <><dt>Material</dt><dd>{result.material_code} · {result.material_name}</dd></>}
                {result.quantity != null && <><dt>Quantity</dt><dd className="mono">{result.quantity} {result.uom || ""}</dd></>}
                {result.warehouse && <><dt>Warehouse</dt><dd>{result.warehouse}</dd></>}
                {result.location && <><dt>Location</dt><dd className="mono">{result.location}</dd></>}
                {result.status && <><dt>Status</dt><dd>{result.status}</dd></>}
                {result.grade && <><dt>Grade</dt><dd>{result.grade}</dd></>}
              </dl>
            )}
          </div>
          {result.open_tasks.length > 0 && (
            <>
              <div className="card-pad" style={{ paddingBottom: 0 }}><strong>Open work here</strong></div>
              <TaskTable tasks={result.open_tasks} />
            </>
          )}
        </Card>
      )}
    </>
  );
}

function TaskTable({ tasks }: { tasks: WarehouseTask[] }) {
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead><tr>
          <th>Task</th><th>Type</th><th>Material</th><th className="num">Qty</th>
          <th>From</th><th>To</th><th>Assigned</th><th>Status</th>
        </tr></thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td className="mono">{t.task_number}</td>
              <td><Chip tone="neutral" label={t.task_type.replace("_", " ")} /></td>
              <td>{t.material_code}{t.roll_number ? ` · ${t.roll_number}` : ""}</td>
              <td className="num">{t.quantity}</td>
              <td className="mono">{t.from_bin_code || "—"}</td>
              <td className="mono">{t.to_bin_code || "—"}</td>
              <td className="muted">{t.assigned_to || "—"}</td>
              <td><Chip status={t.status} /></td>
            </tr>
          ))}
          {!tasks.length && <tr><td colSpan={8} className="muted">Nothing outstanding.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function TaskPanel() {
  const { can } = useAuthorization();
  const canCreate = can("stores", "planner");
  const canComplete = can("stores");
  const toast = useToast();
  const tasks = useAsync(() => api.get<WarehouseTask[]>("/wms/tasks?status=open"));
  const bins = useAsync(() => api.get<Bin[]>("/wms/bins"), [], "/wms/bins");
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"), [], "/masters/materials");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    task_type: "pick", material_id: "", quantity: "", from_bin_id: "", to_bin_id: "", priority: "5",
  });
  const [done, setDone] = useState<Record<number, { qty: string; reason: string }>>({});

  async function act(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    try { await fn(); toast.push(ok); tasks.reload(); }
    catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
  }

  return (
    <>
      {canCreate && (
        <Card title="Direct some work">
          <div className="card-pad">
            <div className="form-row three">
              <Field label="Type">
                <select className="select" value={form.task_type} onChange={(e) => setForm({ ...form, task_type: e.target.value })}>
                  {["pick", "put_away", "replenish", "transfer", "count"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
                </select>
              </Field>
              <Field label="Material">
                <select className="select" value={form.material_id} onChange={(e) => setForm({ ...form, material_id: e.target.value })}>
                  <option value="">Select…</option>
                  {materials.data?.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.name}</option>)}
                </select>
              </Field>
              <Field label="Quantity"><input className="input mono" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>
            </div>
            <div className="form-row three">
              <Field label="From bin">
                <select className="select" value={form.from_bin_id} onChange={(e) => setForm({ ...form, from_bin_id: e.target.value })}>
                  <option value="">Select…</option>
                  {bins.data?.map((b) => <option key={b.id} value={b.id}>{b.code} ({b.warehouse})</option>)}
                </select>
              </Field>
              <Field label="To bin">
                <select className="select" value={form.to_bin_id} onChange={(e) => setForm({ ...form, to_bin_id: e.target.value })}>
                  <option value="">Select…</option>
                  {bins.data?.map((b) => <option key={b.id} value={b.id}>{b.code} ({b.warehouse})</option>)}
                </select>
              </Field>
              <Field label="Priority" hint="lower runs first"><input className="input mono" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} /></Field>
            </div>
            {error && <ErrorBox message={error} />}
            <button className="btn primary" disabled={!form.material_id || !form.quantity}
              onClick={() => act(() => api.post("/wms/tasks", {
                task_type: form.task_type, material_id: Number(form.material_id), quantity: form.quantity,
                from_bin_id: form.from_bin_id ? Number(form.from_bin_id) : null,
                to_bin_id: form.to_bin_id ? Number(form.to_bin_id) : null,
                priority: Number(form.priority),
              }), "Task created")}>Create task</button>
          </div>
        </Card>
      )}

      <div className="section-title">Open work — in route order</div>
      {tasks.loading ? <Spinner /> : !tasks.data?.length ? (
        <Card><div className="empty"><div className="big">Nothing outstanding</div>All directed work is done.</div></Card>
      ) : tasks.data.map((t) => {
        const d = done[t.id] || { qty: t.quantity, reason: "" };
        const short = parseFloat(d.qty) < parseFloat(t.quantity);
        return (
          <Card key={t.id} title={`${t.task_number} · ${t.task_type.replace("_", " ")}`}
            actions={<Chip status={t.status} />}>
            <div className="card-pad">
              <dl className="kv">
                <dt>Material</dt><dd>{t.material_code} · {t.material_name}</dd>
                <dt>Directed</dt><dd className="mono">{t.quantity}</dd>
                <dt>Route</dt><dd className="mono">{t.from_bin_code || "—"} → {t.to_bin_code || "—"}</dd>
              </dl>
              {canComplete && (
                <>
                  <div className="form-row two" style={{ marginTop: 12 }}>
                    <Field label="Completed qty"><input className="input mono" value={d.qty} onChange={(e) => setDone({ ...done, [t.id]: { ...d, qty: e.target.value } })} /></Field>
                    <Field label="Short reason" hint={short ? "required for a short pick" : "only if short"}>
                      <input className="input" value={d.reason} onChange={(e) => setDone({ ...done, [t.id]: { ...d, reason: e.target.value } })} />
                    </Field>
                  </div>
                  <div className="inline-actions">
                    <button className="btn primary" disabled={short && !d.reason}
                      onClick={() => act(() => api.post(`/wms/tasks/${t.id}/complete`, {
                        completed_qty: d.qty,
                        short_reason: d.reason || null,
                        client_key: `task-${t.id}-${d.qty}`,
                      }), "Task completed")}>Confirm</button>
                    <button className="btn" onClick={() => act(() => api.post(`/wms/tasks/${t.id}/cancel`), "Task cancelled")}>Cancel</button>
                  </div>
                </>
              )}
            </div>
          </Card>
        );
      })}
    </>
  );
}

function BinPanel() {
  const { can } = useAuthorization();
  const editable = can("stores");
  const toast = useToast();
  const bins = useAsync(() => api.get<Bin[]>("/wms/bins"), [], "/wms/bins");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    code: "", warehouse: "MAIN", zone: "", bin_type: "storage", pick_sequence: "0",
  });

  return (
    <>
      {editable && (
        <Card title="New bin" hint="Pick sequence orders the walking route.">
          <div className="card-pad">
            <div className="form-row three">
              <Field label="Code"><input className="input mono" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="A-01-01" /></Field>
              <Field label="Warehouse"><input className="input mono" value={form.warehouse} onChange={(e) => setForm({ ...form, warehouse: e.target.value })} /></Field>
              <Field label="Zone"><input className="input" value={form.zone} onChange={(e) => setForm({ ...form, zone: e.target.value })} /></Field>
            </div>
            <div className="form-row two">
              <Field label="Type">
                <select className="select" value={form.bin_type} onChange={(e) => setForm({ ...form, bin_type: e.target.value })}>
                  {["storage", "staging", "receiving", "shipping", "quarantine"].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </Field>
              <Field label="Pick sequence"><input className="input mono" value={form.pick_sequence} onChange={(e) => setForm({ ...form, pick_sequence: e.target.value })} /></Field>
            </div>
            {error && <ErrorBox message={error} />}
            <button className="btn primary" disabled={!form.code}
              onClick={async () => {
                setError(null);
                try {
                  await api.post("/wms/bins", { ...form, zone: form.zone || null, pick_sequence: Number(form.pick_sequence) });
                  toast.push("Bin created"); setForm({ ...form, code: "" }); bins.reload();
                } catch (e) { setError((e as ApiError).message); }
              }}>Create</button>
          </div>
        </Card>
      )}

      <div className="section-title">Bins</div>
      {bins.loading ? <Spinner /> : !bins.data?.length ? (
        <Card><div className="empty"><div className="big">No bins</div>Define locations before directing work.</div></Card>
      ) : (
        <Card>
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Code</th><th>Warehouse</th><th>Zone</th><th>Type</th><th className="num">Sequence</th></tr></thead>
              <tbody>
                {bins.data.map((b) => (
                  <tr key={b.id}>
                    <td className="mono">{b.code}</td>
                    <td>{b.warehouse}</td>
                    <td className="muted">{b.zone || "—"}</td>
                    <td><Chip tone="neutral" label={b.bin_type} /></td>
                    <td className="num">{b.pick_sequence}</td>
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
