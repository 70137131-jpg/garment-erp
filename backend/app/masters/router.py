from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..db import get_session
from ..kernel.numbering import next_document_number
from ..kernel.rbac import Role, require_roles
from .models import (
    Colour,
    ColourCreate,
    ColourRead,
    ColourUpdate,
    Customer,
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
    Material,
    MaterialCreate,
    MaterialRead,
    MaterialType,
    MaterialUpdate,
    Season,
    SeasonCreate,
    SeasonRead,
    SeasonUpdate,
    SizeItemRead,
    SizeRange,
    SizeRangeCreate,
    SizeRangeItem,
    SizeRangeRead,
    SizeRangeUpdate,
    Supplier,
    SupplierCreate,
    SupplierMaterialType,
    SupplierRead,
    SupplierUpdate,
)

router = APIRouter(prefix="/masters", tags=["masters"])


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate or invalid record") from exc


def _apply_update(record, payload) -> None:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)


# --------------------------------------------------------------------------- #
# Colours (1.4)
# --------------------------------------------------------------------------- #
@router.post("/colours", response_model=ColourRead, status_code=201)
def create_colour(payload: ColourCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.merchandiser))):
    colour = Colour.model_validate(payload)
    session.add(colour)
    _commit(session)
    session.refresh(colour)
    return colour


@router.get("/colours", response_model=List[ColourRead])
def list_colours(session: Session = Depends(get_session)):
    return session.exec(select(Colour)).all()


@router.patch("/colours/{colour_id}", response_model=ColourRead)
def update_colour(
    colour_id: int,
    payload: ColourUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    colour = session.get(Colour, colour_id)
    if colour is None:
        raise HTTPException(status_code=404, detail="Colour not found")
    _apply_update(colour, payload)
    session.add(colour)
    _commit(session)
    session.refresh(colour)
    return colour


# --------------------------------------------------------------------------- #
# Seasons (1.5)
# --------------------------------------------------------------------------- #
@router.post("/seasons", response_model=SeasonRead, status_code=201)
def create_season(payload: SeasonCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.merchandiser))):
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="Season end date cannot precede start date")
    season = Season.model_validate(payload)
    session.add(season)
    _commit(session)
    session.refresh(season)
    return season


@router.get("/seasons", response_model=List[SeasonRead])
def list_seasons(session: Session = Depends(get_session)):
    return session.exec(select(Season)).all()


@router.patch("/seasons/{season_id}", response_model=SeasonRead)
def update_season(
    season_id: int,
    payload: SeasonUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    season = session.get(Season, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    _apply_update(season, payload)
    if season.start_date and season.end_date and season.end_date < season.start_date:
        raise HTTPException(status_code=422, detail="Season end date cannot precede start date")
    session.add(season)
    _commit(session)
    session.refresh(season)
    return season


# --------------------------------------------------------------------------- #
# Size ranges (1.6)
# --------------------------------------------------------------------------- #
def _size_range_read(size_range: SizeRange) -> SizeRangeRead:
    return SizeRangeRead(
        id=size_range.id,
        code=size_range.code,
        name=size_range.name,
        active=size_range.active,
        sizes=[
            SizeItemRead(position=s.position, label=s.label)
            for s in size_range.sizes
        ],
    )


@router.post("/size-ranges", response_model=SizeRangeRead, status_code=201)
def create_size_range(payload: SizeRangeCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.merchandiser))):
    size_range = SizeRange(code=payload.code, name=payload.name, active=payload.active)
    size_range.sizes = [
        SizeRangeItem(position=s.position, label=s.label) for s in payload.sizes
    ]
    session.add(size_range)
    _commit(session)
    session.refresh(size_range)
    return _size_range_read(size_range)


@router.get("/size-ranges", response_model=List[SizeRangeRead])
def list_size_ranges(session: Session = Depends(get_session)):
    ranges = session.exec(select(SizeRange).options(selectinload(SizeRange.sizes))).all()
    return [_size_range_read(r) for r in ranges]


@router.patch("/size-ranges/{size_range_id}", response_model=SizeRangeRead)
def update_size_range(
    size_range_id: int,
    payload: SizeRangeUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    size_range = session.get(SizeRange, size_range_id)
    if size_range is None:
        raise HTTPException(status_code=404, detail="Size range not found")
    _apply_update(size_range, payload)
    session.add(size_range)
    _commit(session)
    session.refresh(size_range)
    return _size_range_read(size_range)


# --------------------------------------------------------------------------- #
# Customers (1.1)
# --------------------------------------------------------------------------- #
@router.post("/customers", response_model=CustomerRead, status_code=201)
def create_customer(payload: CustomerCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.merchandiser))):
    code = next_document_number(session, "CUSTOMER", "CUST")
    customer = Customer.model_validate(payload, update={"code": code})
    session.add(customer)
    _commit(session)
    session.refresh(customer)
    return customer


@router.get("/customers", response_model=List[CustomerRead])
def list_customers(session: Session = Depends(get_session)):
    return session.exec(select(Customer)).all()


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.merchandiser)),
):
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    _apply_update(customer, payload)
    session.add(customer)
    _commit(session)
    session.refresh(customer)
    return customer


# --------------------------------------------------------------------------- #
# Suppliers (1.2)
# --------------------------------------------------------------------------- #
def _supplier_read(supplier: Supplier) -> SupplierRead:
    return SupplierRead(
        id=supplier.id,
        code=supplier.code,
        name=supplier.name,
        currency=supplier.currency,
        payment_terms=supplier.payment_terms,
        lead_time_days=supplier.lead_time_days,
        restricted_substance_certified=supplier.restricted_substance_certified,
        active=supplier.active,
        material_types=[mt.material_type for mt in supplier.material_types],
    )


@router.post("/suppliers", response_model=SupplierRead, status_code=201)
def create_supplier(payload: SupplierCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.procurement, Role.merchandiser))):
    code = next_document_number(session, "SUPPLIER", "SUP")
    supplier = Supplier(
        code=code,
        name=payload.name,
        currency=payload.currency,
        payment_terms=payload.payment_terms,
        lead_time_days=payload.lead_time_days,
        restricted_substance_certified=payload.restricted_substance_certified,
        active=payload.active,
    )
    supplier.material_types = [
        SupplierMaterialType(material_type=mt) for mt in payload.material_types
    ]
    session.add(supplier)
    _commit(session)
    session.refresh(supplier)
    return _supplier_read(supplier)


@router.get("/suppliers", response_model=List[SupplierRead])
def list_suppliers(session: Session = Depends(get_session)):
    suppliers = session.exec(
        select(Supplier).options(selectinload(Supplier.material_types))
    ).all()
    return [_supplier_read(s) for s in suppliers]


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.procurement, Role.merchandiser)),
):
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    values = payload.model_dump(exclude_unset=True, exclude={"material_types"})
    for field, value in values.items():
        setattr(supplier, field, value)
    if payload.material_types is not None:
        supplier.material_types = [
            SupplierMaterialType(material_type=material_type)
            for material_type in dict.fromkeys(payload.material_types)
        ]
    session.add(supplier)
    _commit(session)
    session.refresh(supplier)
    return _supplier_read(supplier)


# --------------------------------------------------------------------------- #
# Materials (1.3)
# --------------------------------------------------------------------------- #
_MATERIAL_CODE_PREFIX = {
    MaterialType.fabric: ("MATERIAL_FAB", "FAB"),
    MaterialType.trims: ("MATERIAL_TRM", "TRM"),
    MaterialType.thread: ("MATERIAL_THR", "THR"),
    MaterialType.labels: ("MATERIAL_LBL", "LBL"),
    MaterialType.hangtags: ("MATERIAL_HTG", "HTG"),
    MaterialType.packaging: ("MATERIAL_PKG", "PKG"),
    MaterialType.chemicals: ("MATERIAL_CHM", "CHM"),
    MaterialType.consumables: ("MATERIAL_CON", "CON"),
    MaterialType.service: ("MATERIAL_SVC", "SVC"),
}


@router.post("/materials", response_model=MaterialRead, status_code=201)
def create_material(payload: MaterialCreate, session: Session = Depends(get_session), _: str = Depends(require_roles(Role.procurement, Role.merchandiser))):
    if payload.purchase_to_base_factor <= 0:
        raise HTTPException(status_code=422, detail="purchase_to_base_factor must be positive")
    doc_type, prefix = _MATERIAL_CODE_PREFIX[payload.material_type]
    code = next_document_number(session, doc_type, prefix)
    material = Material.model_validate(payload, update={"code": code})
    session.add(material)
    _commit(session)
    session.refresh(material)
    return material


@router.get("/materials", response_model=List[MaterialRead])
def list_materials(
    material_type: MaterialType | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Material)
    if material_type is not None:
        stmt = stmt.where(Material.material_type == material_type)
    return session.exec(stmt).all()


@router.get("/materials/{material_id}", response_model=MaterialRead)
def get_material(material_id: int, session: Session = Depends(get_session)):
    material = session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


@router.patch("/materials/{material_id}", response_model=MaterialRead)
def update_material(
    material_id: int,
    payload: MaterialUpdate,
    session: Session = Depends(get_session),
    _: str = Depends(require_roles(Role.procurement, Role.merchandiser)),
):
    material = session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(material, field, value)
    session.add(material)
    _commit(session)
    session.refresh(material)
    return material
