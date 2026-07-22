"""Decimal precision helpers.

Locked-in decision (build-plan): money and quantities are ``Decimal`` with a
*defined* precision — never floats near cost or stock. These helpers give every
model the same column types so the database, not chance, fixes the scale.

Three profiles:

* **quantity** — metres, kilos, pieces: ``Numeric(18, 4)``.
* **money** — prices, amounts, balances: ``Numeric(18, 2)``.
* **rate** — conversion factors, cost-per-minute, percentages: ``Numeric(18, 6)``.

Use the ``*_field`` factories when declaring SQLModel columns so persisted values
round-trip at a predictable scale.
"""

from decimal import Decimal
from typing import Optional

from sqlalchemy import Numeric
from sqlmodel import Field

QUANTITY_PRECISION = 18
QUANTITY_SCALE = 4
MONEY_PRECISION = 18
MONEY_SCALE = 2
RATE_PRECISION = 18
RATE_SCALE = 6

ZERO_QTY = Decimal("0.0000")
ZERO_MONEY = Decimal("0.00")


def quantize_qty(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.0001"))


def quantize_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def quantity_field(default: Optional[Decimal] = Decimal("0"), **kwargs) -> Field:
    return Field(
        default=default,
        sa_type=Numeric(QUANTITY_PRECISION, QUANTITY_SCALE),
        **kwargs,
    )


def money_field(default: Optional[Decimal] = Decimal("0"), **kwargs) -> Field:
    return Field(
        default=default,
        sa_type=Numeric(MONEY_PRECISION, MONEY_SCALE),
        **kwargs,
    )


def rate_field(default: Optional[Decimal] = Decimal("1"), **kwargs) -> Field:
    return Field(
        default=default,
        sa_type=Numeric(RATE_PRECISION, RATE_SCALE),
        **kwargs,
    )
