import { useEffect, useRef, useState } from "react";
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
  prominent,
}: {
  onDone: (result: UploadResult) => void;
  prominent?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function send(files: File[]) {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      const uploaded = await api.upload(files);
      setResult(uploaded);
      onDone(uploaded);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Spine margin={prominent ? undefined : <span />}>
      {prominent && (
        <div className="mt-8 mb-5">
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
            The PDF or text file a retailer sent back when you filed a
            right-to-know request. If it arrived as a zip, unzip it first and
            drop what was inside. It is read here, on this machine, and nothing
            is uploaded anywhere.
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
          accept=".pdf,.txt,.text,.json,.csv,.md,application/pdf,text/plain"
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
              PDF or text. Or click to choose a file.
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
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      )}

      {result && (
        <div className="mt-4 border-t border-rule pt-3">
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
              Low confidence. Check this is the retailer you meant before reading
              anything into it.
            </p>
          )}
          <p className="num mt-1 text-[11.5px] text-muted">
            {result.summary.transactions.toLocaleString()} visits ·{" "}
            {result.summary.items.toLocaleString()} line items ·{" "}
            {result.summary.identities} identifiers · {result.summary.inferences}{" "}
            inferred attributes
          </p>
          {result.warnings.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.warnings.map((w, i) => (
                <li key={i} className="max-w-[62ch] text-muted">
                  {w.message}
                  {w.locator && (
                    <span className="num ml-1 text-[11.5px] text-faint">{w.locator}</span>
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
