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

export type VarianceType =
  | "material_price" | "material_usage" | "labour_rate"
  | "labour_efficiency" | "overhead" | "subcontract";
export interface CostVariance {
  id: number; variance_type: VarianceType;
  standard_amount: string; actual_amount: string; amount: string;
  favourable: boolean; explanation?: string | null;
}
export interface ActualCostRun {
  id: number; run_number: string; sales_order_id: number; order_number?: string | null;
  cost_sheet_version_id?: number | null; status: "draft" | "posted"; currency: string;
  as_of: string; produced_qty: number;
  std_material_cost: string; std_labour_cost: string; std_overhead_cost: string; std_total_cost: string;
  actual_material_cost: string; actual_labour_cost: string; actual_overhead_cost: string;
  actual_subcontract_cost: string; actual_total_cost: string;
  std_material_qty: string; actual_material_qty: string;
  std_minutes: string; actual_minutes: string;
  std_rate_per_min: string; actual_rate_per_min: string;
  total_variance: string; unit_std_cost: string; unit_actual_cost: string;
  journal_entry_id?: number | null; posted_at?: string | null; posted_by?: string | null;
  notes?: string | null; variances: CostVariance[];
}

// --- Planning: MRP, capacity, ATP ------------------------------------------
export type WorkCentreType =
  | "cutting" | "sewing" | "finishing" | "packing" | "embroidery" | "washing";
export interface WorkCentre {
  id: number; code: string; name: string; centre_type: WorkCentreType;
  operators: number; shift_minutes: number; shifts_per_day: number;
  efficiency_pct: string; daily_minutes: string; active: boolean;
}
export interface CapacityBucket {
  bucket_start: string; bucket_end: string;
  capacity_minutes: string; loaded_minutes: string; available_minutes: string;
  utilisation_pct: string; overloaded: boolean;
}
export interface WorkCentreLoad {
  work_centre_id: number; code: string; name: string; centre_type: WorkCentreType;
  buckets: CapacityBucket[]; total_capacity: string; total_load: string;
  utilisation_pct: string; overloaded_buckets: number;
}
export interface CapacityBoard {
  horizon_start: string; horizon_end: string; bucket_days: number;
  centres: WorkCentreLoad[];
}
export interface CapacityBooking {
  id: number; work_centre_id: number; sewing_order_id?: number | null;
  cut_order_id?: number | null; sales_order_id?: number | null;
  description?: string | null; start_date: string; end_date: string;
  minutes: string; status: "planned" | "confirmed" | "released" | "cancelled";
}
export interface MrpDemandSource { sales_order_id: number; quantity: string; need_date?: string | null; }
export interface MrpBucket {
  id: number; material_id: number; material_code?: string | null; material_name?: string | null;
  sequence: number; bucket_start: string; bucket_end: string;
  opening_balance: string; gross_requirement: string; scheduled_receipts: string;
  net_requirement: string; planned_order_qty: string; projected_available: string;
  demand_sources: MrpDemandSource[];
}
export interface PlannedOrder {
  id: number; run_id: number; material_id: number;
  material_code?: string | null; material_name?: string | null;
  quantity: string; need_date: string; release_date: string; lead_time_days: number;
  status: "planned" | "firmed" | "cancelled"; requisition_id?: number | null; past_due: boolean;
}
export interface MrpRun {
  id: number; run_number: string; horizon_start: string; horizon_end: string;
  bucket_days: number; status: "draft" | "firmed"; generated_by?: string | null;
  notes?: string | null; material_count: number; planned_order_count: number; past_due_count: number;
}
export interface MrpRunDetail extends MrpRun { buckets: MrpBucket[]; planned_orders: PlannedOrder[]; }
export interface AtpMaterialLine {
  material_id: number; material_code?: string | null; material_name?: string | null;
  required_qty: string; available_qty: string; shortfall_qty: string;
  lead_time_days: number; earliest_available?: string | null;
}
export interface AtpResponse {
  style_id: number; quantity: number; wanted_date: string; can_promise: boolean;
  promise_date?: string | null; limiting_factor: string;
  required_minutes: string; available_minutes: string;
  capacity_ready_date?: string | null; material_ready_date?: string | null;
  materials: AtpMaterialLine[]; notes: string[];
}

// --- MES / OEE --------------------------------------------------------------
export type DowntimeCategory =
  | "breakdown" | "changeover" | "material_shortage" | "no_operator"
  | "quality_issue" | "power" | "planned_maintenance" | "other";
export interface DowntimeReason {
  id: number; code: string; description: string;
  category: DowntimeCategory; planned: boolean; active: boolean;
}
export interface DowntimeEventRead {
  id: number; reason_id: number; reason_code?: string | null;
  reason_description?: string | null; category?: DowntimeCategory | null;
  planned: boolean; minutes: string; note?: string | null; recorded_by?: string | null;
}
export interface ShiftLog {
  id: number; log_number: string; work_centre_id: number; work_centre_code?: string | null;
  machine_id?: number | null; machine_code?: string | null; sewing_order_id?: number | null;
  log_date: string; shift: string; operators: number; planned_minutes: string;
  total_count: number; good_count: number; reject_count: number;
  ideal_cycle_seconds?: string | null; status: "open" | "closed";
  planned_downtime_minutes: string; unplanned_downtime_minutes: string; run_minutes: string;
  availability_pct: string; performance_pct: string; quality_pct: string; oee_pct: string;
  performance_capped: boolean; closed_at?: string | null; closed_by?: string | null;
  notes?: string | null; downtime: DowntimeEventRead[];
}
export interface DowntimeParetoLine {
  reason_id: number; reason_code: string; description: string;
  category: DowntimeCategory; planned: boolean; minutes: string;
  events: number; share_pct: string; cumulative_pct: string;
}
export interface OeeSummary {
  date_from: string; date_to: string; work_centre_id?: number | null; shifts: number;
  planned_minutes: string; planned_downtime_minutes: string; unplanned_downtime_minutes: string;
  run_minutes: string; total_count: number; good_count: number; reject_count: number;
  availability_pct: string; performance_pct: string; quality_pct: string; oee_pct: string;
  pareto: DowntimeParetoLine[];
}
export interface AndonLine {
  work_centre_id: number; code: string; name: string; open_logs: number;
  today_oee_pct: string; today_downtime_minutes: string; status: string;
  top_downtime_reason?: string | null;
}
export interface AndonBoard { as_of: string; lines: AndonLine[]; }
export interface Machine {
  id: number; code: string; name: string; work_centre_id: number;
  ideal_cycle_seconds: string; active: boolean;
}

// --- Mobile WMS -------------------------------------------------------------
export type BinType = "storage" | "staging" | "receiving" | "shipping" | "quarantine";
export interface Bin {
  id: number; code: string; warehouse: string; zone?: string | null;
  bin_type: BinType; pick_sequence: number; capacity_qty?: string | null; active: boolean;
}
export type WmsTaskType = "put_away" | "pick" | "replenish" | "count" | "transfer";
export type WmsTaskStatus = "open" | "assigned" | "completed" | "cancelled";
export interface WarehouseTask {
  id: number; task_number: string; task_type: WmsTaskType; status: WmsTaskStatus;
  priority: number; material_id: number; material_code?: string | null;
  material_name?: string | null; roll_id?: number | null; roll_number?: string | null;
  quantity: string; from_bin_id?: number | null; from_bin_code?: string | null;
  to_bin_id?: number | null; to_bin_code?: string | null;
  reference_type?: string | null; reference_id?: number | null;
  assigned_to?: string | null; due_date?: string | null;
  completed_qty: string; completed_at?: string | null; completed_by?: string | null;
  short_reason?: string | null; warehouse_operation_id?: number | null; notes?: string | null;
}
export interface ScanResult {
  kind: "roll" | "bin" | "material" | "unknown";
  id?: number | null; code?: string | null; label?: string | null;
  material_id?: number | null; material_code?: string | null; material_name?: string | null;
  quantity?: string | null; uom?: string | null; warehouse?: string | null;
  location?: string | null; status?: string | null; grade?: string | null;
  open_tasks: WarehouseTask[];
}

// --- Markers and cut plans --------------------------------------------------
export interface MarkerSizeRead { size_label: string; quantity: number; }
export interface Marker {
  id: number; marker_code: string; style_id: number; bom_version_id?: number | null;
  width_cm: string; length_cm: string; pattern_area_cm2: string; efficiency_pct: string;
  max_plies: number; status: "draft" | "approved" | "retired";
  notes?: string | null; pieces_per_ply: number; sizes: MarkerSizeRead[];
}
export interface CutPlanLay {
  id: number; sequence: number; marker_id: number; marker_code?: string | null;
  plies: number; fabric_cm: string; fabric_m: string; pieces: number; efficiency_pct: string;
}
export interface CutPlanDemand {
  size_label: string; required_qty: number; planned_qty: number; overcut_qty: number;
}
export interface CutPlan {
  id: number; plan_number: string; style_id: number;
  sales_order_id?: number | null; cut_order_id?: number | null;
  fabric_material_id?: number | null; width_cm: string; max_plies: number;
  status: "draft" | "approved" | "cancelled";
  total_fabric_cm: string; total_fabric_m: string; total_plies: number; lay_count: number;
  required_pieces: number; planned_pieces: number; overcut_pieces: number; overcut_pct: string;
  weighted_efficiency_pct: string; bom_fabric_cm?: string | null; bom_fabric_m?: string | null;
  saving_vs_bom_m?: string | null; algorithm: string; notes?: string | null;
  lays: CutPlanLay[]; demands: CutPlanDemand[];
}

export interface Account { id: number; code: string; name: string; type: string; active: boolean; }
export interface JournalLine { account_id: number; account_code: string; debit: string; credit: string; description?: string; }
export interface JournalEntry { id: number; entry_number: string; memo?: string; source: string; status: string; total_debit: string; total_credit: string; lines: JournalLine[]; }
export interface ARInvoice { id: number; invoice_number: string; customer_id: number; amount: string; settled_amount: string; outstanding: string; status: string; }
export interface APBill {
  id: number; bill_number: string; supplier_id: number; purchase_order_id?: number; goods_receipt_id?: number;
  supplier_invoice_number?: string; amount: string; settled_amount: string; outstanding: string; status: string;
  match_status: "pending" | "matched" | "exception"; received_amount: string; variance_amount: string;
}
export interface PnL { total_income: string; total_expense: string; net_profit: string; by_account: Record<string, string>; }
export interface TrialBalanceLine { account_id: number; account_code: string; account_name: string; account_type: string; debit: string; credit: string; }
export interface TrialBalance { as_of: string; total_debit: string; total_credit: string; lines: TrialBalanceLine[]; }
export interface StatementLine { account_code: string; account_name: string; amount: string; }
export interface BalanceSheet { as_of: string; assets: StatementLine[]; liabilities: StatementLine[]; equity: StatementLine[]; total_assets: string; total_liabilities: string; total_equity: string; liabilities_and_equity: string; }
export interface CashFlow { date_from: string; date_to: string; operating: string; investing: string; financing: string; net_change: string; }
export interface AgingBucket { party_id: number; current: string; days_1_30: string; days_31_60: string; days_61_90: string; over_90: string; total: string; }
export interface AgingReport { as_of: string; ledger: string; total: string; parties: AgingBucket[]; }
