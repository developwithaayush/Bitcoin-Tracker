"""P2P broadcast -- how a transaction reaches the network, and what btctrace records.

A transaction is not sent to a bank. It is handed to a handful of peers, each of which
hands it on, until every node has it. This page replays that gossip for one transaction
so the network layer of the project has a picture behind it.

What is real: the transaction, its amount, and the source IP, country and ASN of the node
that announced it -- those come from the corpus (or from the wallet simulator) and are
exactly what the network features are built on. What is simulated: the peers it relayed
through and the timing, because a packet capture records the announcement it saw, not the
route the gossip took. The page says so on the page rather than only in this docstring.
"""

from __future__ import annotations

import json
import math
import random
from html import escape as esc
from pathlib import Path

import pandas as pd
import streamlit as st

from btctrace.generate import NETWORKS, Generator
from btctrace.live import REAL_NODES, REAL_TXS
from btctrace.ui import (INK, INK_3, LINE, NEUTRAL, PANEL, RED, bar, card, cell, note,
                        strip, style)

DATA = Path("data")
RINGS = (4, 5, 4)              # peers per hop; 13 peers plus the origin fills the frame
RADII = (0, 130, 225, 305)
HOP_MS = (40, 190)             # relay delay drawn per edge, in milliseconds
IDLE_LINKS = 7                 # peer links the announcement did not travel over
# Real propagation is over in well under a second, which on a projector is a flicker.
# The figures quoted on the page stay in real milliseconds; only the replay is slowed.
SLOWDOWN = 7
# Money in green, money out red, the same vocabulary the console's link graph uses
# (ui.EDGE_COLOURS). These are the darker text-weight tones of that pair: an edge only
# has to be seen, a label has to be read on white.
IN, OUT = "#248a3d", RED
TIP_W, TIP_LINE, LEG_CAP = 320, 16, 4

# SVG has no native zoom and a pan/zoom library would mean a CDN request this project
# cannot make, so the viewBox is driven directly. This is the only script on the page.
ZOOM_JS = """
const svg = document.querySelector('svg');
const home = {x: 0, y: 0, w: __W__, h: __H__};
let vb = {...home}, drag = null;
const apply = () => svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  const mx = vb.x + (e.clientX - r.left) / r.width * vb.w;
  const my = vb.y + (e.clientY - r.top) / r.height * vb.h;
  const k = Math.min(Math.max(vb.w * (e.deltaY < 0 ? 0.85 : 1 / 0.85), home.w / 6),
                     home.w * 2) / vb.w;
  vb = {x: mx - (mx - vb.x) * k, y: my - (my - vb.y) * k, w: vb.w * k, h: vb.h * k};
  apply();
}, {passive: false});
svg.addEventListener('pointerdown', e => {
  drag = {x: e.clientX, y: e.clientY, vx: vb.x, vy: vb.y};
  svg.classList.add('dragging');
  svg.setPointerCapture(e.pointerId);
});
svg.addEventListener('pointermove', e => {
  if (!drag) return;
  const r = svg.getBoundingClientRect();
  vb.x = drag.vx - (e.clientX - drag.x) / r.width * vb.w;
  vb.y = drag.vy - (e.clientY - drag.y) / r.height * vb.h;
  apply();
});
for (const ev of ['pointerup', 'pointercancel', 'pointerleave'])
  svg.addEventListener(ev, () => { drag = null; svg.classList.remove('dragging'); });
svg.addEventListener('dblclick', () => { vb = {...home}; apply(); });
"""

FRAME_H = 620

CSS = """
@keyframes bt-arrive { from { opacity: 0; transform: scale(.4); }
                       to   { opacity: 1; transform: scale(1); } }
@keyframes bt-ping   { from { r: 10; opacity: .55; } to { r: 42; opacity: 0; } }
@keyframes bt-relay  { to { stroke-dashoffset: 0; } }
.bt-p2p .peer  { animation: bt-arrive .45s ease-out both; transform-origin: center;
                 transform-box: fill-box; }
.bt-p2p .ping  { animation: bt-ping 1.1s ease-out both; fill: none; stroke-width: 2; }
.bt-p2p .relay { stroke-dasharray: 1; stroke-dashoffset: 1; animation-name: bt-relay;
                 animation-timing-function: linear; animation-fill-mode: both; }
.bt-p2p text   { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                 font-size: 11px; }
/* Only the hit target takes the pointer: the drawn circles, labels and packets must not
   steal the hover from the node they belong to. */
.bt-p2p .peer, .bt-p2p .ping, .bt-p2p .relay, .bt-p2p .packet { pointer-events: none; }
.bt-p2p .hit  { fill: transparent; cursor: pointer; }
.bt-p2p .idle { stroke-width: 1; stroke-dasharray: 3 4; pointer-events: none; }
svg { cursor: grab; touch-action: none; }
svg.dragging { cursor: grabbing; }
.bt-p2p .tip  { opacity: 0; transition: opacity .12s ease; pointer-events: none; }
.bt-p2p .node:hover .tip { opacity: 1; }
"""


@st.cache_data(ttl=120)
def live_nodes() -> list[tuple[str, str, int]]:
    """Addresses of real Bitcoin nodes, if a capture has been pulled."""
    if not REAL_NODES.exists():
        return []
    import csv

    with REAL_NODES.open(encoding="utf-8") as fh:
        return [(r["ip"], r["country"], int(r["asn"])) for r in csv.DictReader(fh)]


@st.cache_data(ttl=120)
def live_txs() -> list[dict]:
    """Real mainnet transactions pulled from a public Esplora mirror."""
    if not REAL_TXS.exists():
        return []
    return [{"txid": t["txid"], "timestamp": t["seen_at"],
             "amount": sum(a for _, a in t["outputs"]),
             "n_in": len(t["inputs"]), "n_out": len(t["outputs"]),
             "inputs": [(a, v) for a, v in t["inputs"]],
             "outputs": [(a, v) for a, v in t["outputs"]],
             "fee": t["fee"], "confirmed": t["confirmed"], "real": True,
             "origin": "live mainnet"} for t in json.loads(REAL_TXS.read_text("utf-8"))]


def sources() -> list[dict]:
    """Transactions to choose from: the live wallet if the simulator has one, else the corpus.

    Session state is shared across pages, so a transaction just emitted next door is
    already here -- no file to write and re-read.
    """
    live = live_txs()
    if live:
        # A real transaction carries no announcing IP -- nothing public does. The origin
        # shown is a real node, chosen deterministically per txid, and the page says so.
        pool = live_nodes()
        for t in live:
            ip, country, asn = (pool[int(t["txid"][:8], 16) % len(pool)] if pool
                                else ("0.0.0.0", "??", 0))
            t.update(src_ip=ip, geo_country=country, asn=asn)
        return live
    w = st.session_state.get("wallet")
    if w is not None and getattr(w, "records", None):
        return [{"txid": r["txid"], "timestamp": r["timestamp"],
                 "amount": sum(r["output_amounts"]), "src_ip": r["src_ip"],
                 "geo_country": r["geo_country"], "asn": r["asn"],
                 "n_in": len(r["input_amounts"]), "n_out": len(r["output_amounts"]),
                 "inputs": list(zip(r["input_addresses"], r["input_amounts"])),
                 "outputs": list(zip(r["output_addresses"], r["output_amounts"])),
                 "origin": "your wallet"} for r in reversed(w.records)]
    path = DATA / "canonical.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path).tail(40).iloc[::-1]
    return [{"txid": r.txid, "timestamp": int(r.timestamp.timestamp()),
             "amount": float(sum(r.output_amounts)), "src_ip": r.src_ip,
             "geo_country": r.geo_country, "asn": r.asn,
             "n_in": len(r.input_amounts), "n_out": len(r.output_amounts),
             "inputs": [(a, float(v)) for a, v in zip(r.input_addresses, r.input_amounts)],
             "outputs": [(a, float(v)) for a, v in zip(r.output_addresses, r.output_amounts)],
             "origin": "corpus"} for r in df.itertuples(index=False)]


def gossip(tx: dict, pool: list | None = None) -> tuple[list[dict], list[tuple[int, int]]]:
    """Lay the origin and its peers out in rings, one ring per hop, and time the relays.

    Seeded from the txid, so replaying the same transaction draws the same network -- a
    demo that reshuffles under the judges' eyes looks like noise rather than a mechanism.
    """
    rng = random.Random(int(tx["txid"][:12], 16))
    gen = Generator(seed=rng.randrange(1 << 30))
    nodes = [{"ip": tx["src_ip"], "country": tx["geo_country"], "asn": tx["asn"],
              "hop": 0, "delay": 0, "x": 0.0, "y": 0.0, "parent": None}]
    previous = [0]
    for hop, count in enumerate(RINGS, start=1):
        ring, base = [], rng.uniform(0, math.tau)
        for i in range(count):
            ip, country, asn = (tuple(rng.choice(pool)) if pool
                                else gen._ip(rng.choice(NETWORKS)))
            angle = base + i * math.tau / count + rng.uniform(-0.18, 0.18)
            radius = RADII[hop] * rng.uniform(0.92, 1.08)
            parent = rng.choice(previous)
            ring.append(len(nodes))
            nodes.append({
                "ip": ip, "country": country, "asn": asn, "hop": hop,
                "delay": nodes[parent]["delay"] + rng.randint(*HOP_MS),
                "x": radius * math.cos(angle), "y": radius * math.sin(angle),
                "parent": parent,
            })
        previous = ring

    # A node keeps many connections open and hears a transaction over exactly one of
    # them. Drawing a few of the others is what makes "connected peers" mean anything,
    # and the question they raise -- why did nothing travel along this link? -- has the
    # right answer: that peer already had it.
    idle, pairs = [], set()
    for _ in range(IDLE_LINKS * 4):
        if len(idle) == IDLE_LINKS:
            break
        a, b = rng.sample(range(1, len(nodes)), 2)
        key = (min(a, b), max(a, b))
        if key in pairs or nodes[a]["parent"] == b or nodes[b]["parent"] == a:
            continue
        if abs(nodes[a]["hop"] - nodes[b]["hop"]) > 1:
            continue                       # keep the mesh local, or it crosses the frame
        pairs.add(key)
        idle.append(key)
    return nodes, idle


def tooltip(n: dict, nodes: list[dict], tx: dict, links: dict, x: float, y: float,
            w: int, h: int) -> str:
    """The node, its hit target, and the payment it is carrying, shown on hover.

    Every peer relays the same transaction, so the legs are the same wherever you hover;
    what changes per node is when it heard the announcement and from whom. Inputs are the
    money going in, outputs the money coming out.
    """
    lines: list[tuple[str, str, str]] = [
        (f'{n["ip"]} · {n["country"]} · AS{n["asn"]}', INK, ""),
        ("announced this transaction" if n["parent"] is None
         else f'received it {n["delay"]} ms in, from {nodes[n["parent"]]["ip"]}', INK_3, ""),
        (f'{links["degree"]} connected peers · relayed it on to {links["relayed"]}',
         INK_3, ""),
        ("", INK, ""),
    ]
    for title, legs, colour in (("IN", tx["inputs"], IN), ("OUT", tx["outputs"], OUT)):
        total = sum(a for _, a in legs)
        lines.append((f'{title}  {len(legs)} · {total:.8f} BTC', colour, "b"))
        for addr, amt in legs[:LEG_CAP]:
            lines.append((f'{addr[:20]}…|{amt:.8f}', colour, ""))
        if len(legs) > LEG_CAP:
            lines.append((f'+{len(legs) - LEG_CAP} more', colour, ""))

    th = 14 + TIP_LINE * len(lines)
    tx0 = x + 22 if x < w / 2 else x - 22 - TIP_W
    tx0 = min(max(tx0, 8), w - TIP_W - 8)
    ty0 = min(max(y - th / 2, 8), h - th - 8)

    body = [f'<rect x="0" y="0" width="{TIP_W}" height="{th}" rx="10" fill="{PANEL}" '
            f'stroke="{LINE}"/>']
    for i, (text, colour, weight) in enumerate(lines):
        if not text:
            continue
        ty = 22 + i * TIP_LINE
        left, _, right = text.partition("|")
        bold = ' font-weight="600"' if weight else ""
        body.append(f'<text x="12" y="{ty}" fill="{colour}"{bold}>{esc(left)}</text>')
        if right:
            body.append(f'<text x="{TIP_W - 12}" y="{ty}" text-anchor="end" '
                        f'fill="{colour}">{esc(right)}</text>')
    return (f'<g class="node"><circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="20"/>'
            f'<g class="tip" transform="translate({tx0:.1f},{ty0:.1f})">'
            f'{"".join(body)}</g></g>')


def svg(nodes: list[dict], idle: list[tuple[int, int]], tx: dict, run: int) -> str:
    """One inline SVG. Every node and edge carries its own animation-delay, so the ripple
    plays in the browser -- no rerun loop, no frames pushed from the server."""
    w, h = 940, 620
    cx, cy = w / 2, h / 2
    parts = []
    degree = {i: 0 for i in range(len(nodes))}
    relayed = {i: 0 for i in range(len(nodes))}
    for i, n in enumerate(nodes):
        if n["parent"] is not None:
            degree[i] += 1
            degree[n["parent"]] += 1
            relayed[n["parent"]] += 1
    for a, b in idle:
        degree[a] += 1
        degree[b] += 1
        parts.append(
            f'<line class="idle" x1="{cx + nodes[a]["x"]:.1f}" y1="{cy + nodes[a]["y"]:.1f}" '
            f'x2="{cx + nodes[b]["x"]:.1f}" y2="{cy + nodes[b]["y"]:.1f}" stroke="{LINE}"/>')
    for n in nodes:
        if n["parent"] is None:
            continue
        p = nodes[n["parent"]]
        x1, y1 = cx + p["x"], cy + p["y"]
        x2, y2 = cx + n["x"], cy + n["y"]
        begin, travel = p["delay"] * SLOWDOWN, (n["delay"] - p["delay"]) * SLOWDOWN
        # The link lights up as the announcement crosses it, and a packet rides along the
        # same line -- animateMotion is the SVG feature for exactly this, so there is no
        # JavaScript and nothing to keep in sync from the server.
        parts.append(
            f'<line class="relay" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{LINE}" stroke-width="1.4" pathLength="1" '
            f'style="animation-delay:{begin}ms;animation-duration:{travel}ms"/>'
            f'<circle class="packet" r="5" fill="{RED}" opacity="0">'
            f'<animateMotion begin="{begin}ms" dur="{travel}ms" '
            f'path="M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"/>'
            f'<animate attributeName="opacity" begin="{begin}ms" dur="{travel}ms" '
            f'values="1;1;0"/></circle>')
    for n in nodes:
        x, y = cx + n["x"], cy + n["y"]
        origin = n["parent"] is None
        colour = RED if origin else NEUTRAL
        parts.append(
            f'<circle class="ping" cx="{x:.1f}" cy="{y:.1f}" stroke="{colour}" '
            f'style="animation-delay:{n["delay"] * SLOWDOWN}ms"/>'
            f'<circle class="peer" cx="{x:.1f}" cy="{y:.1f}" r="{9 if origin else 6}" '
            f'fill="{colour}" style="animation-delay:{n["delay"] * SLOWDOWN}ms"/>'
            f'<text class="peer" x="{x:.1f}" y="{y - 14:.1f}" text-anchor="middle" '
            f'fill="{INK if origin else INK_3}" style="animation-delay:{n["delay"] * SLOWDOWN}ms">'
            f'{esc(n["country"])} · AS{esc(str(n["asn"]))}</text>'
            f'<text class="peer" x="{x:.1f}" y="{y + 22:.1f}" text-anchor="middle" '
            f'fill="{INK_3}" style="animation-delay:{n["delay"] * SLOWDOWN}ms">'
            f'{esc(n["ip"])}{" · origin" if origin else ""}</text>')
    # A projector audience reads the picture before anyone explains it, so the frame
    # names its own parts.
    key = [(RED, "origin: the node that announced it"),
           (RED, "the announcement, in flight"),
           (NEUTRAL, "peer holding the transaction")]
    for i, (colour, label) in enumerate(key):
        ky = 26 + i * 20
        parts.append(
            f'<circle cx="24" cy="{ky - 4}" r="{5 if i else 7}" fill="{colour}"'
            f'{" opacity=\".55\"" if i == 1 else ""}/>'
            f'<text x="40" y="{ky}" fill="{INK_3}">{esc(label)}</text>')
    parts.append(
        f'<text x="16" y="{h - 38}" fill="{INK_3}">hover a node for the payment it '
        f'carries — <tspan fill="{IN}">inputs green</tspan>, '
        f'<tspan fill="{OUT}">outputs red</tspan> — and its peer count</text>'
        f'<text x="16" y="{h - 18}" fill="{INK_3}">scroll to zoom · drag to pan · '
        f'double-click to reset · a faint link is a peer that already had it</text>')

    # Hover targets and their tooltips come last, so a tooltip paints over the nodes it
    # explains rather than under them -- SVG has no z-index, only document order.
    for i, n in enumerate(nodes):
        parts.append(tooltip(n, nodes, tx, {"degree": degree[i], "relayed": relayed[i]},
                             cx + n["x"], cy + n["y"], w, h))
    # Rendered into its own frame rather than into the page: st.markdown reuses the
    # same DOM node between reruns, so the SVG timeline survives and Replay does nothing.
    # A frame whose document changes is reloaded, which restarts every animation on it.
    return (f'<!doctype html><meta charset="utf-8"><title>p2p {run}</title>'
            f'<style>html,body{{margin:0;height:100%;background:{PANEL}}}{CSS}</style>'
            f'<div class="bt-p2p" style="height:100%">'
            f'<svg viewBox="0 0 {w} {h}" width="100%" height="100%" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg></div>'
            f'<script>{ZOOM_JS.replace("__W__", str(w)).replace("__H__", str(h))}</script>')


style()

bar("P2P broadcast",
    "A transaction is announced to a few peers, and they relay it on until the whole "
    "network holds it. This replays that spread for one transaction, and marks the node "
    "whose IP btctrace actually recorded -- the anchor for every network feature.",
    tag="network", mark="P")

txs = sources()
if not txs:
    st.info("No transactions to replay. Emit some on the Wallet simulator page, or run "
            "the demo pipeline to build data/canonical.parquet.")
    st.stop()

labels = [f'{t["txid"][:12]}…  ·  {t["amount"]:.4f} BTC  ·  '
          f'{t["geo_country"]} · AS{t["asn"]}' for t in txs]
real = txs[0].get("real", False)
pick, replay, refresh = st.columns([4, 1, 1], vertical_alignment="bottom")
choice = pick.selectbox(f"Transaction ({txs[0]['origin']})", range(len(txs)),
                        format_func=lambda i: labels[i])
# Re-rendering with a different marker replaces the SVG node, and the browser restarts
# every animation on it. That is the whole replay mechanism.
if replay.button("Replay", width="stretch", type="primary"):
    st.session_state.p2p_run = st.session_state.get("p2p_run", 0) + 1
# Pulling live in front of an audience is the point: these transactions are in the
# mempool right now and can be checked on any explorer while the page is open.
if refresh.button("Fetch live", width="stretch",
                  help="Pull fresh unconfirmed transactions and node addresses off the "
                       "real Bitcoin network."):
    from btctrace.live import real_nodes, real_transactions
    with st.spinner("Reading the live mempool and resolving node addresses…"):
        try:
            got = real_transactions(20)
            real_nodes(30)
            live_txs.clear()
            live_nodes.clear()
            st.session_state.p2p_run = st.session_state.get("p2p_run", 0) + 1
            st.success(f'Pulled {got["transactions"]} live transactions.')
            st.rerun()
        except Exception as exc:
            st.error(f"Live fetch failed: {exc}")

tx = txs[choice]
nodes, idle = gossip(tx, live_nodes())
last = max(n["delay"] for n in nodes)

strip(
    cell("Amount", f'{tx["amount"]:.4f}', "BTC"),
    cell("Shape", f'{tx["n_in"]} in / {tx["n_out"]} out'),
    cell("Nodes reached", f"{len(nodes)}"),
    cell("Full propagation", f"{last / 1000:.2f}", "s"),
    cell("Announced by" if not real else "Origin node (modelled)",
         f'{tx["src_ip"]} · {tx["geo_country"]} · AS{tx["asn"]}', ident=True),
)

with card("Gossip"):
    note("The red node announced the transaction. Each ring is one hop of relay: a peer "
         "that has it tells the peers it is connected to, and within a couple of seconds "
         "every node on the network holds the same transaction. No server sits in the "
         "middle -- that is what peer-to-peer means, and it is why the only thing an "
         "observer can record is which host it heard the announcement from. Hover any "
         "node to see the payment it is carrying: inputs in green, outputs in red.")
    st.iframe(svg(nodes, idle, tx, st.session_state.get("p2p_run", 0)), height=FRAME_H)
    note(f'Transaction {tx["txid"]} · replayed at {SLOWDOWN}× slower than the '
         f'modelled timing, which finishes in {last} ms.', pad=True)

left, right = st.columns([1, 1], gap="large")

with left, card("Relay order"):
    note("Who received it when, in the order the gossip reached them.")
    st.dataframe(
        pd.DataFrame([{"hop": n["hop"], "at (ms)": n["delay"], "peer": n["ip"],
                       "country": n["country"], "ASN": n["asn"]}
                      for n in sorted(nodes, key=lambda n: n["delay"])]),
        hide_index=True, width="stretch", height=330,
        column_config={"peer": st.column_config.TextColumn(width="medium")})

with right, card("What is real here"):
    if real:
        note("This transaction is real and unconfirmed on Bitcoin mainnet: the txid, the "
             "addresses, the amounts and the fee came from a public Esplora mirror "
             "minutes ago. Paste the hash into any block explorer and it is there.")
        note("The nodes are real too -- their addresses come from the Bitcoin DNS seeds, "
             "the same bootstrap every new node uses, with country and ASN resolved per "
             "IP. These machines are running the network right now.", pad=True)
        note("Modelled: which node announced it, and the relay timings. No public source "
             "publishes that, and this network resets any connection carrying the "
             "Bitcoin protocol -- `python -m btctrace.live --mode probe` demonstrates it "
             "packet by packet. On an unfiltered link, --mode p2p captures the real "
             "announcements instead.", pad=True)
    else:
        note("Real: the transaction, its amount, and the announcing host's IP, country "
             "and ASN, straight from the corpus or from a wallet you used next door.")
        note("Modelled: the peers and the timings. A capture records the announcement a "
             "sensor saw, never the path the gossip took.", pad=True)
    st.markdown(
        '<dl class="bt-dl">'
        f'<dt>Announcing IP</dt><dd class="mono">{esc(tx["src_ip"])}</dd>'
        f'<dt>Country / ASN</dt><dd>{esc(tx["geo_country"])} &middot; '
        f'AS{esc(str(tx["asn"]))}</dd>'
        f'<dt>Broadcast at</dt><dd>'
        f'{pd.to_datetime(tx["timestamp"], unit="s").strftime("%d %b %Y %H:%M:%S")} UTC</dd>'
        f'<dt>Hops drawn</dt><dd>{len(RINGS)}</dd>'
        '</dl>', unsafe_allow_html=True)
    note("This is the layer the console correlates against the chain: many unrelated "
         "wallets announcing from one host is what ip_cohort_components measures, and "
         "one wallet announcing from five countries in a day is what geo_hop_rate "
         "measures. Neither signal exists in the chain data alone.", pad=True)
