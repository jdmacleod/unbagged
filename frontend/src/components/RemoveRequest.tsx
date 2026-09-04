import { useState } from "react";
import { api, ApiError } from "../api";
import { day } from "../format";
import type { RequestMeta } from "../types";

/**
 * Remove one imported response.
 *
 * The endpoint and its client wrapper existed from the start and nothing ever
 * called them, so a response could be added and never taken back. Upload the
 * wrong file, or a test file, and it stayed in the retailer selector for good;
 * the only documented escape was `make reset CONFIRM=yes`, which moves the
 * entire data directory aside — a whole-archive operation offered as the remedy
 * for one bad row.
 *
 * **This is the one call site for red in the whole app.** See DESIGN.md: colour
 * means provenance, interaction, quantity or identity, never severity — with a
 * single exception reserved for exactly this confirmation. Until now that
 * exception described a control that did not exist, so the design system's one
 * documented use of red was fiction.
 *
 * Two-step on purpose, and the second step names the retailer rather than
 * saying "are you sure". There is no undo: the rows are gone when the cascade
 * runs. What is *not* gone is the file you uploaded, which stays in `data/`, so
 * the honest framing is that this removes a reading rather than destroying the
 * evidence — and saying so is what makes the button safe to press.
 */
export function RemoveRequest({
  request,
  onRemoved,
}: {
  request: RequestMeta;
  onRemoved: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteRequest(request.id);
      setConfirming(false);
      onRemoved();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not remove it. Nothing was changed.",
      );
    } finally {
      setBusy(false);
    }
  }

  const covered =
    request.period_start && request.period_end
      ? `${day(request.period_start)} → ${day(request.period_end)}`
      : null;

  if (!confirming) {
    return (
      <button
        onClick={() => {
          setError(null);
          setConfirming(true);
        }}
        className="text-faint underline decoration-dotted underline-offset-2 hover:text-ink"
      >
        Remove this response
      </button>
    );
  }

  return (
    // A rule and whitespace, not a modal and not a card. The page does not need
    // to be blocked: the destructive control is one of two buttons and the
    // other one is Keep.
    <div className="border-t border-rule pt-3">
      <p className="max-w-[62ch] text-muted">
        Remove <strong className="text-ink">{request.display_name}</strong>
        {covered && (
          <>
            , covering <span className="num text-[12.5px]">{covered}</span>
          </>
        )}
        ? Its baskets, line items, identifiers, inferred attributes and
        disclosure findings are deleted from the database, and there is no undo.
      </p>
      <p className="mt-1 max-w-[62ch] text-[11.5px] text-faint">
        The file you uploaded stays on disk in <code className="num">data/</code>.
        You can read it again by dropping it back in.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          onClick={() => void remove()}
          disabled={busy}
          className="rounded-[2px] border border-danger px-3 py-1.5 text-danger hover:bg-danger hover:text-page disabled:opacity-60"
        >
          {busy ? "Removing…" : `Remove ${request.display_name}`}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={busy}
          className="rounded-[2px] border border-line px-3 py-1.5 hover:bg-sunken"
        >
          Keep it
        </button>
        {/* Not red. The confirm button beside it is already the one red thing
            in this block, and two reds in six inches is how red stopped meaning
            anything the first time. */}
        {error && <span className="text-muted">{error}</span>}
      </div>
    </div>
  );
}
