import { useCallback, useEffect, useState } from "react";

type State<T> = { data: T | null; error: string | null; loading: boolean };

/**
 * Minimal data fetching. No client library: this app makes a handful of GETs
 * against its own origin, and a cache layer would be more code than the code it
 * manages.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): State<T> & {
  reload: () => void;
} {
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ data: null, error: error.message, loading: false });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload };
}
