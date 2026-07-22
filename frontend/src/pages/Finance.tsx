import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Account, APBill, ARInvoice, JournalEntry, PnL } from "../api/types";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { money, titled } from "../lib/format";

export default function Finance() {
  const [tab, setTab] = useState("overview");
  return (
    <div>
      <PageHeader
        eyebrow="Module 8"
        title="Finance"
        subtitle="Double-entry journals with event-driven auto-posting — receipts and shipments book their own accounting. AR, AP and a live P&L follow automatically."
      />
      <Tabs
        tabs={[
          { key: "overview", label: "P&L & Accounts" },
          { key: "journals", label: "Journals" },
          { key: "ar", label: "Receivables" },
          { key: "ap", label: "Payables" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "overview" && <Overview />}
      {tab === "journals" && <Journals />}
      {tab === "ar" && <Receivables />}
      {tab === "ap" && <Payables />}
    </div>
  );
}

function Overview() {
  const pnl = useAsync(() => api.get<PnL>("/finance/profit-and-loss"));
  const accounts = useAsync(() => api.get<Account[]>("/finance/accounts"));
  const toast = useToast();
  async function seed() {
    try { await api.post("/finance/accounts/seed-defaults"); toast.push("Chart of accounts seeded"); accounts.reload(); }
    catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }
  return (
    <div className="stagger">
      <div className="grid cols-3" style={{ marginBottom: 20 }}>
        <div className="card stat accent"><div className="k">Income (posted)</div><div className="v" style={{ fontSize: 26 }}>{pnl.loading ? "…" : money(pnl.data?.total_income)}</div></div>
        <div className="card stat"><div className="k">Expense (posted)</div><div className="v" style={{ fontSize: 26 }}>{pnl.loading ? "…" : money(pnl.data?.total_expense)}</div></div>
        <div className="card stat accent-madder"><div className="k">Net profit</div><div className="v" style={{ fontSize: 26 }}>{pnl.loading ? "…" : money(pnl.data?.net_profit)}</div></div>
      </div>

      <Card title="Chart of accounts" hint="hierarchical"
        actions={<button className="btn sm" onClick={seed}>Seed defaults</button>}>
        {accounts.loading ? <Spinner /> : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>Code</th><th>Account</th><th>Type</th><th className="num">Balance (posted)</th></tr></thead>
              <tbody>
                {accounts.data?.map((a) => (
                  <tr key={a.id}>
                    <td className="code">{a.code}</td>
                    <td>{a.name}</td>
                    <td><Chip tone="neutral" label={titled(a.type)} /></td>
                    <td className="num">{pnl.data?.by_account?.[a.code] ? money(pnl.data.by_account[a.code]) : "—"}</td>
                  </tr>
                ))}
                {!accounts.data?.length && <tr><td colSpan={4} className="muted">No accounts. Seed the defaults to begin.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Journals() {
  const [items, setItems] = useState<JournalEntry[]>([]);
  const accounts = useAsync(() => api.get<Account[]>("/finance/accounts"));
  const [open, setOpen] = useState(false);
  const toast = useToast();

  async function reverse(id: number) {
    try {
      const rev = await api.post<JournalEntry>(`/finance/journal-entries/${id}/reverse`);
      const orig = await api.get<JournalEntry>(`/finance/journal-entries/${id}`);
      setItems((xs) => [rev, ...xs.map((x) => (x.id === id ? orig : x))]);
      toast.push("Journal reversed");
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <Card title="Journal entries" hint="double-entry · posted are immutable"
      actions={<button className="btn primary sm" onClick={() => setOpen(true)}>+ New journal</button>}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Entry</th><th>Memo</th><th>Source</th><th>Status</th><th className="num">Debit</th><th className="num">Credit</th><th></th></tr></thead>
          <tbody>
            {items.map((j) => (
              <tr key={j.id}>
                <td className="code">{j.entry_number}</td>
                <td>{j.memo || "—"}</td>
                <td><Chip tone="neutral" label={j.source} /></td>
                <td><Chip status={j.status} /></td>
                <td className="num">{money(j.total_debit)}</td>
                <td className="num">{money(j.total_credit)}</td>
                <td className="right">{j.status === "posted" && <button className="btn sm" onClick={() => reverse(j.id)}>Reverse</button>}</td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan={7} className="muted">No journals in view. Post one, or trigger auto-posting via a goods receipt / shipment.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <JournalForm accounts={accounts.data ?? []} onClose={() => setOpen(false)} onDone={(j) => { setOpen(false); setItems([j, ...items]); }} />}
    </Card>
  );
}

function JournalForm({ accounts, onClose, onDone }: { accounts: Account[]; onClose: () => void; onDone: (j: JournalEntry) => void }) {
  const toast = useToast();
  const [memo, setMemo] = useState("");
  const [lines, setLines] = useState([
    { account_code: accounts[0]?.code ?? "", debit: "0", credit: "0", description: "" },
    { account_code: accounts[1]?.code ?? "", debit: "0", credit: "0", description: "" },
  ]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const totalD = lines.reduce((a, l) => a + parseFloat(l.debit || "0"), 0);
  const totalC = lines.reduce((a, l) => a + parseFloat(l.credit || "0"), 0);

  async function save() {
    setSaving(true); setError(null);
    try {
      const j = await api.post<JournalEntry>("/finance/journal-entries", { memo, lines });
      toast.push(`Journal ${j.entry_number} posted`); onDone(j);
    } catch (e) { const m = (e as ApiError).message; setError(m); toast.push("Failed", { detail: m, bad: true }); }
    finally { setSaving(false); }
  }

  return (
    <Drawer title="New journal entry" sub="Module 8.2" onClose={onClose}
      footer={<>
        <span className="mono muted" style={{ marginRight: "auto", fontSize: 11 }}>
          Dr {money(totalD)} · Cr {money(totalC)} {Math.abs(totalD - totalC) < 0.005 ? "✓" : "≠"}
        </span>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={saving} onClick={save}>Post</button>
      </>}>
      {error && <ErrorBox message={error} />}
      <Field label="Memo"><input className="input" value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="Opening balance" /></Field>
      <div className="flex-between" style={{ margin: "6px 0 10px" }}>
        <span className="mono muted" style={{ fontSize: 11 }}>LINES</span>
        <button className="btn sm" onClick={() => setLines([...lines, { account_code: accounts[0]?.code ?? "", debit: "0", credit: "0", description: "" }])}>+ Add line</button>
      </div>
      {lines.map((l, i) => (
        <div className="form-row three" key={i} style={{ marginBottom: 8, alignItems: "end" }}>
          <Field label="Account">
            <select className="select" value={l.account_code} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, account_code: e.target.value } : x))}>
              {accounts.map((a) => <option key={a.code} value={a.code}>{a.code} — {a.name}</option>)}
            </select>
          </Field>
          <Field label="Debit"><input className="input mono" value={l.debit} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, debit: e.target.value } : x))} /></Field>
          <Field label="Credit"><input className="input mono" value={l.credit} onChange={(e) => setLines(lines.map((x, idx) => idx === i ? { ...x, credit: e.target.value } : x))} /></Field>
        </div>
      ))}
    </Drawer>
  );
}

function Receivables() {
  const ar = useAsync(() => api.get<ARInvoice[]>("/finance/ar-invoices"));
  const toast = useToast();
  async function settle(inv: ARInvoice) {
    const v = prompt(`Settle how much of ${inv.invoice_number}? (outstanding ${inv.outstanding})`, inv.outstanding);
    if (!v) return;
    try { await api.post(`/finance/ar-invoices/${inv.id}/settle`, { amount: v }); toast.push("Receipt applied"); ar.reload(); }
    catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }
  return (
    <Card title="Accounts receivable" hint="auto-raised on shipment">
      {ar.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Invoice</th><th className="num">Amount</th><th className="num">Settled</th><th className="num">Outstanding</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {ar.data?.map((i) => (
                <tr key={i.id}>
                  <td className="code">{i.invoice_number}</td>
                  <td className="num">{money(i.amount)}</td>
                  <td className="num">{money(i.settled_amount)}</td>
                  <td className="num" style={{ color: parseFloat(i.outstanding) > 0 ? "var(--madder)" : "var(--ok)" }}>{money(i.outstanding)}</td>
                  <td><Chip status={i.status} /></td>
                  <td className="right">{parseFloat(i.outstanding) > 0 && <button className="btn sm" onClick={() => settle(i)}>Settle</button>}</td>
                </tr>
              ))}
              {!ar.data?.length && <tr><td colSpan={6} className="muted">No receivables yet. Ship an order to raise one.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Payables() {
  const ap = useAsync(() => api.get<APBill[]>("/finance/ap-bills"));
  const toast = useToast();
  async function settle(bill: APBill) {
    const v = prompt(`Pay how much of ${bill.bill_number}? (outstanding ${bill.outstanding})`, bill.outstanding);
    if (!v) return;
    try { await api.post(`/finance/ap-bills/${bill.id}/settle`, { amount: v }); toast.push("Payment applied"); ap.reload(); }
    catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }
  return (
    <Card title="Accounts payable" hint="auto-raised on goods receipt">
      {ap.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Bill</th><th className="num">Amount</th><th className="num">Settled</th><th className="num">Outstanding</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {ap.data?.map((b) => (
                <tr key={b.id}>
                  <td className="code">{b.bill_number}</td>
                  <td className="num">{money(b.amount)}</td>
                  <td className="num">{money(b.settled_amount)}</td>
                  <td className="num" style={{ color: parseFloat(b.outstanding) > 0 ? "var(--madder)" : "var(--ok)" }}>{money(b.outstanding)}</td>
                  <td><Chip status={b.status} /></td>
                  <td className="right">{parseFloat(b.outstanding) > 0 && <button className="btn sm" onClick={() => settle(b)}>Pay</button>}</td>
                </tr>
              ))}
              {!ap.data?.length && <tr><td colSpan={6} className="muted">No payables yet. Post a goods receipt to raise one.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
