"""How the model decides -- one wallet, followed from raw numbers to a ranked alert.

The console answers "what did it find". This page answers "why should anyone believe it",
by walking a single wallet through every stage the detector actually runs: the features it
measures, the anomaly score, the attribution that says which numbers produced that score,
the rule layer that names a typology, and the arithmetic that combines them into risk and
confidence. Every number shown is read back from the fitted run, not recomputed here, so
the page cannot quietly disagree with the detector it is describing.
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from btctrace.detect import TYPOLOGY_TERMS, thresholds
from btctrace.features import feature_columns
from btctrace.ui import (ACCENT, INK_3, RED, bar, card, cell, note, severity_of, strip,
                        style)

DATA = Path("data")

# The four families the write-up uses. A feature the model sees but this list forgets
# still appears, under "other", rather than vanishing from the page.
FAMILIES = {
    "Value flow": ["n_spends", "n_receives", "total_sent", "total_received", "mean_received",
                   "degree", "retention_ratio", "round_amount_frac", "uniformity_received",
                   "uniformity_sent", "max_tx_fanout", "max_tx_fanin", "n_counterparties"],
    "Temporal": ["lifetime_days", "burst_max_24h", "night_frac", "interarrival_cv",
                 "hop_latency_h"],
    "Graph": ["upstream_max_receives", "peel_chain_depth", "entity_size"],
    "Network": ["n_ips", "n_countries", "n_asns", "nonstd_port_frac", "shared_ip_cohort",
                "ip_cohort_components", "geo_hop_rate"],
}


@st.cache_data(show_spinner=False)
def load():
    alerts = pd.read_parquet(DATA / "alerts.parquet")
    feats = pd.read_parquet(DATA / "features.parquet")
    cols = feature_columns(feats)
    raw = feats[cols].astype(float).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    return alerts.sort_values("risk", ascending=False).reset_index(drop=True), raw


def family_of(col: str) -> str:
    return next((name for name, cols in FAMILIES.items() if col in cols), "Other")


style()

bar("How the model decides",
    "One wallet, followed through every stage the detector runs: what it measured, what "
    "the model made of it, which numbers drove that, which named rules fired, and the "
    "arithmetic that turns all of it into a ranked alert.",
    tag="model", mark="M")

if not (DATA / "alerts.parquet").exists() or not (DATA / "features.parquet").exists():
    st.info("No scored run found. Run `make demo` (or the pipeline) to build "
            "data/alerts.parquet and data/features.parquet first.")
    st.stop()

alerts, raw = load()
cuts = thresholds(raw)

# The default is the top-ranked alert, because that is the wallet anyone will ask about
# first. The contrast option matters just as much: the same machinery run over an
# ordinary wallet has to produce an ordinary answer, or the demo proves nothing.
explained = alerts[alerts["explanation"] != "[]"]
ordinary = alerts[(alerts["degree"] >= 4)].tail(1)
choices = list(explained.head(40).index) + list(ordinary.index)
labels = {i: (f'#{i + 1}  ·  risk {alerts.loc[i, "risk"]:.3f}  ·  '
              f'{alerts.loc[i, "address"][:16]}…'
              f'{"  ·  ordinary wallet, for contrast" if i in ordinary.index else ""}')
          for i in choices}
picked = st.selectbox("Wallet", choices, format_func=lambda i: labels[i])

row = alerts.loc[picked]
addr = row["address"]
values = raw.loc[addr]
pct = raw.rank(pct=True).loc[addr]
sev_name, sev_colour = severity_of(row["risk"])
tags = [t for t in (row["typologies"] or "").split(",") if t]

strip(
    cell("Rank", f"{picked + 1:,}", f"of {len(alerts):,}"),
    cell("Risk", f'{row["risk"]:.3f}', sev_name),
    cell("Model score", f'{row["model_score"]:.3f}'),
    cell("Confidence", f'{row["confidence"]:.3f}'),
    cell("Typologies fired", f"{len(tags)}"),
    cell("Address", addr, ident=True),
)

# ---- 1. features ----------------------------------------------------------------
with card("1 · What it measures"):
    note(f"Every wallet becomes the same {raw.shape[1]} numbers -- no address, no label, "
         "no identity. "
         "Percentile is this wallet's standing in the whole population, which is the only "
         "thing that makes a raw value meaningful: 12 receipts is unremarkable for an "
         "exchange and extraordinary for a personal wallet.")
    table = pd.DataFrame({
        "family": [family_of(c) for c in raw.columns],
        "feature": raw.columns,
        "this wallet": [values[c] for c in raw.columns],
        "population median": [raw[c].median() for c in raw.columns],
        "percentile": [pct[c] * 100 for c in raw.columns],
    }).sort_values("percentile", ascending=False)
    st.dataframe(
        table, hide_index=True, width="stretch", height=320,
        column_config={
            "this wallet": st.column_config.NumberColumn(format="%.3f"),
            "population median": st.column_config.NumberColumn(format="%.3f"),
            "percentile": st.column_config.ProgressColumn(
                format="%.0f", min_value=0, max_value=100),
        })

left, right = st.columns([1, 1], gap="large")

# ---- 2. the model ---------------------------------------------------------------
with left, card("2 · What the model makes of it"):
    note(f"An IsolationForest builds {300} random trees over those {raw.shape[1]} numbers. "
         "A "
         "wallet that is easy to cut off from the rest of the population -- isolated in "
         "few splits -- is anomalous. It is unsupervised: no labelled fraud is ever shown "
         "to it, so it cannot simply memorise the frauds we planted.")
    hist = alt.Chart(alerts[["model_score"]].sample(min(6000, len(alerts)),
                                                    random_state=0)).mark_bar().encode(
        x=alt.X("model_score:Q", bin=alt.Bin(maxbins=40), title="anomaly score"),
        y=alt.Y("count()", title="wallets"),
        color=alt.value(INK_3))
    mark = alt.Chart(pd.DataFrame({"s": [row["model_score"]]})).mark_rule(
        strokeWidth=2.5).encode(x="s:Q", color=alt.value(RED))
    st.altair_chart(hist + mark, width="stretch")
    note(f'This wallet scores **{row["model_score"]:.3f}** — more anomalous than '
         f'**{row["model_score"] * 100:.1f}%** of the {len(alerts):,} wallets scored. '
         "The score is rank-normalised, so it means the same thing on any dataset.",
         pad=True)

# ---- 3. attribution -------------------------------------------------------------
with right, card("3 · Which numbers drove the score"):
    note("Each feature is reset to the population's own median and the wallet re-scored, "
         "then the reverse: a median wallet with only that feature set to the real value. "
         "Averaging the two is the standard two-endpoint Shapley approximation — the same "
         "quantity SHAP estimates, at two forest passes and no extra dependency.")
    items = json.loads(row["explanation"])
    if not items:
        note("Attribution is computed for the top 300 alerts only, and this wallet is "
             "outside that band. Pick one of the ranked wallets above to see it.", pad=True)
    else:
        ev = pd.DataFrame(items)
        st.altair_chart(
            alt.Chart(ev).mark_bar().encode(
                x=alt.X("contribution:Q", title="share of the score", axis=alt.Axis(format="%")),
                y=alt.Y("feature:N", sort="-x", title=None),
                color=alt.value(ACCENT),
                tooltip=["feature", alt.Tooltip("value:Q", format=".3f"),
                         alt.Tooltip("percentile:Q", format=".0f")]),
            width="stretch")
        st.markdown("\n".join(
            f'- **{e["contribution"]:.0%}** — this wallet {e["text"]}, against a '
            f'population median of {e["population_median"]:.3f} '
            f'(percentile **{e["percentile"]:.0f}**)' for e in items))

# ---- 4. the rule layer ----------------------------------------------------------
with card("4 · The rule layer, and what it saw"):
    note("The score says *odd*. It cannot say *odd how*. A second, separate layer looks "
         "for named laundering shapes and is never fed into the score — it corroborates "
         "it. Every cut-off below is a percentile of this population, not a constant, so "
         "the rules travel to a dataset of a different size without retuning.")
    rules = []
    for name, terms in TYPOLOGY_TERMS.items():
        for term in terms:
            rules.append({
                "typology": name,
                "fired": name in tags,
                "feature": term,
                "this wallet": float(values.get(term, float("nan"))),
                "threshold": float(cuts.get(term, float("nan"))),
                "percentile": float(pct.get(term, float("nan")) * 100),
            })
    st.dataframe(
        pd.DataFrame(rules), hide_index=True, width="stretch", height=300,
        column_config={
            "fired": st.column_config.CheckboxColumn("fired", width="small"),
            "this wallet": st.column_config.NumberColumn(format="%.3f"),
            "threshold": st.column_config.NumberColumn(format="%.3f"),
            "percentile": st.column_config.NumberColumn(format="%.0f"),
        })
    note("A threshold is a term in the rule, not the whole rule: `rapid_passthrough` needs "
         "quick re-spend *and* near-zero retention *and* at least two receipts. The "
         "typology fires only when every term of it holds.", pad=True)

# ---- 5. the arithmetic ----------------------------------------------------------
with card("5 · The arithmetic"):
    note("Nothing here is a black box or a learned weighting. Two published formulas "
         "combine the model and the rules, and both are shown with this wallet's numbers "
         "substituted.")
    n_tags = len(tags)
    fams = int(row["corroborating_families"])
    st.markdown(
        '<dl class="bt-dl">'
        f'<dt>Risk</dt><dd class="mono">0.65 × {row["model_score"]:.3f} + 0.35 × '
        f'min(1, {n_tags}/3) = <b>{row["risk"]:.3f}</b></dd>'
        f'<dt>Confidence</dt><dd class="mono">0.5 × {fams}/3 + 0.5 × '
        f'{row["model_score"]:.3f} = <b>{row["confidence"]:.3f}</b></dd>'
        f'<dt>Severity</dt><dd>{sev_name}</dd>'
        f'<dt>Typologies</dt><dd>{", ".join(tags) if tags else "none fired"}</dd>'
        f'<dt>Families agreeing</dt><dd>{fams} of 3 (on-chain rule · network rule · '
        f'model score above 0.90)</dd>'
        '</dl>', unsafe_allow_html=True)
    note("Confidence deliberately measures *agreement*, not magnitude. A wallet three "
         "independent families of evidence point at is a safer lead than one an extreme "
         "score alone singles out, and an investigator's time is the scarce resource.",
         pad=True)

# ---- 6. limits ------------------------------------------------------------------
with card("6 · What this does not claim"):
    note("The model is unsupervised: it ranks wallets by how unlike the population they "
         "are. Unusual is not illegal — an exchange hot wallet is wildly anomalous and "
         "entirely lawful, which is why a typology and a named reason accompany every "
         "alert instead of a bare score.")
    note("Thresholds are relative to the population being scored, so the same wallet in a "
         "different corpus can rank differently. That is deliberate, and it is why the "
         "whole population is re-scored whenever new data arrives.", pad=True)
    note("An alert is a lead for a human analyst, not a verdict and not evidence of "
         "identity. The network layer correlates a broadcast host with on-chain "
         "behaviour; it does not prove who was at the keyboard.", pad=True)
