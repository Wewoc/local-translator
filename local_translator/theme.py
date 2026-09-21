"""
theme.py — LocalTranslate, color palette table (synced copy from Garmin Local
Archive's src/theme.py — Single Source of Truth stays in GLA)

This file is a manual copy of GLA's palette table. When the palette in GLA's
theme.py changes, re-copy the THEME_1..N dicts + _THEMES table below into this
file (nothing else needs to change here).

Unlike GLA's own theme.py, this file has NO active-theme resolution — there is
no local settings file to read from, because that concept is GLA-specific.
Instead, the FastAPI backend (app.py, GET /theme) serves the full _THEMES
table as JSON, and the frontend (static/app.js) lets the user pick a theme
from a dropdown and remembers the choice in the browser's localStorage. So the
"active theme" only ever exists client-side, per browser.

COLOR ROLES (same meaning in every theme, see GLA's theme.py for the full
explanation):
  bg0, bg, bg2, bg3 — background layers (deepest to raised)
  accent, accent2   — primary / hover
  text, text2       — main / secondary text
  green, yellow, red — success / warning / error
"""

THEME_1 = {
    "name":    "Monochrome + Rust Accent (default)",
    "bg0":     "#0a0b0c",
    "bg":      "#111214",
    "bg2":     "#191b1e",
    "bg3":     "#232629",
    "accent":  "#c76a3f",
    "accent2": "#9c5330",
    "text":    "#e8e9eb",
    "text2":   "#9a9ea3",
    "green":   "#4ecca3",
    "yellow":  "#f5c542",
    "red":     "#e94560",
}

THEME_2 = {
    "name":    "Violet (Legacy)",
    "bg0":     "#0a0a1a",
    "bg":      "#12101f",
    "bg2":     "#1a1729",
    "bg3":     "#231f38",
    "accent":  "#a259f7",
    "accent2": "#6e3fcf",
    "text":    "#eaeaea",
    "text2":   "#a0a0b0",
    "green":   "#4ecca3",
    "yellow":  "#f5a623",
    "red":     "#e94560",
}

THEME_3 = {
    "name":    "Amber & Copper",
    "bg0":     "#0d0a08",
    "bg":      "#161210",
    "bg2":     "#201a17",
    "bg3":     "#2b231e",
    "accent":  "#d98a3d",
    "accent2": "#b56c28",
    "text":    "#f0e6da",
    "text2":   "#b8a894",
    "green":   "#4ecca3",
    "yellow":  "#f5c542",
    "red":     "#e94560",
}

THEME_4 = {
    "name":    "Olive & Sand",
    "bg0":     "#0e0f0a",
    "bg":      "#14150f",
    "bg2":     "#1c1e15",
    "bg3":     "#262919",
    "accent":  "#a9b34d",
    "accent2": "#818c37",
    "text":    "#eeeadb",
    "text2":   "#aeaa96",
    "green":   "#4ecca3",
    "yellow":  "#f5c542",
    "red":     "#e94560",
}

THEME_5 = {
    "name":    "Toxic",
    "bg0":     "#0a0c0b",
    "bg":      "#121513",
    "bg2":     "#262c2a",
    "bg3":     "#3a423e",
    "accent":  "#7a9e00",
    "accent2": "#6b8c00",
    "text":    "#e4e8e5",
    "text2":   "#9aa39d",
    "green":   "#4ecca3",
    "yellow":  "#f5c542",
    "red":     "#e94560",
}

THEME_6 = {
    "name":    "Ice Blue",
    "bg0":     "#0a0c0b",
    "bg":      "#121513",
    "bg2":     "#262c2a",
    "bg3":     "#3a423e",
    "accent":  "#1f6488",
    "accent2": "#184e69",
    "text":    "#e4e8e5",
    "text2":   "#9aa39d",
    "green":   "#4ecca3",
    "yellow":  "#f5c542",
    "red":     "#e94560",
}

_THEMES = {
    1: THEME_1,
    2: THEME_2,
    3: THEME_3,
    4: THEME_4,
    5: THEME_5,
    6: THEME_6,
}
