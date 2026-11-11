VER3 Light UI assets
==========================
Source: project Figma Design CCtz444SPLckGcKKuYgUi3, VER3 page 351:2.
Brand: 776:50072. Sidebar: 776:50071. Header: 357:5142.
Original icon states are reused from the V3/Sidebar component instances:
362:6627, 362:6975, 469:14950, 362:7888, 475:16956,
362:7733, 362:8389, 362:8700.
Files preserve original asset filenames and artwork. No temporary asset URLs
are used at runtime.

Sidebar brand update (approved option B, 2026-11-09):
setron-logo.png is the approved spacing-v2 transparent SETRON wordmark in SEMES blue,
including the SEMES TRACKING MONITORING subtitle. The sidebar uses this
wordmark alone, with no standalone SEMES logo, divider, or mascot. The supplied
PNG is preserved; only transparent margins are excluded at display time.
Source: output/branding/setron-logo-wider-spacing-20261109-v2.png (workspace).
Dark mode renders that same artwork in white
using its original alpha, so letter spacing and geometry exactly match light mode.
No backing plate is added. Both use the same reduced 128 x 36 logical-pixel slot,
centered horizontally in the sidebar, including centering the rendered artwork
inside the transparent slot, and preserving the aspect ratio.
The original semes-logo.png remains available but is not shown in the sidebar.

setron-window-icon.svg is the approved Blue Tile S for the native main window
title bar. It is separate from the sidebar wordmark and keeps its brand colors
at all display sizes and in both app themes.

Task 4 dashboard additions (original artwork and root geometry preserved):
KPI row 781:12122: 32ac4.svg, bbc12.svg, 60dbd.svg, e53b6.svg.
Panel heading 357:5496: 7880d.svg.
Matrix risk signal 357:5551: 5ced8.svg.
Charts are rendered dynamically from data with QtCharts; prototype chart SVGs
and screenshot backgrounds are not runtime assets.

Task 5 heatmap reuses the same original KPI assets and 7880d.svg from
VER3 panel heading 357:6382. Tiles and family segments are native widgets
driven by the shared snapshot, not screenshots or modified SVG artwork.

Task 6 equipment list 419:14163 and detail 499:24803 reuse existing KPI,
brand and navigation assets with original geometry. Tables are native Qt
model/views; no screenshot background or altered icon is used.

Fonts from Google Fonts' official repository:
https://github.com/google/fonts/tree/main/ofl/ibmplexsanskr
https://github.com/google/fonts/tree/main/ofl/inter
Inter variable TTF is stored as Inter.ttf. The full SIL Open Font Licenses
are bundled in fonts/IBM-OFL.txt and fonts/Inter-OFL.txt.
