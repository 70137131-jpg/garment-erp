export function money(v: string | number | null | undefined, currency = "USD"): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(n);
}

export function qty(v: string | number | null | undefined, unit = ""): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "—";
  const s = n.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return unit ? `${s} ${unit}` : s;
}

export function num(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  return Number.isNaN(n) ? "—" : n.toLocaleString("en-US");
}

export function pct(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  return Number.isNaN(n) ? "—" : `${n.toFixed(2)}%`;
}

export function titled(s: string | null | undefined): string {
  if (!s) return "—";
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export type Tone = "neutral" | "info" | "ok" | "warn" | "bad";

const STATUS_TONE: Record<string, Tone> = {
  // generic lifecycle
  draft: "neutral",
  planned: "neutral",
  open: "neutral",
  pending: "neutral",
  pending_inspection: "warn",
  active: "info",
  confirmed: "info",
  issued: "info",
  in_production: "info",
  in_cutting: "info",
  fabric_reserved: "info",
  partially_received: "warn",
  part_paid: "warn",
  quarantined: "warn",
  approved: "ok",
  available: "ok",
  received: "ok",
  completed: "ok",
  passed: "ok",
  shipped: "ok",
  paid: "ok",
  posted: "ok",
  closed: "neutral",
  reserved: "info",
  superseded: "neutral",
  consumed: "neutral",
  released: "neutral",
  cancelled: "bad",
  reversed: "bad",
  failed: "bad",
  rejected: "bad",
};

export function statusTone(status: string | null | undefined): Tone {
  if (!status) return "neutral";
  return STATUS_TONE[status] ?? "neutral";
}
