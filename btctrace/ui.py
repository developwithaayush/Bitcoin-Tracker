"""Shared look for the btctrace pages -- Apple-derived design language.

One palette and one stylesheet, imported by every page, so the console and the wallet
simulator read as the same tool rather than two apps that happen to share a repo.

The system is Apple's: a white global nav over a parchment canvas, white cards at an
18px radius behind a single hairline, one Action Blue for every interactive element,
SF Pro's type ladder with negative tracking at display sizes, and exactly one shadow.
Two deliberate departures, both forced by what this page is:

  * Density. Apple's tiles are one per viewport; an alert worklist is a 400-row grid.
    The chassis is Apple, the section rhythm is a dashboard's -- 40px of air between
    sections rather than 80px.
  * Data colour. Apple ships no chart palette, and its rule "one accent, no second
    brand colour" governs interactive elements, not data. The severity ramp below is
    Apple's own accessible system palette (HIG "accessible colours on white"), which
    keeps the ramp inside the family while clearing 4.5:1 on the card surface.

No webfont is requested and no colour is inlined at a call site: the console has to
render identically on an air-gapped machine, and the two renderers that cannot read CSS
-- Altair and pyvis -- import their colours from the constants below.
"""

from __future__ import annotations

from contextlib import contextmanager
from html import escape as esc

import streamlit as st

# ---- Surface ----------------------------------------------------------------------
CANVAS = "#f5f5f7"        # parchment: the page ground
PANEL = "#ffffff"         # white: cards, panels, the dataframe grid
PANEL_2 = "#fafafc"       # pearl: the fill of a secondary button, lighter than parchment
NAV = "#ffffff"           # clean white: the global nav

# ---- Ink --------------------------------------------------------------------------
INK = "#1d1d1f"           # every headline and every paragraph
INK_2 = "#333333"         # secondary copy
# Apple's fine-print grey (#7a7a7a) drops under 4.5:1 on parchment, so captions use the
# secondary label tone instead and #7a7a7a is left for disabled text, which is exempt.
INK_3 = "#6e6e73"
INK_DISABLED = "#7a7a7a"

# ---- Accent -----------------------------------------------------------------------
# Action Blue is the single interactive colour: links, pills, focus. There is no second
# brand colour, and blue is never used to encode data.
ACCENT = "#0066cc"
ACCENT_FOCUS = "#0071e3"  # a hair brighter, for the keyboard focus ring only
ACCENT_DIM = "#f0f6ff"    # the ghost pill's press fill

# ---- Hairlines --------------------------------------------------------------------
LINE = "#e0e0e0"          # the 1px card and grid hairline
LINE_STRONG = "#d2d2d7"
GRID = "#f0f0f0"          # divider-soft: chart gridlines

# ---- Data colour ------------------------------------------------------------------
# Apple's accessible system palette. Ordered by lightness as well as hue, so the ramp
# survives greyscale and colour blindness.
RED = "#d70015"
SEVERITY = ((0.85, "Critical", "#d70015"), (0.70, "High", "#c93400"),
            (0.55, "Elevated", "#a05a00"), (0.0, "Low", "#6c6c70"))

# Entity colours are the console's data vocabulary, shared by the graph and the charts,
# so a colour means the same thing wherever it appears. Transactions are deliberately
# the one neutral: they are structural connectors between entities rather than entities
# under investigation, and they carry a text label plus a legend entry as relief.
NODE_COLOURS = {"wallet": ACCENT, "subject": RED, "tx": "#6c6c70", "ip": "#c93400"}
# Edges are lines, not text: they sit lighter than the nodes they join so the nodes stay
# the figure and the edges the ground.
# Money in is green and money out is red, the convention every block explorer uses and
# the one the P2P broadcast page reads in its tooltips. Both sit lighter than the nodes
# they join, so an out-edge never competes with the red of the subject wallet itself.
EDGE_COLOURS = {"broadcast": "#d9a05b", "in": "#6fbf8b", "out": "#e8867f"}
# The one data neutral: a series that is context rather than subject recedes to it.
NEUTRAL = "#c7c7cc"

# ---- Type -------------------------------------------------------------------------
# SF Pro is proprietary; Apple's own guidance puts the system stack first, which
# resolves to the real SF Pro on macOS and to Segoe UI on Windows. Substituting Inter
# would mean a Google Fonts request, and this console has to run air-gapped.
_TAIL = ('-apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", Roboto, '
         '"Helvetica Neue", Arial, sans-serif')
DISPLAY = f'"SF Pro Display", {_TAIL}'
SANS = f'"SF Pro Text", {_TAIL}'

# The one shadow in the system. It gives a rendered artifact weight; it is never used on
# a card, a button or text, where elevation comes from the surface change instead.
SHADOW = "rgba(0, 0, 0, 0.22) 3px 5px 30px 0"


def severity_of(risk: float) -> tuple[str, str]:
    for floor, name, colour in SEVERITY:
        if risk >= floor:
            return name, colour
    return SEVERITY[-1][1], SEVERITY[-1][2]


# Streamlit marks the collapsed sidebar with aria-expanded="false" on the element that
# carries this testid, which is the only hook for restyling the collapse.
SB = '[data-testid="stSidebar"]'

CSS = f"""
<style>
:root {{
  --bt-canvas: {CANVAS}; --bt-panel: {PANEL}; --bt-pearl: {PANEL_2}; --bt-nav: {NAV};
  --bt-ink: {INK}; --bt-ink-2: {INK_2}; --bt-ink-3: {INK_3};
  --bt-accent: {ACCENT}; --bt-accent-focus: {ACCENT_FOCUS}; --bt-accent-dim: {ACCENT_DIM};
  --bt-line: {LINE}; --bt-line-strong: {LINE_STRONG};
  --bt-display: {DISPLAY};
  --bt-sans: {SANS};
  --bt-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Segoe UI Mono", Menlo,
             Consolas, "Liberation Mono", monospace;
  /* 8px base. Structural layout snaps to 8/12/16/24/32; the sub-base values exist for
     typographic adjustment only. */
  --sp-xs: 8px; --sp-sm: 12px; --sp-md: 17px; --sp-lg: 24px; --sp-xl: 32px;
  --r-sm: 8px; --r-md: 11px; --r-lg: 18px; --r-pill: 9999px;
}}

html, body, [data-testid="stAppViewContainer"] {{
  font-family: var(--bt-sans); color: var(--bt-ink); background: var(--bt-canvas); }}
[data-testid="stAppViewContainer"] {{ background: var(--bt-canvas); }}

/* Streamlit ships a tall empty header bar; the global nav takes that space instead.
   The container's horizontal padding is pinned here so the nav can bleed to its edges
   by exactly the same amount. */
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
.block-container {{ padding: 0 var(--pad, 48px) 64px; max-width: 1440px; }}
@media (max-width: 1068px) {{ .block-container {{ --pad: 24px; }} }}
@media (max-width: 640px)  {{ .block-container {{ --pad: 16px; }} }}

/* ---- Global nav: 44px of clean white, edge to edge, on every page ---- */
.bt-nav {{ display: flex; align-items: center; gap: var(--sp-sm); height: 44px;
  margin: 0 calc(-1 * var(--pad, 48px)); padding: 0 var(--pad, 48px);
  background: var(--bt-nav); color: var(--bt-ink);
  border-bottom: 1px solid var(--bt-line); }}
.bt-nav .bt-word {{ font-family: var(--bt-display); font-size: 14px; font-weight: 600;
  letter-spacing: -0.224px; color: var(--bt-ink); }}
.bt-nav .bt-crumb {{ font-size: 12px; font-weight: 400; letter-spacing: -0.12px;
  color: var(--bt-ink-3); }}
/* Streamlit's own toolbar overflows the zero-height header and floats at the top right,
   on top of the nav. Give it dark glyphs so it reads against the white, and keep the
   nav's right cluster clear of it. */
.bt-nav .bt-right {{ margin-left: auto; display: flex; align-items: center;
  gap: var(--sp-sm); padding-right: 88px; }}
[data-testid="stToolbar"] {{ color: var(--bt-ink-2); }}
[data-testid="stToolbar"] svg {{ fill: var(--bt-ink-2); }}
/* Solid, not a gradient: the system has no decorative gradients. */
.bt-mark {{ width: 22px; height: 22px; border-radius: 6px; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--bt-accent); color: #fff;
  font-family: var(--bt-display); font-size: 12px; font-weight: 600; }}
.bt-tag {{ font-size: 12px; font-weight: 400; letter-spacing: -0.12px; color: var(--bt-ink-2);
  background: var(--bt-pearl); border: 1px solid var(--bt-line); border-radius: var(--r-pill); padding: 3px 10px;
  white-space: nowrap; }}

/* ---- Hero: the page's one display headline, on the parchment ground ---- */
.bt-hero {{ padding: 40px 0 32px; }}
.bt-hero h1 {{ font-family: var(--bt-display); font-size: 40px; font-weight: 600;
  line-height: 1.1; letter-spacing: -0.4px; color: var(--bt-ink); margin: 0; }}
.bt-hero p {{ font-size: 17px; font-weight: 400; line-height: 1.47; letter-spacing: -0.374px;
  color: var(--bt-ink-3); margin: 10px 0 0; max-width: 68ch; }}
@media (max-width: 1068px) {{ .bt-hero h1 {{ font-size: 34px; }} }}
@media (max-width: 640px)  {{ .bt-hero h1 {{ font-size: 28px; }} }}

/* ---- Headline figures: the store utility card, one per number ---- */
.bt-strip {{ display: grid; gap: 20px; margin: 0 0 var(--sp-xl);
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
.bt-strip > div {{ background: var(--bt-panel); border: 1px solid var(--bt-line);
  border-radius: var(--r-lg); padding: var(--sp-lg); }}
.bt-k {{ display: block; font-size: 14px; font-weight: 400; line-height: 1.43;
  letter-spacing: -0.224px; color: var(--bt-ink-3); margin-bottom: 6px; }}
.bt-v {{ display: block; font-family: var(--bt-display); font-size: 34px; font-weight: 600;
  line-height: 1.1; letter-spacing: -0.374px; color: var(--bt-ink);
  font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
.bt-v small {{ font-family: var(--bt-sans); font-size: 14px; font-weight: 400;
  letter-spacing: -0.224px; color: var(--bt-ink-3); }}
/* An address is a 42-character string, not a figure; it drops to the mono reading size
   rather than forcing the whole card row to the width of its longest value. */
.bt-v.bt-id {{ font-family: var(--bt-mono); font-size: 14px; font-weight: 400;
  line-height: 1.5; letter-spacing: 0; }}

/* ---- Severity bands: the shape of the worklist, as pills ---- */
.bt-bands {{ display: flex; flex-wrap: wrap; gap: var(--sp-xs); margin: 0 0 var(--sp-md); }}
.bt-band {{ display: inline-flex; align-items: center; gap: 8px; border-radius: var(--r-pill);
  padding: 7px 14px; font-size: 14px; letter-spacing: -0.224px; color: var(--bt-ink-2);
  background: var(--bt-panel); border: 1px solid var(--bt-line); }}
.bt-band b {{ font-weight: 600; color: var(--bt-ink); font-variant-numeric: tabular-nums; }}
.bt-band i {{ width: 8px; height: 8px; border-radius: 50%; flex: none; }}
.bt-band.zero {{ color: var(--bt-ink-3); }}
.bt-band.zero b {{ color: var(--bt-ink-3); }}

/* ---- Panels ---- */
.bt-panel {{ background: var(--bt-panel); border: 1px solid var(--bt-line);
  border-radius: var(--r-lg); padding: var(--sp-lg); height: 100%; }}
.bt-h {{ font-size: 14px; font-weight: 600; line-height: 1.29; letter-spacing: -0.224px;
  color: var(--bt-ink); margin: 0 0 var(--sp-sm); }}
.bt-h.bare {{ margin-bottom: 8px; }}
.bt-note {{ font-size: 14px; font-weight: 400; line-height: 1.43; letter-spacing: -0.224px;
  color: var(--bt-ink-3); margin: 0 0 var(--sp-sm); }}
.bt-quiet {{ color: var(--bt-ink-3); }}

/* ---- Card: the store-utility-card grammar, wrapped around live widgets ----
   Raw HTML cannot enclose Streamlit widgets, so the panel is drawn by marking the
   container from the inside and selecting it with :has(). The marker is a direct child
   of its own container's block and of no other, so an ancestor block cannot match it
   and nest a second border around the first. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .bt-card) {{
  background: var(--bt-panel); border: 1px solid var(--bt-line);
  border-radius: var(--r-lg); padding: var(--sp-lg); }}
[data-testid="stElementContainer"]:has(.bt-card) {{ display: none; }}
/* A rule inside a card, for the one place a card holds two readings. */
.bt-split {{ border-top: 1px solid var(--bt-line); margin: var(--sp-md) 0 var(--sp-sm); }}
/* A qualifier trailing a primary reading -- the caption size, never smaller. */
.bt-meta {{ font-size: 14px; letter-spacing: -0.224px; color: var(--bt-ink-3); }}
.bt-addr {{ font-family: var(--bt-mono); font-size: 17px; font-weight: 400;
  color: var(--bt-ink); word-break: break-all; line-height: 1.47; }}

.bt-dl {{ display: grid; grid-template-columns: 9rem 1fr; gap: 8px var(--sp-md);
  margin: var(--sp-md) 0 0; }}
.bt-dl dt {{ font-size: 14px; letter-spacing: -0.224px; color: var(--bt-ink-3); }}
.bt-dl dd {{ font-size: 14px; letter-spacing: -0.224px; color: var(--bt-ink); margin: 0;
  font-variant-numeric: tabular-nums; word-break: break-word; }}
.bt-dl dd.mono {{ font-family: var(--bt-mono); font-size: 13px; }}

/* ---- Chips, severity, legend ---- */
.bt-chip {{ display: inline-block; font-size: 14px; letter-spacing: -0.224px;
  padding: 5px 12px; margin: 0 8px 8px 0; border-radius: var(--r-pill);
  background: var(--bt-canvas); color: var(--bt-ink-2); border: 1px solid var(--bt-line); }}
.bt-sev {{ display: inline-flex; align-items: center; gap: 8px; font-size: 17px;
  font-weight: 600; letter-spacing: -0.374px; color: var(--bt-ink); }}
.bt-legend {{ display: flex; flex-wrap: wrap; gap: 8px var(--sp-lg); margin: 0 0 var(--sp-sm); }}
.bt-legend span {{ display: inline-flex; align-items: center; gap: 8px; font-size: 14px;
  letter-spacing: -0.224px; color: var(--bt-ink-2); }}
.bt-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }}

/* ---- Streamlit widgets ---- */
[data-testid="stSidebar"] {{ background: var(--bt-panel);
  border-right: 1px solid var(--bt-line); }}
[data-testid="stSidebar"] .block-container {{ padding-top: var(--sp-xl); }}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{ padding-top: var(--sp-lg); }}

/* ---- Collapsed sidebar: a 64px rail, not a disappearance ----
   Streamlit collapses by setting the sidebar to min/max-width 0 and translating it off
   screen; overriding both leaves a rail. The filters are meaningless at 64px and are
   dropped, but navigation is not -- losing the way to the other page is what makes the
   default collapse feel like the app went away. */
{SB}[aria-expanded="false"] {{
  min-width: 64px !important; max-width: 64px !important;
  transform: none !important; visibility: visible !important;
  border-right: 1px solid var(--bt-line);
  /* Streamlit gives the sidebar z-index header+1, so a rail with real width paints over
     the expander, which lives in the app header. Nothing overlaps the rail horizontally,
     so dropping it under the header costs nothing and puts the button back on top. */
  z-index: 0 !important; }}
{SB}[aria-expanded="false"] [data-testid="stSidebarContent"] {{
  width: 64px !important; min-width: 64px !important; overflow-x: hidden; }}
{SB}[aria-expanded="false"] [data-testid="stSidebarUserContent"] {{ display: none !important; }}
{SB}[aria-expanded="false"] [data-testid="stSidebarHeader"] {{ padding: 8px 0 !important; }}
/* Collapsing an already-collapsed sidebar is not an action; the float-out expander is. */
{SB}[aria-expanded="false"] [data-testid="stSidebarCollapseButton"] {{ display: none !important; }}
/* The top slot is reserved for the expander pinned below, so the first page icon does
   not sit underneath it. */
{SB}[aria-expanded="false"] [data-testid="stSidebarNav"] {{
  padding: 56px 0 8px !important; max-height: none !important; }}
/* A nav link renders as [icon wrapper][label], so the label is the second child and can
   be dropped outright rather than clipped. That matters for alignment: a clipped label
   still occupies the flex row and pushes the icon off centre. With it gone, the link is
   a 40px box centred in the 64px rail, which puts every icon's centre on the rail's
   midline -- the same midline the expander below is pinned to. */
{SB}[aria-expanded="false"] [data-testid="stSidebarNavLinkContainer"] {{
  padding: 0 !important; margin: 0 !important; }}
{SB}[aria-expanded="false"] [data-testid="stSidebarNavLink"] {{
  width: 40px; height: 40px; margin: 4px auto; padding: 0; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--r-sm); }}
{SB}[aria-expanded="false"] [data-testid="stSidebarNavLink"] > :nth-child(2) {{
  display: none !important; }}
/* Streamlit renders the expander inside the zero-height app header, so it floats loose
   at the top-left and reads as a stray control sitting beside the rail. Pinning it to
   the rail's reserved top slot makes it the rail's own button. It is only in the DOM
   while the sidebar is collapsed, so this needs no expanded-state guard. */
[data-testid="stExpandSidebarButton"] {{
  position: fixed !important; top: 10px !important; left: 12px !important;
  width: 40px; height: 40px; z-index: 1000;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--r-sm); transition: background 140ms ease; }}
[data-testid="stExpandSidebarButton"]:hover {{ background: var(--bt-pearl); }}
label, .stSlider label, .stCheckbox label {{ font-size: 14px !important;
  letter-spacing: -0.224px; color: var(--bt-ink-2) !important; }}

/* Tabs read as Apple's sub-nav: quiet labels, the active one in ink, a blue rule. */
.stTabs [data-baseweb="tab-list"] {{ gap: var(--sp-lg); border-bottom: 1px solid var(--bt-line);
  background: transparent; margin-bottom: var(--sp-lg); }}
.stTabs [data-baseweb="tab"] {{ height: 44px; padding: 0; font-size: 14px; font-weight: 400;
  letter-spacing: -0.224px; color: var(--bt-ink-3); }}
.stTabs [aria-selected="true"] {{ color: var(--bt-ink) !important; font-weight: 600; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--bt-accent); height: 2px; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}

[data-testid="stMetricValue"] {{ font-family: var(--bt-display); font-size: 34px;
  font-weight: 600; letter-spacing: -0.374px; color: var(--bt-ink); }}
[data-testid="stMetricLabel"] {{ font-size: 14px; letter-spacing: -0.224px;
  color: var(--bt-ink-3); }}
[data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
[data-testid="stDataFrame"] > div {{ border: 1px solid var(--bt-line);
  border-radius: var(--r-lg); overflow: hidden; }}

/* The link graph is this console's rendered artifact, so it takes the system's one
   shadow -- the same role Apple gives a product render resting on a surface. */
iframe {{ border-radius: var(--r-lg); background: var(--bt-panel); box-shadow: {SHADOW}; }}

/* ---- Buttons: two grammars, and nothing between them ----
   Primary is the blue pill. Everything else is the pearl capsule at 11px. */
.stButton button, .stDownloadButton button, .stFormSubmitButton button {{
  font-family: var(--bt-sans); font-size: 14px; font-weight: 400; letter-spacing: -0.224px;
  min-height: 36px; padding: 8px 14px; border-radius: var(--r-md);
  background: var(--bt-pearl); color: var(--bt-ink-2);
  border: 1px solid var(--bt-line); box-shadow: none;
  transition: transform 120ms ease, background 120ms ease, border-color 120ms ease; }}
.stButton button:hover:not(:disabled), .stDownloadButton button:hover:not(:disabled) {{
  background: #fff; border-color: var(--bt-line-strong); color: var(--bt-ink); }}
/* The system-wide press: scale, never a colour change. */
.stButton button:active:not(:disabled), .stDownloadButton button:active:not(:disabled),
[data-testid="stBaseButton-primary"]:active:not(:disabled) {{ transform: scale(0.95); }}
.stButton button:disabled, .stDownloadButton button:disabled {{
  color: {INK_DISABLED}; background: var(--bt-pearl); opacity: 1; }}

.stButton button[kind="primary"], [data-testid="stBaseButton-primary"] {{
  background: var(--bt-accent); border: 1px solid var(--bt-accent); color: #fff;
  border-radius: var(--r-pill); padding: 11px 22px; font-size: 17px; line-height: 1;
  letter-spacing: -0.374px; }}
.stButton button[kind="primary"]:hover:not(:disabled),
[data-testid="stBaseButton-primary"]:hover:not(:disabled) {{
  background: var(--bt-accent-focus); border-color: var(--bt-accent-focus); color: #fff; }}
/* Export is the second CTA on its row, so it takes the ghost pill. */
.stDownloadButton button {{ background: transparent; color: var(--bt-accent);
  border: 1px solid var(--bt-accent); border-radius: var(--r-pill); padding: 11px 22px; }}
.stDownloadButton button:hover:not(:disabled) {{ background: var(--bt-accent-dim);
  color: var(--bt-accent); border-color: var(--bt-accent); }}

:where(.stButton, .stDownloadButton) button:focus-visible,
:focus-visible {{ outline: 2px solid var(--bt-accent-focus); outline-offset: 2px; }}

/* Inputs carry the pill grammar too -- search is a pill on Apple's own surfaces. */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {{
  background: var(--bt-panel) !important; border-radius: var(--r-pill) !important;
  border: 1px solid rgba(0, 0, 0, 0.08) !important; }}
[data-baseweb="input"] input, [data-baseweb="base-input"] input {{
  font-size: 14px; color: var(--bt-ink) !important; }}
[data-testid="stCode"], pre {{ border-radius: var(--r-lg); background: var(--bt-panel) !important;
  border: 1px solid var(--bt-line); }}
[data-testid="stAlert"] {{ border-radius: var(--r-lg); border: 1px solid var(--bt-line);
  background: var(--bt-panel); font-size: 14px; letter-spacing: -0.224px; }}
[data-testid="stCaptionContainer"], .stCaption {{ font-size: 12px; letter-spacing: -0.12px;
  color: var(--bt-ink-2); }}
hr {{ margin: var(--sp-xl) 0; border-color: var(--bt-line); }}
a {{ color: var(--bt-accent); }}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition-duration: 1ms !important; animation-duration: 1ms !important; }}
}}
</style>
"""


def style() -> None:
    """Install the stylesheet. First call on every page, before anything renders."""
    st.markdown(CSS, unsafe_allow_html=True)


def bar(title: str, subtitle: str, tag: str = "", mark: str = "B") -> None:
    """The global nav and the page's one display headline.

    The nav is the same 44px white strip on every page and carries the product identity;
    the hero below it carries what this particular page is.
    """
    tag_html = f'<span class="bt-tag">{esc(tag)}</span>' if tag else ""
    st.markdown(
        f'<div class="bt-nav"><span class="bt-mark">{esc(mark)}</span>'
        f'<span class="bt-word">btctrace</span>'
        f'<span class="bt-crumb">{esc(title)}</span>'
        f'<span class="bt-right">{tag_html}</span></div>'
        f'<div class="bt-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>',
        unsafe_allow_html=True)


def cell(label: str, value: str, note: str = "", ident: bool = False) -> str:
    """One headline figure, as a card. `ident` marks a value that is a string, not a number."""
    tail = f" <small>{esc(note)}</small>" if note else ""
    return (f'<div><span class="bt-k">{esc(label)}</span>'
            f'<span class="bt-v{" bt-id" if ident else ""}">{esc(value)}{tail}</span></div>')


def strip(*cells: str) -> None:
    """A row of headline figures, one card each."""
    st.markdown(f'<div class="bt-strip">{"".join(cells)}</div>', unsafe_allow_html=True)


def bands(counts: dict[str, int]) -> None:
    """The severity make-up of a worklist, in the order an analyst works it.

    Empty bands stay on the row, greyed: "no Critical alerts" is itself the answer to
    the first question anyone asks of this screen, and a band that vanishes cannot say it.
    """
    html = "".join(
        f'<span class="bt-band{"" if counts.get(name) else " zero"}">'
        f'<i style="background:{colour}"></i><b>{counts.get(name, 0):,}</b> {name}</span>'
        for _, name, colour in SEVERITY)
    st.markdown(f'<div class="bt-bands">{html}</div>', unsafe_allow_html=True)


def head(text: str, bare: bool = False) -> None:
    """A section label. `bare` tightens the gap when live widgets follow immediately."""
    st.markdown(f'<p class="bt-h{" bare" if bare else ""}">{text}</p>', unsafe_allow_html=True)


def note(text: str, pad: bool = False) -> None:
    """An explanatory line under a section head, in the console's quiet voice.

    `pad` reserves two lines. Streamlit columns stack independently, so a one-line
    caption beside a two-line one knocks the next row of controls out of alignment.
    """
    height = ' style="min-height:2.9em"' if pad else ""
    st.markdown(f'<p class="bt-note"{height}>{text}</p>', unsafe_allow_html=True)


@contextmanager
def card(title: str = ""):
    """A white panel around live widgets. Use as `with card("Trade"):`."""
    box = st.container()
    with box:
        st.markdown('<span class="bt-card"></span>', unsafe_allow_html=True)
        if title:
            head(title)
        yield box


def _contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two #rrggbb colours."""
    def lum(h: str) -> float:
        ch = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        ch = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def demo() -> None:
    """Self-check: every ink and data colour is legible on every surface it lands on.

    Apple's palette is tuned for white; this console also paints text on the parchment
    canvas and on the pearl button, where a colour that passes on white can quietly fail.
    Disabled text is excluded -- WCAG exempts it, and it is the one tone that may recede.
    """
    surfaces = {"white": PANEL, "parchment": CANVAS, "pearl": PANEL_2}
    text = {"INK": INK, "INK_2": INK_2, "INK_3": INK_3, "ACCENT": ACCENT, "RED": RED}
    text |= {name: colour for _, name, colour in SEVERITY}
    text |= {f"node {k}": v for k, v in NODE_COLOURS.items()}
    worst = min(((_contrast(c, s), f"{n} on {sn}") for n, c in text.items()
                 for sn, s in surfaces.items()), key=lambda p: p[0])
    assert worst[0] >= 4.5, f"{worst[1]} is only {worst[0]:.2f}:1, below WCAG AA"
    # The primary pill and the nav invert the pairing, so neither is covered above.
    assert _contrast("#ffffff", ACCENT) >= 4.5, "primary pill label fails on Action Blue"
    assert _contrast(INK, NAV) >= 4.5, "nav text fails on white"
    assert _contrast(INK_3, NAV) >= 4.5, "nav secondary text fails on white"
    print(f"PASS  {len(text)} colours on {len(surfaces)} surfaces, "
          f"worst {worst[1]} at {worst[0]:.2f}:1")


if __name__ == "__main__":
    demo()
