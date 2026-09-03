import type { ReactNode } from "react";
import type { Provenance } from "../types";

export function Card({
  title,
  children,
  className = "",
  actions,
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  return (
    <section
      className={`rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900 ${className}`}
    >
      {(title || actions) && (
        <header className="mb-3 flex items-baseline justify-between gap-3">
          {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-stone-200 bg-white px-4 py-3 dark:border-stone-800 dark:bg-stone-900">
      <div className="text-xs text-stone-500 dark:text-stone-400">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">{hint}</div>}
    </div>
  );
}

/**
 * Where a value came from. Every displayed number has one of these, because the
 * whole point of the tool is that you can check it against the document.
 */
export function ProvenanceTag({ provenance }: { provenance?: Provenance | null }) {
  if (!provenance?.locator) return null;
  const page = provenance.page ? `p.${provenance.page}` : "";
  return (
    <span
      className="cursor-help font-mono text-[11px] text-stone-400 dark:text-stone-500"
      title={`Source document ${provenance.source_document_id ?? "?"}${
        page ? `, page ${provenance.page}` : ""
      }\n${provenance.locator}`}
    >
      {page || provenance.locator}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-stone-300 px-4 py-8 text-center text-sm text-stone-500 dark:border-stone-700 dark:text-stone-400">
      {children}
    </p>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <p className="px-4 py-8 text-center text-sm text-stone-500 dark:text-stone-400">
      {label}…
    </p>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return (
    <p className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
      {error}
    </p>
  );
}

export function Pill({
  tone = "neutral",
  children,
  title,
}: {
  tone?: "neutral" | "warn" | "bad" | "good";
  children: ReactNode;
  title?: string;
}) {
  const tones = {
    neutral: "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
    good: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
    warn: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
    bad: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  };
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
