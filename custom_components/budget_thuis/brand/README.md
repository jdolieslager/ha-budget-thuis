# Brand assets

Home Assistant serves these to identify the integration (bundled brand images,
HA 2026.3+ Brands Proxy API — no `home-assistant/brands` PR needed).

| File | Source | Notes |
|------|--------|-------|
| `icon.png` / `icon@2x.png` | budgetthuis.nl `/images/logo.svg` | "BUDGET THUIS" wordmark centered on a square 256/512 canvas (HA shows the icon nearly everywhere; the text-less house mark was not recognizable) |
| `dark_icon.png` / `dark_icon@2x.png` | same wordmark | Color-only white recolor of the same squared wordmark |
| `logo.png` / `logo@2x.png` | budgetthuis.nl `/images/logo.svg` | Full "BUDGET THUIS" wordmark, landscape, transparent |
| `dark_logo.png` / `dark_logo@2x.png` | same wordmark | **Color-only** recolor to white for dark-mode legibility; shape unchanged |

All are the official Budget Thuis marks. The only modifications are layout
(centering the wordmark on a square canvas for the icons), the color-only
white dark-mode variants HA expects via the `dark_` prefix, and rendering the
letters as transparent knockouts instead of painted white (the source asset's
painted-white letters vanish on dark themes); the mark shapes themselves are
unaltered. They are used solely to identify the service this integration
connects to.

> **Trademark notice.** "Budget Thuis" and the Budget Thuis logo are trademarks
> of their respective owner. This is an unofficial, community-built integration,
> not affiliated with or endorsed by Budget Thuis. If the trademark owner objects
> to this use, the maintainer will remove the assets.

Requirements met: PNG, transparent, trimmed of empty space, square icons
(256/512), landscape logos (shortest side 256/512).

> Note: HACS' own download-panel list may still show "icon not available" for
> locally-bundled brands (it reads the HACS CDN) — the icon renders correctly
> inside Home Assistant. Tracked upstream: hacs/integration #5223.
