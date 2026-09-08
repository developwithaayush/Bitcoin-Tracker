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

from btctrace.ui import bar, card, cell, note, severity_of, strip, style
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


def tx_detail(rec: dict, wallet: Wallet) -> None:
    """One transaction, the way an explorer shows it -- from what this project records.

    Confirmations, weight, RBF, locktime and fiat value are not modelled anywhere in
    btctrace, so they are absent rather than invented. Size and fee rate are estimates
    from the input and output counts at p2wpkh sizing, and say so.
    """
    ins, outs = rec["input_amounts"], rec["output_amounts"]
    spent, paid = sum(ins), sum(outs)
    ts = pd.to_datetime(rec["timestamp"], unit="s")
    age = max(wallet.ts - rec["timestamp"], 0)
    vsize = 68 * len(ins) + 31 * len(outs) + 11
    sats = round(rec["fee"] * 1e8)

    st.markdown('<div class="bt-split"></div>', unsafe_allow_html=True)
    strip(
        cell("Amount", f"{paid:.8f}", "BTC"),
        cell("Fee", f"{sats:,}", "sats"),
        cell("From", str(len(ins)), "inputs"),
        cell("To", str(len(outs)), "outputs"),
    )
    st.markdown(
        '<dl class="bt-dl">'
        f'<dt>Hash</dt><dd class="mono">{rec["txid"]}</dd>'
        f'<dt>Broadcast</dt><dd>{ts.strftime("%d %b %Y %H:%M:%S")} UTC</dd>'
        f'<dt>Age</dt><dd>{age // 3600}h {age % 3600 // 60}m on this wallet’s clock</dd>'
        f'<dt>Input value</dt><dd>{spent:.8f} BTC</dd>'
        f'<dt>Output value</dt><dd>{paid:.8f} BTC</dd>'
        f'<dt>Fee</dt><dd>{rec["fee"]:.8f} BTC</dd>'
        f'<dt>Size (est.)</dt><dd>{vsize:,} vBytes &middot; {sats / vsize:.3f} sat/vB</dd>'
        f'<dt>Script type</dt><dd>{esc(rec["script_type"])}</dd>'
        f'<dt>Broadcast from</dt><dd class="mono">{esc(rec["src_ip"])} '
        f'&middot; {esc(rec["geo_country"])} &middot; AS{esc(str(rec["asn"]))}</dd>'
        '</dl>', unsafe_allow_html=True)

    # Marking the wallet's own addresses is the point of showing the legs at all: the
    # second output of an ordinary payment is the change coming home.
    for title, addrs, amounts in (("From", rec["input_addresses"], ins),
                                  ("To", rec["output_addresses"], outs)):
        st.markdown(f'<p class="bt-h">{title}</p>', unsafe_allow_html=True)
        legs = pd.DataFrame({
            "#": range(1, len(addrs) + 1), "address": addrs, "amount": amounts,
            "owner": ["yours" if a in wallet.addresses else "counterparty" for a in addrs],
        })
        st.dataframe(
            legs, hide_index=True, width="stretch", height=min(38 * len(legs) + 40, 190),
            column_config={
                "#": st.column_config.NumberColumn(width="small"),
                "address": st.column_config.TextColumn(width="large"),
                "amount": st.column_config.NumberColumn("BTC", format="%.8f"),
                "owner": st.column_config.TextColumn(width="small"),
            })


def new_wallet() -> Wallet:
    return Wallet(start_ts=corpus_end())


style()

# Session state survives a hot reload, so a wallet built by an older version of this
# module can outlive it. Checking for the attribute covers both "no wallet yet" and
# "a wallet from before the code changed".
if not hasattr(st.session_state.get("wallet"), "addresses"):
    st.session_state.wallet = new_wallet()
w: Wallet = st.session_state.wallet

bar("Wallet simulator",
    "A demo account whose transactions reach the console through the real ingest and "
    "detection path. Trade like an ordinary user, or run a preset to act out a laundering "
    "pattern, then push and see where you rank.",
    tag="sandbox", mark="W")

strip(
    cell("Balance", f"{w.balance:.4f}", "BTC"),
    cell("Unspent outputs", f"{len(w.utxos):,}", "coins"),
    cell("Transactions emitted", f"{len(w.records):,}"),
    cell("Addresses owned", f"{len(w.addresses):,}"),
    cell("Live feed", "loaded" if LIVE_CSV.exists() else "not pushed"),
    cell("Receiving address", w.address, ident=True),
)

left, right = st.columns([1, 1], gap="large")

with left, card("Trade"):
    note("Ordinary activity. A wallet that only buys, sells and pays should stay "
         "unremarkable to the detector -- that contrast is the point of the demo.")
    amount_col, dest_col = st.columns([1, 2], vertical_alignment="bottom")
    amount = amount_col.number_input("Amount (BTC)", 0.0001, 100.0, 0.5, 0.1, format="%.4f")
    # Buy comes from the exchange and Sell goes back to it, so only Send has a
    # counterparty to name. Blank keeps the fast path: a random address, which is what
    # the presets use and what this did before there was a field to fill in.
    dest = dest_col.text_input(
        "Send to address", placeholder="bc1q…  ·  blank for a random counterparty",
        help="Where a Send pays. Buy and Sell always use the exchange.")

    broke = amount > w.balance
    buy_col, sell_col, send_col, _ = st.columns([1, 1, 1, 2])
    if buy_col.button("Buy", width="stretch", type="primary"):
        w.buy(amount)
        st.rerun()
    if sell_col.button("Sell", width="stretch", disabled=broke):
        w.sell(amount)
        st.rerun()
    bad_dest = ""
    if send_col.button("Send", width="stretch", disabled=broke):
        try:
            # Rejecting the typo here is the whole point of asking: ingest checks IPs,
            # ports and amounts but never address shape, so a bad one would reach the
            # link graph as a node named after the mistake.
            w.send(amount, dest.strip() or None)
        except ValueError:
            bad_dest = dest.strip()
        else:
            st.rerun()
    if bad_dest:
        note(f"{esc(bad_dest)} is not a Bitcoin address. Expected 1…, 3…, "
             "bc1q… or bc1p…, or leave it blank.")
    if broke:
        note("Sell and Send need a balance -- buy some coin first.")

with left, card("Unspent outputs"):
    note("What this wallet actually holds. A balance is a total; these are the coins "
         "behind it, and a payment spends whole coins and takes the remainder back as "
         "change -- which is why a Send can consume several at once. The change lands on "
         "a fresh address the wallet owns, never the one that just spent, which is how "
         "one person comes to own thousands of addresses.")
    if w.utxos:
        coins = pd.DataFrame(w.utxos)
        st.dataframe(
            coins.assign(received=pd.to_datetime(coins["ts"], unit="s"),
                         held_at=coins["address"])
            .sort_values("ts")[["amount", "held_at", "txid", "received"]],
            hide_index=True, width="stretch", height=min(38 * len(coins) + 40, 230),
            column_config={
                "amount": st.column_config.NumberColumn("amount (BTC)", format="%.8f"),
                "held_at": st.column_config.TextColumn("held at", width="medium"),
                "txid": st.column_config.TextColumn("from tx", width="large"),
                "received": st.column_config.DatetimeColumn("received", format="DD MMM HH:mm"),
            })
    else:
        note("No coins yet. A Buy creates one.")

with left, card("Demo behaviours"):
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

with right, card("Activity"):
    if w.log:
        st.dataframe(pd.DataFrame(w.log)[["action", "detail", "balance"]],
                     hide_index=True, width="stretch", height=232,
                     column_config={
                         "action": st.column_config.TextColumn("action", width="small"),
                         "balance": st.column_config.NumberColumn("balance", format="%.4f"),
                     })
    else:
        st.info("No activity yet. Buy some coin, or run one of the demo behaviours.")

with right, card("Transactions"):
    note("Every record this wallet has broadcast. Select one to open it, the way an "
         "explorer shows a transaction: what went in, what came out, and which of the "
         "outputs is the change coming back to an address of yours.")
    if w.records:
        txs = pd.DataFrame([{
            "time": pd.to_datetime(r["timestamp"], unit="s"),
            "txid": r["txid"],
            "in": len(r["input_amounts"]),
            "out": len(r["output_amounts"]),
            "amount": sum(r["output_amounts"]),
        } for r in w.records])
        # Row selection is the click target -- st.dataframe does it natively, so the
        # txid needs no link and the page needs no component.
        picked = st.dataframe(
            txs, hide_index=True, width="stretch", height=min(38 * len(txs) + 40, 190),
            on_select="rerun", selection_mode="single-row", key="tx_pick",
            column_config={
                "time": st.column_config.DatetimeColumn("broadcast", format="DD MMM HH:mm"),
                "txid": st.column_config.TextColumn("transaction id", width="large"),
                "amount": st.column_config.NumberColumn("out (BTC)", format="%.8f"),
            })
        rows = picked.selection["rows"]
        if rows:
            tx_detail(w.records[rows[0]], w)
        else:
            note("Nothing selected. Click a row to open the transaction.", pad=True)
    else:
        st.info("No transactions yet. Buy some coin, or run a demo behaviour.")

with right, card("Push to btctrace"):
    note("Re-ingests the full corpus and re-runs detection over every wallet. Takes about "
         "30 seconds: an anomaly score is a wallet's position in a population, so the "
         "population has to be scored with it.")

    push, reset = st.columns([2, 1], vertical_alignment="bottom")
    if push.button("Analyse now", type="primary", width="stretch", disabled=not w.records):
        with st.spinner("Ingesting, building features, scoring the population..."):
            save_records(w.records)
            out = rescore(DATA)
        alerts = out["alerts"].reset_index(drop=True)
        # Change scatters this account across many addresses, so look up all of them
        # and report the one the detector ranked highest.
        hit = alerts.index[alerts["address"].isin(w.addresses)]
        st.session_state.result = {
            "rank": int(hit[0]) + 1 if len(hit) else None,
            "total": len(alerts),
            "row": alerts.loc[hit[0]] if len(hit) else None,
            "scored": len(hit),
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
            '<div class="bt-split"></div>'
            '<p class="bt-h">Detector verdict</p>'
            f'<div><span class="bt-sev"><i class="bt-dot" style="background:{sev_colour}">'
            f'</i>{sev_name}</span><span class="bt-meta"> &middot; '
            f'ranked {res["rank"]:,} of {res["total"]:,} wallets</span></div>'
            f'<div style="margin-top:12px">{tags}</div>'
            '<dl class="bt-dl">'
            f'<dt>Risk</dt><dd>{row["risk"]:.3f}</dd>'
            f'<dt>Confidence</dt><dd>{row["confidence"]:.3f}</dd>'
            f'<dt>Signals</dt><dd>{int(row["corroborating_families"])} of 3 families</dd>'
            f'<dt>Addresses scored</dt><dd>{res["scored"]} of {len(w.addresses)}</dd>'
            f'<dt>Rows rejected</dt><dd>{res["rejected"]}</dd>'
            f'</dl><p class="bt-note" style="margin:16px 0 0">{verdict} Open the Console '
            'page to see this wallet in the ranked alert list, its evidence and its link '
            'graph.</p>',
            unsafe_allow_html=True)
    elif res:
        st.warning("This wallet was not scored. Push again after emitting activity.")

    if (DATA / "alerts.parquet").exists() and LIVE_CSV.exists():
        note("Live wallets are absent from ground_truth.csv, so while a live feed is "
             "loaded the Model performance tab counts them as false positives. Reset "
             "before quoting the headline metrics.")
