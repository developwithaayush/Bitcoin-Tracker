"""Wallet simulator -- a live feed into the btctrace console.

Buy and sell to build an ordinary wallet, or run a preset to act out a laundering
pattern, then push the result through the real pipeline and watch where the detector
ranks you. Nothing here is a special case inside btctrace: the wallet writes ordinary
records to data/raw/live.csv and ingest picks them up like any other data drop.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from btctrace.wallet import LIVE_CSV, Wallet, rescore, save_records

DATA = Path("data")

st.set_page_config(page_title="btctrace wallet simulator", layout="wide")

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


if "wallet" not in st.session_state:
    st.session_state.wallet = new_wallet()
w: Wallet = st.session_state.wallet

st.title("Wallet simulator")
st.caption(
    "A sandbox account whose transactions are fed to the console through the real "
    "ingest and detection path. Buy and sell like an ordinary user, or run a preset to "
    "act out a laundering pattern, then push and see where you rank."
)

left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("Account")
    st.code(w.address, language=None)
    a, b = st.columns(2)
    a.metric("Balance", f"{w.balance:.4f} BTC")
    b.metric("Transactions emitted", len(w.records))

    st.markdown("**Trade**")
    amount = st.number_input("Amount (BTC)", 0.0001, 100.0, 0.5, 0.1, format="%.4f")
    buy, sell, send = st.columns(3)
    if buy.button("Buy", width="stretch", type="primary"):
        w.buy(amount)
        st.rerun()
    if sell.button("Sell", width="stretch", disabled=amount > w.balance):
        w.sell(amount)
        st.rerun()
    if send.button("Send", width="stretch", disabled=amount > w.balance):
        w.send(amount)
        st.rerun()
    if amount > w.balance:
        st.caption("Sell and Send need a balance -- buy some coin first.")

    st.markdown("**Demo behaviours**")
    st.caption("Each one emits a burst of activity shaped like the named typology.")
    for name, (tag, help_text, action) in PRESETS.items():
        if st.button(name, width="stretch", help=help_text, key=f"p_{tag}"):
            action(w)
            st.rerun()

with right:
    st.subheader("Activity")
    if w.log:
        st.dataframe(pd.DataFrame(w.log)[["action", "detail", "balance"]],
                     hide_index=True, width="stretch")
    else:
        st.info("No activity yet. Buy some coin, or run one of the demo behaviours.")

    st.divider()
    st.subheader("Push to btctrace")
    st.caption(
        "Re-ingests the full corpus and re-runs detection over every wallet. Takes about "
        "30 seconds: an anomaly score is a wallet's position in a population, so the "
        "population has to be scored with it."
    )

    push, reset = st.columns([2, 1])
    if push.button("Analyse now", type="primary", width="stretch",
                   disabled=not w.records):
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
        tags = row["typologies"] or ""
        c1, c2, c3 = st.columns(3)
        c1.metric("Rank", f"{res['rank']:,} of {res['total']:,}")
        c2.metric("Risk", f"{row['risk']:.3f}")
        c3.metric("Corroborating families", int(row["corroborating_families"]))
        if tags:
            st.error(f"**Flagged:** {tags.replace(',', ', ')}")
        else:
            st.success("No typology fired -- this wallet reads as ordinary traffic.")
        st.caption("Open the console page to see this wallet in the ranked alert list, "
                   "its evidence, and its link graph.")
    elif res:
        st.warning("This wallet was not scored. Push again after emitting activity.")

    if (DATA / "alerts.parquet").exists() and LIVE_CSV.exists():
        st.caption(
            ":grey[Live wallets are absent from ground_truth.csv, so while a live feed is "
            "loaded the Model performance tab counts them as false positives. Reset "
            "before quoting the headline metrics.]"
        )
