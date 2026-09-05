# TODOS

Deferred work, with the measurement that justified deferring it. Each item
carries enough context to be picked up cold.

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

*Two security findings were logged by /cso on 2026-09-04 and fixed the same day.*

*The Timeline month rail shipped on 2026-09-05.*

- **The month is a running head that runs.** The 148px inline chart is gone above
  `lg`; the months are rotated into the margin as a sticky rail, each row a label
  and a bar, each one a link that jumps. A one-line running head names the month
  you are in and how much of the roll is on the page. Below `lg` the margin does
  not exist, so the chart stays inline and its bars became buttons — the picture
  was the only thing showing all two years at once and the only thing you could
  not act on.

  Three things came out of building it that the filing did not anticipate.
  **The margin cannot hold both** a sticky rail and a per-row citation: the
  citations scroll underneath the rail and vanish behind it, so `p.5` moved onto
  the row at every width and `DESIGN.md`'s decisions log carries the departure
  from "footnotes belong in the margin". **The filter row is not pinned**, which
  the filing asked for: at 320px those four controls wrap to about 150px of
  sticky furniture, half a phone screen given to a set-and-forget control, and
  the rail now carries the navigation that made pinning attractive. **The rail
  entries are links, not buttons**, because `@media (pointer: coarse)` puts a
  44px floor under every button and exempts `a` inside `li` — two years of months
  as buttons is a 1,000px rail in a 176px column on a tablet, with nothing left
  for `sticky` to stick to. Same trap the product index logged a decision about.

  Two bugs found by driving it rather than reading it, both now covered in the
  browser tier: the second jump did nothing at all, because revealing rows is a
  `setState` that returns an unchanged value once they are revealed and React
  bails out of the render the scroll was waiting on; and revealing exactly
  through the target made it the last row on the page, which a browser cannot
  scroll to the top, so a jump to Jan 25 landed on a screen whose head read
  Oct 24.

- **The Python dependencies were unpinned and unhashed.** `pip install .` in the
  Dockerfile resolved 29 packages fresh at every build with no hashes, into the
  container that reads people's reports, while the UI half already had a
  lockfile. `docker/requirements.txt` is now the image's lock, installed with
  `--require-hashes`, which is all-or-nothing: pip refuses the whole file unless
  every requirement is pinned and hashed, so it cannot silently degrade into a
  floor-based resolve. The four constraints this item was filed on are each
  answered. *Platform:* `make lock` compiles inside `python:3.12-slim` on
  linux/amd64, because a lock built in a macOS venv pins wheels the build cannot
  install. *Two audiences:* the contributor path is untouched and stays
  `pip install -e ".[dev]"`, which is why the file is in `docker/` rather than at
  the root looking like the thing you install from. *Decay:* Dependabot has a
  `/docker` entry. *Verification:* the image builds, the container tier passes
  against it, and the lock was proved load-bearing by corrupting a hash and
  watching the build fail — which also established that pip accepts an artifact
  matching *any* of a package's listed hashes, so a partial tamper is not a
  valid test.
- **`sanitize.py` preserved object keys verbatim.** The reasoning was sound for
  field names and wrong for maps keyed by the user's data, and the counterexample
  was already in this repository: a Kroger identity blob keys `loyaltyCards` by
  the card number, which `tests/test_fixtures.py` asserts. `CONTRIBUTING.md`
  tells people to attach the skeleton to a public issue, so the guarantee had to
  match the behaviour. Option 1 from the filing: keys matching a long digit run,
  a UUID or an email address are masked to `<key:len=N>`, keeping the length so
  the shape of the map stays legible; every field name survives. The patterns
  mirror `tools/scan_pii.py` rather than importing it, because the shipped
  application must not depend on repo tooling, and a test asserts the two have
  not drifted.

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
- **`docs/handoff.md` §8 described a UI that no longer exists.** §8 and §9 are now
  marked historical, with a table of what shipped against what the brief asked
  for and why each departure was made. §§0-7 stay authoritative, because
  `adapters/base.py`, `models.py`, `db.py` and four test modules cite §4 and §5
  as the live contract — which is why marking the whole file historical would
  have been wrong. `CLAUDE.md` now says which half is which.
