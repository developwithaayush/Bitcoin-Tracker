"""Wallet simulator -- a live feed into the btctrace console.

Buy and sell to build an ordinary wallet, or run a preset to act out a laundering
pattern, then push the result through the real pipeline and watch where the detector
ranks you. Nothing here is a special case inside btctrace: the wallet writes ordinary
records to data/raw/live.csv and ingest picks them up like any other data drop.
"""

from __future__ import annotations

from html import escape as esc
from pathlib import Path

import pandas as pd
import streamlit as st

from btctrace.ui import bar, cell, head, severity_of, strip, style
from btctrace.wallet import LIVE_CSV, Wallet, rescore, save_records

DATA = Path("data")

PRESETS = {
    "Rapid pass-through": (
        "rapid_passthrough",
        "Receive four payments and forward every satoshi within the hour.",
        lambda w: w.rapid_passthrough(),
    ),
    "Peeling chain": (
        "peeling_chain",
        "Peel a small payment off at each of eight hops, carrying the remainder on.",
        lambda w: w.peeling_chain(),
    ),
    "Fan-in collection": (
        "ransomware_fanin",
        "Collect twelve near-identical payments from unrelated payers in one day.",
        lambda w: w.fanin_collection(),
    ),
    "Geo hopping": (
        "geo_hopping",
        "Broadcast the same wallet's spends from five countries inside a day.",
        lambda w: w.geo_hop(),
    ),
}


def corpus_end() -> int:
    """Anchor live activity just after the existing corpus, so the window stays coherent."""
    path = DATA / "canonical.parquet"
    if path.exists():
        df = pd.read_parquet(path, columns=["timestamp"])
        return int(df["timestamp"].max().timestamp()) + 3600 if len(df) else 0
    return int(pd.Timestamp.utcnow().timestamp())


def new_wallet() -> Wallet:
    return Wallet(start_ts=corpus_end())


def note(text: str, pad: bool = False) -> None:
    """Explanatory line under a section head, in the console's quiet voice.

    `pad` reserves two lines. Streamlit columns stack independently, so a one-line
    caption beside a two-line one would knock the next row of buttons out of alignment.
    """
    height = ' style="min-height:2.9em"' if pad else ""
    st.markdown(f'<p class="bt-note"{height}>{text}</p>', unsafe_allow_html=True)


style()

if "wallet" not in st.session_state:
    st.session_state.wallet = new_wallet()
w: Wallet = st.session_state.wallet

bar("Wallet simulator",
    "A demo account whose transactions reach the console through the real ingest and "
    "detection path. Trade like an ordinary user, or run a preset to act out a laundering "
    "pattern, then push and see where you rank.",
    tag="sandbox", mark="W")

strip(
    cell("Balance", f"{w.balance:.4f}", "BTC"),
    cell("Transactions emitted", f"{len(w.records):,}"),
    cell("Actions logged", f"{len(w.log):,}"),
    cell("Live feed", "loaded" if LIVE_CSV.exists() else "not pushed"),
    cell("Address", w.address, ident=True),
)

left, right = st.columns([1, 1], gap="large")

with left:
    head("Trade", bare=True)
    note("Ordinary activity. A wallet that only buys, sells and pays should stay "
         "unremarkable to the detector -- that contrast is the point of the demo.")
    amount_col, buy_col, sell_col, send_col = st.columns([2, 1, 1, 1],
                                                         vertical_alignment="bottom")
    amount = amount_col.number_input("Amount (BTC)", 0.0001, 100.0, 0.5, 0.1, format="%.4f")
    broke = amount > w.balance
    if buy_col.button("Buy", width="stretch", type="primary"):
        w.buy(amount)
        st.rerun()
    if sell_col.button("Sell", width="stretch", disabled=broke):
        w.sell(amount)
        st.rerun()
    if send_col.button("Send", width="stretch", disabled=broke):
        w.send(amount)
        st.rerun()
    if broke:
        note("Sell and Send need a balance -- buy some coin first.")

    st.markdown("")
    head("Demo behaviours", bare=True)
    note("Each one emits a burst of activity shaped like the named typology, so the "
         "detector has something it is supposed to catch.")
    # Two columns: four full-width buttons in a stack read as an undifferentiated list,
    # and the description belongs on the page rather than hidden in a hover tooltip.
    cols = st.columns(2, gap="medium")
    for i, (name, (tag, help_text, action)) in enumerate(PRESETS.items()):
        with cols[i % 2]:
            if st.button(name, width="stretch", key=f"p_{tag}"):
                action(w)
                st.rerun()
            note(esc(help_text), pad=True)

with right:
    head("Activity")
    if w.log:
        st.dataframe(pd.DataFrame(w.log)[["action", "detail", "balance"]],
                     hide_index=True, width="stretch", height=232,
                     column_config={
                         "action": st.column_config.TextColumn("action", width="small"),
                         "balance": st.column_config.NumberColumn("balance", format="%.4f"),
                     })
    else:
        st.info("No activity yet. Buy some coin, or run one of the demo behaviours.")

    head("Push to btctrace", bare=True)
    note("Re-ingests the full corpus and re-runs detection over every wallet. Takes about "
         "30 seconds: an anomaly score is a wallet's position in a population, so the "
         "population has to be scored with it.")

    push, reset = st.columns([2, 1], vertical_alignment="bottom")
    if push.button("Analyse now", type="primary", width="stretch", disabled=not w.records):
        with st.spinner("Ingesting, building features, scoring the population..."):
            save_records(w.records)
            out = rescore(DATA)
        alerts = out["alerts"].reset_index(drop=True)
        hit = alerts.index[alerts["address"] == w.address]
        st.session_state.result = {
            "rank": int(hit[0]) + 1 if len(hit) else None,
            "total": len(alerts),
            "row": alerts.loc[hit[0]] if len(hit) else None,
            "rejected": out["rejected"],
        }
        st.rerun()

    if reset.button("Reset", width="stretch",
                    help="Clear the live feed, re-score, and start a fresh account."):
        had_feed = LIVE_CSV.exists()
        LIVE_CSV.unlink(missing_ok=True)
        # Dropping the feed is not enough: alerts.parquet still holds the live wallets
        # until detection runs again, so the console would keep showing them.
        if had_feed:
            with st.spinner("Clearing the live feed and restoring the baseline..."):
                rescore(DATA)
        st.session_state.wallet = new_wallet()
        st.session_state.pop("result", None)
        st.rerun()

    res = st.session_state.get("result")
    if res and res["rank"]:
        row = res["row"]
        sev_name, sev_colour = severity_of(row["risk"])
        tags = "".join(f'<span class="bt-chip">{esc(t)}</span>'
                       for t in (row["typologies"] or "").split(",") if t)
        verdict = ("This wallet reads as ordinary traffic: no typology fired."
                   if not tags else "Flagged by the rule layer as well as the model.")
        st.markdown(
            '<div class="bt-panel" style="margin-top:16px">'
            '<p class="bt-h">Detector verdict</p>'
            f'<div><span class="bt-sev"><i class="bt-dot" style="background:{sev_colour}">'
            f'</i>{sev_name}</span><span class="bt-meta"> &middot; '
            f'ranked {res["rank"]:,} of {res["total"]:,} wallets</span></div>'
            f'<div style="margin-top:12px">{tags}</div>'
            '<dl class="bt-dl">'
            f'<dt>Risk</dt><dd>{row["risk"]:.3f}</dd>'
            f'<dt>Confidence</dt><dd>{row["confidence"]:.3f}</dd>'
            f'<dt>Signals</dt><dd>{int(row["corroborating_families"])} of 3 families</dd>'
            f'<dt>Rows rejected</dt><dd>{res["rejected"]}</dd>'
            f'</dl><p class="bt-note" style="margin:16px 0 0">{verdict} Open the Console '
            'page to see this wallet in the ranked alert list, its evidence and its link '
            'graph.</p></div>',
            unsafe_allow_html=True)
    elif res:
        st.warning("This wallet was not scored. Push again after emitting activity.")

    if (DATA / "alerts.parquet").exists() and LIVE_CSV.exists():
        note("Live wallets are absent from ground_truth.csv, so while a live feed is "
             "loaded the Model performance tab counts them as false positives. Reset "
             "before quoting the headline metrics.")
