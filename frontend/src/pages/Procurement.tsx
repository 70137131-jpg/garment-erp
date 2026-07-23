import { useState } from "react";
import { api, ApiError } from "../api/client";
import { GoodsReceipt as GoodsReceiptRecord, Material, PurchaseOrder, Supplier } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { ListToolbar, PdfLink, useListView } from "../components/ListTools";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, qty } from "../lib/format";

export default function Procurement() {
  const { can } = useAuthorization();
  const [tab, setTab] = useState("orders");
  return (
    <div>
      <PageHeader
        eyebrow="Module 3"
        title="Procurement"
        subtitle="Purchase orders track received-vs-ordered; goods receipt is the pivot — it posts stock, creates a roll per physical roll, and books the payable."
      />
      <Tabs tabs={[{ key: "orders", label: "Purchase Orders" }, ...(can("stores") ? [{ key: "receipt", label: "Post Receipt" }] : []), { key: "history", label: "Receipt History" }, { key: "performance", label: "Supplier Performance" }]} active={tab} onChange={setTab} />
      {tab === "orders" && <POs />}
      {tab === "receipt" && <GoodsReceiptCapture />}
      {tab === "history" && <GoodsReceiptHistory />}
      {tab === "performance" && <SupplierPerformance />}
    </div>
  );
}

function POs() {
  const { can } = useAuthorization();
  const pos = useAsync(() => api.get<PurchaseOrder[]>("/procurement/purchase-orders"));
  const [open, setOpen] = useState(false);
  const view = useListView(pos.data ?? [], (po) => `${po.order_number} ${po.status} ${po.supplier_id}`, (a, b) => a.id - b.id);
  const toast = useToast();
  async function approve(id: number) {
    try { await api.post(`/procurement/purchase-orders/${id}/approval`, { approved: true, comment: "Approved in purchasing workbench" }); toast.push("Purchase order approved"); pos.reload(); }
    catch (error) { toast.push("Approval failed", { detail: (error as ApiError).message, bad: true }); }
  }
  return (
    <Card title="Purchase orders" actions={can("procurement") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New PO</button> : undefined}>
      <ListToolbar query={view.query} onQuery={view.setQuery} descending={view.descending} onDirection={view.toggleDirection} page={view.page} pages={view.pages} total={view.total} previous={view.previous} next={view.next} exportPath="/procurement/purchase-orders/export" placeholder="Search PO, supplier, status..." />
      {pos.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>PO</th><th>Status</th><th className="num">Lines</th><th className="num">Value</th><th></th></tr></thead>
            <tbody>
              {view.rows.map((p) => (
                <tr key={p.id}><td className="code">{p.order_number}</td><td><Chip status={p.status} /></td>
                  <td className="num">{p.lines.length}</td><td className="num">{money(p.total_value, p.currency)}</td><td className="right"><div className="inline-actions"><PdfLink path={`/documents/purchase-orders/${p.id}/pdf`} />{can("procurement") && p.status === "pending_approval" && <button className="btn primary sm" onClick={() => approve(p.id)}>Approve</button>}</div></td></tr>
              ))}
              {!view.rows.length && <tr><td colSpan={5} className="muted">No purchase orders match.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {open && <POForm onClose={() => setOpen(false)} onDone={() => { setOpen(false); pos.reload(); }} />}
    </Card>
  );
}

function POForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const suppliers = useAsync(() => api.get<Supplier[]>("/masters/suppliers"));
  const materials = useAsync(() => api.get<Material[]>("/masters/materials"));
  const [supplierId, setSupplierId] = useState(0);
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [lines, setLines] = useState<{ material_id: number; ordered_qty: string; unit_price: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function addLine() {
    const m = materials.data?.[0];
    if (!m) { toast.push("Create a material first", { bad: true }); return; }
    setLines([...lines, { material_id: m.id, ordered_qty: "0", unit_price: "0" }]);
  }
  async function save() {
    setSaving(true); setError(null);
    try {
      await api.post("/procurement/purchase-orders", {
        supplier_id: Number(supplierId),
        requires_approval: requiresApproval,
        lines: lines.map((l) => ({ material_id: Number(l.material_id), ordered_qty: l.ordered_qty, unit_price: l.unit_price })),
      });
      toast.push("Purchase order created"); onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <Drawer title="New purchase order" sub="Module 3.1" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !supplierId || !lines.length} onClick={save}>Issue PO</button></>}>
      {error && <ErrorBox message={error} />}
      <Field label="Supplier" required>
        <select className="select" value={supplierId} onChange={(e) => setSupplierId(Number(e.target.value))}>
          <option value={0}>Select…</option>
          {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </Field>
      <label className="check-row"><input type="checkbox" checked={requiresApproval} onChange={(event) => setRequiresApproval(event.target.checked)} /> Route this PO for approval before receipt</label>
      <div className="flex-between" style={{ margin: "6px 0 10px" }}>
        <div className="section-title mt-0" style={{ margin: 0 }}>Lines</div>
        <button className="btn sm" onClick={addLine}>+ Add line</button>
      </div>
      {lines.map((l, i) => (
        <div className="form-row three" key={i} style={{ marginBottom: 10, alignItems: "end" }}>
          <Field label="Material">
            <select className="select" value={l.material_id} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, material_id: Number(e.target.value) } : x))}>
              {materials.data?.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.name}</option>)}
            </select>
          </Field>
          <Field label="Qty (base UoM)"><input className="input mono" value={l.ordered_qty} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, ordered_qty: e.target.value } : x))} /></Field>
          <Field label="Unit price"><input className="input mono" value={l.unit_price} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, unit_price: e.target.value } : x))} /></Field>
        </div>
      ))}
    </Drawer>
  );
}

function GoodsReceiptCapture() {
  const pos = useAsync(() => api.get<PurchaseOrder[]>("/procurement/purchase-orders"));
  const toast = useToast();
  const [poId, setPoId] = useState(0);
  const po = pos.data?.find((p) => p.id === poId);
  const [rolls, setRolls] = useState<{ line: number; length: string; width_cm: string; dye_lot: string; shade_group: string; grade: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<any>(null);

  function addRoll() {
    if (!po) return;
    setRolls([...rolls, { line: po.lines[0].id, length: "50", width_cm: "150", dye_lot: "DL-1", shade_group: "SG-A", grade: "A" }]);
  }
  async function post() {
    setSaving(true); setError(null);
    try {
      const r = await api.post<any>("/procurement/goods-receipts", {
        purchase_order_id: poId,
        rolls: rolls.map((x) => ({ purchase_order_line_id: Number(x.line), length: x.length, width_cm: x.width_cm, dye_lot: x.dye_lot, shade_group: x.shade_group, grade: x.grade })),
      });
      setResult(r); setRolls([]); toast.push(`Goods receipt ${r.receipt_number} posted`);
      pos.reload();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <div className="grid cols-2">
      <Card title="Post goods receipt" hint="one row per physical roll">
        <div className="card-pad">
          {error && <ErrorBox message={error} />}
          <Field label="Against purchase order" required>
            <select className="select" value={poId} onChange={(e) => { setPoId(Number(e.target.value)); setRolls([]); setResult(null); }}>
              <option value={0}>Select…</option>
              {pos.data?.filter((p) => !["received", "closed", "cancelled"].includes(p.status)).map((p) => <option key={p.id} value={p.id}>{p.order_number}</option>)}
            </select>
          </Field>
          {po && (
            <>
              <div className="flex-between" style={{ margin: "4px 0 10px" }}>
                <span className="mono muted" style={{ fontSize: 11 }}>ROLLS</span>
                <button className="btn sm" onClick={addRoll}>+ Add roll</button>
              </div>
              {rolls.map((r, i) => (
                <div className="card" key={i} style={{ marginBottom: 8, padding: 12 }}>
                  <div className="form-row three">
                    <Field label="Length (m)"><input className="input mono" value={r.length} onChange={(e) => setRolls(rolls.map((x, idx) => idx === i ? { ...x, length: e.target.value } : x))} /></Field>
                    <Field label="Width (cm)"><input className="input mono" value={r.width_cm} onChange={(e) => setRolls(rolls.map((x, idx) => idx === i ? { ...x, width_cm: e.target.value } : x))} /></Field>
                    <Field label="Grade">
                      <select className="select" value={r.grade} onChange={(e) => setRolls(rolls.map((x, idx) => idx === i ? { ...x, grade: e.target.value } : x))}>
                        <option>A</option><option>B</option><option>C</option>
                      </select>
                    </Field>
                  </div>
                  <div className="form-row two">
                    <Field label="Dye lot"><input className="input mono" value={r.dye_lot} onChange={(e) => setRolls(rolls.map((x, idx) => idx === i ? { ...x, dye_lot: e.target.value } : x))} /></Field>
                    <Field label="Shade group"><input className="input mono" value={r.shade_group} onChange={(e) => setRolls(rolls.map((x, idx) => idx === i ? { ...x, shade_group: e.target.value } : x))} /></Field>
                  </div>
                </div>
              ))}
              <button className="btn primary" style={{ marginTop: 6 }} disabled={saving || !rolls.length} onClick={post}>Post receipt ({rolls.length} rolls)</button>
            </>
          )}
        </div>
      </Card>

      <Card title="Result" hint="rolls arrive pending inspection">
        <div className="card-pad">
          {!result ? <div className="empty"><div className="big">Nothing posted yet</div>Select a PO and capture the rolls received.</div> : (
            <>
              <dl className="kv">
                <dt>Receipt</dt><dd className="mono">{result.receipt_number}</dd>
                <dt>Total length</dt><dd>{qty(result.total_length)} m</dd>
                <dt>Value</dt><dd>{money(result.total_value)}</dd>
              </dl>
              <div className="divider" />
              <div className="table-wrap">
                <table className="tbl">
                  <thead><tr><th>Roll</th><th className="num">Length</th><th>Status</th></tr></thead>
                  <tbody>
                    {result.rolls.map((r: any) => (
                      <tr key={r.roll_id}><td className="code">{r.roll_number}</td><td className="num">{qty(r.length)} m</td><td><Chip status={r.status} /></td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}

function GoodsReceiptHistory() {
  const receipts = useAsync(() => api.get<GoodsReceiptRecord[]>("/procurement/goods-receipts"));
  return (
    <Card title="Goods receipt history" hint="persisted roll-level receipts">
      {receipts.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Receipt</th><th>PO</th><th>Supplier</th><th className="num">Rolls</th><th className="num">Length</th><th className="num">Value</th><th></th></tr></thead>
            <tbody>
              {receipts.data?.map((receipt) => (
                <tr key={receipt.id}>
                  <td className="code">{receipt.receipt_number}</td>
                  <td className="mono">#{receipt.purchase_order_id}</td>
                  <td className="mono">#{receipt.supplier_id}</td>
                  <td className="num">{receipt.rolls.length}</td>
                  <td className="num">{qty(receipt.total_length)} m</td>
                  <td className="num">{money(receipt.total_value)}</td><td><PdfLink path={`/documents/goods-receipts/${receipt.id}/pdf`} /></td>
                </tr>
              ))}
              {!receipts.data?.length && <tr><td colSpan={7} className="muted">No goods receipts yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

interface SupplierMetric { supplier_id: number; purchase_orders: number; receipts: number; ordered_value: string; ordered_qty: string; received_qty: string; fulfilment_pct: string; on_time_pct: string; acceptance_pct: string; }
function SupplierPerformance() {
  const metrics = useAsync(() => api.get<SupplierMetric[]>("/procurement/supplier-performance"));
  const suppliers = useAsync(() => api.get<Supplier[]>("/masters/suppliers"));
  const name = (id: number) => suppliers.data?.find((supplier) => supplier.id === id)?.name ?? `#${id}`;
  return <Card title="Supplier performance" hint="Delivery, fulfilment, and quality outcomes">{metrics.loading ? <Spinner /> : <div className="table-wrap"><table className="tbl">
    <thead><tr><th>Supplier</th><th className="num">POs</th><th className="num">Receipts</th><th className="num">Ordered value</th><th className="num">Fulfilment</th><th className="num">On time</th><th className="num">Accepted</th></tr></thead>
    <tbody>{metrics.data?.map((row) => <tr key={row.supplier_id}><td>{name(row.supplier_id)}</td><td className="num">{row.purchase_orders}</td><td className="num">{row.receipts}</td><td className="num">{money(row.ordered_value)}</td><td className="num">{qty(row.fulfilment_pct)}%</td><td className="num">{qty(row.on_time_pct)}%</td><td className="num">{qty(row.acceptance_pct)}%</td></tr>)}{!metrics.data?.length && <tr><td colSpan={7} className="muted">No supplier activity yet.</td></tr>}</tbody>
  </table></div>}</Card>;
}
