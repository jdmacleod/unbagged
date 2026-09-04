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

## DESIGN.md: the display serif is unspecified in practice

**What:** Add a decisions-log row recording that the display voice varies by
operating system, and state which variance is accepted.

**Why:** `ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", serif`
resolves to Georgia on macOS and Windows and to the browser's generic serif on
most Linux desktops, commonly Liberation Serif or DejaVu Serif. Those are
materially different in colour and width. DESIGN.md calls this face "what makes
a $43.11 grocery trip read as a page rather than a row in a table", which makes
it the product's character voice, and a Linux reader gets a different product.

**Pros:** Makes an existing tradeoff explicit instead of leaving the document
describing a face some readers never see. Costs nothing at runtime.

**Cons:** None, unless the conclusion is to ship a second font file, which the
one-font allowlist test in `tests/test_frontend_build.py` forbids and which the
zero-third-party-request constraint outranks anyway.

**Context:** Raised during /plan-design-review on 2026-09-04, Pass 5. This is a
documentation fix, not a code fix: the constraint that produced it is correct.

**Depends on:** Nothing.

## Product index: export the portrait as an image

**What:** Let the reader save the index as an image.

**Why:** Fits the local-first framing. Deferred by the CEO review on 2026-09-03,
carried forward here unchanged.

**Cons:** Adds a canvas dependency to a build that vendors everything, and the
zero-external-requests rule has to be weighed first.

**Depends on:** The index shipping.
