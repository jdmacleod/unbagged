# TODOS

Deferred work, with the measurement that justified deferring it. Each item
carries enough context to be picked up cold.

## Timeline: the month is a running head that does not run

**What:** Rotate the month chart 90 degrees into the margin as a sticky,
clickable scroll rail. Pin the month label and the filter row.

**Why:** Measured at ~5,300px for 121 visits, roughly six screens. The month
prints once at the change and then scrolls away, so from about row 40 onward
nothing on screen answers "when am I?". The 148px month chart is a picture you
cannot act on, and the margin beside the roll carries a four-character citation
per row and nothing else.

**Pros:** Reclaims 148px inline. Replaces roughly 4,500px of scrolling with one
click. Answers Krug's wayfinding questions for the whole roll. Uses the margin
DESIGN.md already says must have a job, and introduces no new visual language.

**Cons:** Touches the most-reviewed view in the project, which has a history of
rewrites dropping prior fixes (see the `rewriting-a-view-silently-drops-its-review-history`
learning). Any change here needs the prior QA findings re-verified empirically,
not grepped for.

**Context:** Deferred during /plan-design-review on 2026-09-04 to keep the
product-index branch's blast radius on the new view. The click-through from the
index lands the reader on this surface unchanged, which is why D6 in the plan
adds an explicit arrival statement instead.

**Depends on:** Nothing. Independent of the index work.

## Compliance: the letter draft is 350px of read-only preview

**What:** Reduce the follow-up draft from `rows={18}` to about 8, with a control
to expand.

**Why:** ~350px of a read-only textarea on a view whose per-retailer section is
already ~700px. The field cannot be edited and the only action on it is Copy, so
the height buys a preview of a document that will be read in a mail client.

**Pros:** Cuts roughly 200px on a dense view. Keeps every word reachable.

**Cons:** Adds a disclosure to the one view that was rebuilt specifically to take
evidence out from behind a click. Reasonable people will argue that a letter you
are about to send in your own name should be shown in full.

**Context:** Measured during /plan-design-review on 2026-09-04.
`frontend/src/views/Compliance.tsx`, the `Letter` component.

**Depends on:** Nothing.

## Product index: export the portrait as an image

**What:** Let the reader save the index as an image.

**Why:** Fits the local-first framing. Deferred by the CEO review on 2026-09-03,
carried forward here unchanged.

**Cons:** Adds a canvas dependency to a build that vendors everything, and the
zero-external-requests rule has to be weighed first.

**Depends on:** The index shipping.

---

## Resolved

*Four findings were logged here by /devex-review on 2026-09-04 and fixed the
same day. Kept as a record of what changed and why, not as open work.*

- **No way to remove an imported report.** `DELETE /api/requests/{id}` and
  `api.ts`'s `deleteRequest` both existed with zero call sites, so a response
  could be added and never taken back and the only remedy was `make reset`.
  `components/RemoveRequest.tsx` is the control: a quiet text button that opens
  a confirmation naming the retailer and its coverage window, saying plainly
  that there is no undo and that the uploaded file stays on disk. This is now
  the single call site for red that `DESIGN.md` always reserved and nothing
  occupied. It also makes the duplicate-upload error actionable — that message
  told people to "remove the existing one first", which was impossible.
- **The Products jump rail sat 400 tab stops into the page.** `Spine` gained a
  `marginFirst` option that places the margin ahead of the content in the DOM
  while explicit grid columns keep it on the right. Measured: the first rail
  anchor moved from tab stop 408 to 8, ahead of the entries at 27, with no
  positive `tabindex` and no visual change.
- **Nothing asserted the footer's version.** `TestVersionIsOneNumber` in
  `tests/test_packaging.py` compares `VERSION` to `unbagged.__version__` and
  fails with an instruction to reinstall. Verified against a deliberately
  mismatched `VERSION` before being kept.
- **The display serif's OS variance was undocumented.** Already closed before
  this pass: `DESIGN.md`'s decisions log carries the row dated 2026-09-04. The
  entry here was itself stale and has been removed.
- **`HANDOFF.md` §8 described a UI that no longer exists.** §8 and §9 are now
  marked historical, with a table of what shipped against what the brief asked
  for and why each departure was made. §§0-7 stay authoritative, because
  `adapters/base.py`, `models.py`, `db.py` and four test modules cite §4 and §5
  as the live contract — which is why marking the whole file historical would
  have been wrong. `CLAUDE.md` now says which half is which.
