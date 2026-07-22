// Mirrors of the backend read schemas (only the fields the UI uses).

export interface Colour { id: number; code: string; name: string; pantone?: string; hex?: string; active: boolean; }
export interface Season { id: number; code: string; name: string; active: boolean; }
export interface SizeItem { position: number; label: string; }
export interface SizeRange { id: number; code: string; name: string; active: boolean; sizes: SizeItem[]; }
export interface Customer { id: number; code: string; name: string; currency: string; credit_limit: string; active: boolean; }
export interface Supplier { id: number; code: string; name: string; currency: string; lead_time_days: number; material_types: string[]; active: boolean; }
export interface Material {
  id: number; code: string; name: string; material_type: string;
  base_uom: string; purchase_uom: string; purchase_to_base_factor: string;
  lot_tracked: boolean; width_cm?: string; gsm?: string; composition?: string; active: boolean;
}

export interface Style {
  id: number; style_number: string; description: string; size_range_id: number;
  customer_id?: number; season_id?: number; gender: string; status: string; standard_sam?: string;
}
export interface Colourway { id: number; style_id: number; colour_id: number; buyer_reference?: string; lab_dip_approved: boolean; }
export interface SizeConsumption { size_label: string; position: number; consumption: string; }
export interface BomLine { id: number; material_id: number; colour_id?: number; wastage_pct: string; optional_component: boolean; size_consumption: SizeConsumption[]; }
export interface BomVersion { id: number; style_id: number; version_no: number; status: string; approved_by?: string; lines: BomLine[]; }

export interface SizeCell { size_label: string; position: number; ordered_qty: number; confirmed_qty: number; shipped_qty: number; }
export interface SalesOrderLine { id: number; style_id: number; colour_id: number; unit_price: string; line_quantity: number; line_value: string; sizes: SizeCell[]; }
export interface SalesOrder {
  id: number; order_number: string; status: string; customer_id: number; customer_po_number?: string;
  currency: string; total_quantity: number; total_value: string; lines: SalesOrderLine[];
}

export interface POLine { id: number; material_id: number; ordered_qty: string; received_qty: string; unit_price: string; uom: string; outstanding_qty: string; }
export interface PurchaseOrder { id: number; order_number: string; status: string; supplier_id: number; currency: string; total_value: string; lines: POLine[]; }
export interface GRRoll { roll_id: number; roll_number: string; purchase_order_line_id: number; length: string; status: string; }
export interface GoodsReceipt { id: number; receipt_number: string; purchase_order_id: number; supplier_id: number; total_length: string; total_value: string; rolls: GRRoll[]; }

export interface Roll {
  id: number; roll_number: string; material_id: number; status: string; grade: string;
  dye_lot?: string; shade_group?: string; width_cm?: string; length: string;
}
export interface LedgerEntry { id: number; material_id: number; roll_id?: number; movement_type: string; quantity: string; uom: string; reference_type?: string; created_at: string; note?: string; }
export interface Reservation { id: number; material_id: number; roll_id?: number; reserved_qty: string; status: string; reference_type?: string; reference_id?: number; }

export interface CutSize { size_label: string; position: number; planned_qty: number; cut_qty: number; }
export interface CutOrder {
  id: number; order_number: string; status: string; style_id: number; colour_id: number;
  fabric_material_id: number; fabric_required: string; fabric_issued: string; pieces_cut: number; sizes: CutSize[];
}
export interface SewingOrder { id: number; order_number: string; status: string; style_id: number; line?: string; planned_qty: number; produced_qty: number; sam?: string; average_efficiency_pct: string; }
export interface Subcontract { id: number; order_number: string; subcontractor_id: number; process: string; sent_qty: number; received_qty: number; outstanding_qty: number; rate: string; status: string; }

export interface FourPoint { id: number; inspection_number: string; roll_id: number; total_points: number; points_per_100sqyd: string; result: string; roll_status: string; }
export interface Inline { id: number; inspection_number: string; sewing_order_id: number; units_checked: number; defects_found: number; dhu: string; }
export interface Final { id: number; inspection_number: string; sales_order_id: number; lot_size: number; aql: string; sample_size: number; accept_number: number; defects_found: number; result: string; }

export interface CostLine { id: number; category: string; description?: string; quantity: string; rate: string; wastage_pct: string; amount: string; }
export interface CostSheet {
  id: number; style_id: number; version_no: number; status: string; currency: string;
  sam: string; overhead_pct: string; margin_pct: string; lines: CostLine[];
  material_cost: string; sewing_cost: string; overhead_cost: string; total_cost: string; selling_price: string; margin_amount: string;
}
export interface Profitability { sales_order_id: number; order_number: string; total_quantity: number; revenue: string; unit_cost: string; total_cost: string; profit: string; margin_pct: string; }

export interface Account { id: number; code: string; name: string; type: string; active: boolean; }
export interface JournalLine { account_id: number; account_code: string; debit: string; credit: string; description?: string; }
export interface JournalEntry { id: number; entry_number: string; memo?: string; source: string; status: string; total_debit: string; total_credit: string; lines: JournalLine[]; }
export interface ARInvoice { id: number; invoice_number: string; customer_id: number; amount: string; settled_amount: string; outstanding: string; status: string; }
export interface APBill { id: number; bill_number: string; supplier_id: number; amount: string; settled_amount: string; outstanding: string; status: string; }
export interface PnL { total_income: string; total_expense: string; net_profit: string; by_account: Record<string, string>; }
