import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ARInvoice, APBill, CutOrder, PnL, Roll, SalesOrder } from "../api/types";
import { Card, Chip, PageHeader, Spinner, Stat } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { money, num } from "../lib/format";

export default function Dashboard({ permissions }: { permissions: string[] }) {
  const has = (permission: string) => permissions.includes(permission);
  const orders = useAsync(() => has("sales:read") ? api.get<SalesOrder[]>("/sales-orders") : Promise.resolve([]));
  const cuts = useAsync(() => has("production:read") ? api.get<CutOrder[]>("/production/cut-orders") : Promise.resolve([]));
  const rolls = useAsync(() => has("inventory:read") ? api.get<Roll[]>("/inventory/rolls") : Promise.resolve([]));
  const pnl = useAsync(() => has("finance:read") ? api.get<PnL>("/finance/profit-and-loss") : Promise.resolve(null as unknown as PnL));
  const ar = useAsync(() => has("finance:read") ? api.get<ARInvoice[]>("/finance/ar-invoices") : Promise.resolve([]));
  const ap = useAsync(() => has("finance:read") ? api.get<APBill[]>("/finance/ap-bills") : Promise.resolve([]));

  const openOrders = (orders.data ?? []).filter(
    (o) => !["shipped", "closed", "cancelled"].includes(o.status)
  ).length;
  const orderBacklog = (orders.data ?? [])
    .filter((o) => !["cancelled"].includes(o.status))
    .reduce((s, o) => s + parseFloat(o.total_value || "0"), 0);
  const availableRolls = (rolls.data ?? []).filter((r) => r.status === "available").length;
  const arOutstanding = (ar.data ?? []).reduce((s, i) => s + parseFloat(i.outstanding || "0"), 0);
  const apOutstanding = (ap.data ?? []).reduce((s, b) => s + parseFloat(b.outstanding || "0"), 0);

  return (
    <div className="stagger">
      <PageHeader
        eyebrow="Control room"
        title="Operations Dashboard"
        subtitle="A single source of truth across the order-to-cash spine — demand, stock, production and money in one glance."
      />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat
          k="Order backlog"
          v={money(orderBacklog)}
          foot={`${openOrders} open sales orders`}
          accent="indigo"
        />
        <Stat
          k="Net profit (posted)"
          v={!has("finance:read") ? "Restricted" : pnl.loading ? "…" : money(pnl.data?.net_profit)}
          foot={has("finance:read") ? `Revenue ${money(pnl.data?.total_income)}` : "Finance role required"}
          accent="madder"
        />
        <Stat k="AR outstanding" v={has("finance:read") ? money(arOutstanding) : "Restricted"} foot="Owed by customers" />
        <Stat k="AP outstanding" v={has("finance:read") ? money(apOutstanding) : "Restricted"} foot="Owed to suppliers" />
      </div>

      <div className="grid cols-4" style={{ marginBottom: 22 }}>
        <Stat k="Sales orders" v={num(orders.data?.length ?? 0)} />
        <Stat k="Cut orders" v={num(cuts.data?.length ?? 0)} />
        <Stat k="Rolls available" v={num(availableRolls)} foot={`${rolls.data?.length ?? 0} total in register`} />
        <Stat
          k="Journals net"
          v={money(pnl.data?.total_income)}
          foot="Income recognised"
        />
      </div>

      <div className="grid cols-2">
        <Card title="Recent sales orders" hint="latest first">
          {orders.loading ? (
            <Spinner />
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Status</th>
                    <th className="num">Qty</th>
                    <th className="num">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(orders.data ?? [])].reverse().slice(0, 7).map((o) => (
                    <tr key={o.id} className="clickable">
                      <td className="code">
                        <Link to={`/sales/${o.id}`}>{o.order_number}</Link>
                      </td>
                      <td>
                        <Chip status={o.status} />
                      </td>
                      <td className="num">{num(o.total_quantity)}</td>
                      <td className="num">{money(o.total_value, o.currency)}</td>
                    </tr>
                  ))}
                  {(orders.data ?? []).length === 0 && (
                    <tr>
                      <td colSpan={4} className="muted">
                        No sales orders yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Production floor" hint="cut orders">
          {cuts.loading ? (
            <Spinner />
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Cut order</th>
                    <th>Status</th>
                    <th className="num">Fabric req.</th>
                    <th className="num">Pieces</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(cuts.data ?? [])].reverse().slice(0, 7).map((c) => (
                    <tr key={c.id}>
                      <td className="code">{c.order_number}</td>
                      <td>
                        <Chip status={c.status} />
                      </td>
                      <td className="num">{num(c.fabric_required)} m</td>
                      <td className="num">{num(c.pieces_cut)}</td>
                    </tr>
                  ))}
                  {(cuts.data ?? []).length === 0 && (
                    <tr>
                      <td colSpan={4} className="muted">
                        No cut orders yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
