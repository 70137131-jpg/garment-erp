/**
 * Route chunk prefetching.
 *
 * Route-level code splitting keeps first paint small, but it costs a round
 * trip on every first visit to a page: the click downloads the chunk, and only
 * once it has parsed does the page mount and start fetching its data. Two
 * serial waits before anything appears.
 *
 * Starting the chunk download on hover or keyboard focus overlaps that first
 * wait with the time a person spends moving the pointer and deciding to click
 * — typically a few hundred milliseconds, which is most of the chunk fetch on
 * a normal connection. By the time the click lands the module is usually
 * already parsed, leaving only the data fetch.
 *
 * The importers below must stay identical to the `lazy()` calls in App.tsx so
 * both resolve to the same chunk; a different specifier would download a
 * second copy instead of reusing the first.
 */

type Importer = () => Promise<unknown>;

const ROUTE_CHUNKS: Record<string, Importer> = {
  "/": () => import("../pages/Dashboard"),
  "/sales": () => import("../pages/Sales"),
  "/styles": () => import("../pages/Styles"),
  "/costing": () => import("../pages/Costing"),
  "/planning": () => import("../pages/Planning"),
  "/procurement": () => import("../pages/Procurement"),
  "/inventory": () => import("../pages/Inventory"),
  "/production": () => import("../pages/Production"),
  "/markers": () => import("../pages/Markers"),
  "/shop-floor": () => import("../pages/ShopFloor"),
  "/warehouse": () => import("../pages/Warehouse"),
  "/quality": () => import("../pages/Quality"),
  "/masters": () => import("../pages/Masters"),
  "/finance": () => import("../pages/Finance"),
  "/users": () => import("../pages/Users"),
  "/settings": () => import("../pages/Settings"),
};

const started = new Set<string>();

/** Begin downloading a route's chunk. Safe to call repeatedly. */
export function prefetchRoute(path: string) {
  if (started.has(path)) return;
  const importer = ROUTE_CHUNKS[path];
  if (!importer) return;
  started.add(path);
  // Failures are deliberately swallowed: a prefetch that does not arrive must
  // never surface an error, because the real navigation will retry it anyway.
  importer().catch(() => started.delete(path));
}
