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

DATA = Path("data")

# Entity colours are the console's data vocabulary, shared by the graph, the severity ramp
# and the charts, so a colour means the same thing wherever it appears. Validated for CVD
# separation and >=3:1 contrast on the light surface. Transactions are deliberately the one
# neutral: they are structural connectors between entities rather than entities under
# investigation, and they carry a text label plus a legend entry as relief.
INK, INK_2, INK_3, LINE = "#0f172a", "#334155", "#566277", "#e4e9f0"
ACCENT = "#2563eb"
NODE_COLOURS = {"wallet": "#2563eb", "subject": "#dc2626", "tx": "#64748b", "ip": "#b45309"}

# ponytail: fixed bands, not distribution quantiles -- an operator needs "Critical" to mean
# the same thing across runs. Retune here if the risk formula in detect.py changes.
SEVERITY = ((0.85, "Critical", "#b91c1c"), (0.70, "High", "#dc2626"),
            (0.55, "Elevated", "#b45309"), (0.0, "Low", "#64748b"))

SANS = ("ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")

st.set_page_config(page_title="btctrace console", layout="wide", page_icon="B",
                   initial_sidebar_state="expanded")


def severity_of(risk: float) -> tuple[str, str]:
    for floor, name, colour in SEVERITY:
        if risk >= floor:
            return name, colour
    return SEVERITY[-1][1], SEVERITY[-1][2]


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
                "gridColor": "#eef2f7", "gridDash": [2, 3], "labelPadding": 5,
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


CSS = """
<style>
:root {
  /* Cool-neutral ground, white panels. Sidebar and app bar sit on a second neutral
     layer so tool chrome reads as chrome, not as content. */
  --bt-canvas: #f7f9fb; --bt-panel: #ffffff;
  --bt-line: #e4e9f0; --bt-line-strong: #cbd5e1;
  /* Body ink is slate-700+ so it clears 4.5:1 on both the panel and the canvas. */
  --bt-ink: #0f172a; --bt-ink-2: #334155; --bt-ink-3: #566277;
  /* Blue is the accent and never a data colour; red is a data colour (risk) and never
     an action, so the two vocabularies can never be confused. */
  --bt-accent: #2563eb; --bt-accent-dim: #eff4ff;
  --bt-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
             "Helvetica Neue", Arial, sans-serif;
  --bt-mono: ui-monospace, "Cascadia Mono", "Segoe UI Mono", "SF Mono", Menlo,
             Consolas, "Liberation Mono", monospace;
}

html, body, [data-testid="stAppViewContainer"] { font-family: var(--bt-sans); }

/* Streamlit ships a tall empty header bar; reclaiming it buys a full row of data. */
[data-testid="stHeader"] { background: transparent; height: 0; }
.block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1600px; }

/* ---- App bar ---- */
.bt-bar { display: flex; align-items: baseline; flex-wrap: wrap; gap: .3rem .75rem;
  padding-bottom: .7rem; border-bottom: 1px solid var(--bt-line); margin-bottom: .95rem; }
.bt-bar h1 { font-size: 1.15rem; font-weight: 650; letter-spacing: -0.012em;
  color: var(--bt-ink); margin: 0; line-height: 1.2; }
.bt-bar .bt-sub { font-size: .82rem; color: var(--bt-ink-3); line-height: 1.45;
  flex: 1 1 22ch; min-width: 20ch; }
.bt-tag { font-size: .68rem; font-weight: 650; letter-spacing: .04em; text-transform: uppercase;
  padding: .18rem .44rem; border-radius: 4px; white-space: nowrap;
  color: #b45309; background: #fdf5e9; border: 1px solid #f0dcbe; }

/* ---- Status strip: dense label/value pairs, deliberately not metric cards ---- */
.bt-strip { display: flex; flex-wrap: wrap; gap: .55rem 1.9rem; margin-bottom: 1.15rem; }
.bt-k { display: block; font-size: .69rem; font-weight: 600; letter-spacing: .045em;
  text-transform: uppercase; color: var(--bt-ink-3); margin-bottom: .1rem; }
.bt-v { display: block; font-family: var(--bt-mono); font-size: 1.02rem; font-weight: 600;
  color: var(--bt-ink); font-variant-numeric: tabular-nums; }
.bt-v small { font-family: var(--bt-sans); font-size: .74rem; font-weight: 500;
  color: var(--bt-ink-3); }

/* ---- Panels ---- */
.bt-panel { background: var(--bt-panel); border: 1px solid var(--bt-line);
  border-radius: 6px; padding: .95rem 1.05rem; height: 100%; }
.bt-h { font-size: .72rem; font-weight: 650; letter-spacing: .05em; text-transform: uppercase;
  color: var(--bt-ink-3); margin: 0 0 .6rem; padding-bottom: .4rem;
  border-bottom: 1px solid var(--bt-line); }
.bt-addr { font-family: var(--bt-mono); font-size: .95rem; font-weight: 600;
  color: var(--bt-ink); word-break: break-all; line-height: 1.35; }

/* Fixed label column so values align down the panel. */
.bt-dl { display: grid; grid-template-columns: 8.5rem 1fr; gap: .34rem .8rem; margin: .75rem 0 0; }
.bt-dl dt { font-size: .76rem; color: var(--bt-ink-3); }
.bt-dl dd { font-size: .82rem; color: var(--bt-ink-2); margin: 0;
  font-variant-numeric: tabular-nums; word-break: break-word; }
.bt-dl dd.mono { font-family: var(--bt-mono); font-size: .78rem; }

/* ---- Chips, severity, legend ---- */
.bt-chip { display: inline-block; font-family: var(--bt-mono); font-size: .72rem;
  font-weight: 600; padding: .12rem .4rem; margin: 0 .28rem .28rem 0; border-radius: 4px;
  background: var(--bt-accent-dim); color: #1e40af; border: 1px solid #d3e0fb; }
.bt-sev { display: inline-flex; align-items: center; gap: .36rem; font-size: .82rem;
  font-weight: 650; color: var(--bt-ink); }
.bt-legend { display: flex; flex-wrap: wrap; gap: .35rem 1rem; margin: .1rem 0 .7rem; }
.bt-legend span { display: inline-flex; align-items: center; gap: .38rem; font-size: .78rem;
  color: var(--bt-ink-2); }
.bt-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }

/* ---- Streamlit widget alignment ---- */
[data-testid="stSidebar"] { background: var(--bt-panel); border-right: 1px solid var(--bt-line); }
[data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }
.stTabs [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 1px solid var(--bt-line); }
.stTabs [data-baseweb="tab"] { height: 2.3rem; font-size: .85rem; font-weight: 550;
  color: var(--bt-ink-3); padding: 0 .85rem;
  transition: color 160ms cubic-bezier(.22,1,.36,1); }
.stTabs [data-baseweb="tab"]:hover { color: var(--bt-ink); }
.stTabs [aria-selected="true"] { color: var(--bt-accent) !important; }
[data-testid="stMetricValue"] { font-family: var(--bt-mono); font-size: 1.45rem; font-weight: 600; }
[data-testid="stMetricLabel"] { font-size: .72rem; letter-spacing: .04em;
  text-transform: uppercase; color: var(--bt-ink-3); }
[data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }

/* Analysts tab through the alert list; focus must stay visible. */
:focus-visible { outline: 2px solid var(--bt-accent); outline-offset: 2px; border-radius: 3px; }

@media (prefers-reduced-motion: reduce) {
  * { transition-duration: 1ms !important; animation-duration: 1ms !important; }
}
</style>
"""


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
                  bgcolor="#ffffff", font_color=INK)
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
        net.add_edge(r.src_ip, r.txid, color="#e2b877", title="broadcast")
        for a, v in zip(r.input_addresses, r.input_amounts):
            node(a, a[:10] + "\u2026", "subject" if a == address else "wallet", a)
            net.add_edge(a, r.txid, title=f"in {v:.8f} BTC", color="#9dbdf0")
        for a, v in zip(r.output_addresses, r.output_amounts):
            node(a, a[:10] + "\u2026", "subject" if a == address else "wallet", a)
            net.add_edge(r.txid, a, title=f"out {v:.8f} BTC", color="#8fc7a4")
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
        .mark_bar(cornerRadiusEnd=3, color="#dc2626", height=15)
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


def _cell(label: str, value: str, note: str = "") -> str:
    tail = f" <small>{esc(note)}</small>" if note else ""
    return (f'<div><span class="bt-k">{esc(label)}</span>'
            f'<span class="bt-v">{esc(value)}{tail}</span></div>')


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        alerts, df, truth = load(stamp())
    except FileNotFoundError:
        st.error("No analysis found. Run `python -m btctrace.cli pipeline` first.")
        return

    st.markdown(
        '<div class="bt-bar"><h1>btctrace console</h1>'
        '<span class="bt-tag">synthetic data</span>'
        '<span class="bt-sub">Unsupervised anomaly detection over correlated '
        'network-layer and blockchain-layer metadata. Every alert carries the evidence '
        'that produced it.</span></div>',
        unsafe_allow_html=True)

    tagged = int((alerts["typologies"] != "").sum())
    explained = int(alerts["explanation"].ne("[]").sum())
    st.markdown(
        '<div class="bt-strip">'
        + _cell("Wallets scored", f"{len(alerts):,}")
        + _cell("Transactions", f"{len(df):,}")
        + _cell("Carrying a typology", f"{tagged:,}", f"{tagged / len(alerts):.1%}")
        + _cell("Explained alerts", f"{explained:,}")
        + _cell("Observation window", f"{df['timestamp'].min():%d %b}"
                f" \u2013 {df['timestamp'].max():%d %b %Y}")
        + "</div>",
        unsafe_allow_html=True)

    with st.sidebar:
        st.markdown('<p class="bt-h">Filters</p>', unsafe_allow_html=True)
        min_risk = st.slider("Minimum risk", 0.0, 1.0, 0.5, 0.01)
        all_tags = sorted({t for s in alerts["typologies"] for t in s.split(",") if t})
        picked = st.multiselect("Typology", all_tags,
                                help="Rule-based corroborating tags, not the model score.")
        only_explained = st.checkbox("Only explained alerts", value=True)
        st.divider()
        st.markdown('<p class="bt-h">Severity bands</p>', unsafe_allow_html=True)
        st.markdown("".join(
            f'<span class="bt-legend"><span><i class="bt-dot" style="background:{c}"></i>'
            f'{n} <span style="color:#566277">&ge; {f:.2f}</span></span></span>'
            for f, n, c in SEVERITY), unsafe_allow_html=True)

    view = alerts[alerts["risk"] >= min_risk]
    if picked:
        view = view[view["typologies"].apply(lambda s: any(t in s.split(",") for t in picked))]
    if only_explained:
        view = view[view["explanation"] != "[]"]

    tab_alerts, tab_graph, tab_metrics = st.tabs(
        ["Ranked alerts", "Link analysis", "Model performance"]
    )

    with tab_alerts:
        shown = view.head(400)
        st.markdown(
            f'<p class="bt-h">{len(view):,} alerts at or above risk {min_risk:.2f}'
            f'{" \u00b7 showing first 400" if len(view) > 400 else ""}</p>',
            unsafe_allow_html=True)
        if not len(shown):
            st.info("No alerts match these filters. Lower the minimum risk, or clear the "
                    "typology filter to see wallets the model ranked highly on score alone.")
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
            '<span style="color:#566277;font-size:.82rem">none</span>'

        st.markdown("")
        c1, c2 = st.columns([2, 3], gap="medium")
        with c1:
            st.markdown(
                '<div class="bt-panel"><p class="bt-h">Subject</p>'
                f'<div class="bt-addr">{esc(row["address"])}</div>'
                f'<div style="margin-top:.6rem"><span class="bt-sev">'
                f'<i class="bt-dot" style="background:{sev_colour}"></i>{sev_name}</span>'
                f'<span style="color:#566277;font-size:.82rem"> &middot; risk '
                f'{row["risk"]:.3f} &middot; confidence {row["confidence"]:.3f}</span></div>'
                f'<div style="margin-top:.7rem">{chips}</div>'
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
            st.markdown('<p class="bt-h">Why this was flagged</p>', unsafe_allow_html=True)
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
            st.markdown(
                '<div class="bt-strip">'
                + _cell("ROC-AUC", f"{report['roc_auc']:.3f}", "wallet level")
                + _cell("Precision@100", f"{report['precision@100']:.3f}")
                + _cell("Precision@500", f"{report['precision@500']:.3f}")
                + _cell("Operations planted", f"{report['operations_planted']}")
                + _cell("Op. recall@500", f"{report['operation_recall@500']:.1%}")
                + _cell("Op. recall@1000", f"{report['operation_recall@1000']:.1%}")
                + "</div>",
                unsafe_allow_html=True)
            st.caption(
                "Operation recall asks whether at least one wallet from each planted "
                "criminal operation reached the analyst's worklist -- the question an "
                "investigator actually cares about. Wallet-level precision and recall are "
                "reported alongside it because neither number alone is honest."
            )

            g1, g2 = st.columns([3, 2], gap="medium")
            with g1:
                st.markdown('<p class="bt-h">Risk distribution</p>', unsafe_allow_html=True)
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
                                            range=["#dc2626", "#c9d3e0"])),
                        tooltip=[alt.Tooltip("known:N", title="group"),
                                 alt.Tooltip("count()", title="wallets")],
                    )
                    .properties(height=300)
                )
                st.altair_chart(hist, width="stretch", theme=None)
            with g2:
                st.markdown('<p class="bt-h">Detection by typology</p>',
                            unsafe_allow_html=True)
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
    main()
