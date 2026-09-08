"""Detection, ranking and explanation.

Scoring is model-driven: an IsolationForest over the full feature matrix (on-chain,
temporal, graph and network features alike) produces the primary anomaly score. Graph
motifs and network correlations are not the score -- they are corroborating evidence that
gives an investigator a named typology and a reason to act.

Explanations use occlusion attribution: replace one feature with its population baseline,
re-score, and the drop in anomaly is that feature's contribution. It is deterministic,
model-faithful, needs no extra dependency, and reads as a plain sentence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import QuantileTransformer

from .features import feature_columns

RANDOM_STATE = 0

# Human-readable phrasing for each feature, used to build explanation sentences.
FEATURE_PHRASES = {
    "retention_ratio": "keeps {value:.0%} of what it receives",
    "hop_latency_h": "re-spends funds after {value:.2f} h",
    "peel_chain_depth": "sits on a {value:.0f}-hop peeling chain",
    "shared_ip_cohort": "shares a host with {value:.0f} other wallets",
    "ip_cohort_components": "shares a host with {value:.0f} mutually unrelated wallet groups",
    "n_asns": "broadcast from {value:.0f} distinct ASNs",
    "n_countries": "broadcast from {value:.0f} countries",
    "n_ips": "broadcast from {value:.0f} IP addresses",
    "geo_hop_rate": "changes country {value:.1f} times per active day",
    "burst_max_24h": "made {value:.0f} transactions in a single 24 h window",
    "max_tx_fanout": "spent into a transaction with {value:.0f} outputs",
    "max_tx_fanin": "received from a transaction with {value:.0f} inputs",
    "n_counterparties": "transacted with {value:.0f} distinct counterparties",
    "uniformity_received": "receives repeated near-identical amounts (uniformity {value:.2f})",
    "uniformity_sent": "sends repeated near-identical amounts (uniformity {value:.2f})",
    "upstream_max_receives": "was funded by a wallet that had collected from {value:.0f} counterparties",
    "degree": "appears in {value:.0f} transaction legs",
    "lifetime_days": "was active for {value:.1f} days",
    "night_frac": "makes {value:.0%} of its transactions between 00:00-06:00 UTC",
    "interarrival_cv": "has bursty timing (interval variation {value:.2f})",
    "nonstd_port_frac": "used a non-standard port for {value:.0%} of traffic",
    "total_received": "received {value:.4f} BTC in total",
    "total_sent": "sent {value:.4f} BTC in total",
    "entity_size": "belongs to a {value:.0f}-address entity cluster",
    "round_amount_frac": "receives round-number amounts {value:.0%} of the time",
    "n_spends": "spent {value:.0f} times",
    "n_receives": "received {value:.0f} times",
    "mean_received": "receives {value:.4f} BTC on average",
}


@dataclass
class Model:
    """A fitted detector plus everything needed to explain its scores."""

    forest: IsolationForest
    transformer: QuantileTransformer
    columns: list
    baseline: np.ndarray          # population median, in transformed space
    raw_median: pd.Series         # population median, in original units


def fit(f: pd.DataFrame, n_estimators: int = 300) -> tuple:
    """Fit the anomaly model. Unsupervised -- ground truth is never shown to it."""
    cols = feature_columns(f)
    raw = f[cols].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    transformer = QuantileTransformer(
        n_quantiles=min(1000, max(10, len(raw))),
        output_distribution="normal",
        random_state=RANDOM_STATE,
        subsample=100_000,
    )
    X = transformer.fit_transform(raw)
    forest = IsolationForest(
        n_estimators=n_estimators,
        contamination="auto",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    ).fit(X)
    model = Model(
        forest=forest,
        transformer=transformer,
        columns=cols,
        baseline=np.median(X, axis=0),
        raw_median=raw.median(),
    )
    return model, X, raw


def anomaly_scores(model: Model, X: np.ndarray) -> np.ndarray:
    """Rank-normalised anomaly score in [0, 1]; higher is more anomalous.

    Rank-normalising rather than using the raw forest score makes the number comparable
    across datasets and directly interpretable as "more anomalous than N% of wallets".
    """
    raw = -model.forest.score_samples(X)
    return pd.Series(raw).rank(pct=True).to_numpy()


def cohorts(X: np.ndarray, eps: float = 1.4, min_samples: int = 12) -> np.ndarray:
    """Behavioural cohorts via DBSCAN on a PCA projection.

    DBSCAN in the full 26-dimensional space is defeated by distance concentration, so the
    matrix is reduced first. Label -1 means the wallet joined no cohort at all, which is
    itself a mild signal of oddity.
    """
    comps = min(8, X.shape[1], max(2, X.shape[0] - 1))
    reduced = PCA(n_components=comps, random_state=RANDOM_STATE).fit_transform(X)
    return DBSCAN(eps=eps, min_samples=min_samples, n_jobs=-1).fit_predict(reduced)


# The features each typology reads, so a dashboard can show a rule's terms and the value
# it saw without restating the rule itself and drifting from it.
TYPOLOGY_TERMS = {
    "peeling_chain": ["peel_chain_depth"],
    "mixer_layering": ["max_tx_fanout", "retention_ratio"],
    "ransomware_fanin": ["n_receives", "uniformity_received", "burst_max_24h"],
    "rapid_passthrough": ["hop_latency_h", "retention_ratio", "n_receives"],
    "sybil_broadcast": ["ip_cohort_components"],
    "geo_hopping": ["n_asns", "geo_hop_rate"],
}


def thresholds(raw: pd.DataFrame) -> dict:
    """The cut-offs the rule layer is using on this population.

    Percentiles of the observed data, not hard-coded constants, so the detectors travel
    to a dataset with a different scale without retuning. A floor keeps a tiny or unusually
    quiet population from producing a threshold that fires on ordinary behaviour.
    """
    def p(col, q):
        return raw[col].quantile(q) if col in raw else np.inf

    return {
        "max_tx_fanout": max(6, p("max_tx_fanout", 0.995)),
        "burst_max_24h": max(4, p("burst_max_24h", 0.99)),
        "n_receives": max(10, p("n_receives", 0.99)),
        "ip_cohort_components": max(8, p("ip_cohort_components", 0.995)),
        "peel_chain_depth": 5,
        "uniformity_received": 0.8,
        "hop_latency_h": 1.0,
        "retention_ratio": 0.1,
        "n_asns": 4,
        "geo_hop_rate": 1.0,
    }


def typologies(raw: pd.DataFrame) -> pd.Series:
    """Named graph/network motifs, as corroborating evidence rather than as the score."""
    t = thresholds(raw)
    hot_fanout = t["max_tx_fanout"]
    burst = t["burst_max_24h"]
    many_recv = t["n_receives"]
    fragmented = t["ip_cohort_components"]

    quick = (raw["hop_latency_h"] >= 0) & (raw["hop_latency_h"] < 1.0)
    drains = raw["retention_ratio"].abs() < 0.1

    # Each tag names one shape and must not fire on the others. An earlier, looser
    # ransomware rule (many counterparties OR a wide fan-in transaction) also matched
    # pass-through and mixer wallets, so alerts arrived carrying a typology that
    # contradicted their own evidence -- worse than no label at all, because an
    # investigator acts on the name. Collection is specifically: many receipts, of
    # near-identical size, concentrated in time.
    tags = {
        "peeling_chain": raw["peel_chain_depth"] >= 5,
        "mixer_layering": (raw["max_tx_fanout"] >= hot_fanout) & drains,
        "ransomware_fanin": (raw["n_receives"] >= many_recv)
        & (raw["uniformity_received"] >= 0.8)
        & (raw["burst_max_24h"] >= burst),
        "rapid_passthrough": quick & drains & (raw["n_receives"] >= 2),
        "sybil_broadcast": raw["ip_cohort_components"] >= fragmented,
        "geo_hopping": (raw["n_asns"] >= 4) & (raw["geo_hop_rate"] >= 1.0),
    }
    hits = pd.DataFrame(tags, index=raw.index)
    return hits.apply(lambda row: [c for c in hits.columns if row[c]], axis=1)


def attribute(model: Model, X: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Per-feature attribution, averaged over both ends of the coalition.

    Two marginal contributions are measured for each feature:

      occlusion  -- from the real wallet, reset the feature to the population baseline and
                    see how much anomaly is lost;
      inclusion  -- from an all-baseline wallet, set that one feature to its real value and
                    see how much anomaly appears.

    Occlusion alone is misleading whenever features are correlated, which here they
    thoroughly are: a peeling-chain wallet is simultaneously deep in a chain, high-value
    and high-degree, so neutralising any single one of those leaves the forest isolating it
    just as easily via the others, and every genuinely damning feature scores near zero.
    Inclusion catches exactly that case. Averaging the marginal contribution at the empty
    and full coalitions is the standard two-endpoint approximation of a Shapley value --
    the same quantity SHAP estimates, at two forest passes instead of a sampling loop and
    with no extra dependency to install on an offline machine.
    """
    n_feat = X.shape[1]
    subjects = X[rows]

    full = -model.forest.score_samples(subjects)
    baseline_row = model.baseline[None, :]
    empty = float(-model.forest.score_samples(baseline_row)[0])

    # Row block i covers wallet i; within a block, row j varies only feature j.
    occluded_in = np.repeat(subjects, n_feat, axis=0)
    included_in = np.repeat(baseline_row, n_feat * len(rows), axis=0)
    for j in range(n_feat):
        occluded_in[j::n_feat, j] = model.baseline[j]
        included_in[j::n_feat, j] = subjects[:, j]

    occluded = -model.forest.score_samples(occluded_in).reshape(len(rows), n_feat)
    included = -model.forest.score_samples(included_in).reshape(len(rows), n_feat)

    return 0.5 * ((full[:, None] - occluded) + (included - empty))


def explain(model: Model, raw: pd.DataFrame, contribs: np.ndarray,
            addresses, top_k: int = 5) -> list:
    """Turn attributions into ranked, plain-English evidence."""
    pct_rank = raw.rank(pct=True)
    out = []
    for i, addr in enumerate(addresses):
        c = contribs[i]
        positive = c.clip(min=0)
        total = positive.sum()
        order = np.argsort(-c)[:top_k]
        items = []
        for j in order:
            if c[j] <= 0:
                continue
            col = model.columns[j]
            value = float(raw.iloc[i][col])
            phrase = FEATURE_PHRASES.get(col, col.replace("_", " ") + " = {value:.2f}")
            items.append({
                "feature": col,
                "value": value,
                "population_median": float(model.raw_median[col]),
                "percentile": round(float(pct_rank.iloc[i][col]) * 100, 1),
                "contribution": round(float(c[j] / total) if total else 0.0, 4),
                "text": phrase.format(value=value),
            })
        out.append(items)
    return out


def detect(df: pd.DataFrame, f: pd.DataFrame, top_k: int = 300) -> pd.DataFrame:
    """Run the full detection pipeline and return a ranked, explained alert list."""
    model, X, raw = fit(f)
    score = anomaly_scores(model, X)
    cohort = cohorts(X)
    tags = typologies(raw)

    n_tags = tags.map(len).to_numpy()
    # Corroboration counts *independent families* of evidence, so three variations of the
    # same network signal do not out-vote a genuine on-chain finding.
    network_tags = {"sybil_broadcast", "geo_hopping"}
    onchain_hit = tags.map(lambda t: any(x not in network_tags for x in t)).to_numpy()
    network_hit = tags.map(lambda t: any(x in network_tags for x in t)).to_numpy()
    families = onchain_hit.astype(int) + network_hit.astype(int) + (score > 0.9).astype(int)

    risk = 0.65 * score + 0.35 * np.minimum(1.0, n_tags / 3.0)
    # Confidence is agreement, not magnitude: a wallet three independent signals point at
    # is a safer lead than one an extreme score alone singles out.
    confidence = 0.5 * (families / 3.0) + 0.5 * score

    alerts = pd.DataFrame({
        "address": f.index,
        "risk": risk,
        "confidence": confidence,
        "model_score": score,
        "corroborating_families": families,
        "typologies": tags.to_numpy(),
        "cohort": cohort,
    }).set_index("address")
    alerts = alerts.sort_values("risk", ascending=False)

    top = alerts.head(top_k)
    positions = f.index.get_indexer(top.index)
    contribs = attribute(model, X, positions)
    explanations = explain(model, raw.loc[top.index], contribs, top.index)
    alerts["explanation"] = pd.Series(
        [json.dumps(e) for e in explanations], index=top.index
    ).reindex(alerts.index).fillna("[]")

    meta = [c for c in f.columns if c.startswith("meta_")]
    alerts = alerts.join(f[meta + ["degree", "total_received", "total_sent",
                                   "retention_ratio", "hop_latency_h"]])
    alerts["typologies"] = alerts["typologies"].map(lambda t: ",".join(t))
    return alerts.reset_index()


def evaluate(alerts: pd.DataFrame, ground_truth: pd.DataFrame, ks=(100, 500, 1000)) -> dict:
    """Score the ranked list against planted ground truth, at two different units.

    Wallet level answers "how clean is the worklist" -- precision, recall and ROC-AUC over
    every scored address.

    Operation level answers the question an investigator actually asks: of the criminal
    operations planted in this data, how many did the system surface *at all*? One good
    lead into a mixer is an opened investigation; ranking all 26 of its single-use
    intermediates highly is not the goal, and averaging them into a wallet-level recall
    understates the system by treating disposable addresses as if each were a separate
    target. Both numbers are reported because neither alone is honest.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    truth = set(ground_truth["address"])
    y = alerts["address"].isin(truth).astype(int).to_numpy()
    scores = alerts["risk"].to_numpy()
    n_pos = int(y.sum())

    out = {
        "wallets_scored": len(alerts),
        "planted_illicit_wallets": n_pos,
        "roc_auc": round(float(roc_auc_score(y, scores)), 4) if 0 < n_pos < len(y) else None,
        "average_precision": round(float(average_precision_score(y, scores)), 4)
        if n_pos else None,
    }
    for k in ks:
        kk = min(k, len(alerts))
        out[f"precision@{k}"] = round(float(y[:kk].mean()), 4)
        out[f"recall@{k}"] = round(float(y[:kk].sum() / n_pos), 4) if n_pos else None
    if n_pos:
        out["r_precision"] = round(float(y[:min(n_pos, len(y))].mean()), 4)

    # --- operation level ---
    gt = ground_truth.set_index("address")
    ops = gt["operation_id"] if "operation_id" in gt else gt["typology"]
    op_typ = dict(zip(ops, gt["typology"]))
    rank_of = {a: i for i, a in enumerate(alerts["address"])}
    best_rank = {}
    for addr, op in ops.items():
        r = rank_of.get(addr)
        if r is not None and r < best_rank.get(op, len(alerts) + 1):
            best_rank[op] = r

    per_typ = {}
    for op, rank in best_rank.items():
        per_typ.setdefault(op_typ[op], []).append(rank)
    op_summary = {}
    for typ, ranks in sorted(per_typ.items()):
        ranks = sorted(ranks)
        op_summary[typ] = {
            "operations": len(ranks),
            **{f"detected@{k}": sum(r < k for r in ranks) for k in ks},
            "median_best_rank": int(np.median(ranks)),
        }
    out["by_typology"] = op_summary
    total_ops = len(best_rank)
    out["operations_planted"] = total_ops
    for k in ks:
        out[f"operation_recall@{k}"] = round(
            sum(r < k for r in best_rank.values()) / total_ops, 4) if total_ops else None
    return out
