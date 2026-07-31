import { useCallback, useEffect, useRef, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  /** True only when there is nothing to show yet — drives the spinner. */
  loading: boolean;
  /** True whenever a request is in flight, including background revalidation. */
  fetching: boolean;
  error: string | null;
  reload: () => void;
}

/** Cached payloads, keyed by the caller's `key`. Survives unmount/remount. */
const cache = new Map<string, unknown>();
/** In-flight requests, so N components asking for the same key share one call. */
const inflight = new Map<string, Promise<unknown>>();

/** Drop cached entries after a mutation. Prefix match, so `/styles` clears
 *  `/styles?x=1` too. */
export function invalidate(prefix: string) {
  for (const key of [...cache.keys()]) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}

/**
 * Data fetching with stale-while-revalidate.
 *
 * Two behaviours matter for how the app *feels*:
 *
 * 1. **Existing data stays on screen while refetching.** Previously any change
 *    of dependency reset state to `loading`, so changing a filter blanked the
 *    table and replaced it with a spinner. Keeping the stale rows and marking
 *    them as refreshing is the difference between "responsive" and "flickery".
 *
 * 2. **A `key` opts into a shared cache.** Reference data like the style list
 *    is requested by several pages; without a cache each navigation refetches
 *    it and each mount shows a spinner. With one, a revisit paints instantly
 *    from cache and revalidates in the background. Concurrent callers for the
 *    same key share a single in-flight request rather than racing.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  key?: string
): AsyncState<T> {
  const cached = (key !== undefined ? (cache.get(key) as T | undefined) : undefined) ?? null;
  const [data, setData] = useState<T | null>(cached);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  // Held in a ref so `run` does not need `fn` in its dependency list — callers
  // pass a fresh closure on every render, which would otherwise loop forever.
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(() => {
    let alive = true;
    setFetching(true);
    setError(null);

    // Repoint at whatever this key currently holds, so switching filters shows
    // that filter's cached rows rather than the previous one's.
    if (key !== undefined && cache.has(key)) {
      setData(cache.get(key) as T);
    }

    let request: Promise<unknown>;
    if (key !== undefined && inflight.has(key)) {
      request = inflight.get(key)!;
    } else {
      request = fnRef.current();
      if (key !== undefined) {
        inflight.set(key, request);
        request.finally(() => inflight.delete(key));
      }
    }

    request
      .then((d) => {
        if (key !== undefined) cache.set(key, d);
        if (alive) setData(d as T);
      })
      .catch((e: unknown) => {
        if (alive) setError((e as Error).message || "Request failed");
      })
      .finally(() => {
        if (alive) setFetching(false);
      });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, key]);

  useEffect(run, [run]);

  return {
    data,
    // Only a true cold start blocks the UI; a refresh over existing data does not.
    loading: fetching && data === null,
    fetching,
    error,
    reload: () => {
      if (key !== undefined) cache.delete(key);
      setTick((t) => t + 1);
    },
  };
}
