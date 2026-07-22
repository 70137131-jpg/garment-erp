import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Colour, Final, Profitability, SalesOrder, Style } from "../api/types";
import { Card, Chip, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, num } from "../lib/format";

export default function SalesDetail() {
  const { id } = useParams();
  const oid = Number(id);
  const order = useAsync(() => api.get<SalesOrder>(`/sales-orders/${oid}`), [oid]);
  const styles = useAsync(() => api.get<Style[]>("/styles"));
  const colours = useAsync(() => api.get<Colour[]>("/masters/colours"));
  const prof = useAsync(() => api.get<Profitability>(`/costing/sales-orders/${oid}/profitability`), [oid]);
  const toast = useToast();

  if (order.loading) return <Spinner />;
  if (!order.data) return <div className="err">Order not found.</div>;
  const o = order.data;
  const styleNo = (sid: number) => styles.data?.find((s) => s.id === sid)?.style_number ?? `#${sid}`;
  const colourNm = (cid: number) => colours.data?.find((c) => c.id === cid)?.name ?? `#${cid}`;

  async function act(path: string, ok: string) {
    try {
      await api.post(path);
      toast.push(ok);
      order.reload(); prof.reload();
    } catch (e) { toast.push("Action failed", { detail: (e as ApiError).message, bad: true }); }
  }

  async function runFinalThenShip() {
    try {
      await api.post("/quality/final-inspections", {
        sales_order_id: oid, lot_size: o.total_quantity, aql: "2.5", defects_found: 0,
      });
      toast.push("AQL final inspection passed");
      order.reload();
    } catch (e) { toast.push("Inspection failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <div>
      <PageHeader
        eyebrow={<Link to="/sales" style={{ color: "var(--madder)" }}>← Sales Orders</Link>}
        title={o.order_number}
        subtitle={o.customer_po_number ? `Customer PO ${o.customer_po_number}` : undefined}
        actions={
          <div className="inline-actions">
            <Chip status={o.status} />
            {o.status === "draft" && <button className="btn primary" onClick={() => act(`/sales-orders/${oid}/confirm`, "Order confirmed")}>Confirm</button>}
            {(o.status === "confirmed" || o.status === "in_production") && (
              <>
                <button className="btn" onClick={runFinalThenShip}>Run AQL final</button>
                <button className="btn primary" onClick={() => act(`/sales-orders/${oid}/ship`, "Order shipped")}>Ship</button>
              </>
            )}
          </div>
        }
      />

      <div className="grid cols-4" style={{ marginBottom: 20 }}>
        <div className="card stat accent"><div className="k">Total qty</div><div className="v">{num(o.total_quantity)}</div></div>
        <div className="card stat"><div className="k">Order value</div><div className="v" style={{ fontSize: 24 }}>{money(o.total_value, o.currency)}</div></div>
        <div className="card stat accent-madder"><div className="k">Est. profit</div><div className="v" style={{ fontSize: 24 }}>{prof.loading ? "…" : money(prof.data?.profit, o.currency)}</div>
          <div className="foot">{prof.data ? `${parseFloat(prof.data.margin_pct).toFixed(1)}% margin` : "—"}</div></div>
        <div className="card stat"><div className="k">Unit cost</div><div className="v" style={{ fontSize: 24 }}>{prof.loading ? "…" : money(prof.data?.unit_cost, o.currency)}</div><div className="foot">from cost sheet</div></div>
      </div>

      {o.lines.map((ln) => (
        <Card key={ln.id} title={`${styleNo(ln.style_id)} · ${colourNm(ln.colour_id)}`} hint={`${money(ln.unit_price, o.currency)} / unit · ${money(ln.line_value, o.currency)} line`} >
          <div className="table-wrap" style={{ padding: "0 0 6px" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Size</th>
                  {ln.sizes.map((s) => <th key={s.size_label} className="num">{s.size_label}</th>)}
                  <th className="num">Total</th>
                </tr>
              </thead>
              <tbody>
                <tr><td className="mono muted">Ordered</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.ordered_qty}</td>)}<td className="num"><b>{ln.sizes.reduce((a, s) => a + s.ordered_qty, 0)}</b></td></tr>
                <tr><td className="mono muted">Confirmed</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.confirmed_qty || "—"}</td>)}<td className="num">{ln.sizes.reduce((a, s) => a + s.confirmed_qty, 0) || "—"}</td></tr>
                <tr><td className="mono muted">Shipped</td>{ln.sizes.map((s) => <td key={s.size_label} className="num">{s.shipped_qty || "—"}</td>)}<td className="num">{ln.sizes.reduce((a, s) => a + s.shipped_qty, 0) || "—"}</td></tr>
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}
