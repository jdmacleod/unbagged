import { useEffect, useState } from "react";
import { api } from "./api";
import { useAsync } from "./components/useAsync";
import { RemoveRequest } from "./components/RemoveRequest";
import { StaleReading } from "./components/StaleReading";
import { Upload } from "./components/Upload";
import { ErrorBox, Spine, Spinner } from "./components/ui";
import { Compare } from "./views/Compare";
import { Compliance } from "./views/Compliance";
import { PriceHistory } from "./views/PriceHistory";
import { ProductIndex } from "./views/ProductIndex";
import { Profile } from "./views/Profile";
import { Timeline } from "./views/Timeline";

/** The running version, in the footer.
 *
 *  There is no telemetry, no crash reporting and no update check here by
 *  design, which leaves a person filing an issue with no way to say what they
 *  are running. One string in the footer is the whole fix.
 *
 *  It comes from the API rather than being baked into the bundle at build time,
 *  so in dev it reports the backend actually answering rather than whatever was
 *  compiled into the page. Renders nothing until it arrives, and nothing at all
 *  if the call fails: a footer is not the place to raise an error, and an
 *  unknown version is better left unsaid than guessed at.
 */
function Version() {
  const health = useAsync(() => api.health(), []);
  if (!health.data?.version) return null;
  return (
    <span
      className="num sm:ml-auto"
      title="The version of unbagged serving this page"
    >
      v{health.data.version}
    </span>
  );
}

const TABS = [
  { id: "timeline", label: "Timeline", hint: "What they have" },
  { id: "profile", label: "Profile", hint: "What they infer" },
  { id: "compliance", label: "Compliance", hint: "What they didn't tell you" },
  { id: "compare", label: "Compare", hint: "Across retailers" },
  { id: "prices", label: "Prices", hint: "What things cost you" },
  { id: "products", label: "Products", hint: "Everything you bought" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const TAB_IDS = TABS.map((t) => t.id) as readonly string[];

/** Which view the URL is asking for. Falls back to the default on anything
 *  unrecognised, so a hand-edited or stale link still lands somewhere sane. */
function readUrl(): {
  tab: TabId;
  request: number | null;
  query: string | null;
  label: string | null;
} {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  const request = Number(params.get("r"));
  return {
    tab: (TAB_IDS.includes(tab ?? "") ? tab : "timeline") as TabId,
    request: Number.isFinite(request) && request > 0 ? request : null,
    // Timeline's product filter. It lives in the URL because the Products index
    // links into it: a click has to survive a reload, a bookmark and the back
    // button, and a click handler mutating local state does none of that.
    //
    // `q` is a UPC when the Products index sent it, because the timeline's
    // search matches substrings and product names contain each other. `label`
    // carries the human name so the arrival sentence can say "BANANAS EA"
    // rather than a fourteen-digit code, and is display-only.
    query: params.get("q") || null,
    label: params.get("label") || null,
  };
}

export default function App() {
  // View state lives in the URL. Without it the tabs were pure React state, so
  // the browser back button walked out of the app entirely rather than to the
  // previous view, a reload always dumped you back on Timeline, and there was
  // no way to bookmark or send someone a link to the compliance matrix.
  const [{ tab, request: selected, query, label }, setView] = useState(readUrl);

  function href(
    next: Partial<{
      tab: TabId;
      request: number | null;
      query: string | null;
      label: string | null;
    }>,
  ) {
    const merged = { tab, request: selected, query, label, ...next };
    const params = new URLSearchParams();
    params.set("tab", merged.tab);
    if (merged.request !== null) params.set("r", String(merged.request));
    // The product filter belongs to Timeline. Carrying it onto another tab
    // would leave it in the URL, silently filtering a view the reader never
    // pointed it at.
    if (merged.query && merged.tab === "timeline") {
      params.set("q", merged.query);
      if (merged.label) params.set("label", merged.label);
    }
    return { merged, search: `?${params}` };
  }

  function go(
    next: Partial<{
      tab: TabId;
      request: number | null;
      query: string | null;
      label: string | null;
    }>,
  ) {
    const { merged, search } = href(next);
    window.history.pushState(merged, "", search);
    setView(merged);
  }

  useEffect(() => {
    // Back and forward restore a view instead of leaving the app.
    const onPop = () => setView(readUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const requests = useAsync(() => api.requests(), []);

  const rows = requests.data?.requests ?? [];
  // A bookmarked ?r= can outlive the response it names — `make reset` is the
  // obvious way. Falling back to whatever is loaded beats showing "No request
  // with id 999" beside a retailer selector confidently displaying a different
  // one. Putting view state in the URL is what made stale links possible, so
  // handling them is part of the same change.
  const known = rows.some((r) => r.id === selected);
  const current = (known ? selected : null) ?? rows[0]?.id ?? null;

  return (
    // A page on a desk. The sheet is sized to hold the reading measure plus the
    // margin the marginalia lives in, and nothing wider: the app does not grow
    // to fill a 27-inch display, because a document does not. See DESIGN.md.
    <div className="mx-auto min-h-screen max-w-[64rem] bg-page px-6 py-10 sm:px-12">
      <header>
        <h1 className="font-serif text-[26px] leading-none font-semibold">unbagged</h1>
        <p className="mt-2 max-w-[62ch] text-muted">
          Read what the grocery store knows about you. Everything here stays on this
          machine.
        </p>
      </header>

      {rows.length > 0 && (
        <nav className="mt-8 mb-9 flex flex-wrap items-baseline gap-x-6 gap-y-2 border-b border-rule">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => go({ tab: t.id, query: null, label: null })}
              aria-current={tab === t.id ? "page" : undefined}
              // A rule under the current view rather than a filled pill. The
              // pills read as buttons in a toolbar; this reads as a running
              // head, which is what it is.
              className={`-mb-px border-b-2 pb-2 text-left transition-colors ${
                tab === t.id
                  ? "border-ink font-medium"
                  : "border-transparent text-muted hover:text-ink"
              }`}
            >
              <span className="block">{t.label}</span>
              {/* The hint was a `title`, which meant the five sentences that
                  explain what this product does were reachable only by hovering
                  and did not exist at all on touch. They also carry the only
                  thing distinguishing Products from Prices, which are adjacent
                  and are both lists of things you bought. */}
              <span className="block text-[10.5px] leading-tight text-faint">
                {t.hint}
              </span>
            </button>
          ))}
          {rows.length > 1 && (
            <select
              // The only control on the page with no visible label. It also read
              // as a nav item on mobile, sitting inline with the tabs.
              aria-label="Which retailer's response to show"
              title="Which retailer's response to show"
              className="mb-2 ml-auto self-end rounded-[2px] border border-line bg-transparent px-2 py-1 text-ink focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent"
              value={current ?? ""}
              onChange={(e) => go({ request: Number(e.target.value) })}
            >
              {rows.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.display_name}
                  {r.period_start ? ` · ${r.period_start.slice(0, 10)}` : ""}
                </option>
              ))}
            </select>
          )}
        </nav>
      )}

      {/* A caveat about the reading, not about the retailer, so it sits above
          the views rather than inside any one of them. */}
      {current !== null && rows.find((r) => r.id === current) && (
        <StaleReading request={rows.find((r) => r.id === current)!} />
      )}

      <main>
        {requests.loading && (
          <Spine>
            <Spinner label="Looking for stored responses" />
          </Spine>
        )}
        {requests.error && (
          <Spine>
            <ErrorBox error={requests.error} />
          </Spine>
        )}

        {/* One box, not two. The empty state used to stack a dashed drop zone on
            top of a dashed "nothing loaded yet" panel, which restated the box
            directly above it in the same visual language. The drop zone is
            already the empty state, and on a first run it is the whole screen,
            so it is the one place in the app that gets to be large. */}
        {!requests.loading && rows.length === 0 && (
          <Upload prominent onDone={() => requests.reload()} />
        )}

        {current !== null && (
          <>
            {tab === "timeline" && (
              <Timeline
                // Back and forward change the URL's product filter while the
                // view holds its own search state. Keying on it remounts rather
                // than leaving the two disagreeing.
                key={query ?? ""}
                requestId={current}
                arrival={query ? { query, label: label ?? query } : null}
                onClearArrival={() => go({ query: null, label: null })}
              />
            )}
            {tab === "profile" && <Profile requestId={current} />}
            {tab === "compliance" && <Compliance />}
            {tab === "compare" && <Compare />}
            {tab === "prices" && <PriceHistory requestId={current} />}
            {tab === "products" && (
              <ProductIndex
                requestId={current}
                onOpenProduct={(entry) =>
                  href({
                    tab: "timeline",
                    query: entry.upc,
                    label: entry.description,
                  }).search
                }
              />
            )}
          </>
        )}
      </main>

      {/* Adding another response is a footnote once you have one, not a panel
          competing with the document above it on every single view. */}
      {rows.length > 0 && (
        <div className="mt-14">
          <Upload onDone={() => requests.reload()} />
          {/* Removing one is a smaller footnote still, and it lives here rather
              than beside the retailer selector: the selector is used constantly
              and a destructive control does not belong under a hand that is
              only trying to switch views. */}
          {current !== null && (
            <div className="mt-4">
              <RemoveRequest
                key={current}
                request={rows.find((r) => r.id === current)!}
                onRemoved={() => {
                  // Drop the filter and the selection with it: `?r=` would
                  // otherwise name a response that no longer exists, and `?q=`
                  // would keep filtering a timeline that just changed under it.
                  go({ request: null, query: null, label: null });
                  requests.reload();
                }}
              />
            </div>
          )}
        </div>
      )}

      <footer className="mt-10 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-rule pt-4 text-[11.5px] text-faint">
        <span className="max-w-[62ch]">
          unbagged reports what a response contained and what it did not. It is not
          legal advice, and it never sends anything anywhere.
        </span>
        <Version />
      </footer>
    </div>
  );
}
