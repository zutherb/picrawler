# Visual style guide (brand, colours, mascot)

For anyone changing how PiCrawler looks — written mainly for coding agents
(Claude/Opus). The look follows the PiCrawler card on bernd-zuther.de: dark
workshop look, amber accent, Inter + JetBrains Mono, terminal motifs.

The same logo and the same colours exist **in several formats in several
places** (SVG, PNG, CSS, Swift, JSON, Python). Past changes fixed one place
and missed the others; the hollow-ring-eye bug took three commits
(a647ed9, 5a59b48, ff1bfb9) to remove everywhere. So:

> **A brand change is only done when every file in section 4 has been checked
> and every raster file in section 5 has been re-rendered.**

## 1. Mascot rules

The mascot is a white, round robot head with a near-black visor, amber ears,
pink cheeks and an antenna with a ball. Full figure: `logo.svg`; head on a dark
tile: `logo-mark.svg`.

### 1.1 Eyes: always comic eyes, never rings

Every eye, at every size and in every format, is built from **three filled
circles**, drawn in this order:

1. **Eye:** filled white circle `#FFFFFF`, radius `R`.
2. **Pupil:** filled dark circle (`#0D0F14` in `logo.svg`, `#14161D` in the
   mark/Swift), same centre, radius **0.55–0.60 × R**.
3. **Highlight:** small filled white circle (opacity 0.9), radius about
   **0.18 × R**, offset **up and to the left** inside the pupil.

**Never** draw an eye as a hollow ring (stroke-only circle, `fill="none"`,
`.stroke()` / `strokeBorder` in SwiftUI, a dark disc with a white outline,
etc.). On the dark visor a ring reads as a blank, staring eye. This was a
recurring bug and appeared in several files at once.

Reference geometry (copy it, don't re-invent it):

| File | Eye centres | R (eye) | Pupil r | Highlight r / offset |
|---|---|---|---|---|
| `services/web/static/logo.svg` (= `docs/logo.svg`) | (83, 79), (117, 79) | 10.5 | 6.2 (59 %) | 1.9 at (−2.2, −2.4) |
| `services/web/static/logo-mark.svg` | (26, 32.5), (38, 32.5) | 4.6 | 2.7 (59 %) | 0.85 at (−1, −1.2) |
| `apps/ios/.../ConnectView.swift` `RobotMark` (64-unit grid, diameters) | x ±6, y +0.5 from centre | 9.2 ⌀ | 5.4 ⌀ (59 %) | 1.7 ⌀ at (−1, −1.2) |

```svg
<!-- logo.svg: correct -->
<circle cx="83" cy="79" r="10.5" fill="#FFFFFF"/>
<circle cx="83" cy="79" r="6.2" fill="#0D0F14"/>
<circle cx="80.8" cy="76.6" r="1.9" fill="#FFFFFF" opacity="0.9"/>

<!-- WRONG: hollow ring -->
<circle cx="83" cy="79" r="10.5" fill="none" stroke="#FFFFFF" stroke-width="3"/>
```

```swift
// ConnectView.swift RobotMark: correct (s = size / 64)
Circle().fill(.white).frame(width: 9.2 * s).offset(x: x * s, y: 0.5 * s)
Circle().fill(Theme.background).frame(width: 5.4 * s).offset(x: x * s, y: 0.5 * s)
Circle().fill(.white.opacity(0.9)).frame(width: 1.7 * s).offset(x: (x - 1) * s, y: -0.7 * s)
```

Pupil and eye share the same centre (no "looking sideways" offsets), so the
mark stays symmetric at 16 px favicon size.

### 1.2 Other mascot parts

| Part | Colour |
|---|---|
| Shell (head, body, front legs) | gradient `#FFFFFF` → `#F4F1EA` (→ `#E2DDD2` in `logo.svg`), stroke `#CBC5B9` 1.2 |
| Visor | `#0D0F14` (mark), gradient `#1C1F28` → `#0D0F14` (`logo.svg`) |
| Ears, joints, body "lens" ring | amber gradient `#FFC555` → `#E89A0C` (`id="ear"`) |
| Cheeks | `#FF8C8C` ellipses below the eyes, opacity 0.9 |
| Back legs / feet / side joints | `#D3CEC4` / `#1A1C22` / `#2A2D36` |
| Antenna | shell colours, ball top right |
| Tile of the mark | gradient `#232631` → `#0D0F14`, rx 15 of 64, thin amber edge `#FFB020` at 45 % opacity |
| Eye glow (`logo.svg` only) | `filter="url(#glow)"` Gaussian blur 2.2 on the eye group |

`docs/logo.svg` (README) is an **identical copy** of
`services/web/static/logo.svg`. Edit the web file, then `cp` it over.

## 2. Colour palette

### 2.1 Brand and surfaces

| Token (CSS / Swift) | Hex | Use |
|---|---|---|
| `--accent-1` / `Theme.accent` | `#FFB020` | **The** brand amber: buttons (`button.primary`), active nav underline, focus rings, checkbox/range accent, borders on hover, joystick, map crawler, eyebrows/text **on dark** backgrounds |
| `--accent-2` / `Theme.accent2` | `#FFC555` | Light end of the amber gradient (`--accent-gradient`, joystick knob, map rays) |
| (SVG `ear` gradient end) | `#E89A0C` | Dark end of the amber gradient in the artwork only |
| `--accent-text` (light mode) | `#9A5B00` | Amber **as text on light backgrounds** (panel eyebrows `.panel h2`, link/hover text). 5.1:1 on `#FAF8F3`, 5.4:1 on `#FFFFFF` (WCAG AA) |
| `--accent-text` (dark mode) | = `--accent-1` | `#FFB020` on `#14161D` is 9.9:1 |
| `--bg-deep` / `Theme.deep`, `Theme.background` | `#14161D` | Dark background (dark mode `--bg`, login page, iOS, OLED splash key colour) |
| `--bg-deep-2` / `Theme.deep2` | `#0D0F14` | Darker: header in dark mode, input fields, `pre.payload`, visor |
| dark `--panel` / `Theme.panel` | `#1C1F28` | Cards in dark mode / iOS |
| `--fg-on-deep` / `Theme.text` | `#F4F1EA` | Warm off-white text on dark |
| `--fg-on-deep-muted` / `Theme.muted` | `#B3B6C2` | Secondary text on dark |
| light `--bg` | `#FAF8F3` | Page background, light mode |
| light `--panel` | `#FFFFFF` | Cards, light mode |
| light `--text` | `#14161D` | Text, light mode |
| light `--muted` | `#5E6170` | Secondary text, light mode (5.8:1) |
| text on amber | `#101018` | Label colour on amber buttons/flash (10.4:1) |
| `LaunchBackground.colorset` | `#14161D` | iOS launch screen |

Rule of thumb:

- **Amber fills/strokes are always `--accent-1` (`#FFB020`).** Hover tint
  `rgba(255, 176, 32, 0.10–0.12)`, focus ring `rgba(255, 176, 32, 0.2)`.
- **Amber text uses `var(--accent-text)`**, never `var(--accent-1)` —
  except on surfaces that are always dark (login page: `.login .eyebrow`,
  `.login .prompt .cursor`), which may use `--accent-1` directly.
  `#FFB020` on the light background is only 1.7:1.
- iOS is dark-only (`.preferredColorScheme(.dark)` in `PiCrawlerApp.swift`),
  so `Theme.accent` is fine for text there. If a light mode is ever added,
  add a `Theme.accentText = #9A5B00` for light and use it for text.

```css
/* services/web/static/app.css */
:root {
  --accent-1: #FFB020;
  --accent-2: #FFC555;
  --accent-text: #9A5B00;            /* light mode: amber as text */
}
:root[data-theme="dark"] { --accent-text: var(--accent-1); }   /* also in the prefers-color-scheme block */

.panel h2 { color: var(--accent-text); }                         /* text */
button.primary { background: var(--accent-1); color: #101018; }  /* fill */
```

### 2.2 Status colours (decided, keep them)

Status is **not** amber: amber is the brand accent and would be confused with
"warning". Decision (review of ff1bfb9, confirmed again): **ok = green,
warn = orange, bad/stop = red.**

| Token | Light mode | Dark mode / iOS | Used for |
|---|---|---|---|
| `--ok` / `Theme.ok` | `#1E9E5A` | `#3DD68C` | live dot, battery fill, link up |
| `--warn` / `Theme.warn` | `#D9731A` | `#FF8A3D` | battery low, warn tags, map obstacles, pending robots |
| `--bad` / `Theme.bad` | `#E5484D` | `#FF5F57` | errors, battery critical, link down |
| `--stop` / `Theme.stop` | `#E5484D` | `#E5484D` | STOP button (white text) |

They are meant for dots, fills, borders and icons (≥ 3:1 non-text contrast:
ok 3.3:1, warn 3.1:1 on `#FAF8F3`; dark mode 9.6:1 / 7.7:1). Known
limitation: in light mode `.battery.low` also colours its label text with
`--warn` (3.1:1); keep labels short or add a darker text variant like
`--accent-text` if this becomes a problem.

```swift
// apps/ios/PiCrawler/App/Theme.swift
/// Status green: amber is the brand accent and too close to `warn`.
static let ok = Color(hex: 0x3DD68C)
static let warn = Color(hex: 0xFF8A3D)
```

### 2.3 Data visualisation

- Detection boxes (`app.js` `BOX_COLORS`): `#FFB020`, `#FFC555`, `#FF8A3D`,
  `#FF5F57`, `#B3B6C2` — always drawn on the dark video.
- Local map (`remote.js`, `MapView.swift`): crawler = accent, ultrasonic rays
  amber `255, 176, 32`, camera rays off-white `244, 241, 234`, obstacles =
  warn, free cells `#3A3E4A` / `rgba(179, 182, 194, 0.22)`. The map canvas is
  always dark.

### 2.4 Type

Inter (headings/body) and JetBrains Mono (labels, eyebrows, terminal
prompts), with system fallbacks. Nothing is loaded from the internet (CSP).
Eyebrows: monospace, uppercase, letter-spacing 0.12em, accent text colour
(`Theme.eyebrow()` on iOS).

## 3. Contrast check

Check every new text colour against its background, WCAG AA = 4.5:1 for
normal text (3:1 for large text and non-text UI):

```python
def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

def contrast(a, b):
    a, b = sorted((lum(a), lum(b)), reverse=True)
    return (a + 0.05) / (b + 0.05)

contrast("#9A5B00", "#FAF8F3")   # 5.11
```

## 4. Where brand colours and the logo live (check ALL of them)

| File | What | Format |
|---|---|---|
| `services/web/static/logo.svg` | full mascot (login page art) — **master of the figure** | SVG |
| `docs/logo.svg` | README logo, identical copy of the file above | SVG |
| `services/web/static/logo-mark.svg` | head on a dark tile (header, favicon of every page) — **master of the mark** | SVG |
| `services/web/static/app.css` | all web colours as CSS variables (light, dark, `prefers-color-scheme`) | CSS |
| `services/web/static/app.js` | `BOX_COLORS` | JS |
| `services/web/static/remote.js` | map canvas colours (hard-coded `rgba(...)`) | JS |
| `services/web/static/*.html` | reference `logo-mark.svg` (favicon, header) and `logo.svg` (login) | HTML |
| `services/display/assets/robot.png` | raster mascot artwork (240×320, dark background) for the OLED splash | PNG |
| `services/display/splash.py` | `BACKGROUND = (20, 22, 29)` key colour (#14161D), pink-cheek/amber thresholds for 1-bit dithering | Python |
| `apps/ios/PiCrawler/App/Theme.swift` | all iOS colours | Swift |
| `apps/ios/PiCrawler/Views/ConnectView.swift` | `RobotMark`: the mark drawn natively (tile, ears, head, visor, comic eyes, antenna) | Swift |
| `apps/ios/PiCrawler/Views/{MapView,RemoteView,JoystickView}.swift` | use `Theme.*`, `MapView` has `0x3A3E4A` | Swift |
| `apps/ios/PiCrawler/Assets.xcassets/AppIcon.appiconset/icon-1024.png` | app icon, rendered from `logo-mark.svg` | PNG |
| `apps/ios/PiCrawler/Assets.xcassets/LaunchBackground.colorset/Contents.json` | launch colour `0x14 0x16 0x1D` | JSON |

The OLED boot splash (`splash.bin`) is generated during deployment from
`robot.png` by `splash.py`; nothing to commit there.

Find stragglers after a change (old hex values, ring eyes):

```bash
grep -rniE '#?(FFB020|FFC555|E89A0C|14161D|0D0F14|1C1F28|F4F1EA|B3B6C2)' \
  --include='*.svg' --include='*.css' --include='*.js' --include='*.html' \
  --include='*.swift' --include='*.py' --include='*.json' . | grep -v '^./.git/'
grep -rnE 'fill="none"[^>]*stroke="#FFF' --include='*.svg' .      # ring eyes?
grep -rn '0x[0-9A-F]\{6\}' apps/ios                                # Swift hex colours
```

Replace the hex list with the **old** values when you migrate away from them.

## 5. Raster files: no build script, re-render by hand

There is no build step for images. The PNGs are committed and **drift from
the SVGs unless you update them in the same commit**:

| PNG | Source | How |
|---|---|---|
| `icon-1024.png` | `logo-mark.svg` | full-bleed: remove `rx="15"` from the tile rect and drop the amber edge rect (iOS applies its own corner mask), render 1024×1024 |
| `robot.png` | none (raster artwork) | edit pixels directly (ff1bfb9 recoloured it pixel-wise); keep 240×320 and the `#14161D` background, check the eyes, then run the splash tests |

Render recipe (browser over CDP; reproduces the committed `icon-1024.png`
pixel-identically). With the agent's browser helpers:

```python
import base64, re
svg = open("services/web/static/logo-mark.svg").read()
svg = svg.replace('rx="15" fill="url(#bg)"', 'fill="url(#bg)"')       # full-bleed tile
svg = re.sub(r'\s*<rect x="1" y="1"[^>]*/>', "", svg)                   # no amber edge
html = ('<!doctype html><body style="margin:0"><img style="display:block;width:1024px;height:1024px" '
        'src="data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode() + '">')
cdp("Emulation.setDeviceMetricsOverride", width=1024, height=1024, deviceScaleFactor=1, mobile=False)
cdp("Page.navigate", url="data:text/html;base64," + base64.b64encode(html.encode()).decode())
# wait ~1 s for the load, then:
png = cdp("Page.captureScreenshot", format="png", clip={"x": 0, "y": 0, "width": 1024, "height": 1024, "scale": 1})
open("apps/ios/PiCrawler/Assets.xcassets/AppIcon.appiconset/icon-1024.png", "wb").write(base64.b64decode(png["data"]))
cdp("Emulation.clearDeviceMetricsOverride")
```

Equivalent without an agent browser: `chromium --headless --screenshot=out.png
--window-size=1024,1024 --hide-scrollbars page.html` (the snap build cannot
read hidden directories such as `~/.cache`; use a data: URL or a visible path).
Always look at the result (eyes!) before committing.

After a raster change run `python3 -m unittest tests.test_splash tests.test_display`;
`splash.py` keys the background on `#14161D` and dithers amber, so a new
background colour or a pink/amber change needs `BACKGROUND` and the
thresholds in `robot_bitmap()` adjusted.

## 6. Checklist for a visual change

1. Change the masters: `logo.svg`, `logo-mark.svg`, `app.css`, `Theme.swift`.
2. Mirror: `cp services/web/static/logo.svg docs/logo.svg`; `RobotMark` in
   `ConnectView.swift`; `LaunchBackground.colorset`; JS colours in
   `app.js`/`remote.js`; `splash.py` key colour.
3. Re-render `icon-1024.png`; update `robot.png` if the figure changed.
4. Eyes: three filled circles everywhere (section 1.1), no rings.
5. Text colours ≥ 4.5:1 (section 3); amber text via `--accent-text`.
6. Status colours stay green/orange/red.
7. Grep for old values (section 4), run the full test suite, and list every
   touched brand file in the commit message.
