// Mirrors of the backend read schemas (only the fields the UI uses).

export interface Colour { id: number; code: string; name: string; pantone?: string; hex?: string; active: boolean; }
export interface Season { id: number; code: string; name: string; start_date?: string; end_date?: string; active: boolean; }
export interface SizeItem { position: number; label: string; }
export interface SizeRange { id: number; code: string; name: string; active: boolean; sizes: SizeItem[]; }
export interface Customer { id: number; code: string; name: string; currency: string; payment_terms?: string; credit_limit: string; billing_address?: string; active: boolean; }
export interface Supplier { id: number; code: string; name: string; currency: string; payment_terms?: string; lead_time_days: number; restricted_substance_certified: boolean; material_types: string[]; active: boolean; }
export interface Material {
  id: number; code: string; name: string; material_type: string;
  base_uom: string; purchase_uom: string; purchase_to_base_factor: string;
  lot_tracked: boolean; lead_time_days: number; min_order_qty: string; width_cm?: string; gsm?: string;
  composition?: string; construction?: string; weave?: string; active: boolean;
  valuation_method?: "weighted_average" | "fifo";
}

export interface Style {
  id: number; style_number: string; description: string; size_range_id: number;
  customer_id?: number; season_id?: number; gender: string; status: string; standard_sam?: string;
}
export interface Colourway { id: number; style_id: number; colour_id: number; buyer_reference?: string; lab_dip_approved: boolean; }
export interface SizeConsumption { size_label: string; position: number; consumption: string; }
export interface BomLine { id: number; material_id: number; colour_id?: number; wastage_pct: string; optional_component: boolean; size_consumption: SizeConsumption[]; }
export interface BomVersion { id: number; style_id: number; version_no: number; status: string; approved_by?: string; lines: BomLine[]; }

export interface SizeCell { id: number; size_label: string; position: number; ordered_qty: number; confirmed_qty: number; shipped_qty: number; }
export interface SalesOrderLine { id: number; style_id: number; colour_id: number; unit_price: string; line_quantity: number; line_value: string; sizes: SizeCell[]; }
export interface SalesOrder {
  id: number; order_number: string; status: string; customer_id: number; customer_po_number?: string;
  currency: string; total_quantity: number; total_shipped_quantity: number; total_value: string; lines: SalesOrderLine[];
}
export interface ShipmentLine { id: number; sales_order_line_id: number; sales_order_size_cell_id: number; size_label: string; quantity: number; carton_count: number; }
export interface Shipment { id: number; shipment_number: string; sales_order_id: number; shipment_date?: string; shipping_reference?: string; destination?: string; status: string; cost_of_goods: string; total_quantity: number; total_cartons: number; lines: ShipmentLine[]; }

export interface POLine { id: number; material_id: number; ordered_qty: string; received_qty: string; unit_price: string; uom: string; outstanding_qty: string; }
export interface PurchaseOrder { id: number; order_number: string; status: string; supplier_id: number; currency: string; total_value: string; requires_approval?: boolean; approved_by?: string; approved_at?: string; lines: POLine[]; }
export interface GRRoll { roll_id: number; roll_number: string; purchase_order_line_id: number; length: string; status: string; }
export interface GoodsReceipt { id: number; receipt_number: string; purchase_order_id: number; supplier_id: number; total_length: string; total_value: string; rolls: GRRoll[]; }

export interface Roll {
  id: number; roll_number: string; material_id: number; status: string; grade: string;
  dye_lot?: string; shade_group?: string; width_cm?: string; length: string;
  warehouse?: string; location?: string; parent_roll_id?: number;
}
export interface LedgerEntry { id: number; material_id: number; roll_id?: number; movement_type: string; quantity: string; unit_cost: string; extended_cost: string; uom: string; reference_type?: string; created_at: string; note?: string; }
export interface Reservation { id: number; material_id: number; roll_id?: number; reserved_qty: string; status: string; reference_type?: string; reference_id?: number; }
export interface WarehouseOperationLine { material_id: number; roll_id?: number; result_roll_id?: number; quantity: string; from_warehouse?: string; from_location?: string; to_warehouse?: string; to_location?: string; old_grade?: string; new_grade?: string; system_qty?: string; counted_qty?: string; variance_qty?: string; }
export interface WarehouseOperation { id: number; operation_number: string; operation_type: string; reference?: string; reason?: string; created_at: string; created_by?: string; lines: WarehouseOperationLine[]; }

export interface CutSize { size_label: string; position: number; planned_qty: number; cut_qty: number; }
export interface CutOrder {
  id: number; order_number: string; status: string; style_id: number; colour_id: number;
  fabric_material_id: number; fabric_required: string; fabric_issued: string; pieces_cut: number; sizes: CutSize[];
}
export interface SewingOrder { id: number; order_number: string; status: string; style_id: number; line?: string; planned_qty: number; produced_qty: number; sam?: string; average_efficiency_pct: string; }
export interface Subcontract { id: number; order_number: string; subcontractor_id: number; process: string; sent_qty: number; received_qty: number; outstanding_qty: number; rate: string; status: string; }
export interface ProductionRouteStep { id: number; sequence: number; operation: string; work_center?: string; standard_minutes: string; subcontract_allowed: boolean; }
export interface ProductionRoute { id: number; route_number: string; style_id: number; name: string; version_no: number; active: boolean; steps: ProductionRouteStep[]; }
export interface WipSummary { route_step_id: number; sequence: number; operation: string; work_center?: string; quantity_in: number; quantity_out: number; rejected_qty: number; wip_qty: number; }

export interface FourPoint { id: number; inspection_number: string; roll_id: number; total_points: number; points_per_100sqyd: string; result: string; roll_status: string; }
export interface Inline { id: number; inspection_number: string; sewing_order_id: number; units_checked: number; defects_found: number; dhu: string; }
export interface Final { id: number; inspection_number: string; sales_order_id: number; lot_size: number; aql: string; sample_size: number; accept_number: number; defects_found: number; result: string; }
export interface LabTest { id: number; test_number: string; material_id: number; roll_id?: number; supplier_id?: number; test_type: string; method?: string; specification?: string; measured_value?: string; status: string; submitted_date?: string; completed_date?: string; laboratory?: string; certificate_reference?: string; tested_by?: string; note?: string; }
export interface DefectAnalytics { defect_key: string; occurrences: number; penalty_points: number; share_pct: string; }

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
export interface TrialBalanceLine { account_id: number; account_code: string; account_name: string; account_type: string; debit: string; credit: string; }
export interface TrialBalance { as_of: string; total_debit: string; total_credit: string; lines: TrialBalanceLine[]; }
export interface StatementLine { account_code: string; account_name: string; amount: string; }
export interface BalanceSheet { as_of: string; assets: StatementLine[]; liabilities: StatementLine[]; equity: StatementLine[]; total_assets: string; total_liabilities: string; total_equity: string; liabilities_and_equity: string; }
export interface CashFlow { date_from: string; date_to: string; operating: string; investing: string; financing: string; net_change: string; }
export interface AgingBucket { party_id: number; current: string; days_1_30: string; days_31_60: string; days_61_90: string; over_90: string; total: string; }
export interface AgingReport { as_of: string; ledger: string; total: string; parties: AgingBucket[]; }
