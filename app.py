"""btctrace console -- ranked alerts, per-alert evidence, and link analysis.

Run with:  streamlit run app.py

Everything renders from local parquet files and the vis.js bundle is inlined, so the whole
dashboard works with no network access. Typography is a system-font stack and the theme is
plain CSS for the same reason: no webfont request, nothing to fail on an isolated network.
"""

from __future__ import annotations

import json
from html import escape as esc
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Palette, stylesheet and the small HTML helpers live in btctrace.ui, so the charts and
# the link graph -- neither of which can read CSS -- stay in step with the stylesheet,
# and the wallet simulator page renders in the same visual language.
from btctrace.ui import (EDGE_COLOURS, GRID, INK, INK_2, INK_3, LINE, NEUTRAL, NODE_COLOURS,
                         PANEL, RED, SANS, SEVERITY, bands, bar, cell, head, severity_of,
                         strip, style)

DATA = Path("data")

st.set_page_config(page_title="btctrace", layout="wide", page_icon="B",
                   initial_sidebar_state="expanded")


@alt.theme.register("btctrace", enable=True)
def _btctrace_theme() -> alt.theme.ThemeConfig:
    """One chart theme, registered once, so every chart shares an axis and type voice."""
    return {
        "config": {
            "background": "transparent",
            "font": SANS,
            "view": {"stroke": "transparent"},
            "axis": {
                "labelFont": SANS, "titleFont": SANS,
                "labelColor": INK_3, "titleColor": INK_3,
                "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 600,
                "titlePadding": 8, "domainColor": LINE, "tickColor": LINE,
                "gridColor": GRID, "gridDash": [2, 3], "labelPadding": 5,
            },
            "legend": {
                "labelFont": SANS, "titleFont": SANS, "labelColor": INK_2,
                "titleColor": INK_3, "labelFontSize": 11, "titleFontSize": 11,
                "symbolType": "square", "symbolSize": 90, "orient": "top",
                "direction": "horizontal", "offset": 4,
            },
            "title": {"font": SANS, "color": INK, "fontSize": 12, "fontWeight": 600,
                      "anchor": "start", "offset": 10},
        }
    }


SOURCES = (DATA / "alerts.parquet", DATA / "canonical.parquet", DATA / "ground_truth.csv")


def stamp() -> tuple:
    """Cache key that moves whenever the pipeline rewrites its outputs."""
    return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in SOURCES)


@st.cache_data(show_spinner=False)
def load(key: tuple):
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
                  bgcolor=PANEL, font_color=INK)
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
                     size=26 if kind == "subject" else 14, shape="dot",
                     font={"size": 13, "face": "system-ui, sans-serif", "color": INK_2})

    node(address, address[:10] + "\u2026", "subject", address)
    for r in rows.itertuples(index=False):
        tx_label = r.txid[:8] + "\u2026"
        node(r.txid, tx_label, "tx", f"txid {r.txid}\n{r.timestamp}\nfee {r.fee:.8f}")
        node(r.src_ip, r.src_ip, "ip", f"{r.src_ip} \u00b7 {r.geo_country} \u00b7 AS{r.asn}")
        net.add_edge(r.src_ip, r.txid, color=EDGE_COLOURS["broadcast"], title="broadcast")
        for a, v in zip(r.input_addresses, r.input_amounts):
            node(a, a[:10] + "\u2026", "subject" if a == address else "wallet", a)
            net.add_edge(a, r.txid, title=f"in {v:.8f} BTC", color=EDGE_COLOURS["in"])
        for a, v in zip(r.output_addresses, r.output_amounts):
            node(a, a[:10] + "\u2026", "subject" if a == address else "wallet", a)
            net.add_edge(r.txid, a, title=f"out {v:.8f} BTC", color=EDGE_COLOURS["out"])
    return _strip_remote_assets(net.generate_html(notebook=False))


def _strip_remote_assets(html_doc: str) -> str:
    """Remove pyvis's leftover external <script>/<link> tags.

    cdn_resources="in_line" inlines vis.js itself but the template still emits a Bootstrap
    CDN tag and a broken ../node_modules relative path. Both are unused, and on an isolated
    network the browser blocks on the CDN request before drawing anything -- so on the one
    deployment that matters here, leaving them in costs a visible stall for nothing.
    """
    import re

    html_doc = re.sub(r'<script[^>]+src="(?:https?:)?//[^"]*"[^>]*>\s*</script>', "", html_doc)
    html_doc = re.sub(r'<script[^>]+src="\.\./[^"]*"[^>]*>\s*</script>', "", html_doc)
    html_doc = re.sub(r'<link[^>]+href="(?:(?:https?:)?//|\.\./)[^"]*"[^>]*>', "", html_doc)
    # Zoom to fit once physics settles, so the whole neighbourhood is visible without the
    # analyst having to pan around hunting for stray nodes.
    html_doc = html_doc.replace(
        "return network;",
        'network.once("stabilizationIterationsDone", function () { network.fit(); });'
        "\n        return network;",
    )
    return html_doc


def evidence_panel(row: pd.Series) -> None:
    items = json.loads(row["explanation"])
    if not items:
        st.info("This wallet is outside the explained top-N. Re-run `btctrace detect --top` "
                "with a larger value to explain further down the ranking.")
        return
    ev = pd.DataFrame(items)
    # One series, so no legend: the axis title names it. Bars are sorted by contribution
    # and directly labelled, which is what makes the ranking readable without a colour key.
    chart = (
        alt.Chart(ev)
        .mark_bar(cornerRadiusEnd=3, color=RED, height=15)
        .encode(
            x=alt.X("contribution:Q", title="share of risk score",
                    axis=alt.Axis(format="%", tickCount=4)),
            y=alt.Y("feature:N", sort="-x", title=None,
                    axis=alt.Axis(labelLimit=230, labelFontSize=11.5)),
            tooltip=[alt.Tooltip("feature:N", title="feature"),
                     alt.Tooltip("value:Q", title="this wallet", format=".3f"),
                     alt.Tooltip("population_median:Q", title="population median", format=".3f"),
                     alt.Tooltip("percentile:Q", title="percentile", format=".0f"),
                     alt.Tooltip("contribution:Q", title="share of risk", format=".1%")],
        )
        .properties(height=27 * len(ev))
    )
    st.altair_chart(chart, width="stretch", theme=None)
    for e in items:
        st.markdown(
            f"- **{e['contribution']:.0%}** — this wallet {e['text']}; "
            f"population median **{e['population_median']:.2f}** "
            f"(percentile **{e['percentile']:.0f}**)"
        )


def main() -> None:
    style()
    try:
        alerts, df, truth = load(stamp())
    except FileNotFoundError:
        st.error("No analysis found. Run `python -m btctrace.cli pipeline` first.")
        return

    bar("btctrace console",
        "Unsupervised anomaly detection over correlated network-layer and blockchain-layer "
        "metadata. Every alert carries the evidence that produced it.",
        tag="synthetic data")

    tagged = int((alerts["typologies"] != "").sum())
    explained = int(alerts["explanation"].ne("[]").sum())
    strip(
        cell("Wallets scored", f"{len(alerts):,}"),
        cell("Transactions", f"{len(df):,}"),
        cell("Carrying a typology", f"{tagged:,}", f"{tagged / len(alerts):.1%}"),
        cell("Explained alerts", f"{explained:,}"),
        cell("Observation window", f"{df['timestamp'].min():%d %b}"
             f" \u2013 {df['timestamp'].max():%d %b %Y}"),
    )

    with st.sidebar:
        head("Filters")
        min_risk = st.slider("Minimum risk", 0.0, 1.0, 0.5, 0.01)
        all_tags = sorted({t for s in alerts["typologies"] for t in s.split(",") if t})
        picked = st.multiselect("Typology", all_tags,
                                help="Rule-based corroborating tags, not the model score.")
        only_explained = st.checkbox("Only explained alerts", value=True)
        # An analyst arriving with an address from another system needs to reach that one
        # wallet without walking the ranking; a substring match also covers a partial paste.
        query = st.text_input("Address contains", placeholder="bc1q\u2026",
                              help="Substring match on the wallet address.")
        st.divider()
        head("Severity bands")
        st.markdown("".join(
            f'<span class="bt-legend"><span><i class="bt-dot" style="background:{c}"></i>'
            f'{n} <span class="bt-quiet">&ge; {f:.2f}</span></span></span>'
            for f, n, c in SEVERITY), unsafe_allow_html=True)

    view = alerts[alerts["risk"] >= min_risk]
    if picked:
        view = view[view["typologies"].apply(lambda s: any(t in s.split(",") for t in picked))]
    if only_explained:
        view = view[view["explanation"] != "[]"]
    if query:
        view = view[view["address"].str.contains(query.strip(), case=False, regex=False)]

    tab_alerts, tab_graph, tab_metrics = st.tabs(
        ["Ranked alerts", "Link analysis", "Model performance"]
    )

    with tab_alerts:
        shown = view.head(400)
        title, export = st.columns([4, 1], vertical_alignment="bottom")
        with title:
            head(f'{len(view):,} alerts at or above risk {min_risk:.2f}'
                 f'{" \u00b7 showing first 400" if len(view) > 400 else ""}')
        # The worklist is the product of this screen; an analyst hands it on to a case
        # system, so it has to leave as a file rather than as a screenshot.
        export.download_button("Export worklist", view.to_csv(index=False),
                               "btctrace-worklist.csv", "text/csv", width="stretch",
                               disabled=not len(view),
                               help="The filtered alert list, as CSV.")
        # How many alerts of each severity, before the table. "Is there anything critical
        # today" is the first question asked of this screen and it should not require
        # reading a 400-row grid to answer.
        bands(view["risk"].map(lambda r: severity_of(r)[0]).value_counts().to_dict())
        if not len(shown):
            st.info("No alerts match these filters. Lower the minimum risk, clear the "
                    "typology filter, or clear the address search.")
            return

        table = shown.assign(severity=shown["risk"].map(lambda r: severity_of(r)[0]))[
            ["address", "severity", "risk", "confidence", "typologies",
             "corroborating_families", "degree", "total_received",
             "retention_ratio", "meta_countries"]].rename(
            columns={"meta_countries": "countries", "corroborating_families": "signals"})
        event = st.dataframe(
            table, width="stretch", hide_index=True, height=430,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "address": st.column_config.TextColumn("address", width="medium"),
                "severity": st.column_config.TextColumn("severity", width="small"),
                "risk": st.column_config.ProgressColumn("risk", min_value=0.0, max_value=1.0,
                                                        format="%.3f"),
                "confidence": st.column_config.NumberColumn(format="%.3f"),
                "signals": st.column_config.NumberColumn("signals", help="Independent "
                                                         "feature families agreeing, of 3."),
                "total_received": st.column_config.NumberColumn(format="%.4f"),
                "retention_ratio": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        picked_rows = event.selection.rows if event and event.selection else []
        row = shown.iloc[picked_rows[0] if picked_rows else 0]
        st.session_state["selected"] = row["address"]

        sev_name, sev_colour = severity_of(row["risk"])
        # A wallet only joins an entity when it was co-spent with another, so most
        # single-use addresses legitimately have none; "nan" is not a number the analyst
        # should ever be shown.
        entity = row["meta_entity_id"]
        entity_txt = "not co-spent" if pd.isna(entity) else f"#{int(entity)}"
        chips = "".join(f'<span class="bt-chip">{esc(t)}</span>'
                        for t in row["typologies"].split(",") if t) or \
            '<span class="bt-meta">none</span>'

        st.markdown("")
        c1, c2 = st.columns([2, 3], gap="medium")
        with c1:
            st.markdown(
                '<div class="bt-panel"><p class="bt-h">Subject</p>'
                f'<div class="bt-addr">{esc(row["address"])}</div>'
                f'<div style="margin-top:12px"><span class="bt-sev">'
                f'<i class="bt-dot" style="background:{sev_colour}"></i>{sev_name}</span>'
                f'<span class="bt-meta"> &middot; risk '
                f'{row["risk"]:.3f} &middot; confidence {row["confidence"]:.3f}</span></div>'
                f'<div style="margin-top:16px">{chips}</div>'
                '<dl class="bt-dl">'
                f'<dt>Signals</dt><dd>{row["corroborating_families"]} of 3 families</dd>'
                f'<dt>Entity cluster</dt><dd>{esc(entity_txt)}</dd>'
                f'<dt>Countries</dt><dd>{esc(row["meta_countries"] or "\u2014")}</dd>'
                f'<dt>Hosts</dt><dd class="mono">{esc(row["meta_ips"] or "\u2014")}</dd>'
                f'<dt>First seen</dt><dd>{esc(str(row["meta_first_seen"]))}</dd>'
                f'<dt>Last seen</dt><dd>{esc(str(row["meta_last_seen"]))}</dd>'
                '</dl></div>',
                unsafe_allow_html=True)
        with c2:
            head("Why this was flagged")
            evidence_panel(row)

    with tab_graph:
        selected = st.session_state.get("selected")
        if not selected:
            st.info("Select an alert in the Ranked alerts tab to plot its neighbourhood.")
        else:
            st.markdown(
                f'<p class="bt-h">Neighbourhood of '
                f'<span style="font-family:ui-monospace,monospace;text-transform:none">'
                f'{esc(selected)}</span></p>'
                '<div class="bt-legend">'
                f'<span><i class="bt-dot" style="background:{NODE_COLOURS["subject"]}"></i>'
                'subject wallet</span>'
                f'<span><i class="bt-dot" style="background:{NODE_COLOURS["wallet"]}"></i>'
                'counterparty wallet</span>'
                f'<span><i class="bt-dot" style="background:{NODE_COLOURS["tx"]}"></i>'
                'transaction</span>'
                f'<span><i class="bt-dot" style="background:{NODE_COLOURS["ip"]}"></i>'
                'broadcasting host</span></div>',
                unsafe_allow_html=True)
            st.components.v1.html(neighbourhood_html(df, selected), height=640)

    with tab_metrics:
        if truth is None:
            st.info("No ground_truth.csv present, so detection quality cannot be scored.")
        else:
            from btctrace.detect import evaluate

            report = evaluate(alerts, truth)
            strip(
                cell("ROC-AUC", f"{report['roc_auc']:.3f}", "wallet level"),
                cell("Precision@100", f"{report['precision@100']:.3f}"),
                cell("Precision@500", f"{report['precision@500']:.3f}"),
                cell("Operations planted", f"{report['operations_planted']}"),
                cell("Op. recall@500", f"{report['operation_recall@500']:.1%}"),
                cell("Op. recall@1000", f"{report['operation_recall@1000']:.1%}"),
            )
            st.caption(
                "Operation recall asks whether at least one wallet from each planted "
                "criminal operation reached the analyst's worklist -- the question an "
                "investigator actually cares about. Wallet-level precision and recall are "
                "reported alongside it because neither number alone is honest."
            )

            g1, g2 = st.columns([3, 2], gap="medium")
            with g1:
                head("Risk distribution")
                # Two series, so a legend is present. This is a highlight-vs-context pair,
                # not a categorical palette: planted illicit carries the one saturated
                # colour and everything else recedes to neutral.
                hist = (
                    alt.Chart(alerts.assign(
                        known=alerts["address"].isin(set(truth["address"])).map(
                            {True: "planted illicit", False: "unlabelled"})))
                    .mark_bar(opacity=0.85)
                    .encode(
                        x=alt.X("risk:Q", bin=alt.Bin(maxbins=50), title="risk score"),
                        y=alt.Y("count()", stack=None, scale=alt.Scale(type="symlog"),
                                title="wallets"),
                        color=alt.Color("known:N", title=None,
                                        scale=alt.Scale(
                                            domain=["planted illicit", "unlabelled"],
                                            range=[RED, NEUTRAL])),
                        tooltip=[alt.Tooltip("known:N", title="group"),
                                 alt.Tooltip("count()", title="wallets")],
                    )
                    .properties(height=300)
                )
                st.altair_chart(hist, width="stretch", theme=None)
            with g2:
                head("Detection by typology")
                by_typ = pd.DataFrame(report["by_typology"]).T
                st.dataframe(
                    by_typ, width="stretch", height=300,
                    column_config={c: st.column_config.NumberColumn(c, format="%d")
                                   for c in by_typ.columns if c != "median_best_rank"},
                )
                st.caption("`median_best_rank` is where the operation's best-ranked wallet "
                           "landed. Lower is better; sybil broadcast is the weak case.")

    st.divider()
    st.caption(
        "Synthetic data \u2014 no real or intercepted Bitcoin traffic. "
        "IP geolocation by [DB-IP](https://db-ip.com) (CC BY 4.0) when a local database is "
        "installed; otherwise country and ASN come from the dataset."
    )


if __name__ == "__main__":
    # Navigation is declared here rather than left to the pages/ directory so the sidebar
    # shows real page names instead of the entry script's filename, and so every page
    # inherits the one set_page_config above.
    # Emoji, not :material/...: -- Streamlit pulls the Material Symbols font from
    # fonts.gstatic.com, which is a blank glyph on an isolated network. The icon is what
    # the sidebar shows once it is collapsed to a rail, so it has to render offline.
    st.navigation([
        st.Page(main, title="Console", icon="📊", url_path="console", default=True),
        st.Page("pages/wallet_simulator.py", title="Wallet simulator", icon="👛"),
    ]).run()
