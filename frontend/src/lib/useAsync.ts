import { useEffect, useState } from "react";

/**
 * A tiny read-only data hook: run an async loader once (re-running when a dep
 * key changes) and expose { data, error, loading }. Deliberately minimal — the
 * platform's config pages each load one endpoint and render it, so a full data
 * layer would be overkill. Errors surface honestly rather than being swallowed.
 */
export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useAsync<T>(loader: () => Promise<T>, depKey: string): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, loading: true });
    loader()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setState({
            data: null,
            error: err instanceof Error ? err.message : "Failed to load",
            loading: false,
          });
      });
    return () => {
      cancelled = true;
    };
    // The loader closure is recreated each render; depKey is the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey]);

  return state;
}
