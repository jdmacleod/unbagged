import type { ReactNode } from "react";
import type { Provenance } from "../types";

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-[2px] border border-dashed border-rule px-4 py-8 text-center text-muted">
      {children}
    </p>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <p className="px-4 py-8 text-center text-muted">
      {label}…
    </p>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return (
    <p className="rounded-[2px] border border-danger/40 px-4 py-3 text-danger">
      {error}
    </p>
  );
}

/**
 * The spine: content holds a reading measure, surplus width becomes a margin
 * with a job. See DESIGN.md — footnotes belong in the margin, not crammed onto
 * the end of a row. Below `lg` the margin collapses and its content is expected
 * to appear inline instead.
 */
export function Spine({
  margin,
  marginFirst,
  children,
}: {
  margin?: ReactNode;
  /** Put the margin ahead of the content in the DOM, and therefore ahead of it
   *  in the tab order, while it still renders on the right.
   *
   *  Only worth setting when the margin holds something focusable. It holds a
   *  jump rail on the product index, and rendering that after the content put
   *  it 400 tab stops past the entries it exists to skip — the navigation
   *  behind the thing being navigated. Explicit column placement does the job
   *  without a positive `tabindex`, which would be a worse cure. */
  marginFirst?: boolean;
  children: ReactNode;
}) {
  const content = (
    <div className={`min-w-0 ${marginFirst ? "lg:col-start-1 lg:row-start-1" : ""}`}>
      {children}
    </div>
  );
  const aside = (
    <div className={`hidden lg:block ${marginFirst ? "lg:col-start-2 lg:row-start-1" : ""}`}>
      {margin}
    </div>
  );
  return (
    <div className="grid gap-x-12 gap-y-3 lg:grid-cols-[minmax(0,var(--measure-read))_var(--spacing-margin)]">
      {marginFirst ? (
        <>
          {aside}
          {content}
        </>
      ) : (
        <>
          {content}
          {aside}
        </>
      )}
    </div>
  );
}

/** Marginalia. Small, mono, quiet. */
export function Aside({ children }: { children: ReactNode }) {
  return <div className="num pt-2 text-[11.5px] text-faint">{children}</div>;
}

/** A page reference, set as a citation rather than a badge. */
export function Cite({ provenance }: { provenance?: Provenance | null }) {
  if (!provenance?.page) return null;
  return (
    <span
      className="num shrink-0 text-[11.5px] text-faint"
      title={provenance.locator ?? undefined}
    >
      p.{provenance.page}
    </span>
  );
}
