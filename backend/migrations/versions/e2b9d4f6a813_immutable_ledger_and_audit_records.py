"""enforce immutable ledger and security-audit records

Revision ID: e2b9d4f6a813
Revises: a7c2f8e4d691
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e2b9d4f6a813"
down_revision: Union[str, Sequence[str], None] = "a7c2f8e4d691"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER stock_ledger_entry_no_update BEFORE UPDATE ON stock_ledger_entry
            BEGIN SELECT RAISE(ABORT, 'Immutable record: stock_ledger_entry is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER stock_ledger_entry_no_delete BEFORE DELETE ON stock_ledger_entry
            BEGIN SELECT RAISE(ABORT, 'Immutable record: stock_ledger_entry is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER security_audit_event_no_update BEFORE UPDATE ON security_audit_event
            BEGIN SELECT RAISE(ABORT, 'Immutable record: security_audit_event is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER security_audit_event_no_delete BEFORE DELETE ON security_audit_event
            BEGIN SELECT RAISE(ABORT, 'Immutable record: security_audit_event is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER journal_line_no_update BEFORE UPDATE ON journal_line
            BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_line is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER journal_line_no_delete BEFORE DELETE ON journal_line
            BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_line is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER journal_entry_no_delete BEFORE DELETE ON journal_entry
            BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_entry is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER journal_entry_limited_update BEFORE UPDATE ON journal_entry
            WHEN NOT (
                OLD.status = 'posted' AND NEW.status = 'reversed'
                AND OLD.entry_number IS NEW.entry_number AND OLD.entry_date IS NEW.entry_date
                AND OLD.memo IS NEW.memo AND OLD.source IS NEW.source
                AND OLD.reference_type IS NEW.reference_type AND OLD.reference_id IS NEW.reference_id
                AND OLD.reversal_of_id IS NEW.reversal_of_id AND OLD.created_at IS NEW.created_at
                AND OLD.created_by IS NEW.created_by
            )
            BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_entry may only be marked reversed'); END
        """)
        return

    op.execute("""
        CREATE OR REPLACE FUNCTION erp_reject_immutable_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Immutable record: % is append-only', TG_TABLE_NAME
                USING ERRCODE = '23000';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION erp_allow_journal_reversal_only() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'posted' AND NEW.status = 'reversed'
               AND OLD.entry_number IS NOT DISTINCT FROM NEW.entry_number
               AND OLD.entry_date IS NOT DISTINCT FROM NEW.entry_date
               AND OLD.memo IS NOT DISTINCT FROM NEW.memo
               AND OLD.source IS NOT DISTINCT FROM NEW.source
               AND OLD.reference_type IS NOT DISTINCT FROM NEW.reference_type
               AND OLD.reference_id IS NOT DISTINCT FROM NEW.reference_id
               AND OLD.reversal_of_id IS NOT DISTINCT FROM NEW.reversal_of_id
               AND OLD.created_at IS NOT DISTINCT FROM NEW.created_at
               AND OLD.created_by IS NOT DISTINCT FROM NEW.created_by
            THEN RETURN NEW; END IF;
            RAISE EXCEPTION 'Immutable record: journal_entry may only be marked reversed'
                USING ERRCODE = '23000';
        END;
        $$ LANGUAGE plpgsql;
    """)
    for name, table, timing, function in (
        ("stock_ledger_entry_no_update", "stock_ledger_entry", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("stock_ledger_entry_no_delete", "stock_ledger_entry", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("security_audit_event_no_update", "security_audit_event", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("security_audit_event_no_delete", "security_audit_event", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_line_no_update", "journal_line", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("journal_line_no_delete", "journal_line", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_entry_no_delete", "journal_entry", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_entry_limited_update", "journal_entry", "BEFORE UPDATE", "erp_allow_journal_reversal_only"),
    ):
        op.execute(f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()")


def downgrade() -> None:
    triggers = (
        ("journal_entry_limited_update", "journal_entry"),
        ("journal_entry_no_delete", "journal_entry"),
        ("journal_line_no_delete", "journal_line"),
        ("journal_line_no_update", "journal_line"),
        ("security_audit_event_no_delete", "security_audit_event"),
        ("security_audit_event_no_update", "security_audit_event"),
        ("stock_ledger_entry_no_delete", "stock_ledger_entry"),
        ("stock_ledger_entry_no_update", "stock_ledger_entry"),
    )
    if op.get_bind().dialect.name == "sqlite":
        for trigger, _ in triggers:
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    else:
        for trigger, table in triggers:
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS erp_allow_journal_reversal_only()")
        op.execute("DROP FUNCTION IF EXISTS erp_reject_immutable_mutation()")
