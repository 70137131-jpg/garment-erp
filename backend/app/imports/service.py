"""CSV onboarding imports: masters and opening stock.

Every factory arrives with existing customers, suppliers, materials, and stock
counts in spreadsheets. These importers turn a CSV export of those sheets into
validated master data. The contract is all-or-nothing: a file either imports
completely or not at all, so a re-run after fixing reported errors never
creates half the rows twice.
"""

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from pydantic import ValidationError
from sqlmodel import SQLModel, Session, select

from ..inventory.models import MovementType
from ..inventory.service import post_movement
from ..kernel.numbering import next_document_number
from ..masters.models import (
    Customer,
    CustomerCreate,
    Material,
    MaterialCreate,
    MaterialType,
    Supplier,
    SupplierCreate,
    SupplierMaterialType,
)

MAX_IMPORT_ROWS = 5000


class RowError(SQLModel):
    row: int  # 1-based data row number (header not counted)
    message: str


class ImportReport(SQLModel):
    kind: str
    total_rows: int
    valid_rows: int
    created: int
    applied: bool
    errors: list[RowError] = []


# Column contracts, also served as downloadable templates.
TEMPLATES: dict[str, list[str]] = {
    "customers": [
        "name", "currency", "payment_terms", "credit_limit", "billing_address",
    ],
    "suppliers": [
        "name", "currency", "payment_terms", "lead_time_days", "material_types",
    ],
    "materials": [
        "name", "material_type", "base_uom", "purchase_uom",
        "purchase_to_base_factor", "lot_tracked", "lead_time_days",
        "min_order_qty", "composition", "gsm", "width_cm",
    ],
    "opening-stock": [
        "material_code", "quantity", "warehouse", "location", "lot", "note",
    ],
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "customers": {"name"},
    "suppliers": {"name"},
    "materials": {"name", "material_type"},
    "opening-stock": {"material_code", "quantity"},
}


def _parse_rows(kind: str, content: bytes) -> tuple[list[dict], list[RowError]]:
    """Decode and read the CSV; verify the header matches the template."""
    try:
        text = content.decode("utf-8-sig")  # tolerate Excel's BOM
    except UnicodeDecodeError:
        return [], [RowError(row=0, message="File must be UTF-8 encoded CSV")]

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], [RowError(row=0, message="File is empty")]
    expected = TEMPLATES[kind]
    got = [name.strip().lower() for name in reader.fieldnames]
    unknown = [name for name in got if name not in expected]
    missing = REQUIRED_COLUMNS[kind] - set(got)
    if missing:
        return [], [RowError(row=0, message=f"Missing required columns: {', '.join(sorted(missing))}")]
    if unknown:
        return [], [RowError(row=0, message=f"Unknown columns: {', '.join(unknown)}. Expected: {', '.join(expected)}")]

    rows = []
    for raw in reader:
        cleaned = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in raw.items()
            if key is not None
        }
        if any(cleaned.values()):  # skip fully blank lines
            rows.append(cleaned)
    if len(rows) > MAX_IMPORT_ROWS:
        return [], [RowError(row=0, message=f"Too many rows ({len(rows)}); the limit per file is {MAX_IMPORT_ROWS}")]
    return rows, []


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err["loc"]) or "row"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def _drop_blank(row: dict) -> dict:
    """Blank CSV cells mean 'use the default', not 'empty string'."""
    return {key: value for key, value in row.items() if value != ""}


def import_customers(session: Session, content: bytes, *, apply: bool) -> ImportReport:
    rows, errors = _parse_rows("customers", content)
    payloads: list[CustomerCreate] = []
    seen: set[str] = set()
    existing = {
        name.strip().lower()
        for name in session.exec(select(Customer.name)).all()
    }
    for index, row in enumerate(rows, start=1):
        try:
            payload = CustomerCreate.model_validate(_drop_blank(row))
        except ValidationError as exc:
            errors.append(RowError(row=index, message=_validation_message(exc)))
            continue
        key = payload.name.strip().lower()
        if not key:
            errors.append(RowError(row=index, message="name is required"))
        elif key in existing:
            errors.append(RowError(row=index, message=f"customer '{payload.name}' already exists"))
        elif key in seen:
            errors.append(RowError(row=index, message=f"duplicate name '{payload.name}' inside this file"))
        else:
            seen.add(key)
            payloads.append(payload)

    created = 0
    applied = apply and not errors
    if applied:
        for payload in payloads:
            code = next_document_number(session, "CUSTOMER", "CUST")
            session.add(Customer(**payload.model_dump(), code=code))
            created += 1
        session.commit()
    return ImportReport(
        kind="customers", total_rows=len(rows), valid_rows=len(payloads),
        created=created, applied=applied, errors=errors,
    )


def import_suppliers(session: Session, content: bytes, *, apply: bool) -> ImportReport:
    rows, errors = _parse_rows("suppliers", content)
    payloads: list[SupplierCreate] = []
    seen: set[str] = set()
    existing = {
        name.strip().lower()
        for name in session.exec(select(Supplier.name)).all()
    }
    for index, row in enumerate(rows, start=1):
        data = _drop_blank(row)
        if "material_types" in data:
            # Pipe-separated in the sheet: "fabric|trims|thread"
            data["material_types"] = [
                part.strip() for part in data["material_types"].split("|") if part.strip()
            ]
        try:
            payload = SupplierCreate.model_validate(data)
        except ValidationError as exc:
            errors.append(RowError(row=index, message=_validation_message(exc)))
            continue
        key = payload.name.strip().lower()
        if not key:
            errors.append(RowError(row=index, message="name is required"))
        elif key in existing:
            errors.append(RowError(row=index, message=f"supplier '{payload.name}' already exists"))
        elif key in seen:
            errors.append(RowError(row=index, message=f"duplicate name '{payload.name}' inside this file"))
        else:
            seen.add(key)
            payloads.append(payload)

    created = 0
    applied = apply and not errors
    if applied:
        for payload in payloads:
            code = next_document_number(session, "SUPPLIER", "SUP")
            supplier = Supplier(**payload.model_dump(exclude={"material_types"}), code=code)
            session.add(supplier)
            session.flush()
            for material_type in payload.material_types:
                session.add(
                    SupplierMaterialType(supplier_id=supplier.id, material_type=material_type)
                )
            created += 1
        session.commit()
    return ImportReport(
        kind="suppliers", total_rows=len(rows), valid_rows=len(payloads),
        created=created, applied=applied, errors=errors,
    )


# Mirrors masters.router._MATERIAL_CODE_PREFIX (imported lazily to avoid a
# circular router import).
def _material_prefix() -> dict[MaterialType, tuple[str, str]]:
    from ..masters.router import _MATERIAL_CODE_PREFIX

    return _MATERIAL_CODE_PREFIX


def import_materials(session: Session, content: bytes, *, apply: bool) -> ImportReport:
    rows, errors = _parse_rows("materials", content)
    payloads: list[MaterialCreate] = []
    seen: set[str] = set()
    existing = {
        name.strip().lower()
        for name in session.exec(select(Material.name)).all()
    }
    for index, row in enumerate(rows, start=1):
        try:
            payload = MaterialCreate.model_validate(_drop_blank(row))
        except ValidationError as exc:
            errors.append(RowError(row=index, message=_validation_message(exc)))
            continue
        key = payload.name.strip().lower()
        if not key:
            errors.append(RowError(row=index, message="name is required"))
        elif key in existing:
            errors.append(RowError(row=index, message=f"material '{payload.name}' already exists"))
        elif key in seen:
            errors.append(RowError(row=index, message=f"duplicate name '{payload.name}' inside this file"))
        else:
            seen.add(key)
            payloads.append(payload)

    created = 0
    applied = apply and not errors
    if applied:
        prefixes = _material_prefix()
        for payload in payloads:
            doc_type, prefix = prefixes[payload.material_type]
            code = next_document_number(session, doc_type, prefix)
            session.add(Material(**payload.model_dump(), code=code))
            created += 1
        session.commit()
    return ImportReport(
        kind="materials", total_rows=len(rows), valid_rows=len(payloads),
        created=created, applied=applied, errors=errors,
    )


def import_opening_stock(
    session: Session, content: bytes, *, apply: bool, actor: Optional[str] = None
) -> ImportReport:
    rows, errors = _parse_rows("opening-stock", content)
    materials = {
        material.code.strip().lower(): material
        for material in session.exec(select(Material)).all()
    }
    valid: list[tuple[Material, Decimal, dict]] = []
    for index, row in enumerate(rows, start=1):
        code = row.get("material_code", "")
        material = materials.get(code.strip().lower())
        if material is None:
            errors.append(RowError(row=index, message=f"unknown material_code '{code}'"))
            continue
        try:
            quantity = Decimal(row.get("quantity", ""))
        except (InvalidOperation, ValueError):
            errors.append(RowError(row=index, message=f"quantity '{row.get('quantity', '')}' is not a number"))
            continue
        if quantity <= 0:
            errors.append(RowError(row=index, message="quantity must be positive for an opening balance"))
            continue
        valid.append((
            material,
            quantity,
            {
                "warehouse": row.get("warehouse") or "MAIN",
                "location": row.get("location") or None,
                "lot": row.get("lot") or None,
                "note": row.get("note") or "opening balance import",
            },
        ))

    created = 0
    applied = apply and not errors
    if applied:
        for material, quantity, extra in valid:
            post_movement(
                session,
                material_id=material.id,
                movement_type=MovementType.adjustment,
                quantity=quantity,
                uom=material.base_uom,
                reference_type="opening_import",
                actor=actor,
                **extra,
            )
            created += 1
        session.commit()
    return ImportReport(
        kind="opening-stock", total_rows=len(rows), valid_rows=len(valid),
        created=created, applied=applied, errors=errors,
    )


IMPORTERS: dict[str, Callable[..., ImportReport]] = {
    "customers": import_customers,
    "suppliers": import_suppliers,
    "materials": import_materials,
    "opening-stock": import_opening_stock,
}
