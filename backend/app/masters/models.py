from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel

from ..kernel.audit import TimestampMixin
from ..kernel.types import money_field, quantity_field, rate_field


class MaterialType(str, Enum):
    fabric = "fabric"
    trims = "trims"
    thread = "thread"
    labels = "labels"
    hangtags = "hangtags"
    packaging = "packaging"
    chemicals = "chemicals"
    consumables = "consumables"
    service = "service"


# --------------------------------------------------------------------------- #
# 1.4 Colour library
# --------------------------------------------------------------------------- #
class ColourBase(SQLModel):
    code: str = Field(index=True, unique=True)
    name: str
    pantone: Optional[str] = None
    hex: Optional[str] = None
    active: bool = True


class Colour(ColourBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class ColourCreate(ColourBase):
    pass


class ColourRead(ColourBase):
    id: int


# --------------------------------------------------------------------------- #
# 1.5 Season and calendar structure
# --------------------------------------------------------------------------- #
class SeasonBase(SQLModel):
    code: str = Field(index=True, unique=True)
    name: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    active: bool = True


class Season(SeasonBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class SeasonCreate(SeasonBase):
    pass


class SeasonRead(SeasonBase):
    id: int


# --------------------------------------------------------------------------- #
# 1.6 Size range definitions (ordered — order is load-bearing downstream)
# --------------------------------------------------------------------------- #
class SizeRangeBase(SQLModel):
    code: str = Field(index=True, unique=True)
    name: str
    active: bool = True


class SizeRange(SizeRangeBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sizes: List["SizeRangeItem"] = Relationship(
        back_populates="size_range",
        sa_relationship_kwargs={
            "order_by": "SizeRangeItem.position",
            "cascade": "all, delete-orphan",
        },
    )


class SizeRangeItem(SQLModel, table=True):
    __tablename__ = "size_range_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    size_range_id: int = Field(foreign_key="sizerange.id", index=True)
    position: int
    label: str
    size_range: Optional[SizeRange] = Relationship(back_populates="sizes")


class SizeItemCreate(SQLModel):
    position: int
    label: str


class SizeItemRead(SQLModel):
    position: int
    label: str


class SizeRangeCreate(SizeRangeBase):
    sizes: List[SizeItemCreate] = []


class SizeRangeRead(SizeRangeBase):
    id: int
    sizes: List[SizeItemRead] = []


# --------------------------------------------------------------------------- #
# 1.1 Customer management (auto-numbered code)
# --------------------------------------------------------------------------- #
class CustomerBase(SQLModel):
    name: str
    currency: str = "USD"
    payment_terms: Optional[str] = None
    credit_limit: Decimal = money_field(default=Decimal("0"))
    billing_address: Optional[str] = None
    active: bool = True


class Customer(CustomerBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)


class CustomerCreate(CustomerBase):
    pass


class CustomerRead(CustomerBase):
    id: int
    code: str


# --------------------------------------------------------------------------- #
# 1.2 Supplier management (auto-numbered code, approved per material type)
# --------------------------------------------------------------------------- #
class SupplierBase(SQLModel):
    name: str
    currency: str = "USD"
    payment_terms: Optional[str] = None
    lead_time_days: int = 0
    restricted_substance_certified: bool = False
    active: bool = True


class Supplier(SupplierBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    material_types: List["SupplierMaterialType"] = Relationship(
        back_populates="supplier",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SupplierMaterialType(SQLModel, table=True):
    __tablename__ = "supplier_material_type"

    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="supplier.id", index=True)
    material_type: MaterialType
    supplier: Optional[Supplier] = Relationship(back_populates="material_types")


class SupplierCreate(SupplierBase):
    material_types: List[MaterialType] = []


class SupplierRead(SupplierBase):
    id: int
    code: str
    material_types: List[MaterialType] = []


# --------------------------------------------------------------------------- #
# 1.3 Material master (dual UoM: buy by roll, store/issue by metre)
# --------------------------------------------------------------------------- #
class MaterialBase(SQLModel):
    name: str
    material_type: MaterialType
    # Dual UoM. `base_uom` is the stocking/issuing unit (e.g. metre); every
    # ledger movement and reservation is denominated in it. `purchase_uom` is
    # how the item is bought (e.g. roll). `purchase_to_base_factor` converts one
    # purchase unit into base units (metres per roll). For single-UoM items the
    # two units match and the factor is 1.
    base_uom: str = "metre"
    purchase_uom: str = "metre"
    purchase_to_base_factor: Decimal = rate_field(default=Decimal("1"))

    lot_tracked: bool = False
    lead_time_days: int = 0
    min_order_qty: Decimal = quantity_field(default=Decimal("0"))

    # Fabric-only descriptive attributes (null for trims/thread/etc.).
    composition: Optional[str] = None
    construction: Optional[str] = None
    weave: Optional[str] = None
    gsm: Optional[Decimal] = quantity_field(default=None, nullable=True)
    width_cm: Optional[Decimal] = quantity_field(default=None, nullable=True)

    active: bool = True


class Material(MaterialBase, TimestampMixin, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)


class MaterialCreate(MaterialBase):
    pass


class MaterialUpdate(SQLModel):
    name: Optional[str] = None
    lot_tracked: Optional[bool] = None
    lead_time_days: Optional[int] = None
    min_order_qty: Optional[Decimal] = None
    composition: Optional[str] = None
    construction: Optional[str] = None
    weave: Optional[str] = None
    gsm: Optional[Decimal] = None
    width_cm: Optional[Decimal] = None
    active: Optional[bool] = None


class MaterialRead(MaterialBase):
    id: int
    code: str
