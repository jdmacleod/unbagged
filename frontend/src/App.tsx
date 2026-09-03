import { useState } from "react";
import { api } from "./api";
import { useAsync } from "./components/useAsync";
import { Upload } from "./components/Upload";
import { ErrorBox, Spinner } from "./components/ui";
import { Compare } from "./views/Compare";
import { Compliance } from "./views/Compliance";
import { PriceHistory } from "./views/PriceHistory";
import { Profile } from "./views/Profile";
import { Timeline } from "./views/Timeline";

const TABS = [
  { id: "timeline", label: "Timeline", hint: "What they have" },
  { id: "profile", label: "Profile", hint: "What they infer" },
  { id: "compliance", label: "Compliance", hint: "What they didn't tell you" },
  { id: "compare", label: "Compare", hint: "Across retailers" },
  { id: "prices", label: "Prices", hint: "What things cost you" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("timeline");
  const [selected, setSelected] = useState<number | null>(null);
  const requests = useAsync(() => api.requests(), []);

  const rows = requests.data?.requests ?? [];
  const current = selected ?? rows[0]?.id ?? null;

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">unbagged</h1>
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
          Read what the grocery store knows about you. Everything here stays on this
          machine.
        </p>
      </header>

      {rows.length > 0 && (
        <nav className="mb-5 flex flex-wrap items-center gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              title={t.hint}
              className={`rounded-full px-3 py-1.5 text-sm transition ${
                tab === t.id
                  ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900"
                  : "bg-stone-200 text-stone-700 hover:bg-stone-300 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
              }`}
            >
              {t.label}
            </button>
          ))}
          {rows.length > 1 && (
            <select
              // The only control on the page with no label. It also read as a
              // nav item on mobile, sitting inline with the tab pills.
              aria-label="Which retailer's response to show"
              title="Which retailer's response to show"
              className="ml-auto rounded-md border border-stone-300 bg-white px-2 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-900"
              value={current ?? ""}
              onChange={(e) => setSelected(Number(e.target.value))}
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

      <div className="space-y-6">
        {requests.loading && <Spinner label="Looking for stored responses" />}
        {requests.error && <ErrorBox error={requests.error} />}

        {/* One box, not two. The empty state used to stack a dashed drop zone on
            top of a dashed "nothing loaded yet, drop in a response" panel, which
            restated the box directly above it in the same visual language. Two
            dashed rectangles competing, one of them redundant. The drop zone is
            already the empty state. */}
        {!requests.loading && rows.length === 0 && (
          <Upload onDone={() => requests.reload()} />
        )}

        {current !== null && (
          <>
            {tab === "timeline" && <Timeline requestId={current} />}
            {tab === "profile" && <Profile requestId={current} />}
            {tab === "compliance" && <Compliance />}
            {tab === "compare" && <Compare />}
            {tab === "prices" && <PriceHistory requestId={current} />}
          </>
        )}

        {rows.length > 0 && <Upload onDone={() => requests.reload()} />}
      </div>

      <footer className="mt-10 border-t border-stone-200 pt-4 text-xs text-stone-500 dark:border-stone-800 dark:text-stone-400">
        unbagged reports what a response contained and what it did not. It is not
        legal advice, and it never sends anything anywhere.
      </footer>
    </div>
  );
}
