"""ANSI/ASQ Z1.4 (ISO 2859-1) single-sampling plans, General Inspection Level II.

Final garment inspection uses AQL sampling: from the lot size we derive a
sample-size code letter, then look up the sample size and acceptance number
(``Ac``) for the chosen AQL. A lot passes when defects found ≤ Ac.

Only the four AQLs common in apparel are tabulated (1.0 / 1.5 / 2.5 / 4.0).
Table "arrows" are resolved the standard way: ``↓`` uses the first plan below,
``↑`` the first plan above.
"""

from decimal import Decimal
from typing import Dict, List, Tuple

DOWN = "down"
UP = "up"

# code letter → sample size (Level II)
_SAMPLE_SIZE: Dict[str, int] = {
    "A": 2, "B": 3, "C": 5, "D": 8, "E": 13, "F": 20, "G": 32, "H": 50,
    "J": 80, "K": 125, "L": 200, "M": 315, "N": 500, "P": 800, "Q": 1250, "R": 2000,
}
_ORDER: List[str] = list(_SAMPLE_SIZE.keys())

# Acceptance numbers per code letter for each supported AQL (arrows as sentinels).
_AC: Dict[str, Dict[str, object]] = {
    "1.0": {"A": DOWN, "B": DOWN, "C": DOWN, "D": DOWN, "E": 0, "F": 0, "G": 1,
            "H": 1, "J": 2, "K": 3, "L": 5, "M": 7, "N": 10, "P": 14, "Q": 21, "R": UP},
    "1.5": {"A": DOWN, "B": DOWN, "C": DOWN, "D": 0, "E": 0, "F": 1, "G": 1,
            "H": 2, "J": 3, "K": 5, "L": 7, "M": 10, "N": 14, "P": 21, "Q": UP, "R": UP},
    "2.5": {"A": DOWN, "B": DOWN, "C": 0, "D": 0, "E": 1, "F": 1, "G": 2,
            "H": 3, "J": 5, "K": 7, "L": 10, "M": 14, "N": 21, "P": UP, "Q": UP, "R": UP},
    "4.0": {"A": DOWN, "B": 0, "C": 0, "D": 1, "E": 1, "F": 2, "G": 3,
            "H": 5, "J": 7, "K": 10, "L": 14, "M": 21, "N": UP, "P": UP, "Q": UP, "R": UP},
}

# lot size upper bound → code letter (Level II)
_LOT_BANDS: List[Tuple[int, str]] = [
    (8, "A"), (15, "B"), (25, "C"), (50, "D"), (90, "E"), (150, "F"),
    (280, "G"), (500, "H"), (1200, "J"), (3200, "K"), (10000, "L"),
    (35000, "M"), (150000, "N"), (500000, "P"),
]

SUPPORTED_AQLS = tuple(_AC.keys())


class AqlError(Exception):
    """Unsupported AQL or lot size."""


def _code_for_lot(lot_size: int) -> str:
    if lot_size < 2:
        raise AqlError("Lot size must be at least 2")
    for upper, code in _LOT_BANDS:
        if lot_size <= upper:
            return code
    return "Q"


def _resolve(aql_key: str, code: str) -> str:
    """Follow arrows to a code letter with a numeric acceptance number."""
    table = _AC[aql_key]
    idx = _ORDER.index(code)
    value = table.get(code)
    step = 0
    if value == DOWN:
        step = 1
    elif value == UP:
        step = -1
    else:
        return code
    idx += step
    while 0 <= idx < len(_ORDER):
        code = _ORDER[idx]
        if isinstance(table.get(code), int):
            return code
        idx += step
    raise AqlError("No valid sampling plan for this lot size / AQL")


def single_sampling_plan(lot_size: int, aql: Decimal) -> Tuple[str, int, int, int]:
    """Return (code_letter, sample_size, accept_number, reject_number)."""
    aql_key = f"{Decimal(aql):.1f}"
    if aql_key not in _AC:
        raise AqlError(
            f"AQL {aql_key} not supported; use one of {', '.join(SUPPORTED_AQLS)}"
        )
    code = _resolve(aql_key, _code_for_lot(lot_size))
    sample_size = _SAMPLE_SIZE[code]
    ac = _AC[aql_key][code]
    assert isinstance(ac, int)
    return code, sample_size, ac, ac + 1
