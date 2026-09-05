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
  /**
   * Render at least far enough to include `index`.
   *
   * A jump control that targets a row the list has not rendered yet scrolls to
   * nothing: the anchor is not in the DOM, the browser stays put, and the
   * control looks broken rather than slow. The timeline's month rail can point
   * at any of two years of months while 25 of 127 rows are on the page, so it
   * has to be able to say "reveal through here" before it scrolls.
   *
   * Reveals a further `step` beyond the target, and that cushion is the point
   * rather than slack. Revealing exactly through the target makes it the last
   * row on the page, and a browser cannot scroll the last row to the top of the
   * viewport because there is nothing beneath it to scroll into. The jump then
   * lands short, and the running head names the month *above* the one you asked
   * for — which is how this was found: clicking Jan 25 scrolled to a page whose
   * head read Oct 24.
   *
   * Never shrinks the list. Revealing rows and then taking them away underneath
   * a reader who is looking at them would be worse than not jumping at all.
   */
  const revealThrough = (index: number) =>
    setLimit((n) => (index < n ? n : index + 1 + step));
  const control =
    remaining > 0 ? (
      <div className="py-3 text-center">
        <button
          onClick={() => setLimit((n) => n + step * 4)}
          className="rounded-[2px] border border-line px-3 py-1.5 hover:bg-sunken"
        >
          {/* "Show 97 more (97 left)" said 97 twice. The count of what you get
              is the useful half; the remainder only differs once you are partway
              through, and then it is worth showing. */}
          Show {Math.min(remaining, step * 4).toLocaleString()} more
          {remaining > step * 4 ? ` (${remaining.toLocaleString()} left)` : ""}
        </button>
      </div>
    ) : null;
  return { visible, control, revealThrough, showingAll: remaining === 0 };
}
