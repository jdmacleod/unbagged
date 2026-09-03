import { useRef, useState } from "react";
import { api } from "../api";
import type { UploadResult } from "../types";
import { Card, ErrorBox, Pill } from "./ui";

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
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void send(Array.from(e.dataTransfer.files));
        }}
        onClick={() => input.current?.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-10 text-center text-sm transition ${
          dragging
            ? "border-stone-500 bg-stone-100 dark:bg-stone-800"
            : "border-stone-300 dark:border-stone-700"
        }`}
      >
        <input
          ref={input}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => void send(Array.from(e.target.files ?? []))}
        />
        {busy ? (
          <span>Reading the response…</span>
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
