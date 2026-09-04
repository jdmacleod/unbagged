import { api } from "../api";
import { useAsync } from "../components/useAsync";
import type { RequestMeta } from "../types";

/**
 * Says so when a stored report was read by an older version of its adapter.
 *
 * `adapters/kroger/adapter.py` claims that "a report parsed by version 1 carries
 * `adapter_schema_version = 1` in the database, which is how a reader can tell
 * that what they are looking at predates the correction." Nothing rendered it,
 * so a reader could not tell. The number was stored, typed and passed to the
 * frontend, and then dropped on the floor.
 *
 * It matters for exactly one reason so far, and it is not a small one. Kroger
 * schema 1 read `loyalty_amt` as a discount to subtract rather than as the price
 * the line cost, so a line bought at its ordinary price came out free. A report
 * still carrying version 1 displays a paid total and a saving that are both
 * wrong, and every figure derived from them is wrong with it.
 *
 * The raw rows were never mutated, so the fix is a re-read rather than a
 * recovery: upload the same file again.
 *
 * Nothing is shown when the versions agree, which is the normal case.
 */
export function StaleReading({ request }: { request: RequestMeta }) {
  const adapters = useAsync(() => api.adapters(), []);

  const current = adapters.data?.adapters.find(
    (a) => a.retailer_id === request.retailer_id,
  )?.schema_version;
  const stored = request.adapter_schema_version;

  // Silent unless we can actually make the comparison. An unknown adapter, or a
  // report stored before the column existed, is not evidence of a stale read.
  if (current === undefined || stored === null || stored >= current) return null;

  return (
    <p className="mt-4 max-w-[62ch] border-l-2 border-dotted border-line pl-3 text-muted">
      This response was read by version <span className="num">{stored}</span> of
      the {request.display_name} adapter; the current version is{" "}
      <span className="num">{current}</span>. What you paid and what you saved
      are computed from that older reading and are wrong for this report.{" "}
      <strong className="font-medium text-ink">
        Upload the same file again to re-read it.
      </strong>{" "}
      Nothing was lost: the amounts the retailer disclosed are stored exactly as
      they arrived, so a re-read recovers the correct figures.
    </p>
  );
}
