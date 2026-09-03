import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { UploadResult } from "../types";
import { Card, ErrorBox, Pill } from "./ui";

/**
 * Shown while a parse is in flight.
 *
 * Reading a real 116-page report is around 14 seconds of PDF text extraction.
 * The previous version showed one unchanging line of text for that whole time,
 * which reads as a hang: people start doubting it at about five seconds, and the
 * natural next move is to drop the file again. An animation says the process is
 * alive, and naming an expected range means the wait is boring rather than
 * alarming.
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
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-stone-500"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </span>
      <span className="font-medium">Reading the response…</span>
      <span className="text-stone-500 dark:text-stone-400">
        {seconds < 8
          ? "A long report takes 10 to 30 seconds."
          : `Still working — ${seconds}s. Long reports are slow to read; nothing is stuck.`}
      </span>
    </span>
  );
}

export function Upload({ onDone }: { onDone: (result: UploadResult) => void }) {
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
    <Card title="Add a response">
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
        className={`rounded-lg border-2 border-dashed px-6 py-10 text-center text-sm transition ${
          busy ? "cursor-wait opacity-90" : "cursor-pointer"
        } ${
          dragging
            ? "border-stone-500 bg-stone-100 dark:bg-stone-800"
            : "border-stone-300 dark:border-stone-700"
        }`}
      >
        <input
          ref={input}
          type="file"
          multiple
          disabled={busy}
          className="hidden"
          onChange={(e) => void send(Array.from(e.target.files ?? []))}
        />
        {busy ? (
          <Working />
        ) : (
          <>
            <span className="font-medium">Drop the retailer&rsquo;s response here</span>
            <span className="mt-1 block text-stone-500 dark:text-stone-400">
              PDF or text. It stays on this machine — nothing is uploaded anywhere.
            </span>
          </>
        )}
      </div>

      {error && (
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      )}

      {result && (
        <div className="mt-3 space-y-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <span>
              Read as <strong>{result.display_name}</strong>
            </span>
            {result.confident ? (
              <Pill tone="good">match {Math.round(result.confidence * 100)}%</Pill>
            ) : (
              <Pill tone="warn" title="Low confidence — check this is the right retailer.">
                uncertain match {Math.round(result.confidence * 100)}%
              </Pill>
            )}
          </p>
          <p className="text-stone-600 dark:text-stone-400">
            {result.summary.transactions.toLocaleString()} visits,{" "}
            {result.summary.items.toLocaleString()} line items,{" "}
            {result.summary.identities} identifiers, {result.summary.inferences}{" "}
            inferred attributes.
          </p>
          {result.warnings.length > 0 && (
            <ul className="space-y-1">
              {result.warnings.map((w, i) => (
                <li key={i} className="text-amber-800 dark:text-amber-300">
                  {w.message}
                  {w.locator && (
                    <span className="ml-1 font-mono text-xs opacity-70">{w.locator}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
