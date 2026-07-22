"""Module 8 — Finance: chart of accounts, double-entry journals, AR/AP.

Double-entry is enforced: a journal only posts when total debits equal total
credits. Posted entries are immutable (blueprint 8.2, Draft→Posted→Reversed); a
correction is a reversing entry, never an edit. Operational events auto-post
their own accounting (8.3) via :mod:`app.finance.posting`.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field


class AccountType(str, Enum):
    asset = "asset"
    liability = "liability"
    equity = "equity"
    income = "income"
    expense = "expense"


class JournalStatus(str, Enum):
    draft = "draft"
    posted = "posted"
    reversed = "reversed"


class JournalSource(str, Enum):
    manual = "manual"
    system = "system"


class SettlementStatus(str, Enum):
    open = "open"
    part_paid = "part_paid"
    paid = "paid"


# --------------------------------------------------------------------------- #
# 8.1 Chart of accounts (hierarchical)
# --------------------------------------------------------------------------- #
class Account(TimestampMixin, table=True):
    __tablename__ = "account"

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    type: AccountType
    parent_id: Optional[int] = Field(default=None, foreign_key="account.id")
    active: bool = True


class AccountCreate(SQLModel):
    code: str
    name: str
    type: AccountType
    parent_id: Optional[int] = None


class AccountRead(SQLModel):
    id: int
    code: str
    name: str
    type: AccountType
    parent_id: Optional[int]
    active: bool


# --------------------------------------------------------------------------- #
# 8.2 Journals & GL
# --------------------------------------------------------------------------- #
class JournalEntry(TimestampMixin, table=True):
    __tablename__ = "journal_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    entry_number: str = Field(index=True, unique=True)
    entry_date: Optional[date] = None
    memo: Optional[str] = None
    source: JournalSource = JournalSource.manual
    status: JournalStatus = Field(default=JournalStatus.draft, index=True)
    reference_type: Optional[str] = Field(default=None, index=True)
    reference_id: Optional[int] = Field(default=None, index=True)
    reversal_of_id: Optional[int] = Field(default=None, foreign_key="journal_entry.id")

    lines: List["JournalLine"] = Relationship(
        back_populates="entry",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class JournalLine(SQLModel, table=True):
    __tablename__ = "journal_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    journal_entry_id: int = Field(foreign_key="journal_entry.id", index=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    debit: Decimal = money_field(default=Decimal("0"))
    credit: Decimal = money_field(default=Decimal("0"))
    description: Optional[str] = None

    entry: Optional[JournalEntry] = Relationship(back_populates="lines")


# --------------------------------------------------------------------------- #
# 8.4 / 8.5 AR & AP subledgers
# --------------------------------------------------------------------------- #
class ARInvoice(TimestampMixin, table=True):
    __tablename__ = "ar_invoice"

    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_number: str = Field(index=True, unique=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    sales_order_id: Optional[int] = Field(default=None, foreign_key="sales_order.id")
    currency: str = "USD"
    amount: Decimal = money_field(default=Decimal("0"))
    settled_amount: Decimal = money_field(default=Decimal("0"))
    status: SettlementStatus = Field(default=SettlementStatus.open, index=True)


class APBill(TimestampMixin, table=True):
    __tablename__ = "ap_bill"

    id: Optional[int] = Field(default=None, primary_key=True)
    bill_number: str = Field(index=True, unique=True)
    supplier_id: int = Field(foreign_key="supplier.id", index=True)
    goods_receipt_id: Optional[int] = Field(default=None, index=True)
    currency: str = "USD"
    amount: Decimal = money_field(default=Decimal("0"))
    settled_amount: Decimal = money_field(default=Decimal("0"))
    status: SettlementStatus = Field(default=SettlementStatus.open, index=True)


# --------------------------------------------------------------------------- #
# API payloads
# --------------------------------------------------------------------------- #
class JournalLineInput(SQLModel):
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None


class JournalEntryCreate(SQLModel):
    entry_date: Optional[date] = None
    memo: Optional[str] = None
    lines: List[JournalLineInput] = []


class JournalLineRead(SQLModel):
    account_id: int
    account_code: str
    debit: Decimal
    credit: Decimal
    description: Optional[str]


class JournalEntryRead(SQLModel):
    id: int
    entry_number: str
    entry_date: Optional[date]
    memo: Optional[str]
    source: JournalSource
    status: JournalStatus
    reference_type: Optional[str]
    reference_id: Optional[int]
    total_debit: Decimal
    total_credit: Decimal
    lines: List[JournalLineRead]


class ARInvoiceRead(SQLModel):
    id: int
    invoice_number: str
    customer_id: int
    sales_order_id: Optional[int]
    amount: Decimal
    settled_amount: Decimal
    outstanding: Decimal
    status: SettlementStatus


class APBillRead(SQLModel):
    id: int
    bill_number: str
    supplier_id: int
    goods_receipt_id: Optional[int]
    amount: Decimal
    settled_amount: Decimal
    outstanding: Decimal
    status: SettlementStatus


class SettleRequest(SQLModel):
    amount: Decimal


class ProfitAndLossRead(SQLModel):
    total_income: Decimal
    total_expense: Decimal
    net_profit: Decimal
    by_account: dict
