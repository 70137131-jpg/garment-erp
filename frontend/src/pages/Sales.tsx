import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Colour, Customer, SalesOrder, SizeRange, Style } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, num } from "../lib/format";

interface DraftLine {
  style_id: number;
  colour_id: number;
  unit_price: string;
  sizes: Record<string, string>;
}

export default function Sales() {
  const orders = useAsync(() => api.get<SalesOrder[]>("/sales-orders"));
  const [open, setOpen] = useState(false);
  return (
    <div>
      <PageHeader
        eyebrow="Module 2"
        title="Sales Orders"
        subtitle="Demand entered once as a size matrix — every colour × size cell tracked from ordered through confirmed to shipped."
        actions={<button className="btn primary" onClick={() => setOpen(true)}>+ New order</button>}
      />
      <Card>
        {orders.loading ? <Spinner /> : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Order</th><th>Customer PO</th><th>Status</th><th className="num">Qty</th><th className="num">Value</th><th></th></tr></thead>
              <tbody>
                {[...(orders.data ?? [])].reverse().map((o) => (
                  <tr key={o.id} className="clickable">
                    <td className="code"><Link to={`/sales/${o.id}`}>{o.order_number}</Link></td>
                    <td className="mono muted">{o.customer_po_number || "—"}</td>
                    <td><Chip status={o.status} /></td>
                    <td className="num">{num(o.total_quantity)}</td>
                    <td className="num">{money(o.total_value, o.currency)}</td>
                    <td className="right"><Link to={`/sales/${o.id}`} className="btn ghost sm">Open →</Link></td>
                  </tr>
                ))}
                {!orders.data?.length && <tr><td colSpan={6} className="muted">No sales orders yet.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {open && <OrderForm onClose={() => setOpen(false)} onDone={() => { setOpen(false); orders.reload(); }} />}
    </div>
  );
}

function OrderForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const customers = useAsync(() => api.get<Customer[]>("/masters/customers"));
  const styles = useAsync(() => api.get<Style[]>("/styles"));
  const colours = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const ranges = useAsync(() => api.get<SizeRange[]>("/masters/size-ranges"));

  const [customerId, setCustomerId] = useState(0);
  const [po, setPo] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const rangeForStyle = (styleId: number) => {
    const st = styles.data?.find((s) => s.id === styleId);
    return ranges.data?.find((r) => r.id === st?.size_range_id);
  };

  function addLine() {
    const firstStyle = styles.data?.[0];
    if (!firstStyle) { toast.push("Create a style first", { bad: true }); return; }
    setLines([...lines, { style_id: firstStyle.id, colour_id: colours.data?.[0]?.id ?? 0, unit_price: "0", sizes: {} }]);
  }
  function updateLine(i: number, patch: Partial<DraftLine>) {
    setLines(lines.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      const payload = {
        customer_id: Number(customerId),
        customer_po_number: po || null,
        currency,
        lines: lines.map((l) => ({
          style_id: Number(l.style_id),
          colour_id: Number(l.colour_id),
          unit_price: l.unit_price,
          sizes: Object.entries(l.sizes)
            .filter(([, v]) => v !== "" && Number(v) > 0)
            .map(([size_label, v]) => ({ size_label, ordered_qty: Number(v) })),
        })),
      };
      await api.post("/sales-orders", payload);
      toast.push("Sales order created");
      onDone();
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Could not save", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  const ready = customerId && lines.length > 0;

  return (
    <Drawer title="New sales order" sub="Module 2 · size matrix" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving || !ready} onClick={save}>Create order</button></>}>
      {error && <ErrorBox message={error} />}
      <div className="form-row three">
        <Field label="Customer" required>
          <select className="select" value={customerId} onChange={(e) => setCustomerId(Number(e.target.value))}>
            <option value={0}>Select…</option>
            {customers.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Customer PO"><input className="input mono" value={po} onChange={(e) => setPo(e.target.value)} /></Field>
        <Field label="Currency"><input className="input mono" value={currency} onChange={(e) => setCurrency(e.target.value)} /></Field>
      </div>

      <div className="flex-between" style={{ margin: "6px 0 12px" }}>
        <div className="section-title mt-0" style={{ margin: 0 }}>Order lines</div>
        <button className="btn sm" onClick={addLine}>+ Add line</button>
      </div>

      {lines.length === 0 && <div className="hintline">Add a line — a style × colour with its size breakdown.</div>}

      {lines.map((line, i) => {
        const range = rangeForStyle(line.style_id);
        return (
          <div key={i} className="card" style={{ marginBottom: 14 }}>
            <div className="card-pad">
              <div className="flex-between" style={{ marginBottom: 12 }}>
                <span className="mono muted" style={{ fontSize: 11 }}>LINE {i + 1}</span>
                <button className="btn ghost sm" onClick={() => setLines(lines.filter((_, idx) => idx !== i))}>Remove</button>
              </div>
              <div className="form-row three">
                <Field label="Style">
                  <select className="select" value={line.style_id} onChange={(e) => updateLine(i, { style_id: Number(e.target.value), sizes: {} })}>
                    {styles.data?.map((s) => <option key={s.id} value={s.id}>{s.style_number}</option>)}
                  </select>
                </Field>
                <Field label="Colour">
                  <select className="select" value={line.colour_id} onChange={(e) => updateLine(i, { colour_id: Number(e.target.value) })}>
                    {colours.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </Field>
                <Field label="Unit price"><input className="input mono" value={line.unit_price} onChange={(e) => updateLine(i, { unit_price: e.target.value })} /></Field>
              </div>
              {range ? (
                <div className="matrix">
                  <table>
                    <thead><tr>{range.sizes.map((z) => <th key={z.label}>{z.label}</th>)}</tr></thead>
                    <tbody>
                      <tr>
                        {range.sizes.map((z) => (
                          <td key={z.label}>
                            <input value={line.sizes[z.label] ?? ""} placeholder="0"
                              onChange={(e) => updateLine(i, { sizes: { ...line.sizes, [z.label]: e.target.value } })} />
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              ) : <div className="hintline">This style has no size range.</div>}
            </div>
          </div>
        );
      })}
    </Drawer>
  );
}
