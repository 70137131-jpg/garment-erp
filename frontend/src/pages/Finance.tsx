import { useState } from "react";
import { api, ApiError } from "../api/client";
import { Account, AgingReport, APBill, ARInvoice, BalanceSheet, CashFlow, JournalEntry, PnL, TrialBalance } from "../api/types";
import { useAuthorization } from "../auth/Authorization";
import { Card, Chip, Drawer, ErrorBox, Field, PageHeader, Spinner, Tabs } from "../components/ui";
import { useToast } from "../components/Toast";
import { useAsync } from "../lib/useAsync";
import { PdfLink } from "../components/ListTools";
import { money, titled } from "../lib/format";

interface GLLine { journal_id: number; entry_number: string; entry_date?: string; account_code: string; account_name: string; description?: string; debit: string; credit: string; running_balance: string; }

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
          { key: "reports", label: "Financial Reports" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "overview" && <Overview />}
      {tab === "journals" && <Journals />}
      {tab === "ar" && <Receivables />}
      {tab === "ap" && <Payables />}
      {tab === "reports" && <FinancialReports />}
    </div>
  );
}

function Overview() {
  const { can } = useAuthorization();
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
        actions={can("finance") ? <button className="btn sm" onClick={seed}>Seed defaults</button> : undefined}>
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
  const { can } = useAuthorization();
  const journals = useAsync(() => api.get<JournalEntry[]>("/finance/journal-entries"));
  const accounts = useAsync(() => api.get<Account[]>("/finance/accounts"));
  const [open, setOpen] = useState(false);
  const toast = useToast();

  async function reverse(id: number) {
    try {
      await api.post<JournalEntry>(`/finance/journal-entries/${id}/reverse`);
      journals.reload();
      toast.push("Journal reversed");
    } catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }

  return (
    <Card title="Journal entries" hint="double-entry · posted are immutable"
      actions={can("finance") ? <button className="btn primary sm" onClick={() => setOpen(true)}>+ New journal</button> : undefined}>
      <div className="table-wrap">
        <table className="tbl">
          <thead><tr><th>Entry</th><th>Memo</th><th>Source</th><th>Status</th><th className="num">Debit</th><th className="num">Credit</th><th></th></tr></thead>
          <tbody>
            {(journals.data ?? []).map((j) => (
              <tr key={j.id}>
                <td className="code">{j.entry_number}</td>
                <td>{j.memo || "—"}</td>
                <td><Chip tone="neutral" label={j.source} /></td>
                <td><Chip status={j.status} /></td>
                <td className="num">{money(j.total_debit)}</td>
                <td className="num">{money(j.total_credit)}</td>
                <td className="right">{can("finance") && j.status === "posted" && <button className="btn sm" onClick={() => reverse(j.id)}>Reverse</button>}</td>
              </tr>
            ))}
            {!journals.data?.length && <tr><td colSpan={7} className="muted">No journals yet. Post one, or trigger auto-posting via a goods receipt or shipment.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <JournalForm accounts={accounts.data ?? []} onClose={() => setOpen(false)} onDone={() => { setOpen(false); journals.reload(); }} />}
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
  const { can } = useAuthorization();
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
                  <td className="right"><div className="inline-actions"><PdfLink path={`/documents/invoices/${i.id}/pdf`} />{can("finance") && parseFloat(i.outstanding) > 0 && <button className="btn sm" onClick={() => settle(i)}>Settle</button>}</div></td>
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
  const { can } = useAuthorization();
  const ap = useAsync(() => api.get<APBill[]>("/finance/ap-bills"));
  const toast = useToast();
  async function settle(bill: APBill) {
    const v = prompt(`Pay how much of ${bill.bill_number}? (outstanding ${bill.outstanding})`, bill.outstanding);
    if (!v) return;
    try { await api.post(`/finance/ap-bills/${bill.id}/settle`, { amount: v }); toast.push("Payment applied"); ap.reload(); }
    catch (e) { toast.push("Failed", { detail: (e as ApiError).message, bad: true }); }
  }
  async function matchInvoice(bill: APBill) {
    if (!bill.goods_receipt_id) return;
    const supplierInvoiceNumber = prompt("Supplier invoice number");
    if (!supplierInvoiceNumber) return;
    const amount = prompt(`Invoice amount (received value ${bill.received_amount})`, bill.received_amount);
    if (!amount) return;
    try {
      await api.post("/finance/supplier-invoices", {
        goods_receipt_id: bill.goods_receipt_id,
        supplier_invoice_number: supplierInvoiceNumber,
        amount,
        bill_date: new Date().toISOString().slice(0, 10),
      });
      toast.push("Supplier invoice matched");
      ap.reload();
    } catch (e) { toast.push("Match failed", { detail: (e as ApiError).message, bad: true }); }
  }
  return (
    <Card title="Accounts payable" hint="match supplier invoices against PO and receipt before payment">
      {ap.loading ? <Spinner /> : (
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Bill</th><th>Supplier invoice</th><th>Match</th><th className="num">Amount</th><th className="num">Variance</th><th className="num">Outstanding</th><th></th></tr></thead>
            <tbody>
              {ap.data?.map((b) => (
                <tr key={b.id}>
                  <td className="code">{b.bill_number}</td>
                  <td className="code">{b.supplier_invoice_number || "—"}</td>
                  <td><Chip status={b.match_status} /></td>
                  <td className="num">{money(b.amount)}</td>
                  <td className="num">{money(b.variance_amount)}</td>
                  <td className="num" style={{ color: parseFloat(b.outstanding) > 0 ? "var(--madder)" : "var(--ok)" }}>{money(b.outstanding)}</td>
                  <td className="right"><div className="inline-actions">{can("finance") && b.match_status === "pending" && !!b.goods_receipt_id && <button className="btn sm" onClick={() => matchInvoice(b)}>Match invoice</button>}{can("finance") && b.match_status === "matched" && parseFloat(b.outstanding) > 0 && <button className="btn sm" onClick={() => settle(b)}>Pay</button>}</div></td>
                </tr>
              ))}
              {!ap.data?.length && <tr><td colSpan={7} className="muted">No payables yet. Post a goods receipt to raise a matchable accrual.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function FinancialReports() {
  const today = new Date().toISOString().slice(0, 10);
  const yearStart = `${today.slice(0, 4)}-01-01`;
  const [asOf, setAsOf] = useState(today);
  const [from, setFrom] = useState(yearStart);
  const trial = useAsync(() => api.get<TrialBalance>(`/finance/reports/trial-balance?as_of=${asOf}`), [asOf]);
  const sheet = useAsync(() => api.get<BalanceSheet>(`/finance/reports/balance-sheet?as_of=${asOf}`), [asOf]);
  const cash = useAsync(() => api.get<CashFlow>(`/finance/reports/cash-flow?date_from=${from}&date_to=${asOf}`), [from, asOf]);
  const ar = useAsync(() => api.get<AgingReport>(`/finance/reports/ar-aging?as_of=${asOf}`), [asOf]);
  const ap = useAsync(() => api.get<AgingReport>(`/finance/reports/ap-aging?as_of=${asOf}`), [asOf]);
  const ledger = useAsync(() => api.get<GLLine[]>(`/finance/general-ledger?date_from=${from}&date_to=${asOf}&limit=500`), [from, asOf]);
  return <div className="stagger">
    <Card title="Reporting period" pad><div className="form-row three"><Field label="From"><input className="input mono" type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></Field><Field label="As of"><input className="input mono" type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} /></Field><div className="field"><label>Export</label><a className="btn" href={`/api/finance/reports/trial-balance/export?as_of=${asOf}`} download>Trial balance CSV</a></div></div></Card>
    <div className="grid cols-4" style={{ margin: "16px 0" }}><div className="card stat accent"><div className="k">Assets</div><div className="v">{money(sheet.data?.total_assets)}</div></div><div className="card stat"><div className="k">Liabilities</div><div className="v">{money(sheet.data?.total_liabilities)}</div></div><div className="card stat"><div className="k">Equity</div><div className="v">{money(sheet.data?.total_equity)}</div></div><div className="card stat accent-madder"><div className="k">Net cash change</div><div className="v">{money(cash.data?.net_change)}</div></div></div>
    <div className="grid cols-2">
      <Card title="Trial balance" hint={trial.data?.as_of}><div className="table-wrap"><table className="tbl"><thead><tr><th>Account</th><th>Type</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead><tbody>{trial.data?.lines.map((line) => <tr key={line.account_id}><td><span className="code">{line.account_code}</span> {line.account_name}</td><td>{titled(line.account_type)}</td><td className="num">{money(line.debit)}</td><td className="num">{money(line.credit)}</td></tr>)}</tbody></table></div></Card>
      <Card title="Cash flow" hint={`${from} to ${asOf}`} pad><dl className="kv"><dt>Operating</dt><dd>{money(cash.data?.operating)}</dd><dt>Investing</dt><dd>{money(cash.data?.investing)}</dd><dt>Financing</dt><dd>{money(cash.data?.financing)}</dd><dt>Net change</dt><dd><b>{money(cash.data?.net_change)}</b></dd></dl></Card>
      <AgingCard title="Receivables aging" report={ar.data} />
      <AgingCard title="Payables aging" report={ap.data} />
    </div>
    <Card title="General ledger inquiry" hint={`${ledger.data?.length ?? 0} posted lines`}>
      <div className="table-wrap"><table className="tbl"><thead><tr><th>Date</th><th>Entry</th><th>Account</th><th>Description</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Running</th></tr></thead><tbody>{ledger.data?.map((line, index) => <tr key={`${line.journal_id}-${line.account_code}-${index}`}><td className="mono muted">{line.entry_date || "-"}</td><td className="code">{line.entry_number}</td><td>{line.account_code} {line.account_name}</td><td>{line.description || "-"}</td><td className="num">{money(line.debit)}</td><td className="num">{money(line.credit)}</td><td className="num">{money(line.running_balance)}</td></tr>)}</tbody></table></div>
    </Card>
  </div>;
}

function AgingCard({ title, report }: { title: string; report: AgingReport | null }) {
  return <Card title={title} hint={`Total ${money(report?.total)}`}><div className="table-wrap"><table className="tbl"><thead><tr><th>Party</th><th className="num">Current</th><th className="num">1-30</th><th className="num">31-60</th><th className="num">61-90</th><th className="num">90+</th><th className="num">Total</th></tr></thead><tbody>{report?.parties.map((row) => <tr key={row.party_id}><td className="code">#{row.party_id}</td><td className="num">{money(row.current)}</td><td className="num">{money(row.days_1_30)}</td><td className="num">{money(row.days_31_60)}</td><td className="num">{money(row.days_61_90)}</td><td className="num">{money(row.over_90)}</td><td className="num"><b>{money(row.total)}</b></td></tr>)}{!report?.parties.length && <tr><td colSpan={7} className="muted">No outstanding balances.</td></tr>}</tbody></table></div></Card>;
}
