from app.kernel.numbering import next_document_number


def test_numbers_are_sequential_and_gap_free(session):
    nums = [next_document_number(session, "SO", "SO", year=2026) for _ in range(3)]
    assert nums == ["SO-2026-00001", "SO-2026-00002", "SO-2026-00003"]


def test_sequences_are_independent_per_type(session):
    a = next_document_number(session, "PO", "PO", year=2026)
    b = next_document_number(session, "GR", "GR", year=2026)
    assert a == "PO-2026-00001"
    assert b == "GR-2026-00001"


def test_sequences_reset_per_year(session):
    x = next_document_number(session, "SO", "SO", year=2026)
    y = next_document_number(session, "SO", "SO", year=2027)
    assert x == "SO-2026-00001"
    assert y == "SO-2027-00001"
