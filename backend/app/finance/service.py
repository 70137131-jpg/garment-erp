"""Finance services: CoA seeding, double-entry journal posting, AR/AP, P&L."""

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlmodel import Session, select

from ..kernel.numbering import next_document_number
from ..kernel.state_machine import StateMachine
from ..kernel.types import quantize_money
from .models import (
    Account,
    AccountType,
    JournalEntry,
    JournalLine,
    JournalSource,
    JournalStatus,
)

# Well-known account codes used by auto-posting.
ACC_INVENTORY = "1000"
ACC_AR = "1100"
ACC_BANK = "1200"
ACC_AP = "2000"
ACC_SALES = "4000"
ACC_COGS = "5000"

_DEFAULT_COA: List[Tuple[str, str, AccountType]] = [
    (ACC_INVENTORY, "Inventory", AccountType.asset),
    (ACC_AR, "Accounts Receivable", AccountType.asset),
    (ACC_BANK, "Bank", AccountType.asset),
    (ACC_AP, "Accounts Payable", AccountType.liability),
    ("3000", "Retained Earnings", AccountType.equity),
    (ACC_SALES, "Sales Revenue", AccountType.income),
    (ACC_COGS, "Cost of Goods Sold", AccountType.expense),
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
