# unbagged — icon assets

Two source SVGs. Everything else is generated from them by `build_icons.py`.

## Sources

| File | Use |
|---|---|
| `unbagged-logo.svg` | Full mark. Mask with nose notch, pupils, smile, 2px outlines. Use at 48px and above. |
| `unbagged-logo-small.svg` | Stripped mark for 16–48px. No smile, no pupils, no outlines. Geometry is snapped to a 6.25-unit grid so every edge lands on a whole pixel at 16, 32, and 48. |

Both are transparent and centered in a 100×100 viewBox.

## Generated

Four of these six are served. `SERVED` in `tools/build_brand.py` excludes `icon-512.png`
(README and avatar, never requested by the app) and the three `favicon-*.png` (already
bundled inside the `.ico`), so `make brand-check` says nothing about them.

| File | Source | Notes |
|---|---|---|
| `favicon.ico` | small | 16 / 32 / 48 bundled. The only generated file carrying no C2PA manifest: every PNG below has one, and this is re-rendered from the SVG (`build_icons.py` line 27) rather than assembled from them, so it inherits nothing. It ships as authored, which `make brand-check` asserts rather than assumes. |
| `favicon-16.png` | small | |
| `favicon-32.png` | small | |
| `favicon-48.png` | small | |
| `apple-touch-icon-180.png` | full | Opaque `#EFE7DC` background, 22px inset. iOS ignores alpha and applies its own corner mask. |
| `icon-512.png` | full | Transparent. PWA manifest, GitHub org avatar, README header. |

## Palette

| Token | Hex | Where |
|---|---|---|
| kraft | `#C99A63` | bag body |
| kraft-fold | `#B07F49` | folded top, full mark |
| kraft-fold-small | `#A9763F` | folded top, small mark — darkened to hold contrast without an outline |
| kraft-edge | `#8A5A2B` | outlines, full mark only |
| ink | `#2E2A26` | mask, pupils, smile |
| paper | `#FFF6EA` | eye whites |
| tile | `#EFE7DC` | apple touch icon background |

## HTML

```html
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/unbagged-logo-small.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon-180.png">
```

Browsers that support SVG favicons take the second line and scale the small mark; the `.ico` is the fallback.

## Regenerating

```bash
.venv/bin/pip install cairosvg    # pillow comes with `make setup`; cairosvg does not
.venv/bin/python build_icons.py   # regenerate the rasters in this directory
make brand                        # refresh the served copies in frontend/public/
```

The venv, not `python3`. Pillow is a dev dependency, so it lives in `.venv` and
the system interpreter cannot import it; every other tool here runs through the
same interpreter (`Makefile` line 7).

Edit the SVGs, never the PNGs.

Both steps are needed. This directory holds the sources; `frontend/public/` holds
what the app actually serves, with the C2PA content-credential manifest stripped:
90-93% of each SVG and 64% of the touch icon. Not a bandwidth argument, whatever
the size suggests — this app is served over loopback, where transfer cost is not
a cost. The manifest names `c2pa.org` and carries a provenance record, in a build
whose README promises it reaches no other host.

`make brand-check` runs in CI, so stopping after `build_icons.py` gets you a red
build. It fails five ways: `DRIFT` (a served file is not what its source
produces), `MISSING` (a served file is absent), `STRAY` (a file in
`frontend/public/` that no source produces), `SYMLINK` (a link anywhere beneath
it, which the walk follows because Vite does), and `UNSTRIPPED`.

`UNSTRIPPED` is broader than the metadata it was built for. It fails on any PNG
chunk that is not pixel data — iCCP and EXIF as much as a C2PA manifest — and on
a served SVG carrying a `<script>`, a `<foreignObject>`, an inline event handler
or an off-origin `href`, because a served SVG is a navigable same-origin document
and not only a picture. `TODOS.md` leans on that breadth: it is the reason the
missing Content-Security-Policy is filed as a gap rather than a live hole.

It is judged on the bytes themselves rather than by comparison, because both
sides of a comparison run through the same stripper and stay equal whether or not
it still strips anything.

**What this does not cover:** `resources/` against the two source SVGs. Nothing
re-runs `build_icons.py` — cairosvg is in no dependency group — so an SVG edited
without it leaves stale rasters here, and `make brand` will faithfully strip and
serve them.

The HTML above is what `frontend/index.html` declares; a test asserts every href
there resolves to a shipped file, so change the two together.

## Notes on the small variant

The smile does not survive below about 24px — it needs a stroke heavy enough that it crowds the eyes. The pupils disappear entirely. Rather than let both degrade, the small mark drops them and keeps the three shapes that still read: bag, band, eyes. If you add a mid-size asset later, 64px is the threshold where the full mark starts working.
