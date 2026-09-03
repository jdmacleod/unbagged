import { useState } from "react";

/**
 * Renders a slice of a long list with a control to extend it.
 *
 * Two years of shopping is a hundred-plus visits and a couple of hundred
 * products. Rendering all of them at once produces a page nobody can navigate,
 * and it buries the summary statistics that are the point of the view.
 */
export function useShowMore<T>(items: T[], step = 25) {
  const [limit, setLimit] = useState(step);
  const visible = items.slice(0, limit);
  const remaining = items.length - visible.length;
  const control =
    remaining > 0 ? (
      <div className="pt-3 text-center">
        <button
          onClick={() => setLimit((n) => n + step * 4)}
          className="rounded border border-stone-300 px-3 py-1 text-xs hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          {/* "Show 97 more (97 left)" said 97 twice. The count of what you get
              is the useful half; the remainder only differs once you are partway
              through, and then it is worth showing. */}
          Show {Math.min(remaining, step * 4).toLocaleString()} more
          {remaining > step * 4 ? ` (${remaining.toLocaleString()} left)` : ""}
        </button>
      </div>
    ) : null;
  return { visible, control, showingAll: remaining === 0 };
}
