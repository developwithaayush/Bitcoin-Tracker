"""Demo wallet: emit live transactions into the ingest corpus.

A presenter-facing sandbox. Buying and selling produce an ordinary wallet the detector
should ignore; the preset behaviours reproduce the shapes it should catch. Records are
written to data/raw/live.csv, which ingest.load_dir already concatenates alongside
traffic.csv, so nothing in the pipeline needed changing to accept them.

Amounts are derived the same way generate._emit derives them -- the fee is whatever the
inputs do not pay out -- so every emitted row passes ingest validation.
"""

from __future__ import annotations

import random
from pathlib import Path

from .generate import NETWORKS, Generator, write_csv
from .schema import SATOSHI_DP

HOUR = 3600
LIVE_CSV = Path("data/raw/live.csv")

# Preset sizes are chosen against the population percentiles detect.typologies uses:
# n_receives fires at >= 10, burst_max_24h at >= 4, peel_chain_depth at >= 5.
FANIN_RECEIPTS = 12
PEEL_HOPS = 8
GEOHOP_LEGS = 5


class Wallet:
    """One demo account. Every action appends canonical records and moves the balance."""

    def __init__(self, start_ts: int, seed: int | None = None, label: str = "demo"):
        seed = random.randrange(1 << 30) if seed is None else seed
        self._g = Generator(seed=seed)
        self.rng = self._g.rng
        self.label = label
        self.address = self._g._addr("p2wpkh")
        self.exchange = self._g._addr("p2sh")
        self.home = self._g._ip(NETWORKS[11])       # a plain domestic ISP
        self.ts = int(start_ts)
        self.balance = 0.0
        self.records: list = []
        self.log: list = []

    # ---------- primitives ----------

    def _tick(self, seconds: float) -> int:
        self.ts += int(seconds)
        return self.ts

    def _row(self, ts, ins, in_amts, outs, out_amts, ip=None) -> None:
        """One observation. The fee absorbs the difference, as a real transaction does."""
        src_ip, country, asn = ip or self.home
        in_amts = [round(v, SATOSHI_DP) for v in in_amts]
        out_amts = [round(v, SATOSHI_DP) for v in out_amts]
        self.records.append({
            "timestamp": int(ts),
            "src_ip": src_ip,
            "dst_ip": self._g._ip()[0],
            "src_port": 8333,
            "dst_port": 8333,
            "txid": self._g._txid(),
            "input_addresses": list(ins),
            "output_addresses": list(outs),
            "input_amounts": in_amts,
            "output_amounts": out_amts,
            "fee": round(sum(in_amts) - sum(out_amts), SATOSHI_DP),
            "script_type": "p2wpkh",
            "geo_country": country,
            "asn": asn,
        })

    def _note(self, action: str, detail: str) -> None:
        self.log.append({"action": action, "detail": detail, "balance": round(self.balance, 8)})

    def _fee(self) -> float:
        return round(self.rng.uniform(2e-6, 4e-5), SATOSHI_DP)

    # ---------- manual actions ----------

    def buy(self, amount: float) -> None:
        """Exchange pays into this wallet."""
        fee = self._fee()
        self._row(self._tick(self.rng.uniform(0.5, 6) * HOUR),
                  [self.exchange], [amount + fee], [self.address], [amount])
        self.balance = round(self.balance + amount, SATOSHI_DP)
        self._note("buy", f"{amount:.4f} BTC from exchange")

    def sell(self, amount: float) -> None:
        """This wallet pays back out to the exchange, keeping the remainder as change."""
        self._spend(amount, self.exchange, "sell", f"{amount:.4f} BTC to exchange")

    def send(self, amount: float, dest: str | None = None) -> str:
        """Ordinary payment to a counterparty."""
        dest = dest or self._g._addr()
        self._spend(amount, dest, "send", f"{amount:.4f} BTC to {dest[:12]}...")
        return dest

    def _spend(self, amount: float, dest: str, action: str, detail: str) -> None:
        if amount > self.balance:
            raise ValueError(f"balance {self.balance:.8f} cannot cover {amount:.8f}")
        fee = self._fee()
        change = round(self.balance - amount - fee, SATOSHI_DP)
        outs, amts = [dest], [amount]
        if change > 0:
            outs.append(self.address)
            amts.append(change)
        self._row(self._tick(self.rng.uniform(0.5, 6) * HOUR),
                  [self.address], [self.balance], outs, amts)
        self.balance = max(0.0, change)
        self._note(action, detail)

    # ---------- preset behaviours ----------

    def rapid_passthrough(self, n: int = 4) -> None:
        """Receive several payments, forward everything within the hour. Keeps nothing."""
        for _ in range(n):
            amt = round(self.rng.uniform(0.4, 2.5), SATOSHI_DP)
            fee = self._fee()
            self._row(self._tick(self.rng.uniform(6, 40) * 60),
                      [self._g._addr()], [amt + fee], [self.address], [amt])
            self.balance = round(self.balance + amt, SATOSHI_DP)
        out_fee = self._fee()
        moved = round(self.balance - out_fee, SATOSHI_DP)
        self._row(self._tick(self.rng.uniform(4, 30) * 60),
                  [self.address], [self.balance], [self._g._addr()], [moved])
        self.balance = 0.0
        self._note("rapid pass-through", f"{n} receipts forwarded in under an hour")

    def peeling_chain(self, hops: int = PEEL_HOPS) -> None:
        """Peel a small payment off at each hop, carry the remainder to a fresh address.

        Each hop is one input and exactly two outputs with the remainder above 60% of the
        funded value, which is the edge features._peel_edges follows.
        """
        held = round(max(self.balance, 4.0), SATOSHI_DP)
        if held > self.balance:                # top up so the chain has something to peel
            self.buy(round(held - self.balance, SATOSHI_DP))
        current, held = self.address, self.balance
        for _ in range(hops):
            fee = self._fee()
            peel = round(held * self.rng.uniform(0.08, 0.16), SATOSHI_DP)
            remainder = round(held - peel - fee, SATOSHI_DP)
            nxt = self._g._addr("p2wpkh")
            self._row(self._tick(self.rng.uniform(20, 90) * 60),
                      [current], [held], [self._g._addr(), nxt], [peel, remainder])
            current, held = nxt, remainder
        self.balance = 0.0
        self._note("peeling chain", f"{hops} hops, remainder now at {current[:12]}...")

    def fanin_collection(self, n: int = FANIN_RECEIPTS) -> None:
        """Many near-identical payments from unrelated payers inside one day."""
        base = round(self.rng.uniform(0.05, 0.4), SATOSHI_DP)
        for _ in range(n):
            amt = round(base * self.rng.uniform(0.97, 1.03), SATOSHI_DP)
            fee = self._fee()
            self._row(self._tick(self.rng.uniform(20, 100) * 60),
                      [self._g._addr()], [amt + fee], [self.address], [amt])
            self.balance = round(self.balance + amt, SATOSHI_DP)
        self._note("fan-in collection", f"{n} near-identical receipts of ~{base:.4f} BTC")

    def geo_hop(self, legs: int = GEOHOP_LEGS) -> None:
        """Broadcast this wallet's spends from several countries inside a day."""
        if self.balance < 0.5:
            self.buy(1.0)
        nets = self.rng.sample(NETWORKS, legs)
        for net in nets:
            if self.balance <= 0:
                break
            fee = self._fee()
            amt = round(self.balance * 0.3, SATOSHI_DP)
            change = round(self.balance - amt - fee, SATOSHI_DP)
            outs, amts = [self._g._addr()], [amt]
            if change > 0:
                outs.append(self.address)
                amts.append(change)
            self._row(self._tick(self.rng.uniform(30, 200) * 60),
                      [self.address], [self.balance], outs, amts, ip=self._g._ip(net))
            self.balance = max(0.0, change)
        self._note("geo hop", f"broadcast from {', '.join(n[0] for n in nets)}")

    # ---------- output ----------

    def save(self, path: Path = LIVE_CSV) -> Path:
        """Rewrite the live feed. Small by construction, so a full rewrite is cheapest."""
        return save_records(self.records, path)


def save_records(records, path: Path = LIVE_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(sorted(records, key=lambda r: r["timestamp"]), path)
    return path


def rescore(data_dir: Path = Path("data"), geoip: Path | None = None, top_k: int = 300) -> dict:
    """Re-ingest data/raw (traffic + live feed) and re-run detection over everything.

    The whole corpus is re-scored rather than the new wallets alone, because an
    IsolationForest score is a statement about a wallet's position in a population --
    there is no meaningful score for one wallet on its own.
    """
    import pandas as pd

    from .detect import detect
    from .features import wallet_features
    from .ingest import ingest

    if geoip is None:
        geoip = data_dir / "geoip" / "dbip-city-lite.mmdb"
    stats = ingest(data_dir / "raw", data_dir, geoip)
    df = pd.read_parquet(data_dir / "canonical.parquet")
    f = wallet_features(df)
    alerts = detect(df, f, top_k=top_k)
    alerts.to_parquet(data_dir / "alerts.parquet", index=False)
    f.to_parquet(data_dir / "features.parquet")
    return {"rows": stats["rows_accepted"], "rejected": stats["rows_rejected"],
            "wallets": len(alerts), "alerts": alerts}


def demo() -> None:
    """Self-check: emitted records survive ingest, and each preset fires its typology."""
    import tempfile

    import pandas as pd

    from .detect import typologies
    from .features import wallet_features
    from .ingest import load, validate

    start = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
    presets = {
        "rapid_passthrough": lambda w: w.rapid_passthrough(),
        "peeling_chain": lambda w: w.peeling_chain(),
        "ransomware_fanin": lambda w: w.fanin_collection(),
        "geo_hopping": lambda w: w.geo_hop(),
    }
    wallets = {}
    for i, (tag, act) in enumerate(presets.items()):
        w = Wallet(start_ts=start, seed=100 + i, label=tag)
        act(w)
        wallets[tag] = w

    # A plain buy/sell wallet must stay clean -- the contrast is the whole demo.
    clean = Wallet(start_ts=start, seed=7, label="clean")
    clean.buy(1.5)
    clean.send(0.4)
    clean.sell(0.3)
    wallets["clean"] = clean

    records = [r for w in wallets.values() for r in w.records]
    path = save_records(records, Path(tempfile.mkdtemp()) / "live.csv")

    good, bad = validate(load(path))
    assert not len(bad), f"emitted invalid rows: {bad['reject_reason'].tolist()[:3]}"
    assert len(good) == len(records), f"{len(good)} of {len(records)} rows survived"

    tags = typologies(wallet_features(good))
    for tag, w in wallets.items():
        got = tags.get(w.address, [])
        if tag == "clean":
            assert not got, f"plain buy/sell wallet was tagged {got}"
        else:
            assert tag in got, f"{tag} preset produced {got or 'no typology'}"
    print(f"PASS  wallet presets fire their typologies, {len(records)} rows, 0 rejected")


if __name__ == "__main__":
    demo()
