from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.rbac import Role, require_roles
from ..kernel.types import quantize_money
from .models import (
    Account,
    AccountCreate,
    AccountRead,
    APBill,
    APBillRead,
    ARInvoice,
    ARInvoiceRead,
    JournalEntry,
    JournalEntryCreate,
    JournalEntryRead,
    JournalLine,
    JournalLineRead,
    JournalSource,
    JournalStatus,
    ProfitAndLossRead,
    SettleRequest,
    SettlementStatus,
)
from .service import (
    FinanceError,
    account_by_code,
    post_journal,
    profit_and_loss,
    reverse_journal,
    seed_chart_of_accounts,
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


@router.get("/ar-invoices", response_model=List[ARInvoiceRead])
def list_ar(session: Session = Depends(get_session)):
    return [
        ARInvoiceRead(
            id=i.id, invoice_number=i.invoice_number, customer_id=i.customer_id,
            sales_order_id=i.sales_order_id, amount=i.amount, settled_amount=i.settled_amount,
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
        sales_order_id=invoice.sales_order_id, amount=invoice.amount,
        settled_amount=invoice.settled_amount,
        outstanding=quantize_money(invoice.amount - invoice.settled_amount), status=invoice.status,
    )


@router.get("/ap-bills", response_model=List[APBillRead])
def list_ap(session: Session = Depends(get_session)):
    return [
        APBillRead(
            id=b.id, bill_number=b.bill_number, supplier_id=b.supplier_id,
            goods_receipt_id=b.goods_receipt_id, amount=b.amount, settled_amount=b.settled_amount,
            outstanding=quantize_money(b.amount - b.settled_amount), status=b.status,
        )
        for b in session.exec(select(APBill).order_by(APBill.id)).all()
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
    return APBillRead(
        id=bill.id, bill_number=bill.bill_number, supplier_id=bill.supplier_id,
        goods_receipt_id=bill.goods_receipt_id, amount=bill.amount, settled_amount=bill.settled_amount,
        outstanding=quantize_money(bill.amount - bill.settled_amount), status=bill.status,
    )


# --------------------------------------------------------------------------- #
# Profitability / P&L (8.6)
# --------------------------------------------------------------------------- #
@router.get("/profit-and-loss", response_model=ProfitAndLossRead)
def pnl(session: Session = Depends(get_session)):
    return profit_and_loss(session)
