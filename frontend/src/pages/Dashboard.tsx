import { Link } from "react-router-dom";
import { api } from "../api/client";
import { APBill, ARInvoice, CutOrder, PnL, Roll, SalesOrder } from "../api/types";
import { Card, Chip, PageHeader, Spinner, Stat } from "../components/ui";
import { money, num } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export default function Dashboard({ permissions }: { permissions: string[] }) {
  const has = (permission: string) => permissions.includes(permission);
  const orders = useAsync(() => has("sales:read") ? api.get<SalesOrder[]>("/sales-orders") : Promise.resolve([]));
  const cuts = useAsync(() => has("production:read") ? api.get<CutOrder[]>("/production/cut-orders") : Promise.resolve([]));
  const rolls = useAsync(() => has("inventory:read") ? api.get<Roll[]>("/inventory/rolls") : Promise.resolve([]));
  const pnl = useAsync(() => has("finance:read") ? api.get<PnL>("/finance/profit-and-loss") : Promise.resolve(null as unknown as PnL));
  const ar = useAsync(() => has("finance:read") ? api.get<ARInvoice[]>("/finance/ar-invoices") : Promise.resolve([]));
  const ap = useAsync(() => has("finance:read") ? api.get<APBill[]>("/finance/ap-bills") : Promise.resolve([]));

  const openOrders = (orders.data ?? []).filter((order) => !["shipped", "closed", "cancelled"].includes(order.status)).length;
  const orderBacklog = (orders.data ?? [])
    .filter((order) => order.status !== "cancelled")
    .reduce((sum, order) => sum + parseFloat(order.total_value || "0"), 0);
  const availableRolls = (rolls.data ?? []).filter((roll) => roll.status === "available").length;
  const arOutstanding = (ar.data ?? []).reduce((sum, invoice) => sum + parseFloat(invoice.outstanding || "0"), 0);
  const apOutstanding = (ap.data ?? []).reduce((sum, bill) => sum + parseFloat(bill.outstanding || "0"), 0);

  return (
    <div className="stagger">
      <PageHeader
        eyebrow="Control room"
        title="Operations Dashboard"
        subtitle="A single source of truth across demand, stock, production, and finance."
      />

      <div className="section-heading">
        <div><span>01</span> Commercial snapshot</div>
        <p>Live values from current transactions</p>
      </div>
      <div className="grid cols-4 metric-grid primary-metrics">
        <Stat k="Order backlog" v={money(orderBacklog)} foot={`${openOrders} open sales orders`} accent="indigo" />
        <Stat
          k="Net profit (posted)"
          v={!has("finance:read") ? "Restricted" : pnl.loading ? "..." : money(pnl.data?.net_profit)}
          foot={has("finance:read") ? `Revenue ${money(pnl.data?.total_income)}` : "Finance role required"}
          accent="madder"
        />
        <Stat k="AR outstanding" v={has("finance:read") ? money(arOutstanding) : "Restricted"} foot="Owed by customers" />
        <Stat k="AP outstanding" v={has("finance:read") ? money(apOutstanding) : "Restricted"} foot="Owed to suppliers" />
      </div>

      <div className="section-heading compact">
        <div><span>02</span> Live operations</div>
        <p>Current document and stock counts</p>
      </div>
      <div className="grid cols-4 metric-grid secondary-metrics">
        <Stat k="Sales orders" v={num(orders.data?.length ?? 0)} />
        <Stat k="Cut orders" v={num(cuts.data?.length ?? 0)} />
        <Stat k="Rolls available" v={num(availableRolls)} foot={`${rolls.data?.length ?? 0} total in register`} />
        <Stat k="Journals net" v={money(pnl.data?.total_income)} foot="Income recognised" />
      </div>

      <div className="section-heading">
        <div><span>03</span> Recent activity</div>
        <p>Latest commercial and production movement</p>
      </div>
      <div className="grid cols-2 dashboard-tables">
        <Card title="Recent sales orders" hint="Latest first">
          {orders.loading ? <Spinner /> : (
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Order</th><th>Status</th><th className="num">Qty</th><th className="num">Value</th></tr></thead>
                <tbody>
                  {[...(orders.data ?? [])].reverse().slice(0, 7).map((order) => (
                    <tr key={order.id} className="clickable">
                      <td className="code"><Link to={`/sales/${order.id}`}>{order.order_number}</Link></td>
                      <td><Chip status={order.status} /></td>
                      <td className="num">{num(order.total_quantity)}</td>
                      <td className="num">{money(order.total_value, order.currency)}</td>
                    </tr>
                  ))}
                  {(orders.data ?? []).length === 0 && <tr><td colSpan={4} className="muted">No sales orders yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Production floor" hint="Cut orders">
          {cuts.loading ? <Spinner /> : (
            <div className="table-wrap">
              <table className="tbl">
                <thead><tr><th>Cut order</th><th>Status</th><th className="num">Fabric req.</th><th className="num">Pieces</th></tr></thead>
                <tbody>
                  {[...(cuts.data ?? [])].reverse().slice(0, 7).map((cut) => (
                    <tr key={cut.id}>
                      <td className="code">{cut.order_number}</td>
                      <td><Chip status={cut.status} /></td>
                      <td className="num">{num(cut.fabric_required)} m</td>
                      <td className="num">{num(cut.pieces_cut)}</td>
                    </tr>
                  ))}
                  {(cuts.data ?? []).length === 0 && <tr><td colSpan={4} className="muted">No cut orders yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
