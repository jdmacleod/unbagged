import { useCallback, useEffect, useState } from "react";

type State<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** How many times a read has SUCCEEDED.
   *
   *  A caller that needs to know "has the list been re-read since I asked for
   *  it" cannot get that from `loading`: `reload()` only bumps a nonce, and the
   *  effect that flips `loading` to true runs after the commit, so the render
   *  immediately following a `reload()` call still reads `loading: false`. A
   *  counter is checkable at any point in that sequence. See App.tsx's
   *  `settling`.
   *
   *  Successes only, and that is the whole point of the field. The question
   *  every caller is actually asking is "do I have EVIDENCE yet" — and a read
   *  that failed is not evidence of anything. Counting failures here would let
   *  a dropped connection stand in for an answer. */
  reads: number;
};

/**
 * Minimal data fetching. No client library: this app makes a handful of GETs
 * against its own origin, and a cache layer would be more code than the code it
 * manages.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): State<T> & {
  /** True while a reload is in flight over data we already hold. Distinct from
   *  `loading`, which is also true for the first read, when there is nothing on
   *  screen yet and a spinner is the right answer. A reload has something on
   *  screen, so the right answer is to leave it there. */
  reloading: boolean;
  reload: () => void;
} {
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    loading: true,
    reads: 0,
  });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => {
        if (!cancelled)
          setState((s) => ({
            data,
            error: null,
            loading: false,
            reads: s.reads + 1,
          }));
      })
      .catch((error: Error) => {
        // `data: s.data`, not `data: null`. A failed re-read is not evidence
        // that the thing is gone — it is evidence that we could not look. This
        // used to null the data, so any failed `GET /api/requests` (a backend
        // restart, a 500, a dropped connection during the reload that follows
        // an upload) emptied `rows`, deleted the parse report the reader had
        // just waited 10 to 30 seconds for, and returned them to the first-run
        // screen — for a response committed in the database and perfectly fine.
        //
        // Keeping it means "there are no responses" and "the list could not be
        // read" stop rendering identically, which is the distinction this whole
        // product is built around, applied to its own request list. The caller
        // gets both facts and decides; see App.tsx's handling of `error` with
        // and without rows. Issue #63.
        if (!cancelled)
          setState((s) => ({
            data: s.data,
            error: error.message,
            loading: false,
            // NOT incremented. See the field's doc: this counts evidence, and a
            // failed read produced none.
            reads: s.reads,
          }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reloading: state.loading && state.data !== null, reload };
}
