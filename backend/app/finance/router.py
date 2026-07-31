from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from ..kernel.query import csv_download, page_bounds
from ..kernel.types import quantize_money
from .models import (
    Account,
    AccountCreate,
    AccountRead,
    AgingBucketRead,
    AgingReportRead,
    APBill,
    APBillRead,
    ARInvoice,
    ARInvoiceRead,
    BalanceSheetRead,
    CashFlowRead,
    GeneralLedgerLineRead,
    JournalEntry,
    JournalEntryCreate,
    JournalEntryRead,
    JournalLine,
    JournalLineRead,
    JournalSource,
    JournalStatus,
    ProfitAndLossRead,
    SettleRequest,
    SupplierInvoiceCreate,
    SettlementStatus,
    ThreeWayMatchExceptionRead,
    ThreeWayMatchStatus,
    TrialBalanceRead,
)
from .service import (
    FinanceError,
    account_by_code,
    balance_sheet,
    cash_flow,
    match_supplier_invoice,
    post_journal,
    profit_and_loss,
    reverse_journal,
    seed_chart_of_accounts,
    trial_balance,
)

router = APIRouter(prefix="/finance", tags=["finance"])


# --------------------------------------------------------------------------- #
# Chart of accounts (8.1)
# --------------------------------------------------------------------------- #
@router.post("/accounts/seed-defaults", response_model=List[AccountRead])
def seed_defaults(
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    seed_chart_of_accounts(session)
    session.commit()
    return session.exec(select(Account).order_by(Account.code)).all()


@router.post("/accounts", response_model=AccountRead, status_code=201)
def create_account(
    payload: AccountCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    account = Account.model_validate(payload)
    session.add(account)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate account code") from exc
    session.refresh(account)
    return account


@router.get("/accounts", response_model=List[AccountRead])
def list_accounts(session: Session = Depends(get_session)):
    return session.exec(select(Account).order_by(Account.code)).all()


# --------------------------------------------------------------------------- #
# General ledger inquiry and statutory-style reports
# --------------------------------------------------------------------------- #
@router.get("/general-ledger", response_model=List[GeneralLedgerLineRead])
def general_ledger(
    account_id: Optional[int] = None,
    account_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = None,
    offset: int = 0,
    limit: int = 200,
    session: Session = Depends(get_session),
):
    offset, limit = page_bounds(offset, limit)
    stmt = (
        select(JournalLine, JournalEntry, Account)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalEntry.status == JournalStatus.posted)
    )
    if account_id is not None:
        stmt = stmt.where(Account.id == account_id)
    if account_code is not None:
        stmt = stmt.where(Account.code == account_code)
    if date_from is not None:
        stmt = stmt.where(or_(JournalEntry.entry_date >= date_from, JournalEntry.entry_date.is_(None)))
    if date_to is not None:
        stmt = stmt.where(or_(JournalEntry.entry_date <= date_to, JournalEntry.entry_date.is_(None)))
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(JournalEntry.entry_number).like(pattern)
            | func.lower(func.coalesce(JournalEntry.memo, "")).like(pattern)
            | func.lower(func.coalesce(JournalLine.description, "")).like(pattern)
        )
    rows = session.exec(
        stmt.order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
        .offset(offset)
        .limit(limit)
    ).all()
    running: dict[int, Decimal] = {}
    output = []
    for line, entry, account in rows:
        change = line.debit - line.credit
        running[account.id] = quantize_money(running.get(account.id, Decimal("0")) + change)
        output.append(GeneralLedgerLineRead(
            journal_id=entry.id,
            entry_number=entry.entry_number,
            entry_date=entry.entry_date,
            account_id=account.id,
            account_code=account.code,
            account_name=account.name,
            description=line.description or entry.memo,
            reference_type=entry.reference_type,
            reference_id=entry.reference_id,
            debit=line.debit,
            credit=line.credit,
            running_balance=running[account.id],
        ))
    return output


@router.get("/reports/trial-balance", response_model=TrialBalanceRead)
def trial_balance_report(
    as_of: Optional[date] = None,
    session: Session = Depends(get_session),
):
    return trial_balance(session, as_of or date.today())


@router.get("/reports/balance-sheet", response_model=BalanceSheetRead)
def balance_sheet_report(
    as_of: Optional[date] = None,
    session: Session = Depends(get_session),
):
    return balance_sheet(session, as_of or date.today())


@router.get("/reports/cash-flow", response_model=CashFlowRead)
def cash_flow_report(
    date_from: date,
    date_to: date,
    session: Session = Depends(get_session),
):
    try:
        return cash_flow(session, date_from, date_to)
    except FinanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _aging(records, *, party_field: str, date_field: str, as_of: date, ledger: str) -> AgingReportRead:
    parties: dict[int, dict[str, Decimal]] = {}
    for record in records:
        outstanding = quantize_money(record.amount - record.settled_amount)
        if outstanding <= 0:
            continue
        party_id = getattr(record, party_field)
        buckets = parties.setdefault(party_id, {
            "current": Decimal("0"), "days_1_30": Decimal("0"),
            "days_31_60": Decimal("0"), "days_61_90": Decimal("0"),
            "over_90": Decimal("0"),
        })
        due_date = getattr(record, "due_date", None) or getattr(record, date_field, None) or record.created_at.date()
        overdue_days = (as_of - due_date).days
        key = "current" if overdue_days <= 0 else "days_1_30" if overdue_days <= 30 else "days_31_60" if overdue_days <= 60 else "days_61_90" if overdue_days <= 90 else "over_90"
        buckets[key] += outstanding
    output = []
    for party_id, buckets in parties.items():
        total = quantize_money(sum(buckets.values(), Decimal("0")))
        output.append(AgingBucketRead(party_id=party_id, total=total, **buckets))
    return AgingReportRead(
        as_of=as_of,
        ledger=ledger,
        total=quantize_money(sum((row.total for row in output), Decimal("0"))),
        parties=output,
    )


@router.get("/reports/ar-aging", response_model=AgingReportRead)
def ar_aging(as_of: Optional[date] = None, session: Session = Depends(get_session)):
    return _aging(
        session.exec(select(ARInvoice).order_by(ARInvoice.customer_id)).all(),
        party_field="customer_id",
        date_field="invoice_date",
        as_of=as_of or date.today(),
        ledger="AR",
    )


@router.get("/reports/ap-aging", response_model=AgingReportRead)
def ap_aging(as_of: Optional[date] = None, session: Session = Depends(get_session)):
    return _aging(
        session.exec(select(APBill).order_by(APBill.supplier_id)).all(),
        party_field="supplier_id",
        date_field="bill_date",
        as_of=as_of or date.today(),
        ledger="AP",
    )


@router.get("/reports/trial-balance/export")
def export_trial_balance(as_of: Optional[date] = None, session: Session = Depends(get_session)):
    report = trial_balance(session, as_of or date.today())
    return csv_download(
        "trial-balance.csv",
        [
            ("account_code", "Account Code"), ("account_name", "Account"),
            ("account_type", "Type"), ("debit", "Debit"), ("credit", "Credit"),
        ],
        report["lines"],
    )


# --------------------------------------------------------------------------- #
# Journals (8.2)
# --------------------------------------------------------------------------- #
def _journal_read(session: Session, entry: JournalEntry) -> JournalEntryRead:
    lines = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in entry.lines:
        account = session.get(Account, line.account_id)
        total_debit += line.debit
        total_credit += line.credit
        lines.append(
            JournalLineRead(
                account_id=line.account_id,
                account_code=account.code if account else "",
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
        )
    return JournalEntryRead(
        id=entry.id,
        entry_number=entry.entry_number,
        entry_date=entry.entry_date,
        memo=entry.memo,
        source=entry.source,
        status=entry.status,
        reference_type=entry.reference_type,
        reference_id=entry.reference_id,
        total_debit=quantize_money(total_debit),
        total_credit=quantize_money(total_credit),
        lines=lines,
    )


@router.post("/journal-entries", response_model=JournalEntryRead, status_code=201)
def create_journal(
    payload: JournalEntryCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    try:
        entry = post_journal(
            session,
            lines=[
                (l.account_code, l.debit, l.credit, l.description) for l in payload.lines
            ],
            memo=payload.memo,
            source=JournalSource.manual,
            entry_date=payload.entry_date,
        )
    except FinanceError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(entry)
    return _journal_read(session, entry)


@router.get("/journal-entries", response_model=List[JournalEntryRead])
def list_journals(session: Session = Depends(get_session)):
    entries = session.exec(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .order_by(JournalEntry.id.desc())
    ).all()
    # Prime the identity map so per-line session.get(Account, ...) inside
    # _journal_read is a dict lookup, not a query per distinct account.
    session.exec(select(Account)).all()
    return [_journal_read(session, entry) for entry in entries]


@router.get("/journal-entries/{entry_id}", response_model=JournalEntryRead)
def get_journal(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(JournalEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return _journal_read(session, entry)


@router.post("/journal-entries/{entry_id}/reverse", response_model=JournalEntryRead)
def reverse(
    entry_id: int,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    entry = session.get(JournalEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if entry.status != JournalStatus.posted:
        raise HTTPException(
            status_code=409, detail=f"Cannot reverse a '{entry.status.value}' entry"
        )
    reversal = reverse_journal(session, entry)
    session.commit()
    session.refresh(reversal)
    return _journal_read(session, reversal)


# --------------------------------------------------------------------------- #
# AR / AP subledgers (8.4 / 8.5)
# --------------------------------------------------------------------------- #
def _settle(record, amount: Decimal) -> None:
    record.settled_amount = quantize_money(record.settled_amount + amount)
    if record.settled_amount >= record.amount:
        record.status = SettlementStatus.paid
    elif record.settled_amount > 0:
        record.status = SettlementStatus.part_paid


def _ap_bill_read(bill: APBill) -> APBillRead:
    return APBillRead(
        id=bill.id,
        bill_number=bill.bill_number,
        supplier_id=bill.supplier_id,
        purchase_order_id=bill.purchase_order_id,
        goods_receipt_id=bill.goods_receipt_id,
        supplier_invoice_number=bill.supplier_invoice_number,
        amount=bill.amount,
        settled_amount=bill.settled_amount,
        outstanding=quantize_money(bill.amount - bill.settled_amount),
        status=bill.status,
        match_status=bill.match_status,
        received_amount=bill.received_amount,
        variance_amount=bill.variance_amount,
        bill_date=bill.bill_date,
        due_date=bill.due_date,
    )


@router.get("/ar-invoices", response_model=List[ARInvoiceRead])
def list_ar(session: Session = Depends(get_session)):
    return [
        ARInvoiceRead(
            id=i.id, invoice_number=i.invoice_number, customer_id=i.customer_id,
            sales_order_id=i.sales_order_id, shipment_id=i.shipment_id, amount=i.amount, settled_amount=i.settled_amount,
            outstanding=quantize_money(i.amount - i.settled_amount), status=i.status,
        )
        for i in session.exec(select(ARInvoice).order_by(ARInvoice.id)).all()
    ]


@router.post("/ar-invoices/{invoice_id}/settle", response_model=ARInvoiceRead)
def settle_ar(
    invoice_id: int,
    payload: SettleRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    invoice = session.get(ARInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="AR invoice not found")
    if payload.amount <= 0:
        raise HTTPException(status_code=422, detail="Settlement amount must be positive")
    if invoice.settled_amount + payload.amount > invoice.amount:
        raise HTTPException(status_code=422, detail="Settlement exceeds invoice amount")
    # Dr Bank / Cr Accounts Receivable
    try:
        post_journal(
            session,
            lines=[("1200", payload.amount, Decimal("0"), "Customer payment"),
                   ("1100", Decimal("0"), payload.amount, "Settle receivable")],
            memo=f"Receipt against {invoice.invoice_number}",
            source=JournalSource.system,
            reference_type="ar_settlement",
            reference_id=invoice.id,
        )
    except FinanceError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _settle(invoice, payload.amount)
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return ARInvoiceRead(
        id=invoice.id, invoice_number=invoice.invoice_number, customer_id=invoice.customer_id,
        sales_order_id=invoice.sales_order_id, shipment_id=invoice.shipment_id, amount=invoice.amount,
        settled_amount=invoice.settled_amount,
        outstanding=quantize_money(invoice.amount - invoice.settled_amount), status=invoice.status,
    )


@router.get("/ap-bills", response_model=List[APBillRead])
def list_ap(session: Session = Depends(get_session)):
    return [_ap_bill_read(bill) for bill in session.exec(select(APBill).order_by(APBill.id)).all()]


@router.post("/supplier-invoices", response_model=APBillRead, status_code=201)
def create_supplier_invoice(
    payload: SupplierInvoiceCreate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    try:
        bill = match_supplier_invoice(session, payload)
    except FinanceError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate supplier invoice number") from exc
    session.commit()
    session.refresh(bill)
    return _ap_bill_read(bill)


@router.get("/three-way-match/exceptions", response_model=List[ThreeWayMatchExceptionRead])
def list_three_way_match_exceptions(session: Session = Depends(get_session)):
    return [
        ThreeWayMatchExceptionRead(
            bill=_ap_bill_read(bill),
            reason=(
                f"Invoice variance {bill.variance_amount} against received amount "
                f"{bill.received_amount}"
            ),
        )
        for bill in session.exec(
            select(APBill)
            .where(APBill.match_status == ThreeWayMatchStatus.exception)
            .order_by(APBill.id.desc())
        ).all()
    ]


@router.post("/ap-bills/{bill_id}/settle", response_model=APBillRead)
def settle_ap(
    bill_id: int,
    payload: SettleRequest,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.finance)),
):
    bill = session.get(APBill, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="AP bill not found")
    if bill.match_status == ThreeWayMatchStatus.pending:
        raise HTTPException(status_code=409, detail="Match the supplier invoice before payment")
    if bill.match_status == ThreeWayMatchStatus.exception:
        raise HTTPException(status_code=409, detail="Resolve the three-way match exception before payment")
    if payload.amount <= 0:
        raise HTTPException(status_code=422, detail="Settlement amount must be positive")
    if bill.settled_amount + payload.amount > bill.amount:
        raise HTTPException(status_code=422, detail="Settlement exceeds bill amount")
    # Dr Accounts Payable / Cr Bank
    try:
        post_journal(
            session,
            lines=[("2000", payload.amount, Decimal("0"), "Settle payable"),
                   ("1200", Decimal("0"), payload.amount, "Supplier payment")],
            memo=f"Payment against {bill.bill_number}",
            source=JournalSource.system,
            reference_type="ap_settlement",
            reference_id=bill.id,
        )
    except FinanceError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _settle(bill, payload.amount)
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return _ap_bill_read(bill)


# --------------------------------------------------------------------------- #
# Profitability / P&L (8.6)
# --------------------------------------------------------------------------- #
@router.get("/profit-and-loss", response_model=ProfitAndLossRead)
def pnl(session: Session = Depends(get_session)):
    return profit_and_loss(session)
