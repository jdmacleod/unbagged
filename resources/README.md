# unbagged — icon assets

Two source SVGs. Everything else is generated from them by `build_icons.py`.

## Sources

| File | Use |
|---|---|
| `unbagged-logo.svg` | Full mark. Mask with nose notch, pupils, smile, 2px outlines. Use at 48px and above. |
| `unbagged-logo-small.svg` | Stripped mark for 16–48px. No smile, no pupils, no outlines. Geometry is snapped to a 6.25-unit grid so every edge lands on a whole pixel at 16, 32, and 48. |

Both are transparent and centered in a 100×100 viewBox.

## Generated

| File | Source | Notes |
|---|---|---|
| `favicon.ico` | small | 16 / 32 / 48 bundled |
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
pip install cairosvg pillow
python3 build_icons.py   # regenerate the rasters in this directory
make brand               # refresh the served copies in frontend/public/
```

Edit the SVGs, never the PNGs.

Both steps are needed. This directory holds the sources; `frontend/public/` holds
what the app actually serves, with the C2PA content-credential manifest stripped
— 90-93% of each SVG and 64% of the touch icon, on a favicon fetched every page
load. `make brand-check` runs in CI and fails if the two fall out of step or if a
file appears in `frontend/public/` that no source produces, so stopping after
`build_icons.py` gets you a red build.

The HTML above is what `frontend/index.html` declares; a test asserts every href
there resolves to a shipped file, so change the two together.

## Notes on the small variant

The smile does not survive below about 24px — it needs a stroke heavy enough that it crowds the eyes. The pupils disappear entirely. Rather than let both degrade, the small mark drops them and keeps the three shapes that still read: bag, band, eyes. If you add a mid-size asset later, 64px is the threshold where the full mark starts working.
