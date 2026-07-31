import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, AuthUser } from "../api/client";
import {
  APBill,
  ARInvoice,
  CapacityBoard,
  CostVariance,
  CutOrder,
  Final,
  FourPoint,
  Inline,
  LabTest,
  MrpRun,
  OeeSummary,
  PnL,
  PurchaseOrder,
  Reservation,
  Roll,
  SalesOrder,
  SewingOrder,
  ShiftLog,
  WarehouseTask,
} from "../api/types";
import { Card, Chip, ErrorBox, PageHeader, Spinner, Stat } from "../components/ui";
import { money, num, pct } from "../lib/format";
import type { Tone } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type DashboardRole =
  | "admin"
  | "merchandiser"
  | "procurement"
  | "stores"
  | "cutting_supervisor"
  | "sewing_supervisor"
  | "quality_inspector"
  | "finance"
  | "planner"
  | "readonly";

interface DashboardProfile {
  label: string;
  subtitle: string;
  focusTitle: string;
  quickLinks: Array<{ label: string; to: string }>;
}

interface DashboardData {
  orders?: SalesOrder[];
  purchaseOrders?: PurchaseOrder[];
  rolls?: Roll[];
  reservations?: Reservation[];
  cuts?: CutOrder[];
  sewing?: SewingOrder[];
  incoming?: FourPoint[];
  inline?: Inline[];
  final?: Final[];
  labTests?: LabTest[];
  mrpRuns?: MrpRun[];
  capacity?: CapacityBoard;
  tasks?: WarehouseTask[];
  shifts?: ShiftLog[];
  oee?: OeeSummary;
  pnl?: PnL;
  ar?: ARInvoice[];
  ap?: APBill[];
  variances?: CostVariance[];
}

interface DashboardLoadResult {
  role: DashboardRole;
  data: DashboardData;
}

interface Metric {
  label: string;
  value: string;
  foot?: string;
  accent?: "indigo" | "madder";
}

interface Priority {
  label: string;
  detail: string;
  count: number;
  tone: Tone;
  to: string;
}

interface FocusRow {
  label: string;
  meta: string;
  status: string;
  value: string;
  to?: string;
}

interface DashboardView {
  metrics: Metric[];
  priorities: Priority[];
  rows: FocusRow[];
}

const DASHBOARD_ROLES = new Set<DashboardRole>([
  "admin",
  "merchandiser",
  "procurement",
  "stores",
  "cutting_supervisor",
  "sewing_supervisor",
  "quality_inspector",
  "finance",
  "planner",
  "readonly",
]);

const CLOSED_STATUSES = new Set(["shipped", "closed", "cancelled", "completed", "received", "rejected"]);

const PROFILES: Record<DashboardRole, DashboardProfile> = {
  admin: {
    label: "Factory control room",
    subtitle: "Cross-functional exceptions and operating health across demand, stock, production, quality, and finance.",
    focusTitle: "Orders requiring coordination",
    quickLinks: [
      { label: "Sales orders", to: "/sales" },
      { label: "Planning", to: "/planning" },
      { label: "Finance", to: "/finance" },
      { label: "Access control", to: "/users" },
    ],
  },
  merchandiser: {
    label: "Merchandising desk",
    subtitle: "Buyer commitments, commercial readiness, material progress, costing, and delivery risk in one view.",
    focusTitle: "Commercial order focus",
    quickLinks: [
      { label: "Sales orders", to: "/sales" },
      { label: "Styles & BOM", to: "/styles" },
      { label: "Costing", to: "/costing" },
      { label: "ATP & planning", to: "/planning" },
    ],
  },
  procurement: {
    label: "Procurement desk",
    subtitle: "Purchase commitments, approvals, material coverage, receipts, and supplier follow-up.",
    focusTitle: "Purchase order focus",
    quickLinks: [
      { label: "Purchase orders", to: "/procurement" },
      { label: "MRP", to: "/planning" },
      { label: "Inventory", to: "/inventory" },
      { label: "Supplier data", to: "/masters" },
    ],
  },
  stores: {
    label: "Stores workspace",
    subtitle: "The next warehouse tasks, roll availability, reservations, quarantine, and stock movement priorities.",
    focusTitle: "Warehouse task queue",
    quickLinks: [
      { label: "Mobile WMS", to: "/warehouse" },
      { label: "Roll register", to: "/inventory" },
      { label: "Production issue", to: "/production" },
      { label: "Incoming quality", to: "/quality" },
    ],
  },
  cutting_supervisor: {
    label: "Cutting room",
    subtitle: "Cut readiness, fabric issue gaps, pieces cut, and incoming fabric quality exceptions.",
    focusTitle: "Cut order focus",
    quickLinks: [
      { label: "Cut orders", to: "/production" },
      { label: "Marker plans", to: "/production" },
      { label: "Roll register", to: "/inventory" },
      { label: "Fabric quality", to: "/quality" },
    ],
  },
  sewing_supervisor: {
    label: "Sewing floor",
    subtitle: "Line execution, remaining pieces, open shifts, efficiency, downtime, and OEE.",
    focusTitle: "Sewing order focus",
    quickLinks: [
      { label: "Shop floor", to: "/shop-floor" },
      { label: "Sewing orders", to: "/production" },
      { label: "Inline quality", to: "/quality" },
      { label: "Quality trends", to: "/quality" },
    ],
  },
  quality_inspector: {
    label: "Quality station",
    subtitle: "Incoming fabric, inline DHU, final AQL, lab tests, and failed inspections requiring containment.",
    focusTitle: "Latest inspection results",
    quickLinks: [
      { label: "Incoming inspection", to: "/quality" },
      { label: "Inline quality", to: "/quality" },
      { label: "Final AQL", to: "/quality" },
      { label: "Roll quarantine", to: "/inventory" },
    ],
  },
  finance: {
    label: "Finance control",
    subtitle: "Posted profitability, receivables, payables, three-way-match exceptions, and cost variance exposure.",
    focusTitle: "Payables and match focus",
    quickLinks: [
      { label: "Accounts receivable", to: "/finance" },
      { label: "Accounts payable", to: "/finance" },
      { label: "Actual costing", to: "/costing" },
      { label: "Financial reports", to: "/finance" },
    ],
  },
  planner: {
    label: "Planning control",
    subtitle: "MRP urgency, capacity overloads, cut release, sewing load, and material promise risk.",
    focusTitle: "Latest planning runs",
    quickLinks: [
      { label: "MRP", to: "/planning" },
      { label: "Capacity board", to: "/planning" },
      { label: "Production", to: "/production" },
      { label: "ATP check", to: "/planning" },
    ],
  },
  readonly: {
    label: "Executive overview",
    subtitle: "A read-only view of the factory's commercial, production, stock, quality, and financial position.",
    focusTitle: "Current order position",
    quickLinks: [
      { label: "Sales orders", to: "/sales" },
      { label: "Production", to: "/production" },
      { label: "Inventory", to: "/inventory" },
      { label: "Finance", to: "/finance" },
    ],
  },
};

function isDashboardRole(role: string): role is DashboardRole {
  return DASHBOARD_ROLES.has(role as DashboardRole);
}

function asNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const parsed = typeof value === "number" ? value : Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sum<T>(items: T[], value: (item: T) => number): number {
  return items.reduce((total, item) => total + value(item), 0);
}

function isOpen(status: string): boolean {
  return !CLOSED_STATUSES.has(status);
}

function latest<T>(items: T[] | undefined, count = 7): T[] {
  return (items ?? []).slice(0, count);
}

async function loadDashboard(role: DashboardRole): Promise<DashboardData> {
  switch (role) {
    case "merchandiser": {
      const [orders, purchaseOrders, cuts, labTests] = await Promise.all([
        api.get<SalesOrder[]>("/sales-orders?limit=50"),
        api.get<PurchaseOrder[]>("/procurement/purchase-orders?limit=50"),
        api.get<CutOrder[]>("/production/cut-orders"),
        api.get<LabTest[]>("/quality/lab-tests"),
      ]);
      return { orders, purchaseOrders, cuts, labTests };
    }
    case "procurement": {
      const [purchaseOrders, mrpRuns, rolls] = await Promise.all([
        api.get<PurchaseOrder[]>("/procurement/purchase-orders?limit=50"),
        api.get<MrpRun[]>("/planning/mrp-runs?limit=10"),
        api.get<Roll[]>("/inventory/rolls?limit=100"),
      ]);
      return { purchaseOrders, mrpRuns, rolls };
    }
    case "stores": {
      const [rolls, reservations, tasks] = await Promise.all([
        api.get<Roll[]>("/inventory/rolls?limit=100"),
        api.get<Reservation[]>("/inventory/reservations"),
        api.get<WarehouseTask[]>("/wms/tasks?limit=50"),
      ]);
      return { rolls, reservations, tasks };
    }
    case "cutting_supervisor": {
      const [cuts, rolls, incoming] = await Promise.all([
        api.get<CutOrder[]>("/production/cut-orders"),
        api.get<Roll[]>("/inventory/rolls?limit=100"),
        api.get<FourPoint[]>("/quality/four-point-inspections?limit=50"),
      ]);
      return { cuts, rolls, incoming };
    }
    case "sewing_supervisor": {
      const [sewing, shifts, oee] = await Promise.all([
        api.get<SewingOrder[]>("/production/sewing-orders"),
        api.get<ShiftLog[]>("/mes/shift-logs?limit=50"),
        api.get<OeeSummary>("/mes/oee"),
      ]);
      return { sewing, shifts, oee };
    }
    case "quality_inspector": {
      const [incoming, inline, final, labTests] = await Promise.all([
        api.get<FourPoint[]>("/quality/four-point-inspections?limit=50"),
        api.get<Inline[]>("/quality/inline-inspections"),
        api.get<Final[]>("/quality/final-inspections"),
        api.get<LabTest[]>("/quality/lab-tests"),
      ]);
      return { incoming, inline, final, labTests };
    }
    case "finance": {
      const [pnl, ar, ap, variances] = await Promise.all([
        api.get<PnL>("/finance/profit-and-loss"),
        api.get<ARInvoice[]>("/finance/ar-invoices"),
        api.get<APBill[]>("/finance/ap-bills"),
        api.get<CostVariance[]>("/costing/variances?adverse_only=true&limit=100"),
      ]);
      return { pnl, ar, ap, variances };
    }
    case "planner": {
      const [mrpRuns, capacity, cuts, sewing] = await Promise.all([
        api.get<MrpRun[]>("/planning/mrp-runs?limit=10"),
        api.get<CapacityBoard>("/planning/capacity-board"),
        api.get<CutOrder[]>("/production/cut-orders"),
        api.get<SewingOrder[]>("/production/sewing-orders"),
      ]);
      return { mrpRuns, capacity, cuts, sewing };
    }
    case "admin":
    case "readonly": {
      const [orders, cuts, rolls, pnl, ap, final] = await Promise.all([
        api.get<SalesOrder[]>("/sales-orders?limit=50"),
        api.get<CutOrder[]>("/production/cut-orders"),
        api.get<Roll[]>("/inventory/rolls?limit=100"),
        api.get<PnL>("/finance/profit-and-loss"),
        api.get<APBill[]>("/finance/ap-bills"),
        api.get<Final[]>("/quality/final-inspections"),
      ]);
      return { orders, cuts, rolls, pnl, ap, final };
    }
  }
}

function orderRows(orders: SalesOrder[] | undefined): FocusRow[] {
  return latest(orders).map((order) => ({
    label: order.order_number,
    meta: order.customer_po_number ? `Buyer PO ${order.customer_po_number}` : `${num(order.total_quantity)} pieces`,
    status: order.status,
    value: money(order.total_value, order.currency),
    to: `/sales/${order.id}`,
  }));
}

function purchaseOrderRows(orders: PurchaseOrder[] | undefined): FocusRow[] {
  return latest(orders).map((order) => ({
    label: order.order_number,
    meta: `${order.lines.filter((line) => asNumber(line.outstanding_qty) > 0).length} open lines`,
    status: order.status,
    value: money(order.total_value, order.currency),
    to: "/procurement",
  }));
}

function financeBillRows(bills: APBill[]): FocusRow[] {
  const ordered = [...bills].sort((a, b) => {
    const aIsException = a.match_status === "exception";
    const bIsException = b.match_status === "exception";
    if (aIsException !== bIsException) return aIsException ? -1 : 1;
    return b.id - a.id;
  });
  return latest(ordered).map((bill) => ({
    label: bill.bill_number,
    meta: bill.supplier_invoice_number ? `Supplier invoice ${bill.supplier_invoice_number}` : "Goods received / invoice pending",
    status: bill.match_status,
    value: money(bill.outstanding),
    to: "/finance",
  }));
}

function buildDashboardView(role: DashboardRole, data: DashboardData): DashboardView {
  const orders = data.orders ?? [];
  const purchaseOrders = data.purchaseOrders ?? [];
  const rolls = data.rolls ?? [];
  const cuts = data.cuts ?? [];
  const sewing = data.sewing ?? [];
  const incoming = data.incoming ?? [];
  const inline = data.inline ?? [];
  const final = data.final ?? [];
  const labTests = data.labTests ?? [];
  const mrpRuns = data.mrpRuns ?? [];
  const tasks = data.tasks ?? [];
  const shifts = data.shifts ?? [];
  const ar = data.ar ?? [];
  const ap = data.ap ?? [];

  if (role === "merchandiser") {
    const openOrders = orders.filter((order) => isOpen(order.status));
    const unshipped = sum(openOrders, (order) => Math.max(0, order.total_quantity - order.total_shipped_quantity));
    const fabricGaps = cuts.filter((cut) => asNumber(cut.fabric_issued) < asNumber(cut.fabric_required));
    return {
      metrics: [
        { label: "Open order value", value: money(sum(openOrders, (order) => asNumber(order.total_value))), foot: `${openOrders.length} active orders`, accent: "indigo" },
        { label: "Unshipped pieces", value: num(unshipped), foot: "Across current buyer commitments" },
        { label: "Draft orders", value: num(orders.filter((order) => order.status === "draft").length), foot: "Awaiting confirmation" },
        { label: "PO coverage", value: num(purchaseOrders.filter((order) => isOpen(order.status)).length), foot: "Open material purchase orders" },
      ],
      priorities: [
        { label: "Orders to confirm", detail: "Commercial details still in draft", count: orders.filter((order) => order.status === "draft").length, tone: "warn", to: "/sales" },
        { label: "Fabric readiness gaps", detail: "Cut plans not fully issued", count: fabricGaps.length, tone: fabricGaps.length ? "bad" : "ok", to: "/production" },
        { label: "Pending lab tests", detail: "Material approval follow-up", count: labTests.filter((test) => test.status === "pending").length, tone: "warn", to: "/quality" },
        { label: "Orders in execution", detail: "Confirmed and in production", count: orders.filter((order) => ["confirmed", "in_production"].includes(order.status)).length, tone: "info", to: "/sales" },
      ],
      rows: orderRows(orders),
    };
  }

  if (role === "procurement") {
    const openPos = purchaseOrders.filter((order) => isOpen(order.status));
    const latestMrp = mrpRuns[0];
    const outstanding = sum(openPos, (order) => sum(order.lines, (line) => asNumber(line.outstanding_qty)));
    return {
      metrics: [
        { label: "Open PO value", value: money(sum(openPos, (order) => asNumber(order.total_value))), foot: `${openPos.length} open purchase orders`, accent: "indigo" },
        { label: "Outstanding quantity", value: num(outstanding), foot: "Across open PO lines" },
        { label: "Past-due MRP", value: num(latestMrp?.past_due_count ?? 0), foot: "Latest material plan", accent: "madder" },
        { label: "Quarantined rolls", value: num(rolls.filter((roll) => roll.status === "quarantined").length), foot: "Supplier or quality follow-up" },
      ],
      priorities: [
        { label: "Approvals required", detail: "Purchase orders awaiting approval", count: purchaseOrders.filter((order) => order.requires_approval && !order.approved_by).length, tone: "warn", to: "/procurement" },
        { label: "POs awaiting receipt", detail: "Material still outstanding", count: openPos.filter((order) => order.lines.some((line) => asNumber(line.outstanding_qty) > 0)).length, tone: "info", to: "/procurement" },
        { label: "Past-due planned orders", detail: "Release action required", count: latestMrp?.past_due_count ?? 0, tone: latestMrp?.past_due_count ? "bad" : "ok", to: "/planning" },
        { label: "Quarantine follow-up", detail: "Received rolls unavailable to production", count: rolls.filter((roll) => roll.status === "quarantined").length, tone: "warn", to: "/quality" },
      ],
      rows: purchaseOrderRows(purchaseOrders),
    };
  }

  if (role === "stores") {
    const openTasks = tasks.filter((task) => ["open", "assigned"].includes(task.status));
    return {
      metrics: [
        { label: "Open warehouse tasks", value: num(openTasks.length), foot: "Ordered by priority and walk route", accent: "indigo" },
        { label: "Available rolls", value: num(rolls.filter((roll) => roll.status === "available").length), foot: `${rolls.length} rolls in current view` },
        { label: "Active reservations", value: num((data.reservations ?? []).filter((reservation) => reservation.status === "active").length), foot: "Committed to production" },
        { label: "Quarantined rolls", value: num(rolls.filter((roll) => roll.status === "quarantined").length), foot: "Do not issue", accent: "madder" },
      ],
      priorities: [
        { label: "High-priority tasks", detail: "Priority 1–2 warehouse work", count: openTasks.filter((task) => task.priority <= 2).length, tone: "bad", to: "/warehouse" },
        { label: "Put-away queue", detail: "Received stock awaiting location", count: openTasks.filter((task) => task.task_type === "put_away").length, tone: "warn", to: "/warehouse" },
        { label: "Pick queue", detail: "Material required for issue", count: openTasks.filter((task) => task.task_type === "pick").length, tone: "info", to: "/warehouse" },
        { label: "Count tasks", detail: "Cycle counts awaiting completion", count: openTasks.filter((task) => task.task_type === "count").length, tone: "neutral", to: "/warehouse" },
      ],
      rows: latest(openTasks).map((task) => ({
        label: task.task_number,
        meta: `${task.task_type.replace(/_/g, " ")} · ${task.material_code ?? "Material"}`,
        status: task.status,
        value: `${num(task.quantity)} units`,
        to: "/warehouse",
      })),
    };
  }

  if (role === "cutting_supervisor") {
    const fabricGaps = cuts.filter((cut) => asNumber(cut.fabric_issued) < asNumber(cut.fabric_required));
    return {
      metrics: [
        { label: "Planned cuts", value: num(cuts.filter((cut) => cut.status === "planned").length), foot: "Awaiting fabric reservation", accent: "indigo" },
        { label: "Cuts in progress", value: num(cuts.filter((cut) => ["fabric_reserved", "in_cutting"].includes(cut.status)).length), foot: "Released to the floor" },
        { label: "Pieces cut", value: num(sum(cuts, (cut) => cut.pieces_cut)), foot: "Recorded output" },
        { label: "Available rolls", value: num(rolls.filter((roll) => roll.status === "available").length), foot: "Fabric eligible for issue" },
      ],
      priorities: [
        { label: "Fabric issue gaps", detail: "Required metres exceed issued metres", count: fabricGaps.length, tone: fabricGaps.length ? "bad" : "ok", to: "/production" },
        { label: "Cuts to release", detail: "Planned cut orders", count: cuts.filter((cut) => cut.status === "planned").length, tone: "warn", to: "/production" },
        { label: "Rejected fabric", detail: "Incoming inspection failures", count: incoming.filter((inspection) => inspection.result === "failed").length, tone: "bad", to: "/quality" },
        { label: "Ready fabric", detail: "Reserved cuts ready to start", count: cuts.filter((cut) => cut.status === "fabric_reserved").length, tone: "info", to: "/production" },
      ],
      rows: latest([...cuts].reverse()).map((cut) => ({
        label: cut.order_number,
        meta: `${num(cut.fabric_required)} m required · ${num(cut.fabric_issued)} m issued`,
        status: cut.status,
        value: `${num(cut.pieces_cut)} pcs`,
        to: "/production",
      })),
    };
  }

  if (role === "sewing_supervisor") {
    const openSewing = sewing.filter((order) => isOpen(order.status));
    const openShifts = shifts.filter((shift) => shift.status === "open");
    return {
      metrics: [
        { label: "Open sewing orders", value: num(openSewing.length), foot: "Current line workload", accent: "indigo" },
        { label: "Remaining pieces", value: num(sum(openSewing, (order) => Math.max(0, order.planned_qty - order.produced_qty))), foot: "Planned less produced" },
        { label: "30-day OEE", value: pct(data.oee?.oee_pct), foot: `${data.oee?.shifts ?? 0} closed shifts` },
        { label: "Unplanned downtime", value: `${num(data.oee?.unplanned_downtime_minutes)} min`, foot: "Current reporting window", accent: "madder" },
      ],
      priorities: [
        { label: "Open shift logs", detail: "Complete output and downtime capture", count: openShifts.length, tone: "warn", to: "/shop-floor" },
        { label: "Low-efficiency orders", detail: "Average efficiency below 70%", count: openSewing.filter((order) => asNumber(order.average_efficiency_pct) < 70).length, tone: "bad", to: "/production" },
        { label: "Orders not started", detail: "No production recorded", count: openSewing.filter((order) => order.produced_qty === 0).length, tone: "neutral", to: "/production" },
        { label: "Active lines", detail: "Orders with production underway", count: openSewing.filter((order) => order.produced_qty > 0).length, tone: "info", to: "/shop-floor" },
      ],
      rows: latest(sewing).map((order) => ({
        label: order.order_number,
        meta: `${num(order.produced_qty)} / ${num(order.planned_qty)} pieces`,
        status: order.status,
        value: `${asNumber(order.average_efficiency_pct).toFixed(1)}% eff.`,
        to: "/production",
      })),
    };
  }

  if (role === "quality_inspector") {
    const averageDhu = inline.length ? sum(inline, (inspection) => asNumber(inspection.dhu)) / inline.length : 0;
    const failedIncoming = incoming.filter((inspection) => inspection.result === "failed");
    const failedFinal = final.filter((inspection) => inspection.result === "failed");
    return {
      metrics: [
        { label: "Pending lab tests", value: num(labTests.filter((test) => test.status === "pending").length), foot: "Awaiting result", accent: "indigo" },
        { label: "Incoming failures", value: num(failedIncoming.length), foot: "Fabric rolls contained", accent: "madder" },
        { label: "Average inline DHU", value: pct(averageDhu), foot: `${inline.length} recorded inspections` },
        { label: "Final AQL failures", value: num(failedFinal.length), foot: "Shipment gate blocked" },
      ],
      priorities: [
        { label: "Lab tests to complete", detail: "Pending specification results", count: labTests.filter((test) => test.status === "pending").length, tone: "warn", to: "/quality" },
        { label: "Failed final inspections", detail: "Shipment cannot proceed", count: failedFinal.length, tone: failedFinal.length ? "bad" : "ok", to: "/quality" },
        { label: "Rejected incoming fabric", detail: "Quarantine and supplier action", count: failedIncoming.length, tone: "bad", to: "/quality" },
        { label: "High-DHU checks", detail: "Inline DHU above 5%", count: inline.filter((inspection) => asNumber(inspection.dhu) > 5).length, tone: "warn", to: "/quality" },
      ],
      rows: [
        ...latest(final, 4).map((inspection) => ({ label: inspection.inspection_number, meta: `Final AQL · order ${inspection.sales_order_id}`, status: inspection.result, value: `${inspection.defects_found} defects`, to: "/quality" })),
        ...latest(incoming, 3).map((inspection) => ({ label: inspection.inspection_number, meta: `Incoming · roll ${inspection.roll_id}`, status: inspection.result, value: `${inspection.total_points} points`, to: "/quality" })),
      ],
    };
  }

  if (role === "finance") {
    const arOutstanding = sum(ar, (invoice) => asNumber(invoice.outstanding));
    const apOutstanding = sum(ap, (bill) => asNumber(bill.outstanding));
    const matchExceptions = ap.filter((bill) => bill.match_status === "exception");
    return {
      metrics: [
        { label: "Net profit (posted)", value: money(data.pnl?.net_profit), foot: `Revenue ${money(data.pnl?.total_income)}`, accent: "indigo" },
        { label: "AR outstanding", value: money(arOutstanding), foot: `${ar.filter((invoice) => asNumber(invoice.outstanding) > 0).length} open invoices` },
        { label: "AP outstanding", value: money(apOutstanding), foot: `${ap.filter((bill) => asNumber(bill.outstanding) > 0).length} open bills` },
        { label: "Match exceptions", value: num(matchExceptions.length), foot: "Payment blocked pending resolution", accent: "madder" },
      ],
      priorities: [
        { label: "Three-way-match exceptions", detail: "Invoice, receipt, or PO variance", count: matchExceptions.length, tone: matchExceptions.length ? "bad" : "ok", to: "/finance" },
        { label: "Invoices awaiting match", detail: "Supplier invoices still pending", count: ap.filter((bill) => bill.match_status === "pending").length, tone: "warn", to: "/finance" },
        { label: "Open receivables", detail: "Customer balances to collect", count: ar.filter((invoice) => asNumber(invoice.outstanding) > 0).length, tone: "info", to: "/finance" },
        { label: "Adverse variances", detail: "Actual cost above standard", count: data.variances?.length ?? 0, tone: "warn", to: "/costing" },
      ],
      rows: financeBillRows(ap),
    };
  }

  if (role === "planner") {
    const latestMrp = mrpRuns[0];
    const overloaded = data.capacity?.centres.reduce((total, centre) => total + centre.overloaded_buckets, 0) ?? 0;
    const openSewing = sewing.filter((order) => isOpen(order.status));
    return {
      metrics: [
        { label: "Past-due MRP", value: num(latestMrp?.past_due_count ?? 0), foot: "Latest material plan", accent: "madder" },
        { label: "Overloaded buckets", value: num(overloaded), foot: "Across active work centres", accent: "indigo" },
        { label: "Cuts awaiting release", value: num(cuts.filter((cut) => cut.status === "planned").length), foot: "Fabric reservation required" },
        { label: "Remaining sewing qty", value: num(sum(openSewing, (order) => Math.max(0, order.planned_qty - order.produced_qty))), foot: "Current production load" },
      ],
      priorities: [
        { label: "Past-due planned orders", detail: "Material release date has passed", count: latestMrp?.past_due_count ?? 0, tone: latestMrp?.past_due_count ? "bad" : "ok", to: "/planning" },
        { label: "Capacity overloads", detail: "Weekly buckets above available minutes", count: overloaded, tone: overloaded ? "bad" : "ok", to: "/planning" },
        { label: "Cuts to reserve", detail: "Planned cut orders awaiting fabric", count: cuts.filter((cut) => cut.status === "planned").length, tone: "warn", to: "/production" },
        { label: "Sewing orders not started", detail: "No production output recorded", count: openSewing.filter((order) => order.produced_qty === 0).length, tone: "neutral", to: "/production" },
      ],
      rows: latest(mrpRuns).map((run) => ({
        label: run.run_number,
        meta: `${run.material_count} materials · ${run.planned_order_count} planned orders`,
        status: run.status,
        value: `${run.past_due_count} past due`,
        to: "/planning",
      })),
    };
  }

  const openOrders = orders.filter((order) => isOpen(order.status));
  const matchExceptions = ap.filter((bill) => bill.match_status === "exception");
  const fabricGaps = cuts.filter((cut) => asNumber(cut.fabric_issued) < asNumber(cut.fabric_required));
  const failedFinal = final.filter((inspection) => inspection.result === "failed");
  return {
    metrics: [
      { label: "Order backlog", value: money(sum(openOrders, (order) => asNumber(order.total_value))), foot: `${openOrders.length} active orders`, accent: "indigo" },
      { label: "Net profit (posted)", value: money(data.pnl?.net_profit), foot: `Revenue ${money(data.pnl?.total_income)}` },
      { label: "Available rolls", value: num(rolls.filter((roll) => roll.status === "available").length), foot: `${rolls.length} rolls in current view` },
      { label: "Critical exceptions", value: num(matchExceptions.length + failedFinal.length + fabricGaps.length), foot: "Finance, quality, and production", accent: "madder" },
    ],
    priorities: [
      { label: "Open customer orders", detail: "Commercial commitments in progress", count: openOrders.length, tone: "info", to: "/sales" },
      { label: "Fabric readiness gaps", detail: "Cut requirements not fully issued", count: fabricGaps.length, tone: fabricGaps.length ? "bad" : "ok", to: "/production" },
      { label: "Failed final inspections", detail: "Shipment gate blocked", count: failedFinal.length, tone: failedFinal.length ? "bad" : "ok", to: "/quality" },
      { label: "Match exceptions", detail: "Supplier payment blocked", count: matchExceptions.length, tone: matchExceptions.length ? "bad" : "ok", to: "/finance" },
    ],
    rows: orderRows(orders),
  };
}

function firstName(displayName: string): string {
  return displayName.trim().split(/\s+/)[0] || displayName;
}

export default function Dashboard({ user }: { user: AuthUser }) {
  const availableRoles = useMemo(() => user.roles.filter(isDashboardRole), [user.roles]);
  const initialRole = availableRoles.includes("admin") ? "admin" : availableRoles[0] ?? "readonly";
  const [role, setRole] = useState<DashboardRole>(initialRole);
  const dashboard = useAsync<DashboardLoadResult>(async () => ({ role, data: await loadDashboard(role) }), [role]);
  const profile = PROFILES[role];
  const roleData = dashboard.data?.role === role ? dashboard.data.data : null;
  const view = useMemo(() => roleData ? buildDashboardView(role, roleData) : null, [roleData, role]);

  return (
    <div className="stagger role-dashboard">
      <PageHeader
        eyebrow={profile.label}
        title={`Welcome, ${firstName(user.display_name)}`}
        subtitle={profile.subtitle}
        actions={availableRoles.length > 1 ? (
          <label className="dashboard-role-select">
            <span>Dashboard view</span>
            <select className="select" value={role} onChange={(event) => setRole(event.target.value as DashboardRole)}>
              {availableRoles.map((availableRole) => (
                <option key={availableRole} value={availableRole}>{PROFILES[availableRole].label}</option>
              ))}
            </select>
          </label>
        ) : <span className="dashboard-role-badge">{profile.label}</span>}
      />

      <nav className="dashboard-quicklinks" aria-label={`${profile.label} shortcuts`}>
        {profile.quickLinks.map((link) => <Link key={`${link.to}-${link.label}`} to={link.to}>{link.label}<span aria-hidden="true">→</span></Link>)}
      </nav>

      {dashboard.error && <ErrorBox message={`Your dashboard could not be loaded: ${dashboard.error}`} />}
      {dashboard.loading && <Card><Spinner /></Card>}

      {view && !dashboard.loading && (
        <>
          <div className="section-heading">
            <div><span>01</span> Your priorities</div>
            <p>Role-specific exceptions and next actions</p>
          </div>
          <div className="dashboard-priority-grid">
            {view.priorities.map((priority) => (
              <Link className={`dashboard-priority ${priority.tone}`} to={priority.to} key={priority.label}>
                <div className="dashboard-priority-count">{num(priority.count)}</div>
                <div className="dashboard-priority-copy">
                  <strong>{priority.label}</strong>
                  <span>{priority.count === 0 ? "No exceptions · all clear" : priority.detail}</span>
                </div>
                <span className="dashboard-priority-open" aria-hidden="true">Open</span>
              </Link>
            ))}
          </div>

          <div className="section-heading compact">
            <div><span>02</span> At a glance</div>
            <p>Metrics selected for {profile.label.toLowerCase()}</p>
          </div>
          <div className="grid cols-4 metric-grid primary-metrics">
            {view.metrics.map((metric) => <Stat key={metric.label} k={metric.label} v={metric.value} foot={metric.foot} accent={metric.accent} />)}
          </div>

          <div className="section-heading">
            <div><span>03</span> {profile.focusTitle}</div>
            <p>Most recent records first</p>
          </div>
          <Card title={profile.focusTitle} hint={`${view.rows.length} records`}>
            <div className="table-wrap dashboard-focus-table-wrap">
              <table className="tbl dashboard-focus-table">
                <thead><tr><th>Reference</th><th>Context</th><th>Status</th><th className="num">Position</th></tr></thead>
                <tbody>
                  {view.rows.map((row, index) => (
                    <tr key={`${row.label}-${index}`}>
                      <td className="code">{row.to ? <Link to={row.to}>{row.label}</Link> : row.label}</td>
                      <td>{row.meta}</td>
                      <td><Chip status={row.status} /></td>
                      <td className="num">{row.value}</td>
                    </tr>
                  ))}
                  {view.rows.length === 0 && <tr><td colSpan={4} className="muted">Nothing needs attention in this view.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="dashboard-focus-mobile">
              {view.rows.map((row) => (
                <article className="dashboard-focus-item" key={`${row.label}-${row.meta}`}>
                  <div className="dashboard-focus-reference">
                    {row.to ? <Link to={row.to}>{row.label}</Link> : <strong>{row.label}</strong>}
                    <span>{row.meta}</span>
                  </div>
                  <Chip status={row.status} />
                  <div className="dashboard-focus-position">{row.value}</div>
                </article>
              ))}
              {view.rows.length === 0 && <div className="dashboard-focus-empty">Nothing needs attention in this view.</div>}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
