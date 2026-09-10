import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useAsync } from "./components/useAsync";
import { RemoveRequest } from "./components/RemoveRequest";
import { StaleReading } from "./components/StaleReading";
import { Upload } from "./components/Upload";
import type { UploadResult } from "./types";
import { Caveat, ErrorBox, Spine, Spinner } from "./components/ui";
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

/** Everything the URL carries. */
export type View = {
  tab: TabId;
  request: number | null;
  query: string | null;
  label: string | null;
};

/** Which view the URL is asking for. Falls back to the default on anything
 *  unrecognised, so a hand-edited or stale link still lands somewhere sane. */
function readUrl(): View {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  const request = Number(params.get("r"));
  return {
    tab: (TAB_IDS.includes(tab ?? "") ? tab : "timeline") as TabId,
    request: Number.isInteger(request) && request > 0 ? request : null,
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

/**
 * Which response the app should show.
 *
 * `selected` is what the URL asked for, `ids` is the list we currently hold,
 * and `settling` is a response an upload just created that the list has not
 * been re-read since.
 *
 * That third argument is the whole point. `useAsync.reload` retains `data`
 * while a reload is in flight, so after an upload sets `selected` to the new id
 * there is at least one render against the OLD rows. Without `settling` the new
 * id is unrecognised, the fallback fires, and `rows[0]` — the OLDEST response,
 * since `repository.list_requests` is `ORDER BY id` — is what renders: a flash
 * of the wrong retailer, a `<select>` naming it, and a full round of view
 * fetches against the wrong request id. Under load that window is visible,
 * because `GET /api/requests` competes for the same threadpool as the
 * synchronous upload endpoint. It also transiently reproduced the exact bug
 * #58 fixed. Issue #60.
 *
 * The row is committed — the POST returned 201 before any of this ran — so
 * trusting it is correct, not optimistic.
 */
export function resolveCurrent(
  ids: readonly number[],
  selected: number | null,
  settling: number | null,
): number | null {
  if (selected !== null) {
    if (ids.includes(selected)) return selected;
    // With no list to fall back to there is no wrong row to pick, so the
    // first-run path is left exactly as it was: nothing selected until the
    // list actually arrives. Trusting `settling` here would put an empty
    // `<main>` above the first-run uploader for the length of the reload.
    if (selected === settling && ids.length > 0) return selected;
  }
  return ids[0] ?? null;
}

/**
 * Would this navigation land on what is already on screen?
 *
 * Compared against what is SHOWN, not against what the URL literally says,
 * because the two differ: `/` with no `?r=` resolves through the `rows[0]`
 * fallback to a specific response. Callers resolve both sides first. A history
 * entry that renders identically to the one before it is a Back press that
 * appears to do nothing, which teaches people the button is broken. Issue #61.
 */
export function isSameEntry(a: View, b: View): boolean {
  return (
    a.tab === b.tab &&
    a.request === b.request &&
    a.query === b.query &&
    a.label === b.label
  );
}

/**
 * What to say out loud when an upload lands.
 *
 * The panel copy, in a sentence. Nothing in the app moved focus or announced
 * anything when `<main>` was replaced under the reader, so for a screen reader
 * user the upload they started produced silence — no signal that anything had
 * happened at all. Issue #62.
 */
export function announceUpload(result: UploadResult): string {
  const s = result.summary;
  const parts = [
    `Read as ${result.display_name}${result.confident ? "" : ", uncertain match"}.`,
    `${s.transactions.toLocaleString()} visits, ${s.items.toLocaleString()} line items, ` +
      `${s.identities} identifiers, ${s.inferences} inferred attributes.`,
  ];
  if (result.warnings.length > 0)
    parts.push(
      `${result.warnings.length} warning${result.warnings.length === 1 ? "" : "s"}.`,
    );
  return parts.join(" ");
}

/** How long the live region holds a message before it is cleared. See the
 *  effect that uses it for why it is this generous. */
const ANNOUNCEMENT_MS = 4000;

export default function App() {
  // View state lives in the URL. Without it the tabs were pure React state, so
  // the browser back button walked out of the app entirely rather than to the
  // previous view, a reload always dumped you back on Timeline, and there was
  // no way to bookmark or send someone a link to the compliance matrix.
  const [{ tab, request: selected, query, label }, setView] = useState(readUrl);
  // Owned here rather than inside Upload because the first upload swaps the
  // prominent uploader for the footer one, and a component that unmounts takes
  // its state with it. That state is the only report on the parse: which
  // retailer matched, how confident, and every warning — including "this file
  // was read but holds no spreadsheet, so nothing from it is in this report",
  // which is how a bundle holding two retailers tells you it kept one.
  const [lastUpload, setLastUpload] = useState<UploadResult | null>(null);
  // Counts uploads that LANDED. The effect that moves focus keys on this rather
  // than on the request id, because ids are not distinct over time: `request.id`
  // is a rowid alias with no `AUTOINCREMENT`, so SQLite hands the same one back
  // after a delete (issue #64). Two uploads in a row that happened to reuse an
  // id would have announced once.
  const [uploadSeq, setUploadSeq] = useState(0);
  // What the live region is currently saying.
  const [announcement, setAnnouncement] = useState("");
  // The response an upload just created, held until the request list has been
  // read again — `asOf` records the read count at the moment the upload landed,
  // so "again" is checkable rather than guessed at. See `resolveCurrent`.
  const [settling, setSettling] = useState<{ id: number; asOf: number } | null>(
    null,
  );
  // The report panel, so an upload that lands can put the reader on it.
  const reportRef = useRef<HTMLDivElement>(null);
  // True while a parse is in flight, hoisted out of `Upload` because two things
  // OUTSIDE the uploader have to know: a failed list read must not replace a
  // running upload with a red "this is not recoverable" box (see
  // `listUnreadable`), and `Upload` must not be unmounted mid-POST, which would
  // take its in-flight guard with it and let a remounted uploader send a second
  // concurrent request — the exact thing issue #48 exists to stop.
  const [uploading, setUploading] = useState(false);
  // The last announcement actually delivered, so the arrival effect cannot
  // re-fire for one it already made. `arrival` is derived from `liveUpload`,
  // which is not monotonic: it can go null and come back. See the effect.
  const announcedSeq = useRef(0);

  const requests = useAsync(() => api.requests(), []);
  const rows = requests.data?.requests ?? [];

  // `requests.reads`, readable from a handler created several renders ago.
  //
  // `uploadFinished` is the closure `Upload.send` captured at DROP time and
  // calls after `await api.upload(files)` — 10 to 30 seconds later. Reading
  // `requests.reads` through that closure gets the count as of the DROP, which
  // is exactly the staleness `viewRef` exists to fix, one screen down this same
  // file. It mattered: any list read that settled during the parse made
  // `reads > asOf` true the instant `settling` was set, so the clearing effect
  // fired on the same commit and `settling` never did its job — the issue #60
  // flash, reopened, with the upload also never announced and never focused
  // because `liveUpload` went null with it. The tab-visibility refetch below is
  // what makes a mid-parse read routine, so #65's fix is what armed it.
  const readsRef = useRef(requests.reads);
  useEffect(() => {
    readsRef.current = requests.reads;
  });

  useEffect(() => {
    // Cleared as soon as the list has been read SUCCESSFULLY again. `reads`
    // counts successes only, and that is what makes this correct rather than
    // merely eventual: the question is "do we have evidence about this row
    // yet", and a failed read is not evidence. Clearing on any settle meant a
    // dropped connection during the post-upload reload handed the question to
    // `rows` — which does not contain the row, because the read that would
    // have added it is the one that just failed — and the reader lost the
    // report, the selection and the announcement for a response the server had
    // already committed. Once the read succeeds, `rows` is the better answer
    // and this steps out of the way.
    if (settling !== null && requests.reads > settling.asOf) setSettling(null);
  }, [settling, requests.reads]);

  // A bookmarked ?r= can outlive the response it names — `make reset` is the
  // obvious way. Falling back to whatever is loaded beats showing "No request
  // with id 999" beside a retailer selector confidently displaying a different
  // one. Putting view state in the URL is what made stale links possible, so
  // handling them is part of the same change.
  const current = resolveCurrent(
    rows.map((r) => r.id),
    selected,
    settling?.id ?? null,
  );
  const currentRow = rows.find((r) => r.id === current) ?? null;

  useEffect(() => {
    // Absence, once established, is permanent.
    //
    // A SUCCESSFUL read that does not contain the row is positive evidence the
    // response is gone, and the report has to die with it. Without this the
    // `requests.error` arm below resurrects it: read succeeds and correctly
    // hides the report, a later read FAILS, and the report comes back — the app
    // volunteering "Read as Kroger. 127 visits" out loud, and re-stealing focus,
    // about a response it watched disappear one read earlier. In an app whose
    // whole thesis is never making a claim about data it does not have, that is
    // the worst state this file could be in.
    //
    // Keyed on `reads` rather than on `rows`, which is a fresh array every
    // render; this has to run once per settled read, not once per render.
    if (!lastUpload || requests.error !== null || settling !== null) return;
    if (requests.loading) return;
    const present = rows.some(
      (r) =>
        r.id === lastUpload.request_id &&
        r.retailer_id === lastUpload.retailer_id,
    );
    if (!present) setLastUpload(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastUpload, requests.reads, requests.error, requests.loading, settling]);

  // The upload report, but only while the response it describes still exists.
  //
  // Holding it on the page is what keeps it alive across the swap from the
  // first-run uploader to the footer one, and that is the point — but a report
  // is a claim about a stored response, so it has to die with one.
  //
  // The retailer is compared as well as the id, and that is not belt-and-braces.
  // `request.id` is `INTEGER PRIMARY KEY` with no `AUTOINCREMENT`, so it is a
  // rowid alias and SQLite REUSES the highest id once its row is deleted. Delete
  // the newest response in another tab, upload a different one, and it can be
  // handed the same id — an id-only check would then pass and pin the old
  // report to the new response, naming the wrong retailer with confident counts.
  //
  // Two arms beyond that, and both are narrower than they look:
  //
  //  * `settling` — the row is committed and the list has not been re-read yet.
  //    Without this the report the reader waited 10 to 30 seconds for is hidden
  //    for the length of the reload and then pops in (issue #60).
  // There is no third arm for "the read failed", and there was: it said a
  // failure is not evidence of absence, which is true, but it ignored `rows`
  // entirely and so also overrode evidence we already had. A read that
  // succeeded and did not contain the row is positive proof the response is
  // gone; the old arm resurrected the report anyway on the next failure,
  // announcing "Read as Kroger. 127 visits" out loud about something the app
  // had watched disappear. With `settling` now holding until a read SUCCEEDS,
  // the failure case is already covered by the first arm and the third one was
  // only ever a way to be wrong.
  //
  // Residual, small enough to name rather than chase: a reused id whose new
  // response is from the SAME retailer still matches, so the counts could be a
  // response out of date. Closing that needs something per-response in both
  // `UploadResult` and the request list, and today they share only these two.
  const liveUpload =
    lastUpload &&
    (settling?.id === lastUpload.request_id ||
      rows.some(
        (r) =>
          r.id === lastUpload.request_id &&
          r.retailer_id === lastUpload.retailer_id,
      ))
      ? lastUpload
      : null;

  // Three states that used to be two.
  //
  //  * `firstLoad` — nothing has ever arrived. A spinner is right.
  //  * `listUnreadable` — the read failed and left us with nothing to show for
  //    it. Red, and a way to try again; see DESIGN.md on what red means.
  //  * `listStale` — the read failed over something we still hold. Nothing was
  //    lost, so this is a caveat about the reading, not a failure, and colour
  //    here would be severity theatre. Issue #63.
  const firstLoad = requests.loading && !requests.reloading;
  const listUnreadable =
    requests.error !== null &&
    // Never having READ a list, which is not the same as having read an empty
    // one. After a successful read `rows` is a fact — "there are none" — and a
    // later failure does not unmake it; the honest screen there is the first-run
    // invitation plus a caveat saying the list could not be refreshed, not a red
    // box claiming we know nothing.
    requests.data === null &&
    liveUpload === null &&
    // Never over a parse that is still running. First run, drop a 30-second
    // file, alt-tab away and back at T+5s: the tab-visibility refetch fires, the
    // read fails, and without this the prominent uploader — spinner, elapsed
    // seconds and all — is unmounted and replaced by the red box, for an upload
    // that is perfectly fine and still going. That box means "this is not
    // recoverable" (DESIGN.md), which would be a lie, and unmounting `Upload`
    // mid-POST also destroys the in-flight guard issue #48 added.
    !uploading;
  const listStale = requests.error !== null && !listUnreadable;

  // The view as it is NOW, not as it was when a handler was created.
  //
  // `href` merged from render-time state, and `Upload.send` captures its
  // `onDone` at DROP time and calls it after `await api.upload(files)` — 10 to
  // 30 seconds for a real report. A reader who dropped a file on Products,
  // wandered to Compliance to read while it parsed, and waited was moved back
  // to Products: the merge replayed the tab from the render where the drop
  // happened. `next` overrides `request`, `query` and `label`, so `tab` was the
  // only field that leaked — and it is the one the reader most visibly chose.
  // Written after commit rather than during render, so a handler always reads a
  // view that was actually on screen. Issue #59.
  const view: View = { tab, request: selected, query, label };
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  });

  function href(next: Partial<View>) {
    const merged: View = { ...viewRef.current, ...next };
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

  /**
   * Change the view.
   *
   * `replace` is for changes that are not navigations. **An upload is a
   * mutation that happens to change what is on screen, and so is a removal** —
   * the reader did not ask to go anywhere, and Back means "undo where I went".
   * `go` used to push unconditionally, and #58 made every upload call it, which
   * produced two separate bugs from that one decision:
   *
   *  * Upload, notice it was the wrong file, remove it — #58's own narrative.
   *    The upload pushed `?r=7` and the removal pushed `?tab=timeline`, so Back
   *    landed on `?r=7` for a response that no longer existed. `known` stopped
   *    it crashing, but the URL then claimed id 7 while the view showed
   *    something else, and a link copied from that state was wrong: the exact
   *    URL-versus-view divergence #58 exists to close, reintroduced through the
   *    Back button on the path the change makes most likely.
   *  * A session starts at `/` with no `?r=`, which already resolves to the
   *    only response through the `rows[0]` fallback. The first upload pushed
   *    `?tab=timeline&r=1`, so Back returned to an identical render. The reader
   *    pressed Back, nothing happened, pressed again, and left the app.
   *
   * Replacing covers both, and costs nothing the pushes were buying: the URL
   * still names the new response, so a bookmark and a manual reload still land
   * on it.
   *
   * Genuine navigations still push — unless they would land on what is already
   * shown, which is the same phantom entry by another route: clicking the tab
   * you are already on. (Not the retailer selector — a `<select>` fires no
   * `change` when the current option is re-chosen, so it never reaches here.)
   *
   * Residual, named rather than chased: an OLDER entry can still name a removed
   * response, because switching tabs while viewing `?r=7` puts `?tab=profile&r=7`
   * in history too. That is the ordinary stale-link case `resolveCurrent`
   * already exists for, and History gives no way to prune arbitrary entries.
   * Issue #61.
   */
  function go(next: Partial<View>, opts?: { replace?: boolean }) {
    const { merged, search } = href(next);
    // Only the NULL case is normalised here, which is the one that matters: a
    // missing `?r=` and the id it resolves to are the same entry. An id that is
    // present but unknown to `rows` is deliberately NOT run through
    // `resolveCurrent`, so it compares as itself and pushes — a stale id is a
    // different entry from the one it happens to fall back to.
    const shown: View = { ...viewRef.current, request: current };
    const landing: View = { ...merged, request: merged.request ?? current };
    const replace = opts?.replace === true || isSameEntry(landing, shown);
    if (replace) window.history.replaceState(merged, "", search);
    else window.history.pushState(merged, "", search);
    setView(merged);
  }

  useEffect(() => {
    // Back and forward restore a view instead of leaving the app.
    const onPop = () => setView(readUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    // Nothing in this app ever re-read the request list, so a second tab
    // removing a response left this one showing it forever: `rows` still held
    // it, the selection stayed on it, and every view fetch 404'd into an
    // ErrorBox while the retailer selector went on confidently offering it. The
    // only recovery was picking a different response or reloading the page,
    // neither signposted. `make reset` does the same to a single open tab.
    //
    // Refetching when the tab becomes visible covers the realistic case — you
    // did something elsewhere, then came back — without polling, which an app
    // that makes no outbound calls and holds no server state has no business
    // doing. Issue #65.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      // Not if one is already in flight. `reload` only bumps a nonce and
      // `useAsync` has no AbortController, so a burst of visibility flaps —
      // alt-tab hunting, Mission Control, minimise and restore all fire this —
      // would queue N uncancellable GETs against the same threadpool the
      // synchronous upload endpoint is using. This app is built to make no
      // unprompted requests; this is the one unbounded source in it.
      if (requests.loading) return;
      requests.reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requests.reload, requests.loading]);

  useEffect(() => {
    // The URL asked for a response the list does not have, so `resolveCurrent`
    // quietly showed a different one. Say so in the URL rather than leave it
    // claiming a response that is not on screen: that divergence is what #58
    // exists to close, and a link or bookmark copied out of it is wrong.
    //
    // Reachable from three directions now. A stale `?r=` from a bookmark or
    // `make reset` was always one. The tab-visibility refetch below is a second,
    // and the sharp one: another tab removes the response this one is parked on,
    // this tab re-reads the list, the view falls back — and without this the URL
    // went on naming the deleted row indefinitely. A failed read is a third, and
    // is excluded: `requests.loading` and `settling` both mean the list is not
    // yet a fact to correct against.
    //
    // `replace`, because this is the app correcting its own address bar, not the
    // reader going anywhere. Issue #65, and the tail of #61.
    if (requests.loading || settling !== null) return;
    // A failed read is not a fact to correct against either, and this guard
    // used to claim it was excluded while excluding nothing: a failure leaves
    // `loading` false, `settling` cleared and `error` set, so it fell straight
    // through. Two things it destroyed. A bookmarked `?r=5` opened while the
    // backend restarts: the read fails, `rows` is empty, `current` is null, and
    // the address bar is rewritten to drop the id the reader asked for — "Try
    // again" then succeeds onto a different response with no record of the
    // request. And a just-landed upload whose reload fails: `settling` clears,
    // the new id is not in the retained rows, and the reader is moved onto the
    // oldest response while the panel above still names the one they added.
    if (requests.error !== null) return;
    // Popstate is NOT excluded, and it was: `replaceState` rewrites the entry
    // popstate just restored, so walking Back through several entries naming a
    // removed `?r=7` rewrites each as it is passed, and two adjacent entries
    // differing only by a dead id collapse to the same render. That residual is
    // real and is accepted here, because suppressing the correction bought
    // something far worse and far likelier: the FIRST Back onto ANY dead `?r=`
    // left the address bar naming a deleted response for the rest of the
    // session, with a different one on screen. One press of Back against a
    // narrow two-dead-entries-on-one-tab case is not a trade worth making — a
    // URL that lies is the whole bug class this file exists to close, and a
    // link copied out of that state is wrong. Found by review on PR #68.
    if (selected === null || selected === current) return;
    go({ request: current }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, current, requests.loading, settling]);

  useEffect(() => {
    if (announcement === "") return;
    // Cleared a while after it is said. A live region is a message, not a
    // status display: assistive technology announces the CHANGE, so the text
    // only has to be in the DOM long enough to be observed. Leaving it there
    // costs twice over — the panel's own words sit duplicated in the page for
    // the rest of the session, outliving the response they describe when it is
    // removed from another tab; and an identical second announcement is no
    // change at all, so dropping the same file twice would say nothing the
    // second time.
    //
    // The delay is generous because `polite` means "when the reader is next
    // free", not "now". This announcement lands in the same instant `<main>`
    // is replaced wholesale, which is a lot of queued speech, and a text
    // removed before the queue reaches it is an upload announced as nothing at
    // all — to precisely the readers issue #62 is for. Long enough to survive a
    // busy queue, short enough that it is gone before it can be read as a
    // status line.
    const clear = setTimeout(() => setAnnouncement(""), ANNOUNCEMENT_MS);
    return () => clearTimeout(clear);
  }, [announcement]);

  // The upload has landed AND no list read is in flight, so the report panel is
  // in its final position. Waiting for the second half matters on the first
  // upload, where the list arriving is what swaps the prominent uploader for
  // the footer one — focusing before that swap puts focus on an element about
  // to be unmounted, which drops it to `<body>`.
  //
  // `!loading` rather than `settling === null`, because a reload that FAILS
  // never clears `settling` (by design, above) and the reader still has to be
  // told their upload finished. The swap does not happen in that case either,
  // so the prominent panel is the final position and focusing it is right.
  const arrival = liveUpload !== null && !requests.loading ? uploadSeq : 0;
  useEffect(() => {
    if (arrival === 0 || !lastUpload) return;
    // Once per upload, and `arrival` alone cannot promise that: it is derived
    // from `liveUpload`, which is not monotonic. It can fall to null when a
    // read shows the row is gone and — before the absence effect above made
    // absence stick — come back on the next failed read, returning `arrival` to
    // a value it has already had. The effect would then re-announce and
    // re-steal focus for a response that no longer exists. Belt and braces
    // beside that fix, because the cost of being wrong here is the app
    // volunteering a confident claim about deleted data, out loud.
    if (arrival <= announcedSeq.current) return;
    announcedSeq.current = arrival;
    setAnnouncement(announceUpload(lastUpload));
    const panel = reportRef.current;
    if (!panel) return;
    // Focus first with the scroll suppressed, then scroll deliberately. The
    // footer uploader means the reader is at the BOTTOM of a long document when
    // they drop a file, and since #58 the upload swaps `<main>` for a response
    // that can be a very different length — a 25-row capped timeline versus a
    // "no purchase data" letter that renders three paragraphs. The document
    // height changes by a large factor, the browser clamps `scrollY` to
    // somewhere arbitrary, and the report can end up above the fold. Issue #62.
    panel.focus({ preventScroll: true });
    // Instant, not smooth, and that is a correctness choice before it is a
    // design one. `scrollIntoView` computes its target offset once, at call
    // time, and the report panel sits BELOW `</main>`; the view children are
    // still resolving their own fetches at this moment, so anything that lands
    // during a 300-500ms animation inserts content above the panel and pushes
    // it down. The animation then finishes short of the report — the same
    // off-screen outcome issue #62 exists to fix, reached by a different route.
    // An instant scroll lands in one frame, before any of that can happen.
    //
    // It also keeps DESIGN.md's Motion section true: the row unfurl stays the
    // only motion in the app, so there is nothing new to suppress under
    // `prefers-reduced-motion` and no decisions-log row to write.
    panel.scrollIntoView({ block: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrival]);

  // What to do when an upload finishes. Shared by both uploaders rather than
  // written twice: they already drifted once, and the half that matters here is
  // easy to leave out of one of them.
  //
  // **Selecting the new response is the load-bearing line.** Without it the
  // upload lands in the list and the app keeps showing whatever it showed
  // before — `current` falls back to `rows[0]`, the OLDEST response. The first
  // upload of a session hides this completely, because the row it just created
  // IS `rows[0]`; from the second upload on, the reader is told "Read as
  // Kroger" while looking at H Mart. Worse, "Remove this response" acts on
  // what is being VIEWED, so the obvious next click deletes the response they
  // did not just add.
  //
  // `r` is null when an upload starts or fails. Neither is a new response, so
  // neither changes the selection — a failed upload must leave the reader
  // where they were.
  //
  // The product filter is dropped for the same reason `onRemoved` drops it: it
  // names a product in the response being left behind, and carrying it onto a
  // different one silently filters a timeline nobody pointed it at.
  function uploadFinished(r: UploadResult | null) {
    setLastUpload(r);
    // The live region describes the last thing that happened, so it is cleared
    // on the same edge the report is: a new upload STARTING, or one that
    // failed. Leaving it saying "Read as Kroger. 127 visits" underneath the
    // refusal from a re-drop is the spoken version of the stale panel — it
    // reads as though the second drop partly worked.
    setAnnouncement("");
    // A null `r` means an upload just STARTED or failed. Nothing on the server
    // changed either way, so re-reading the list buys nothing and costs a
    // `loading: true` — which rendered "Looking for stored responses" over the
    // document while the drop zone was already saying "Reading the response…".
    // Two spinners for one action, one of them about the wrong thing.
    if (!r) return;
    setSettling({ id: r.request_id, asOf: readsRef.current });
    setUploadSeq((n) => n + 1);
    go({ request: r.request_id, query: null, label: null }, { replace: true });
    requests.reload();
  }

  return (
    // A page on a desk. The sheet is sized to hold the reading measure plus the
    // margin the marginalia lives in, and nothing wider: the app does not grow
    // to fill a 27-inch display, because a document does not. See DESIGN.md.
    <div className="mx-auto min-h-screen max-w-[64rem] bg-page px-6 py-10 sm:px-12">
      <header>
        <h1 className="font-serif text-[26px] leading-none font-semibold">
          unbagged
        </h1>
        <p className="mt-2 max-w-[62ch] text-muted">
          Read what the grocery store knows about you. Everything here stays on
          this machine.
        </p>
      </header>

      {/* The app saying out loud what just changed.
          Uploads and removals replace `<main>` wholesale, and before this there
          was no `aria-live` region and no `role="status"` anywhere in the app —
          so a screen reader user who dropped a file got no signal that anything
          had happened at all. Polite, so it waits for a pause rather than
          cutting across whatever is being read. Issue #62. */}
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>

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
              {/* The response an upload just created, until the list catches up
                  and supplies the real row. `current` points at it during that
                  window (see `resolveCurrent`), and a `value` matching no option
                  renders the select blank — which reads as a broken control on
                  the one interaction where the reader is watching hardest. */}
              {currentRow === null && current !== null && liveUpload && (
                <option value={current}>{liveUpload.display_name}</option>
              )}
            </select>
          )}
        </nav>
      )}

      {/* A caveat about the reading, not about the retailer, so it sits above
          the views rather than inside any one of them. */}
      {currentRow && <StaleReading request={currentRow} />}

      {/* The list could not be re-read, but nothing was lost. Deliberately not
          an ErrorBox: DESIGN.md retires red to two call sites and both mean
          "this is not recoverable", which this is not — everything on screen is
          still true and the button beside it is the way back. Issue #63. */}
      {listStale && (
        <Caveat
          action={
            <button
              onClick={requests.reload}
              className="text-ink underline decoration-dotted underline-offset-2"
            >
              Try again
            </button>
          }
        >
          The list of responses could not be re-read: {requests.error}. What is
          below is the last list that loaded, and the response you are reading is
          unaffected.
        </Caveat>
      )}

      <main>
        {/* `firstLoad`, not `loading`. A reload has something on screen already,
            and replacing it with "Looking for stored responses" says the app has
            nothing — a claim the reload has not made. It also put two spinners
            about two different things on screen for one action. */}
        {firstLoad && (
          <Spine>
            <Spinner label="Looking for stored responses" />
          </Spine>
        )}
        {listUnreadable && (
          <Spine>
            <ErrorBox error={requests.error!} onRetry={requests.reload} />
          </Spine>
        )}

        {/* One box, not two. The empty state used to stack a dashed drop zone on
            top of a dashed "nothing loaded yet" panel, which restated the box
            directly above it in the same visual language. The drop zone is
            already the empty state, and on a first run it is the whole screen,
            so it is the one place in the app that gets to be large.

            Not shown when the list is unreadable: "Start with a retailer's
            response" is a claim that there are none, and a failed read is not
            evidence of that. Issue #63. */}
        {!firstLoad && !listUnreadable && rows.length === 0 && (
          <Upload
            // Prominent only when this really is a first run. A live report
            // means a response was just committed and the list simply could not
            // be re-read to confirm it — so "Start with a retailer's response"
            // would be inviting the reader to do the thing they just did. The
            // compact form says "Add another response", which is true, and
            // carries the same report panel.
            prominent={liveUpload === null}
            result={liveUpload}
            resultRef={reportRef}
            onBusy={setUploading}
            onDone={uploadFinished}
          />
        )}

        {current !== null && (
          <>
            {tab === "timeline" && (
              <Timeline
                // Back and forward change the URL's product filter while the
                // view holds its own search state. Keying on it remounts rather
                // than leaving the two disagreeing.
                //
                // `current` is in the key for the same reason, and the response
                // it names is the half that bites. Timeline owns `store`,
                // `from`, `to` and `q` locally; only `q` is ever mirrored in
                // the URL. So switching response without remounting carried the
                // hand-set filters onto a retailer that has never heard of
                // them — and a store code from the previous response matches no
                // option in the new one, so the control reads "every store"
                // while `?store=<old code>` is still on every request. Measured
                // switching a Kroger store filter onto an H Mart response: the
                // header said 67 visits over an empty roll and "No visits match
                // those filters", with nothing on screen to clear.
                key={`${current}:${currentRow?.retailer_id ?? ""}:${query ?? ""}`}
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
          <Upload
            result={liveUpload}
            resultRef={reportRef}
            onBusy={setUploading}
            onDone={uploadFinished}
          />
          {/* Removing one is a smaller footnote still, and it lives here rather
              than beside the retailer selector: the selector is used constantly
              and a destructive control does not belong under a hand that is
              only trying to switch views. */}
          {/* Not while the list might be stale. `useAsync` now RETAINS data
              through a failed read, which is what stops a dropped connection
              deleting the reader's report (issue #63) — but this control is the
              one place retained data is ACTED on, and the action is an
              irreversible DELETE. Before the retention change a failed read
              emptied `rows` and this control vanished on its own; leaving it
              live under a caveat that reassures "the response you are reading
              is unaffected" while offering to delete it is the worst of both.
              It comes back the moment a read succeeds. */}
          {currentRow && requests.error === null && (
            <div className="mt-4">
              <RemoveRequest
                // Identity, not just an id. `request.id` is a rowid alias that
                // SQLite reuses after a delete, and the tab-visibility refetch
                // is a NEW way for the response behind an id to change without
                // the id changing. Without the retailer in the key: the reader
                // opens this confirmation reading "Remove Kroger", a background
                // refetch swaps id 9's content to H Mart, nothing remounts
                // because the id is unchanged, the text updates in place, and
                // the click they already decided on deletes H Mart. Issue #64.
                key={`${current}:${currentRow.retailer_id}`}
                // Found, not asserted. `current` is derived from `rows` in the
                // same render, so this holds today — but it holds by argument
                // rather than by construction, and the cost of being wrong is
                // not a missing control: `request.display_name` on undefined
                // throws during render, and there is no error boundary in the
                // tree (issue #49), so the whole page goes blank.
                request={currentRow}
                onRemoved={() => {
                  // Said before `rows` changes under us.
                  setAnnouncement(`Removed ${currentRow.display_name}.`);
                  // Drop the filter and the selection with it: `?r=` would
                  // otherwise name a response that no longer exists, and `?q=`
                  // would keep filtering a timeline that just changed under it.
                  //
                  // `replace`, because a removal is a mutation rather than a
                  // navigation — and because pushing here is what left `?r=7`
                  // one Back press away, naming the response just deleted. See
                  // `go`. Issue #61.
                  go(
                    { request: null, query: null, label: null },
                    { replace: true },
                  );
                  // The upload panel needs no clearing here: `liveUpload`
                  // already hides a report whose response is gone, and only
                  // that one. Clearing on any removal — which this did — threw
                  // away a still-accurate report, warnings included, when the
                  // reader deleted some OTHER response.
                  requests.reload();
                }}
              />
            </div>
          )}
        </div>
      )}

      <footer className="mt-10 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-rule pt-4 text-[11.5px] text-faint">
        <span className="max-w-[62ch]">
          unbagged reports what a response contained and what it did not. It is
          not legal advice, and it never sends anything anywhere.
        </span>
        <Version />
      </footer>
    </div>
  );
}
