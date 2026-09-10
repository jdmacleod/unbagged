import { useEffect, useRef, useState } from "react";
import type { Ref } from "react";
import { api } from "../api";
import type { UploadResult } from "../types";
import { ErrorBox, Spine } from "./ui";

/**
 * Shown while a parse is in flight.
 *
 * Reading a real 116-page report is around 14 seconds of PDF text extraction.
 * The previous version showed one unchanging line of text for that whole time,
 * which reads as a hang: people start doubting it at about five seconds, and the
 * natural next move is to drop the file again. Something moving says the process
 * is alive, and naming an expected range means the wait is boring rather than
 * alarming.
 *
 * The one place in the app with motion besides the basket unfurl, and it earns
 * it: a progress signal that does not move is not a progress signal. It is
 * suppressed under prefers-reduced-motion by the global rule in index.css, and
 * the elapsed seconds keep counting either way, so nothing becomes invisible.
 */
function Working() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const tick = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, []);

  return (
    <span className="flex flex-col items-center gap-2">
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1 w-1 animate-bounce rounded-full bg-line"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </span>
      <span className="font-medium">Reading the response…</span>
      <span className="num text-[11.5px] text-muted">
        {seconds < 8
          ? "A long report takes 10 to 30 seconds."
          : `Still working — ${seconds}s. Long reports are slow to read; nothing is stuck.`}
      </span>
    </span>
  );
}

/**
 * Adding a response.
 *
 * `prominent` is the first run, when this is the entire screen and deserves to
 * be. Once a response is loaded it becomes a quiet line at the foot of the
 * document instead of a white panel restating itself on all five views.
 */
export function Upload({
  onDone,
  onBusy,
  prominent,
  result = null,
  resultRef,
}: {
  /** Called with the parse report, and with `null` whenever there is no
   *  longer one to show — a new upload starting, or one that failed. The null
   *  arm is what stops a previous success from sitting under "Reading the
   *  response…" for the length of a parse, or beside the error from a refused
   *  re-drop, reading as though the second drop partly worked. */
  onDone: (result: UploadResult | null) => void;
  /** Called whenever a parse starts or stops.
   *
   *  The caller needs it for two things it cannot see from `onDone`: a failed
   *  list read must not replace a RUNNING upload with a red "this is not
   *  recoverable" box, and this component must not be unmounted mid-POST —
   *  doing so takes `inFlight` with it, and a remounted uploader would happily
   *  send a second concurrent request. `onDone(null)` cannot stand in: it fires
   *  when an upload starts AND when one fails, and those need opposite answers. */
  onBusy?: (busy: boolean) => void;
  prominent?: boolean;
  /** What the last upload returned, owned by the caller.
   *
   *  **Not local state, and that is the whole point.** The first-run uploader
   *  and the footer one are different positions in the tree — one inside
   *  `<main>`, one after it — so React cannot carry state between them. A first
   *  upload is exactly what swaps one for the other, which destroyed the panel
   *  reporting on it in the same instant it was created: the retailer match,
   *  the low-confidence caveat and every parse warning, gone before anyone
   *  could read them. Held by `App`, which does not unmount. */
  result?: UploadResult | null;
  /** Handle on the report panel, so the caller can move focus and scroll to it
   *  when an upload lands.
   *
   *  The footer uploader means the reader is at the BOTTOM of a long document
   *  when they drop a file, and since #58 the upload swaps `<main>` for a
   *  different response — one that can be a very different length. The document
   *  height changes by a large factor, the browser clamps `scrollY` somewhere
   *  arbitrary, and the report they waited 10 to 30 seconds for can end up off
   *  screen. Owned by the caller because the caller is what knows an upload
   *  just landed. Issue #62. */
  resultRef?: Ref<HTMLDivElement>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // The in-flight flag AGAIN, as a ref, and the ref is the one that decides.
  //
  // `busy` is render state, so every entry point below tested it out of a
  // render closure. Two events dispatched before React re-renders — a fast
  // double-click, or a drop landing on top of a click — both read the stale
  // `false` and both passed. Two POSTs went out; the loser's `finally` unlocked
  // the drop zone while the winner was still in flight, and whichever `onDone`
  // resolved last won the result panel. The server dedupes on content hash so
  // nothing was corrupted, but the reader got an error that reads like a bug
  // and a report that may describe the wrong upload.
  //
  // A ref is written synchronously, so the second event sees the first one's
  // write. `busy` stays for what it is actually good at: `aria-busy`,
  // `disabled`, the cursor and the spinner. Ref decides, state renders.
  // Issue #48.
  const inFlight = useRef(false);

  async function send(files: File[]) {
    if (!files.length) return;
    if (inFlight.current) {
      // Say so. The `click()` path tests `busy`, which is still false for the
      // render between a drop and its re-render, so the file dialog opens, the
      // reader picks a file, and a silent `return` here swallows it: no error,
      // no spinner change, nothing at all. A refusal the reader can see beats a
      // drop zone that appears to ignore them.
      setError("Still reading the last file. Wait for it to finish, then try again.");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    onBusy?.(true);
    setError(null);
    // The previous report describes the previous upload. Clear it before this
    // one starts rather than after it lands: a parse runs 10 to 30 seconds, and
    // for all of it the old report would sit directly beneath the spinner.
    onDone(null);
    try {
      const uploaded = await api.upload(files);
      onDone(uploaded);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
      onBusy?.(false);
    }
  }

  return (
    <Spine margin={prominent ? undefined : <span />}>
      {prominent && (
        <div className="mt-8 mb-5">
          {/* The one place the mark appears in the app. DESIGN.md bans
              illustration on any surface carrying report data and names two
              exceptions — the tab and this screen — in its 2026-09-05
              decisions row. That row was written after this shipped, because
              review caught the comment asserting a permission the design
              system had never granted. It greets at the door and is gone the
              moment data loads; identity persists in the tab from there. Decorative, so it is hidden from
              assistive technology — the heading below says the same thing in
              words.

              Width and height are set so the row does not reflow when the image
              arrives, and it is `/`-rooted from frontend/public/ rather than
              imported, which keeps it out of the JS bundle. */}
          <img
            src="/unbagged-logo.svg"
            alt=""
            aria-hidden="true"
            width="56"
            height="56"
            className="mb-3 block"
          />
          {/* `mt-8` because the heading had none: it sat 0px under the page's
              intro paragraph and 4px above its own, so it read as the tail of
              the header rather than as the start of this section. A heading
              belongs to what follows it. */}
          <h2 className="font-serif text-[17px] font-semibold">
            Start with a retailer&rsquo;s response
          </h2>
          {/* Said "The PDF or zip". The app reads PDF and text and nothing
              else — `extraction.py` answers a zip with "unzip an archive
              first" — so the first screen was inviting the one action that
              cannot work. */}
          <p className="mt-1 max-w-[62ch] text-muted">
            The PDF, text file or spreadsheet export a retailer sent back when
            you filed a right-to-know request. If it arrived as a zip, unzip it
            first and drop what was inside. It is read here, on this machine,
            and nothing is uploaded anywhere.
          </p>
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          // Locked while a parse is in flight. A long report takes tens of
          // seconds, which is long enough that people assume it has hung and
          // drop the file again; a second upload of the same bytes is refused
          // by the server anyway, but the error reads like a bug.
          if (!busy) void send(Array.from(e.dataTransfer.files));
        }}
        onClick={() => {
          if (!busy) input.current?.click();
        }}
        aria-busy={busy}
        className={`rounded-[2px] border border-dashed text-center transition-colors ${
          prominent ? "px-6 py-12" : "px-4 py-4"
        } ${busy ? "cursor-wait" : "cursor-pointer"} ${
          dragging ? "border-accent bg-sunken" : "border-line hover:bg-sunken"
        }`}
      >
        <input
          ref={input}
          type="file"
          multiple
          // Mirrors extraction.py's TEXT_SUFFIXES plus PDF. Only filters the
          // picker — a drop still accepts anything and the server still
          // explains what it could not read — but it stops the file dialog
          // offering the zip the reader was told not to use.
          accept=".pdf,.txt,.text,.json,.csv,.md,.xls,application/pdf,text/plain"
          disabled={busy}
          className="hidden"
          onChange={(e) => void send(Array.from(e.target.files ?? []))}
        />
        {busy ? (
          <Working />
        ) : prominent ? (
          <>
            <span className="font-medium">Drop it here</span>
            <span className="mt-1 block text-muted">
              PDF, text, or a spreadsheet export — the XML kind, not an Excel
              workbook. Or click to choose a file.
            </span>
            {/* What happens next, and roughly how long. A long report is tens
                of seconds of text extraction, and a reader with no estimate
                assumes it has hung. */}
            <span className="mt-1 block text-[11.5px] text-faint">
              A long report takes 10 to 30 seconds to read.
            </span>
          </>
        ) : (
          <span className="text-muted">
            Add another response — drop a file here, or click to choose one
          </span>
        )}
      </div>

      {error && (
        // `role="alert"` because a refusal is an outcome too. The success path
        // announces through the caller's polite live region; without this the
        // failure path stayed silent, and the reader who most needs telling —
        // one who cannot see the box appear — got nothing back from a drop that
        // did not work. Assertive by role, which is right here: this is the
        // answer to something they just did. Issue #62.
        <div role="alert" className="mt-3">
          <ErrorBox error={error} />
        </div>
      )}

      {result && (
        <div
          ref={resultRef}
          // Focusable by script but not in the tab order: the caller moves
          // focus here when an upload lands so a screen reader lands ON the
          // report rather than being told nothing happened, and so a sighted
          // reader's next Tab continues from the report instead of from
          // wherever scroll clamping left them. Issue #62.
          tabIndex={-1}
          // A real focus ring, not `outline-none`. This element genuinely
          // receives focus, and a keyboard reader who cannot see where they
          // landed is worse off than one who was never moved. `offset-1` because
          // that is what all six other focus rings in the app use.
          className="mt-4 border-t border-rule pt-3 focus:outline-2 focus:outline-offset-1 focus:outline-accent"
        >
          <p>
            Read as <strong>{result.display_name}</strong>
            {/* A word, not a coloured pill. Confidence is a fact about the
                match, not a severity, and colour here means provenance. */}
            <span className="num ml-2 text-[11.5px] text-faint">
              {result.confident ? "match" : "uncertain match"}{" "}
              {Math.round(result.confidence * 100)}%
            </span>
          </p>
          {!result.confident && (
            <p className="mt-1 max-w-[62ch] text-muted">
              Low confidence. Check this is the retailer you meant before
              reading anything into it.
            </p>
          )}
          <p className="num mt-1 text-[11.5px] text-muted">
            {result.summary.transactions.toLocaleString()} visits ·{" "}
            {result.summary.items.toLocaleString()} line items ·{" "}
            {result.summary.identities} identifiers ·{" "}
            {result.summary.inferences} inferred attributes
          </p>
          {result.warnings.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.warnings.map((w, i) => (
                <li key={i} className="max-w-[62ch] text-muted">
                  {w.message}
                  {w.locator && (
                    <span className="num ml-1 text-[11.5px] text-faint">
                      {w.locator}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Spine>
  );
}
