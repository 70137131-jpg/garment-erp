"""Finance services: CoA seeding, double-entry journal posting, AR/AP, P&L."""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..kernel.numbering import next_document_number
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_money
from ..procurement.models import GoodsReceipt
from .models import (
    Account,
    APBill,
    AccountType,
    JournalEntry,
    JournalLine,
    JournalSource,
    JournalStatus,
    SupplierInvoiceCreate,
    ThreeWayMatchStatus,
)

# Well-known account codes used by auto-posting.
ACC_INVENTORY = "1000"
ACC_AR = "1100"
ACC_BANK = "1200"
ACC_AP = "2000"
ACC_GRNI = "2100"
ACC_SALES = "4000"
ACC_COGS = "5000"
ACC_PURCHASE_PRICE_VARIANCE = "6100"
# Manufacturing variance accounts (6.8). These receive a *reclassification* of
# cost already sitting in COGS — never a second charge. See
# ``app.costing.actuals`` for why the GL is actual-cost driven.
ACC_MATERIAL_PRICE_VARIANCE = "6110"
ACC_MATERIAL_USAGE_VARIANCE = "6120"
ACC_LABOUR_RATE_VARIANCE = "6130"
ACC_LABOUR_EFFICIENCY_VARIANCE = "6140"
ACC_OVERHEAD_VARIANCE = "6150"
ACC_SUBCONTRACT_VARIANCE = "6160"

_DEFAULT_COA: List[Tuple[str, str, AccountType]] = [
    (ACC_INVENTORY, "Inventory", AccountType.asset),
    (ACC_AR, "Accounts Receivable", AccountType.asset),
    (ACC_BANK, "Bank", AccountType.asset),
    (ACC_AP, "Accounts Payable", AccountType.liability),
    (ACC_GRNI, "Goods Received Not Invoiced", AccountType.liability),
    ("3000", "Retained Earnings", AccountType.equity),
    (ACC_SALES, "Sales Revenue", AccountType.income),
    (ACC_COGS, "Cost of Goods Sold", AccountType.expense),
    (ACC_PURCHASE_PRICE_VARIANCE, "Purchase Price Variance", AccountType.expense),
    (ACC_MATERIAL_PRICE_VARIANCE, "Material Price Variance", AccountType.expense),
    (ACC_MATERIAL_USAGE_VARIANCE, "Material Usage Variance", AccountType.expense),
    (ACC_LABOUR_RATE_VARIANCE, "Labour Rate Variance", AccountType.expense),
    (ACC_LABOUR_EFFICIENCY_VARIANCE, "Labour Efficiency Variance", AccountType.expense),
    (ACC_OVERHEAD_VARIANCE, "Overhead Variance", AccountType.expense),
    (ACC_SUBCONTRACT_VARIANCE, "Subcontract Variance", AccountType.expense),
    ("6000", "Overhead Expense", AccountType.expense),
]

journal_state_machine = StateMachine(
    transitions={
        JournalStatus.draft.value: {JournalStatus.posted.value},
        JournalStatus.posted.value: {JournalStatus.reversed.value},
        JournalStatus.reversed.value: set(),
    },
    initial=JournalStatus.draft.value,
)


class FinanceError(Exception):
    """Domain error in a finance operation."""


def seed_chart_of_accounts(session: Session) -> None:
    """Idempotently ensure the default chart of accounts exists."""
    for code, name, type_ in _DEFAULT_COA:
        existing = session.exec(select(Account).where(Account.code == code)).first()
        if existing is None:
            session.add(Account(code=code, name=name, type=type_))
    session.flush()


def account_by_code(session: Session, code: str) -> Optional[Account]:
    return session.exec(select(Account).where(Account.code == code)).first()


def post_journal(
    session: Session,
    *,
    lines: List[Tuple[str, Decimal, Decimal, Optional[str]]],
    memo: Optional[str],
    source: JournalSource,
    reference_type: Optional[str] = None,
    reference_id: Optional[int] = None,
    entry_date=None,
) -> JournalEntry:
    """Create and post a balanced journal in one step.

    ``lines`` are ``(account_code, debit, credit, description)``. Debits must
    equal credits and there must be at least two lines. The entry is created
    already ``posted`` (system/manual postings are atomic with their operation).
    """
    if len(lines) < 2:
        raise FinanceError("A journal needs at least two lines")

    total_debit = quantize_money(sum((d for _, d, _, _ in lines), Decimal("0")))
    total_credit = quantize_money(sum((c for _, _, c, _ in lines), Decimal("0")))
    if total_debit != total_credit:
        raise FinanceError(
            f"Journal is not balanced: debits {total_debit} ≠ credits {total_credit}"
        )
    if total_debit == 0:
        raise FinanceError("Journal has no value")

    number = next_document_number(session, "JOURNAL", "JE")
    entry = JournalEntry(
        entry_number=number,
        entry_date=entry_date,
        memo=memo,
        source=source,
        status=JournalStatus.posted,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    session.add(entry)
    session.flush()

    for code, debit, credit, description in lines:
        account = account_by_code(session, code)
        if account is None:
            raise FinanceError(f"Account '{code}' does not exist")
        session.add(
            JournalLine(
                journal_entry_id=entry.id,
                account_id=account.id,
                debit=quantize_money(debit),
                credit=quantize_money(credit),
                description=description,
            )
        )
    session.flush()
    return entry


def match_supplier_invoice(
    session: Session, payload: SupplierInvoiceCreate
) -> APBill:
    """Complete the PO â†’ receipt â†’ supplier-invoice match for one receipt.

    The goods receipt already posted the inventory and GRNI accrual.  Matching
    moves that accrual to Accounts Payable.  Any invoice-price difference is
    explicitly booked to Purchase Price Variance and remains payment-blocked
    until finance resolves it.
    """
    if payload.amount <= 0:
        raise FinanceError("Supplier invoice amount must be positive")
    invoice_number = payload.supplier_invoice_number.strip()
    if not invoice_number:
        raise FinanceError("Supplier invoice number is required")

    receipt = session.get(GoodsReceipt, payload.goods_receipt_id)
    if receipt is None:
        raise FinanceError("Goods receipt not found")
    bill = session.exec(
        select(APBill).where(APBill.goods_receipt_id == receipt.id)
    ).first()
    if bill is None:
        raise FinanceError("No payable accrual exists for this goods receipt")
    if bill.purchase_order_id != receipt.purchase_order_id:
        raise FinanceError("Payable accrual does not match the goods receipt purchase order")
    if bill.supplier_id != receipt.supplier_id:
        raise FinanceError("Payable accrual does not match the goods receipt supplier")
    if bill.supplier_invoice_number is not None:
        raise FinanceError("Goods receipt has already been matched to a supplier invoice")
    if bill.settled_amount:
        raise FinanceError("Cannot match an AP bill that has already been paid")
    duplicate = session.exec(
        select(APBill).where(
            APBill.supplier_id == receipt.supplier_id,
            APBill.supplier_invoice_number == invoice_number,
        )
    ).first()
    if duplicate is not None:
        raise FinanceError("Supplier invoice number already exists for this supplier")

    expected = bill.received_amount
    variance = quantize_money(payload.amount - expected)
    lines = [
        (ACC_GRNI, expected, Decimal("0"), "Clear goods received not invoiced"),
        (ACC_AP, Decimal("0"), payload.amount, "Supplier invoice payable"),
    ]
    if variance > 0:
        lines.insert(
            1,
            (ACC_PURCHASE_PRICE_VARIANCE, variance, Decimal("0"), "Supplier price variance"),
        )
    elif variance < 0:
        lines.insert(
            1,
            (ACC_PURCHASE_PRICE_VARIANCE, Decimal("0"), -variance, "Supplier price variance"),
        )
    post_journal(
        session,
        lines=lines,
        memo=f"Supplier invoice {invoice_number} matched to {receipt.receipt_number}",
        source=JournalSource.system,
        reference_type="supplier_invoice",
        reference_id=bill.id,
        entry_date=payload.bill_date,
    )
    bill.supplier_invoice_number = invoice_number
    bill.amount = quantize_money(payload.amount)
    bill.variance_amount = variance
    bill.match_status = (
        ThreeWayMatchStatus.matched
        if abs(variance) <= Decimal("0.01")
        else ThreeWayMatchStatus.exception
    )
    bill.bill_date = payload.bill_date or date.today()
    bill.due_date = payload.due_date
    session.add(bill)
    session.flush()
    return bill


def reverse_journal(session: Session, entry: JournalEntry) -> JournalEntry:
    """Post a mirror entry that reverses a posted journal (never edit in place)."""
    journal_state_machine.assert_transition(
        entry.status.value, JournalStatus.reversed.value
    )
    number = next_document_number(session, "JOURNAL", "JE")
    reversal = JournalEntry(
        entry_number=number,
        entry_date=entry.entry_date,
        memo=f"Reversal of {entry.entry_number}",
        source=JournalSource.system,
        status=JournalStatus.posted,
        reference_type=entry.reference_type,
        reference_id=entry.reference_id,
        reversal_of_id=entry.id,
    )
    session.add(reversal)
    session.flush()
    for line in entry.lines:
        session.add(
            JournalLine(
                journal_entry_id=reversal.id,
                account_id=line.account_id,
                debit=line.credit,
                credit=line.debit,
                description=f"Reversal: {line.description or ''}".strip(),
            )
        )
    entry.status = JournalStatus.reversed
    session.add(entry)
    session.flush()
    return reversal


def account_balance(session: Session, account: Account) -> Decimal:
    """Signed balance from posted journals, in the account's natural direction.

    Assets/expenses are debit-normal (debit − credit); liabilities/equity/income
    are credit-normal (credit − debit).
    """
    debit, credit = session.exec(
        select(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.account_id == account.id,
            JournalEntry.status == JournalStatus.posted,
        )
    ).one()
    debit, credit = Decimal(debit), Decimal(credit)
    if account.type in (AccountType.asset, AccountType.expense):
        return quantize_money(debit - credit)
    return quantize_money(credit - debit)


def profit_and_loss(session: Session) -> Dict:
    """Sum income and expense accounts from posted journals into a P&L (8.6)."""
    accounts = session.exec(
        select(Account).where(
            Account.type.in_([AccountType.income, AccountType.expense])
        )
    ).all()
    by_account: Dict[str, str] = {}
    total_income = Decimal("0")
    total_expense = Decimal("0")
    for acc in accounts:
        bal = account_balance(session, acc)
        if bal == 0:
            continue
        by_account[acc.code] = str(bal)
        if acc.type == AccountType.income:
            total_income += bal
        else:
            total_expense += bal
    net = quantize_money(total_income - total_expense)
    return {
        "total_income": quantize_money(total_income),
        "total_expense": quantize_money(total_expense),
        "net_profit": net,
        "by_account": by_account,
    }


def account_totals(
    session: Session,
    account: Account,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> tuple[Decimal, Decimal]:
    stmt = (
        select(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.account_id == account.id,
            JournalEntry.status == JournalStatus.posted,
        )
    )
    if date_from is not None:
        stmt = stmt.where(or_(JournalEntry.entry_date >= date_from, JournalEntry.entry_date.is_(None)))
    if date_to is not None:
        stmt = stmt.where(or_(JournalEntry.entry_date <= date_to, JournalEntry.entry_date.is_(None)))
    debit, credit = session.exec(stmt).one()
    return quantize_money(Decimal(debit)), quantize_money(Decimal(credit))


def trial_balance(session: Session, as_of: date) -> Dict:
    lines = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for account in session.exec(select(Account).order_by(Account.code)).all():
        debit, credit = account_totals(session, account, date_to=as_of)
        net = quantize_money(debit - credit)
        debit_balance = max(net, Decimal("0"))
        credit_balance = max(-net, Decimal("0"))
        if debit_balance == 0 and credit_balance == 0:
            continue
        lines.append({
            "account_id": account.id,
            "account_code": account.code,
            "account_name": account.name,
            "account_type": account.type,
            "debit": debit_balance,
            "credit": credit_balance,
        })
        total_debit += debit_balance
        total_credit += credit_balance
    return {
        "as_of": as_of,
        "total_debit": quantize_money(total_debit),
        "total_credit": quantize_money(total_credit),
        "lines": lines,
    }


def balance_sheet(session: Session, as_of: date) -> Dict:
    sections = {AccountType.asset: [], AccountType.liability: [], AccountType.equity: []}
    totals = {kind: Decimal("0") for kind in sections}
    current_earnings = Decimal("0")
    for account in session.exec(select(Account).order_by(Account.code)).all():
        debit, credit = account_totals(session, account, date_to=as_of)
        natural = debit - credit if account.type in (AccountType.asset, AccountType.expense) else credit - debit
        natural = quantize_money(natural)
        if account.type in sections and natural:
            sections[account.type].append({
                "account_code": account.code,
                "account_name": account.name,
                "amount": natural,
            })
            totals[account.type] += natural
        elif account.type == AccountType.income:
            current_earnings += natural
        elif account.type == AccountType.expense:
            current_earnings -= natural
    current_earnings = quantize_money(current_earnings)
    if current_earnings:
        sections[AccountType.equity].append({
            "account_code": "CURRENT",
            "account_name": "Current period earnings",
            "amount": current_earnings,
        })
        totals[AccountType.equity] += current_earnings
    total_assets = quantize_money(totals[AccountType.asset])
    total_liabilities = quantize_money(totals[AccountType.liability])
    total_equity = quantize_money(totals[AccountType.equity])
    return {
        "as_of": as_of,
        "assets": sections[AccountType.asset],
        "liabilities": sections[AccountType.liability],
        "equity": sections[AccountType.equity],
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "liabilities_and_equity": quantize_money(total_liabilities + total_equity),
    }


def cash_flow(session: Session, date_from: date, date_to: date) -> Dict:
    if date_from > date_to:
        raise FinanceError("date_from cannot be after date_to")
    bank = account_by_code(session, ACC_BANK)
    if bank is None:
        raise FinanceError("Bank account is not configured")
    lines = session.exec(
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.account_id == bank.id,
            JournalEntry.status == JournalStatus.posted,
            or_(JournalEntry.entry_date >= date_from, JournalEntry.entry_date.is_(None)),
            or_(JournalEntry.entry_date <= date_to, JournalEntry.entry_date.is_(None)),
        )
    ).all()
    buckets = {"operating": Decimal("0"), "investing": Decimal("0"), "financing": Decimal("0")}
    for line, entry in lines:
        text = f"{entry.memo or ''} {line.description or ''}".lower()
        bucket = "operating"
        if any(word in text for word in ("equipment", "machinery", "asset purchase", "investment")):
            bucket = "investing"
        elif any(word in text for word in ("loan", "capital", "dividend", "equity")):
            bucket = "financing"
        buckets[bucket] += line.debit - line.credit
    for key in buckets:
        buckets[key] = quantize_money(buckets[key])
    return {
        "date_from": date_from,
        "date_to": date_to,
        **buckets,
        "net_change": quantize_money(sum(buckets.values(), Decimal("0"))),
    }
