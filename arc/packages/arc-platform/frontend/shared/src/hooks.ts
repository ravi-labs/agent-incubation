/**
 * Lightweight data-fetching hook used by both dashboards.
 *
 * Deliberately minimal — no caching, no retries, no global state. We
 * can swap in TanStack Query later without touching page code if every
 * page consumes ``useFetch(() => api.X())``.
 */

import { useEffect, useState } from "react";

export interface FetchState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** Re-run the fetcher (useful after mutations land). */
  refetch: () => void;
}

export function useFetch<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcher()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return {
    data,
    error,
    loading,
    refetch: () => setTick((t) => t + 1),
  };
}


/**
 * Polls a fetcher on a fixed interval. Same shape as `useFetch` plus
 * a `paused` toggle the caller can flip to stop polling without
 * unmounting (e.g. when the user opens a modal and we don't want
 * the table to reflow under them).
 *
 * Why polling, not SSE: see docs/guides/live-console.md. Most arc
 * use cases (a few reviewers watching an agent for an hour) have
 * better UX with polling — works through any proxy / Lambda / etc.
 * and has trivial test ergonomics.
 */
export interface PollState<T> extends FetchState<T> {
  /** True while the polling loop is active. */
  isPolling: boolean;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  options: { paused?: boolean; deps?: unknown[] } = {},
): PollState<T> {
  const { paused = false, deps = [] } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (paused) return;

    let cancelled = false;
    const run = async () => {
      try {
        const result = await fetcher();
        if (!cancelled) {
          setData(result);
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err as Error);
          setLoading(false);
        }
      }
    };

    // Fire once immediately, then poll.
    run();
    const id = setInterval(run, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, paused, tick, ...deps]);

  return {
    data,
    error,
    loading,
    isPolling: !paused,
    refetch: () => setTick((t) => t + 1),
  };
}
