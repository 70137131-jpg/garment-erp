"""Database guards for records that must never be retrospectively rewritten.

The application deliberately corrects stock and accounting through new entries.
These triggers make that rule hold even if a future code path accidentally uses
an UPDATE/DELETE statement.  PostgreSQL is the production target; SQLite gets
the same protection for local development.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine


_SQLITE_GUARDS = (
    """
    CREATE TRIGGER IF NOT EXISTS stock_ledger_entry_no_update
    BEFORE UPDATE ON stock_ledger_entry
    BEGIN SELECT RAISE(ABORT, 'Immutable record: stock_ledger_entry is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS stock_ledger_entry_no_delete
    BEFORE DELETE ON stock_ledger_entry
    BEGIN SELECT RAISE(ABORT, 'Immutable record: stock_ledger_entry is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS security_audit_event_no_update
    BEFORE UPDATE ON security_audit_event
    BEGIN SELECT RAISE(ABORT, 'Immutable record: security_audit_event is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS security_audit_event_no_delete
    BEFORE DELETE ON security_audit_event
    BEGIN SELECT RAISE(ABORT, 'Immutable record: security_audit_event is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_line_no_update
    BEFORE UPDATE ON journal_line
    BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_line is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_line_no_delete
    BEFORE DELETE ON journal_line
    BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_line is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_entry_no_delete
    BEFORE DELETE ON journal_entry
    BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_entry is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_entry_limited_update
    BEFORE UPDATE ON journal_entry
    WHEN NOT (
        OLD.status = 'posted' AND NEW.status = 'reversed'
        AND OLD.entry_number IS NEW.entry_number
        AND OLD.entry_date IS NEW.entry_date
        AND OLD.memo IS NEW.memo
        AND OLD.source IS NEW.source
        AND OLD.reference_type IS NEW.reference_type
        AND OLD.reference_id IS NEW.reference_id
        AND OLD.reversal_of_id IS NEW.reversal_of_id
        AND OLD.created_at IS NEW.created_at
        AND OLD.created_by IS NEW.created_by
    )
    BEGIN SELECT RAISE(ABORT, 'Immutable record: journal_entry may only be marked reversed'); END;
    """,
)


_POSTGRES_FUNCTION = """
CREATE OR REPLACE FUNCTION erp_reject_immutable_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Immutable record: % is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

_POSTGRES_LIMITED_JOURNAL_UPDATE = """
CREATE OR REPLACE FUNCTION erp_allow_journal_reversal_only()
RETURNS trigger AS $$
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
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Immutable record: journal_entry may only be marked reversed';
END;
$$ LANGUAGE plpgsql;
"""


def install_immutable_record_guards(engine: Engine) -> None:
    """Install idempotent guards for development databases.

    Production uses the matching Alembic migration.  This path keeps local
    ``create_all`` databases protected as well.
    """
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            for statement in _SQLITE_GUARDS:
                connection.execute(text(statement))
        return

    if engine.dialect.name != "postgresql":
        return

    triggers = (
        ("stock_ledger_entry_no_update", "stock_ledger_entry", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("stock_ledger_entry_no_delete", "stock_ledger_entry", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("security_audit_event_no_update", "security_audit_event", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("security_audit_event_no_delete", "security_audit_event", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_line_no_update", "journal_line", "BEFORE UPDATE", "erp_reject_immutable_mutation"),
        ("journal_line_no_delete", "journal_line", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_entry_no_delete", "journal_entry", "BEFORE DELETE", "erp_reject_immutable_mutation"),
        ("journal_entry_limited_update", "journal_entry", "BEFORE UPDATE", "erp_allow_journal_reversal_only"),
    )
    with engine.begin() as connection:
        connection.execute(text(_POSTGRES_FUNCTION))
        connection.execute(text(_POSTGRES_LIMITED_JOURNAL_UPDATE))
        for name, table, timing, function in triggers:
            connection.execute(text(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger WHERE tgname = '{name}'
                    ) THEN
                        CREATE TRIGGER {name} {timing} ON {table}
                        FOR EACH ROW EXECUTE FUNCTION {function}();
                    END IF;
                END $$;
            """))
