"""Entity graph construction and per-wallet feature engineering.

The graph is heterogeneous -- wallets, transactions and IPs are all nodes -- because the
whole point of the exercise is correlating the network layer with the blockchain layer.
Network features (how many ASNs a wallet broadcast from, how many unrelated wallets share
its host) land in the *same* matrix as the on-chain features, so the model treats them as
evidence rather than having them bolted on afterwards as a rule.

The alerting unit is the wallet. Wallets are grouped into entities via the common-input
ownership heuristic (see entity_clusters).
"""

from __future__ import annotations

import math
from collections import defaultdict

import networkx as nx
import numpy as np
import pandas as pd

# A transaction output carrying most of a single input's value is the "remainder" of a
# peel: the change leg that a peeling chain walks forward.
REMAINDER_FRACTION = 0.6
DAY_SECONDS = 86400.0

# Sentinel for statistics that are genuinely undefined rather than zero (a wallet with a
# single receipt has no amount variation; one that never re-spent has no hop latency).
# Keeping these distinct from a real 0.0 is what lets the model tell "uniform" apart from
# "only one observation".
UNDEFINED = -1.0


def explode(df: pd.DataFrame):
    """Long-form (txid, address, amount) tables for inputs and outputs.

    Everything downstream is a groupby over these two frames rather than a graph walk,
    which keeps feature extraction linear in the number of transaction legs.
    """
    meta = ["txid", "timestamp", "src_ip", "src_port", "dst_ip", "dst_port",
            "script_type", "geo_country", "asn", "fee"]
    ins = df[meta + ["input_addresses", "input_amounts"]].copy()
    ins["address"] = ins.pop("input_addresses")
    ins["amount"] = ins.pop("input_amounts")
    ins = ins.explode(["address", "amount"])

    outs = df[meta + ["output_addresses", "output_amounts"]].copy()
    outs["address"] = outs.pop("output_addresses")
    outs["amount"] = outs.pop("output_amounts")
    outs = outs.explode(["address", "amount"])

    for frame in (ins, outs):
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    return ins.reset_index(drop=True), outs.reset_index(drop=True)


def build_graph(df: pd.DataFrame) -> nx.DiGraph:
    """wallet -> tx (spend), tx -> wallet (receive), ip -> tx (broadcast)."""
    g = nx.DiGraph()
    for row in df.itertuples(index=False):
        tx = row.txid
        g.add_node(tx, kind="tx", timestamp=row.timestamp, fee=row.fee)
        g.add_node(row.src_ip, kind="ip", country=row.geo_country, asn=row.asn)
        g.add_edge(row.src_ip, tx, kind="broadcast")
        for addr, amt in zip(row.input_addresses, row.input_amounts):
            g.add_node(addr, kind="wallet")
            g.add_edge(addr, tx, kind="input", amount=float(amt))
        for addr, amt in zip(row.output_addresses, row.output_amounts):
            g.add_node(addr, kind="wallet")
            g.add_edge(tx, addr, kind="output", amount=float(amt))
    return g


def entity_clusters(df: pd.DataFrame) -> dict:
    """Common-input-ownership heuristic: addresses co-spent in one transaction are
    controlled by the same actor, so merge them into an entity.

    This is the standard first move in blockchain forensics. It is a heuristic, not a
    proof -- CoinJoin and other collaborative spends deliberately violate it, which is
    why the write-up lists it as a known limitation rather than a guarantee.
    """
    g = nx.Graph()
    for addrs in df["input_addresses"]:
        if len(addrs) > 1:
            first = addrs[0]
            g.add_edges_from((first, a) for a in addrs[1:])
    mapping = {}
    for i, component in enumerate(nx.connected_components(g)):
        for addr in component:
            mapping[addr] = i
    return mapping


def _cv(series: pd.Series) -> float:
    """Coefficient of variation, or NaN when undefined (fewer than two observations).

    NaN rather than 0.0 matters: a wallet that received one payment has *no* measurable
    variation, which is a different statement from a wallet that received fifty
    near-identical ones. Collapsing both to 0.0 hides the uniform-amount signature of a
    ransomware collector among tens of thousands of ordinary single-use addresses.
    Callers substitute the UNDEFINED sentinel so the model can separate the two cases.
    """
    if len(series) < 2:
        return np.nan
    mean = series.mean()
    return float(series.std(ddof=0) / mean) if mean else np.nan


MIN_UNIFORMITY_SAMPLES = 3


def _uniformity(grouped, addresses) -> pd.Series:
    """Map amount variation onto [0, 1], where 1 means "many, all near-identical".

    Wallets with too few observations to judge score 0, the same as wildly varying ones,
    so the top of this range contains only wallets that genuinely repeat an amount.
    """
    cv = grouped.apply(_cv)
    counts = grouped.size()
    score = 1.0 / (1.0 + cv)
    score = score.where(counts >= MIN_UNIFORMITY_SAMPLES, 0.0)
    return score.reindex(addresses).fillna(0.0)


def _upstream_max_receives(df: pd.DataFrame, n_receives: pd.Series, addresses) -> pd.Series:
    """For each wallet, the busiest funder one hop upstream.

    A consolidation cash-out is invisible on its own features -- one receipt, keeps
    everything, indistinguishable from a savings address. What gives it away is where the
    money came from: a wallet that had just collected from dozens of counterparties. This
    is one-hop neighbourhood aggregation, so the model still decides what it means, unlike
    diffusing a risk score outwards (which floods every change address with false taint).
    """
    best: dict = {}
    for row in df.itertuples(index=False):
        funder = max((n_receives.get(a, 0.0) for a in row.input_addresses), default=0.0)
        for out_addr in row.output_addresses:
            if funder > best.get(out_addr, 0.0):
                best[out_addr] = funder
    return pd.Series(best, dtype=float).reindex(addresses).fillna(0.0)


def _ip_cohort_components(df: pd.DataFrame, ins: pd.DataFrame, addresses) -> pd.Series:
    """How many *mutually unrelated* wallet groups broadcast from this wallet's busiest host.

    Raw wallets-per-IP cannot separate a sybil operation from an ordinary heavy user: one
    person's node legitimately broadcasts for dozens of their own addresses. The difference
    is relatedness. A real user's addresses are chained together by their own change
    outputs, so they collapse into a single connected component; wallets controlled by an
    operator fronting for unrelated parties share a host while touching no common
    transaction, giving one IP many disjoint components. That ratio is the signal, and it
    is only visible when network and chain data are correlated -- neither layer shows it.
    """
    g = nx.Graph()
    for row in df.itertuples(index=False):
        addrs = list(row.input_addresses) + list(row.output_addresses)
        g.add_nodes_from(addrs)
        first = addrs[0]
        g.add_edges_from((first, a) for a in addrs[1:])
    component = {}
    for i, members in enumerate(nx.connected_components(g)):
        for addr in members:
            component[addr] = i
    seen = ins[["address", "src_ip"]].assign(component=ins["address"].map(component))
    per_ip = seen.groupby("src_ip")["component"].nunique()
    return (
        seen.assign(spread=seen["src_ip"].map(per_ip))
        .groupby("address")["spread"].max().reindex(addresses).fillna(0.0)
    )


def _peel_edges(df: pd.DataFrame):
    """Edges following the remainder leg of single-input transactions.

    A peeling chain is exactly a long path in this subgraph: spend one input, emit a small
    payment, carry the rest to a fresh address, repeat.
    """
    edges = []
    for row in df.itertuples(index=False):
        if len(row.input_addresses) != 1 or len(row.output_addresses) != 2:
            continue
        funded = float(row.input_amounts[0])
        if funded <= 0:
            continue
        for addr, amt in zip(row.output_addresses, row.output_amounts):
            if float(amt) / funded >= REMAINDER_FRACTION:
                edges.append((row.input_addresses[0], addr))
    return edges


def _chain_depths(edges) -> dict:
    """Longest remainder-path ending at each wallet, by memoised DFS over the DAG.

    Cycles cannot occur in honest chains but can in adversarial data, so the traversal
    tracks its own stack and treats a revisit as depth 0 rather than recursing forever.
    """
    succ = defaultdict(list)
    for a, b in edges:
        succ[a].append(b)
    depth: dict = {}
    stack_guard: set = set()

    def walk(node):
        if node in depth:
            return depth[node]
        if node in stack_guard:
            return 0
        stack_guard.add(node)
        best = 0
        for nxt in succ.get(node, ()):
            best = max(best, 1 + walk(nxt))
        stack_guard.discard(node)
        depth[node] = best
        return best

    import sys
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 10000))
    try:
        for node in list(succ):
            walk(node)
    finally:
        sys.setrecursionlimit(limit)
    return depth


def _hop_latency(ins: pd.DataFrame, outs: pd.DataFrame) -> pd.Series:
    """Mean hours between a wallet receiving value and next spending it.

    Near-zero latency is the signature of a pass-through or layering hop; a savings
    wallet or a merchant treasury sits on funds for days.
    """
    recv = outs.groupby("address")["timestamp"].apply(lambda s: np.sort(s.values))
    latencies = {}
    for addr, spends in ins.groupby("address")["timestamp"]:
        received = recv.get(addr)
        if received is None or not len(received):
            continue
        spend_times = np.sort(spends.values)
        idx = np.searchsorted(received, spend_times) - 1
        valid = idx >= 0
        if not valid.any():
            continue
        deltas = (spend_times[valid] - received[idx[valid]]).astype("timedelta64[s]").astype(float)
        latencies[addr] = float(np.mean(deltas) / 3600.0)
    return pd.Series(latencies, dtype=float)


def _max_window_count(times: np.ndarray, window_s: float = DAY_SECONDS) -> int:
    """Largest number of events inside any sliding window of the given width."""
    if len(times) < 2:
        return len(times)
    t = np.sort(times.astype("datetime64[s]").astype(np.int64))
    left = np.searchsorted(t, t - int(window_s), side="left")
    return int(np.max(np.arange(len(t)) - left + 1))


def wallet_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the per-wallet feature matrix the models score.

    Returns numeric feature columns plus a few metadata columns (prefixed meta_) that the
    dashboard shows but the model never sees.
    """
    ins, outs = explode(df)
    addresses = pd.Index(sorted(set(ins["address"]) | set(outs["address"])), name="address")
    f = pd.DataFrame(index=addresses)

    # --- value flow ---
    sent = ins.groupby("address")["amount"]
    recv = outs.groupby("address")["amount"]
    f["n_spends"] = sent.size().reindex(addresses).fillna(0)
    f["n_receives"] = recv.size().reindex(addresses).fillna(0)
    f["total_sent"] = sent.sum().reindex(addresses).fillna(0.0)
    f["total_received"] = recv.sum().reindex(addresses).fillna(0.0)
    f["mean_received"] = recv.mean().reindex(addresses).fillna(0.0)
    f["degree"] = f["n_spends"] + f["n_receives"]

    # Balance retention: ~0 means everything that arrived left again (pass-through /
    # layering); ~1 means the wallet accumulates.
    f["retention_ratio"] = (
        (f["total_received"] - f["total_sent"]) / f["total_received"].replace(0, np.nan)
    ).fillna(0.0).clip(-1, 1)

    # Uniform incoming amounts are a mixer/ransomware tell; organic traffic is ragged.
    # Uniformity, not raw CV. An isolation forest on quantile-transformed features only
    # reacts to values at an *extreme* of the distribution, and raw CV puts the signal in
    # the middle: over half of all wallets have a single receipt and no defined variation,
    # so a ransomware collector's genuinely rare CV of 0.011 sorts just above that mass and
    # reads as ordinary. Mapping to 1/(1+cv), with the undefined case pinned at 0, puts
    # "many receipts, all near-identical" at the top of the range where the model can see
    # it. MIN_SAMPLES guards against calling two coincidentally-equal payments a pattern.
    f["uniformity_received"] = _uniformity(recv, addresses)
    f["uniformity_sent"] = _uniformity(sent, addresses)
    round_frac = outs.assign(
        is_round=lambda d: (d["amount"] * 1e4).round().eq(d["amount"] * 1e4) & d["amount"].gt(0)
    ).groupby("address")["is_round"].mean()
    f["round_amount_frac"] = round_frac.reindex(addresses).fillna(0.0)

    # --- counterparties and transaction shape ---
    tx_shape = df.assign(
        n_in=df["input_addresses"].map(len), n_out=df["output_addresses"].map(len)
    )[["txid", "n_in", "n_out"]]
    f["max_tx_fanout"] = (
        ins.merge(tx_shape, on="txid").groupby("address")["n_out"].max().reindex(addresses).fillna(0)
    )
    f["max_tx_fanin"] = (
        outs.merge(tx_shape, on="txid").groupby("address")["n_in"].max().reindex(addresses).fillna(0)
    )
    counterparties = defaultdict(set)
    for row in df.itertuples(index=False):
        for a in row.input_addresses:
            counterparties[a].update(row.output_addresses)
        for a in row.output_addresses:
            counterparties[a].update(row.input_addresses)
    f["n_counterparties"] = pd.Series(
        {a: len(v) for a, v in counterparties.items()}, dtype=float
    ).reindex(addresses).fillna(0.0)

    # --- temporal ---
    events = pd.concat([ins[["address", "timestamp"]], outs[["address", "timestamp"]]])
    grouped = events.groupby("address")["timestamp"]
    first_seen, last_seen = grouped.min(), grouped.max()
    f["lifetime_days"] = (
        (last_seen - first_seen).dt.total_seconds() / DAY_SECONDS
    ).reindex(addresses).fillna(0.0)
    f["burst_max_24h"] = grouped.apply(lambda s: _max_window_count(s.values)).reindex(addresses).fillna(0)
    f["night_frac"] = (
        events.assign(night=events["timestamp"].dt.hour.lt(6))
        .groupby("address")["night"].mean().reindex(addresses).fillna(0.0)
    )
    interarrival = grouped.apply(
        lambda s: _cv(pd.Series(np.diff(np.sort(s.values)).astype("timedelta64[s]").astype(float)))
        if len(s) > 2 else np.nan
    )
    f["interarrival_cv"] = interarrival.reindex(addresses).fillna(UNDEFINED)
    f["hop_latency_h"] = _hop_latency(ins, outs).reindex(addresses).fillna(UNDEFINED)

    # --- graph structure ---
    f["upstream_max_receives"] = _upstream_max_receives(df, f["n_receives"], addresses)

    # Chain length *through* each wallet, not just the hops ahead of it. Measuring only
    # forward depth credits the head of a 20-hop peel with 20 and its nineteen successors
    # with progressively less, down to 0 at the tail -- yet every one of them is equally a
    # link in the same chain, and the tail is where the money is about to leave. Summing
    # the longest path in each direction gives every member the length it actually sits on.
    peel_edges = _peel_edges(df)
    forward = _chain_depths(peel_edges)
    backward = _chain_depths([(b, a) for a, b in peel_edges])
    depth = pd.Series(forward, dtype=float).add(pd.Series(backward, dtype=float), fill_value=0.0)
    f["peel_chain_depth"] = depth.reindex(addresses).fillna(0.0)
    clusters = entity_clusters(df)
    sizes = pd.Series(clusters).value_counts()
    f["entity_size"] = pd.Series(
        {a: sizes.get(c, 1) for a, c in clusters.items()}, dtype=float
    ).reindex(addresses).fillna(1.0)

    # --- network layer ---
    # Attribute the observed source IP to the *spender* only. The address that receives an
    # output did not broadcast the transaction and may never have been online; crediting it
    # with the sender's IP invents an association that was never observed, and it swamps
    # the shared-host signal by giving every payment recipient its payer's network
    # footprint. Wallets that only ever received therefore have no network features, which
    # is the honest answer rather than a borrowed one.
    net = ins[["address", "src_ip", "dst_ip", "dst_port", "script_type",
               "geo_country", "asn", "src_port", "timestamp"]]
    ng = net.groupby("address")
    f["n_ips"] = ng["src_ip"].nunique().reindex(addresses).fillna(0)
    f["n_countries"] = ng["geo_country"].nunique().reindex(addresses).fillna(0)
    f["n_asns"] = ng["asn"].nunique().reindex(addresses).fillna(0)
    f["nonstd_port_frac"] = (
        net.assign(nonstd=net["src_port"].ne(8333)).groupby("address")["nonstd"].mean()
        .reindex(addresses).fillna(0.0)
    )
    # How many otherwise-unrelated wallets share this wallet's busiest host? A high count
    # is the sybil/shared-infrastructure signal that on-chain data alone cannot see.
    wallets_per_ip = net.groupby("src_ip")["address"].nunique()
    f["shared_ip_cohort"] = (
        net.assign(cohort=net["src_ip"].map(wallets_per_ip)).groupby("address")["cohort"].max()
        .reindex(addresses).fillna(0.0)
    )
    f["ip_cohort_components"] = _ip_cohort_components(df, ins, addresses)
    # How many distinct peers does this wallet's busiest host dial out to? An ordinary node
    # keeps a small, stable peer set; a host flooding the network to announce wallets it
    # does not own keeps a wide one. Exchanges are deliberately wide too, so this is
    # corroborating evidence rather than a tell on its own.
    peers_per_ip = net.groupby("src_ip")["dst_ip"].nunique()
    f["host_peer_fanout"] = (
        net.assign(fanout=net["src_ip"].map(peers_per_ip))
        .groupby("address")["fanout"].max().reindex(addresses).fillna(0.0)
    )
    # Automated wallet software mints every address with one script type, so an operation
    # driven by a single tool is script-monotone where an ordinary user's host is mixed.
    # Dominant share rather than a distinct count: a host seen once is trivially monotone
    # on a count, which would make this a restatement of host activity.
    purity = net.groupby("src_ip")["script_type"].agg(
        lambda s: s.value_counts().iloc[0] / len(s))
    f["host_script_purity"] = (
        net.assign(purity=net["src_ip"].map(purity))
        .groupby("address")["purity"].max().reindex(addresses).fillna(0.0)
    )
    # Relaying to a non-standard destination port means the peer is not a public node --
    # private infrastructure. Symmetric to nonstd_port_frac on the source side.
    f["nonstd_dst_port_frac"] = (
        net.assign(nonstd=net["dst_port"].ne(8333))
        .groupby("address")["nonstd"].mean().reindex(addresses).fillna(0.0)
    )
    # Countries touched per active day -- a wallet legitimately moves, but not this fast.
    f["geo_hop_rate"] = f["n_countries"] / f["lifetime_days"].clip(lower=1.0)

    # --- metadata for the dashboard, never fed to the model ---
    f["meta_first_seen"] = first_seen.reindex(addresses)
    f["meta_last_seen"] = last_seen.reindex(addresses)
    f["meta_entity_id"] = pd.Series(clusters).reindex(addresses)
    f["meta_countries"] = ng["geo_country"].apply(lambda s: ",".join(sorted(set(s)))).reindex(addresses)
    f["meta_ips"] = ng["src_ip"].apply(lambda s: ",".join(sorted(set(s))[:8])).reindex(addresses)
    return f


def feature_columns(f: pd.DataFrame):
    """The numeric columns the model is allowed to see."""
    return [c for c in f.columns if not c.startswith("meta_")]
