"""btctrace dashboard -- ranked alerts, per-alert evidence, and link analysis.

Run with:  streamlit run app.py

Everything renders from local parquet files and the vis.js bundle is inlined, so the whole
dashboard works with no network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DATA = Path("data")
NODE_COLOURS = {"wallet": "#3b82f6", "subject": "#ef4444", "tx": "#94a3b8", "ip": "#f59e0b"}

st.set_page_config(page_title="btctrace", layout="wide", page_icon="₿")


@st.cache_data(show_spinner=False)
def load():
    alerts = pd.read_parquet(DATA / "alerts.parquet")
    df = pd.read_parquet(DATA / "canonical.parquet")
    truth_path = DATA / "ground_truth.csv"
    truth = pd.read_csv(truth_path) if truth_path.exists() else None
    return alerts, df, truth


def neighbourhood_html(df: pd.DataFrame, address: str, max_tx: int = 18) -> str:
    """Interactive wallet/transaction/IP graph around one address."""
    from pyvis.network import Network

    mask = df["input_addresses"].map(lambda a: address in a) | df["output_addresses"].map(
        lambda a: address in a
    )
    rows = df[mask].head(max_tx)

    # cdn_resources="in_line" embeds vis.js in the page; the default pulls it from a CDN,
    # which would leave the graph blank on an air-gapped machine.
    net = Network(height="620px", width="100%", directed=True, cdn_resources="in_line",
                  bgcolor="#ffffff", font_color="#111827")
    # A busy wallet makes a wide star, and the default repulsion throws its leaves far
    # outside the canvas. Softer gravity plus real central gravity keeps the neighbourhood
    # inside the frame; the fit() hook below re-centres once the layout settles.
    net.barnes_hut(gravity=-2600, central_gravity=0.55, spring_length=110,
                   spring_strength=0.05, damping=0.9)
    seen = set()

    def node(nid, label, kind, title=""):
        if nid in seen:
            return
        seen.add(nid)
        net.add_node(nid, label=label, title=title or label, color=NODE_COLOURS[kind],
                     size=26 if kind == "subject" else 14, shape="dot")

    node(address, address[:10] + "…", "subject", address)
    for r in rows.itertuples(index=False):
        tx_label = r.txid[:8] + "…"
        node(r.txid, tx_label, "tx", f"txid {r.txid}\n{r.timestamp}\nfee {r.fee:.8f}")
        node(r.src_ip, r.src_ip, "ip", f"{r.src_ip} · {r.geo_country} · AS{r.asn}")
        net.add_edge(r.src_ip, r.txid, color="#fcd34d", title="broadcast")
        for a, v in zip(r.input_addresses, r.input_amounts):
            node(a, a[:10] + "…", "subject" if a == address else "wallet", a)
            net.add_edge(a, r.txid, title=f"in {v:.8f} BTC", color="#93c5fd")
        for a, v in zip(r.output_addresses, r.output_amounts):
            node(a, a[:10] + "…", "subject" if a == address else "wallet", a)
            net.add_edge(r.txid, a, title=f"out {v:.8f} BTC", color="#86efac")
    return _strip_remote_assets(net.generate_html(notebook=False))


def _strip_remote_assets(html: str) -> str:
    """Remove pyvis's leftover external <script>/<link> tags.

    cdn_resources="in_line" inlines vis.js itself but the template still emits a Bootstrap
    CDN tag and a broken ../node_modules relative path. Both are unused, and on an isolated
    network the browser blocks on the CDN request before drawing anything -- so on the one
    deployment that matters here, leaving them in costs a visible stall for nothing.
    """
    import re

    html = re.sub(r'<script[^>]+src="(?:https?:)?//[^"]*"[^>]*>\s*</script>', "", html)
    html = re.sub(r'<script[^>]+src="\.\./[^"]*"[^>]*>\s*</script>', "", html)
    html = re.sub(r'<link[^>]+href="(?:(?:https?:)?//|\.\./)[^"]*"[^>]*>', "", html)
    # Zoom to fit once physics settles, so the whole neighbourhood is visible without the
    # analyst having to pan around hunting for stray nodes.
    html = html.replace(
        "return network;",
        'network.once("stabilizationIterationsDone", function () { network.fit(); });'
        "\n        return network;",
    )
    return html


def evidence_panel(row: pd.Series) -> None:
    items = json.loads(row["explanation"])
    if not items:
        st.info("This wallet is outside the explained top-N. Re-run `btctrace detect --top` "
                "with a larger value to explain further down the ranking.")
        return
    ev = pd.DataFrame(items)
    chart = (
        alt.Chart(ev)
        .mark_bar(cornerRadiusEnd=3, color="#ef4444")
        .encode(
            x=alt.X("contribution:Q", title="share of risk score", axis=alt.Axis(format="%")),
            y=alt.Y("feature:N", sort="-x", title=None,
                    axis=alt.Axis(labelLimit=220, labelFontSize=12)),
            tooltip=["feature", "value", "population_median", "percentile", "contribution"],
        )
        .properties(height=34 * len(ev))
    )
    st.altair_chart(chart, use_container_width=True)
    for e in items:
        st.markdown(
            f"- **{e['contribution']:.0%}** — this wallet {e['text']}; "
            f"population median **{e['population_median']:.2f}** "
            f"(percentile **{e['percentile']:.0f}**)"
        )


def main() -> None:
    try:
        alerts, df, truth = load()
    except FileNotFoundError:
        st.error("No analysis found. Run `python -m btctrace.cli pipeline` first.")
        return

    st.title("btctrace — Bitcoin transaction traffic monitoring")
    st.caption(
        "Unsupervised anomaly detection over correlated network-layer and blockchain-layer "
        "metadata. Every alert carries the evidence that produced it."
    )

    with st.sidebar:
        st.header("Filters")
        min_risk = st.slider("Minimum risk", 0.0, 1.0, 0.5, 0.01)
        all_tags = sorted({t for s in alerts["typologies"] for t in s.split(",") if t})
        picked = st.multiselect("Typology", all_tags)
        only_explained = st.checkbox("Only explained alerts", value=True)
        st.divider()
        st.metric("Wallets scored", f"{len(alerts):,}")
        st.metric("Carrying a typology", f"{(alerts['typologies'] != '').sum():,}")

    view = alerts[alerts["risk"] >= min_risk]
    if picked:
        view = view[view["typologies"].apply(lambda s: any(t in s.split(",") for t in picked))]
    if only_explained:
        view = view[view["explanation"] != "[]"]

    tab_alerts, tab_graph, tab_metrics = st.tabs(
        ["Ranked alerts", "Link analysis", "Model performance"]
    )

    with tab_alerts:
        st.subheader(f"{len(view):,} alerts")
        shown = view.head(400)
        table = shown[["address", "risk", "confidence", "typologies",
                       "corroborating_families", "degree", "total_received",
                       "retention_ratio", "meta_countries"]].rename(
            columns={"meta_countries": "countries", "corroborating_families": "signals"})
        event = st.dataframe(
            table, width="stretch", hide_index=True, height=420,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "risk": st.column_config.ProgressColumn("risk", min_value=0.0, max_value=1.0,
                                                        format="%.3f"),
                "confidence": st.column_config.NumberColumn(format="%.3f"),
                "total_received": st.column_config.NumberColumn(format="%.4f"),
                "retention_ratio": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        picked_rows = event.selection.rows if event and event.selection else []
        idx = picked_rows[0] if picked_rows else 0
        if len(shown):
            row = shown.iloc[idx]
            st.session_state["selected"] = row["address"]
            st.divider()
            c1, c2 = st.columns([2, 3])
            with c1:
                st.markdown(f"### `{row['address']}`")
                st.markdown(
                    f"**Risk** {row['risk']:.3f}  ·  **Confidence** {row['confidence']:.3f}  ·  "
                    f"**Independent signals** {row['corroborating_families']}/3"
                )
                if row["typologies"]:
                    st.markdown("**Typologies:** " + ", ".join(
                        f"`{t}`" for t in row["typologies"].split(",")))
                # A wallet only joins an entity when it was co-spent with another, so
                # most single-use addresses legitimately have none; "nan" is not a number
                # the analyst should ever be shown.
                entity = row["meta_entity_id"]
                entity_txt = "not co-spent" if pd.isna(entity) else f"#{int(entity)}"
                st.markdown(
                    f"**Entity cluster** {entity_txt}  ·  "
                    f"**Countries** {row['meta_countries'] or '—'}"
                )
                st.markdown(f"**Hosts** `{row['meta_ips'] or '—'}`")
                st.markdown(
                    f"**First seen** {row['meta_first_seen']}  ·  "
                    f"**Last seen** {row['meta_last_seen']}"
                )
            with c2:
                st.markdown("### Why this was flagged")
                evidence_panel(row)

    with tab_graph:
        selected = st.session_state.get("selected")
        if not selected:
            st.info("Select an alert first.")
        else:
            st.subheader(f"Neighbourhood of `{selected}`")
            st.caption("Red = subject wallet · blue = counterparty wallet · "
                       "grey = transaction · amber = broadcasting host")
            st.components.v1.html(neighbourhood_html(df, selected), height=640)

    with tab_metrics:
        if truth is None:
            st.info("No ground_truth.csv present, so detection quality cannot be scored.")
        else:
            from btctrace.detect import evaluate

            report = evaluate(alerts, truth)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("ROC-AUC", f"{report['roc_auc']:.3f}")
            c2.metric("Precision@100", f"{report['precision@100']:.3f}")
            c3.metric("Operations planted", report["operations_planted"])
            c4.metric("Operation recall@1000", f"{report['operation_recall@1000']:.1%}")
            st.caption(
                "Operation recall asks whether at least one wallet from each planted "
                "criminal operation reached the analyst's worklist -- the question an "
                "investigator actually cares about. Wallet-level precision and recall are "
                "reported alongside it because neither number alone is honest."
            )
            st.dataframe(pd.DataFrame(report["by_typology"]).T, width="stretch")

            hist = (
                alt.Chart(alerts.assign(
                    known=alerts["address"].isin(set(truth["address"])).map(
                        {True: "planted illicit", False: "unlabelled"})))
                .mark_bar(opacity=0.75)
                .encode(
                    x=alt.X("risk:Q", bin=alt.Bin(maxbins=50), title="risk score"),
                    y=alt.Y("count()", stack=None, scale=alt.Scale(type="symlog"),
                            title="wallets"),
                    color=alt.Color("known:N", title=None,
                                    scale=alt.Scale(range=["#ef4444", "#cbd5e1"])),
                )
                .properties(height=280)
            )
            st.altair_chart(hist, use_container_width=True)

    st.divider()
    st.caption(
        "Synthetic data — no real or intercepted Bitcoin traffic. "
        "IP geolocation by [DB-IP](https://db-ip.com) (CC BY 4.0) when a local database is "
        "installed; otherwise country and ASN come from the dataset."
    )


if __name__ == "__main__":
    main()
