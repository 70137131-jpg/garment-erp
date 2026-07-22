"""Module 1.7–1.10 — Style master, colourways and the versioned per-size BOM.

The BOM shape is the load-bearing structure of the whole system
(build-plan Phase 1 key risk):

    BomVersion  →  BomLine  →  BomLineSizeConsumption

* A **style** owns many immutable **BOM versions**; approving a revision never
  overwrites history (blueprint A3, 1.10). Downstream documents FK to a specific
  ``bom_version.id`` — never to the style — so a cost sheet or cut order always
  reads the exact recipe it was built against.
* Each **line** is one material in the recipe (fabric, trim, thread…) with a
  wastage %, an optional colour link, and an optional-component flag.
* Consumption is stored as **normalized per-size cells**, one row per size of
  the style's size range (locked-in decision: never JSON). This is what the
  fabric-requirement calculation multiplies against a cut order's size
  breakdown.
"""

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import quantity_field, rate_field


class Gender(str, Enum):
    mens = "mens"
    womens = "womens"
    boys = "boys"
    girls = "girls"
    unisex = "unisex"


class StyleStatus(str, Enum):
    development = "development"
    active = "active"
    on_hold = "on_hold"
    discontinued = "discontinued"


class BomStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


# --------------------------------------------------------------------------- #
# 1.7 Style master
# --------------------------------------------------------------------------- #
class StyleBase(SQLModel):
    style_number: str = Field(index=True, unique=True)
    description: str
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    season_id: Optional[int] = Field(default=None, foreign_key="season.id")
    size_range_id: int = Field(foreign_key="sizerange.id")
    gender: Gender = Gender.unisex
    category: Optional[str] = None
    status: StyleStatus = StyleStatus.development
    # Standard Allowed Minutes for the whole garment — used by SAM-based sewing
    # cost (6.4) and line efficiency (5.5).
    standard_sam: Optional[Decimal] = rate_field(default=None, nullable=True)
    notes: Optional[str] = None


class Style(StyleBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    colourways: List["StyleColourway"] = Relationship(
        back_populates="style",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class StyleCreate(StyleBase):
    pass


class StyleUpdate(SQLModel):
    description: Optional[str] = None
    customer_id: Optional[int] = None
    season_id: Optional[int] = None
    gender: Optional[Gender] = None
    category: Optional[str] = None
    status: Optional[StyleStatus] = None
    standard_sam: Optional[Decimal] = None
    notes: Optional[str] = None


class StyleRead(StyleBase):
    id: int


# --------------------------------------------------------------------------- #
# 1.8 Style colourways
# --------------------------------------------------------------------------- #
class StyleColourwayBase(SQLModel):
    colour_id: int = Field(foreign_key="colour.id")
    buyer_reference: Optional[str] = None
    lab_dip_approved: bool = False


class StyleColourway(StyleColourwayBase, TimestampMixin, table=True):
    __tablename__ = "style_colourway"

    id: Optional[int] = Field(default=None, primary_key=True)
    style_id: int = Field(foreign_key="style.id", index=True)
    style: Optional[Style] = Relationship(back_populates="colourways")


class StyleColourwayCreate(StyleColourwayBase):
    pass


class StyleColourwayRead(StyleColourwayBase):
    id: int
    style_id: int


# --------------------------------------------------------------------------- #
# 1.9 Bill of Materials — versioned, per-size consumption
# --------------------------------------------------------------------------- #
class BomVersion(TimestampMixin, table=True):
    __tablename__ = "bom_version"

    id: Optional[int] = Field(default=None, primary_key=True)
    style_id: int = Field(foreign_key="style.id", index=True)
    version_no: int = Field(index=True)
    status: BomStatus = Field(default=BomStatus.draft)
    notes: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None

    lines: List["BomLine"] = Relationship(
        back_populates="bom_version",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class BomLine(SQLModel, table=True):
    __tablename__ = "bom_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    bom_version_id: int = Field(foreign_key="bom_version.id", index=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    # Optional colour link: e.g. shell fabric follows the garment colour, while
    # thread might be fixed. Null = not colour-specific.
    colour_id: Optional[int] = Field(default=None, foreign_key="colour.id")
    wastage_pct: Decimal = rate_field(default=Decimal("0"))
    optional_component: bool = False
    notes: Optional[str] = None

    bom_version: Optional[BomVersion] = Relationship(back_populates="lines")
    size_consumption: List["BomLineSizeConsumption"] = Relationship(
        back_populates="bom_line",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BomLineSizeConsumption.position",
        },
    )


class BomLineSizeConsumption(SQLModel, table=True):
    __tablename__ = "bom_line_size_consumption"

    id: Optional[int] = Field(default=None, primary_key=True)
    bom_line_id: int = Field(foreign_key="bom_line.id", index=True)
    size_range_item_id: int = Field(foreign_key="size_range_item.id", index=True)
    # Denormalised for ordering and query convenience; the FK above is canonical.
    position: int
    size_label: str
    # Consumption per single garment of this size, in the material's base UoM
    # (before wastage). Fabric requirement applies wastage_pct on top.
    consumption: Decimal = quantity_field(default=Decimal("0"))

    bom_line: Optional[BomLine] = Relationship(back_populates="size_consumption")


# --------------------------------------------------------------------------- #
# API payloads for BOM
# --------------------------------------------------------------------------- #
class SizeConsumptionInput(SQLModel):
    size_label: str
    consumption: Decimal


class BomLineInput(SQLModel):
    material_id: int
    colour_id: Optional[int] = None
    wastage_pct: Decimal = Decimal("0")
    optional_component: bool = False
    notes: Optional[str] = None
    # One entry per size of the style's size range. Missing sizes default to 0.
    size_consumption: List[SizeConsumptionInput] = []


class BomVersionCreate(SQLModel):
    notes: Optional[str] = None
    lines: List[BomLineInput] = []


class SizeConsumptionRead(SQLModel):
    size_label: str
    position: int
    consumption: Decimal


class BomLineRead(SQLModel):
    id: int
    material_id: int
    colour_id: Optional[int]
    wastage_pct: Decimal
    optional_component: bool
    notes: Optional[str]
    size_consumption: List[SizeConsumptionRead]


class BomVersionRead(SQLModel):
    id: int
    style_id: int
    version_no: int
    status: BomStatus
    notes: Optional[str]
    approved_by: Optional[str]
    approved_at: Optional[str]
    lines: List[BomLineRead]
