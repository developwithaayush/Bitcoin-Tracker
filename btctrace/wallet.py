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
import re
from pathlib import Path

from .generate import B32, B58, NETWORKS, Generator, write_csv
from .schema import SATOSHI_DP

HOUR = 3600
LIVE_CSV = Path("data/raw/live.csv")

# A destination typed by a presenter is the one value in this module that does not come
# from the generator, and ingest validates IPs, ports, txids and amounts but never
# address shape -- so a typo would sail through and land in the link graph as a node
# named after the typo. Lengths are the real Bitcoin ranges rather than the exact ones
# _addr emits, so a genuine address pasted from elsewhere is still accepted.
ADDRESS_RE = re.compile(
    rf"(?:[13][{B58}]{{25,34}}|bc1q[{B32}]{{38,58}}|bc1p[{B32}]{{38,58}})"
)

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
        # One person, many addresses. Receipts land on `address` so the account has a
        # stable identity to look up, but change never comes back to it -- see _fresh.
        self.addresses = [self.address]
        self.exchange = self._g._addr("p2sh")
        self.home = self._g._ip(NETWORKS[11])       # a plain domestic ISP
        self.ts = int(start_ts)
        # A wallet holds coins as discrete unspent outputs, not as a number. Modelling
        # that is what lets a spend consume several of them at once, which is the shape
        # real Bitcoin transactions have and the shape the features are written against.
        self.utxos: list[dict] = []
        self.records: list = []
        self.log: list = []

    @property
    def balance(self) -> float:
        """Derived, never assigned: the coins are the truth, the total is a reading."""
        return round(sum(u["amount"] for u in self.utxos), SATOSHI_DP)

    # ---------- primitives ----------

    def _tick(self, seconds: float) -> int:
        self.ts += int(seconds)
        return self.ts

    def _fresh(self) -> str:
        """A new address this wallet owns, for change to land on.

        A spend consumes whole outputs, so the leftover has to be paid back somewhere.
        Sending it to the address that just spent would tie every payment this wallet
        ever makes to one public identifier, so wallets derive a fresh one each time --
        which is why one person ends up owning thousands of addresses.
        """
        addr = self._g._addr("p2wpkh")
        self.addresses.append(addr)
        return addr

    def _credit(self, txid: str, amount: float, ts: int, address: str | None = None) -> None:
        self.utxos.append({"txid": txid, "amount": round(amount, SATOSHI_DP), "ts": int(ts),
                           "address": address or self.address})

    def _select(self, target: float) -> list[dict]:
        """Coins to fund `target`, oldest first, or everything if that is not enough.

        ponytail: FIFO, not branch-and-bound. It is what a simple wallet does, it is
        stable across runs so a demo repeats, and it produces the multi-input spends
        that make the emitted transactions look real. Swap in a smarter selector only
        if the change-output pattern itself ever becomes the thing being detected.
        """
        chosen: list[dict] = []
        total = 0.0
        for u in sorted(self.utxos, key=lambda u: u["ts"]):
            chosen.append(u)
            total = round(total + u["amount"], SATOSHI_DP)
            if total >= target:
                break
        return chosen

    def _row(self, ts, ins, in_amts, outs, out_amts, ip=None) -> str:
        """One observation. The fee absorbs the difference, as a real transaction does."""
        src_ip, country, asn = ip or self.home
        in_amts = [round(v, SATOSHI_DP) for v in in_amts]
        out_amts = [round(v, SATOSHI_DP) for v in out_amts]
        txid = self._g._txid()
        self.records.append({
            "timestamp": int(ts),
            "src_ip": src_ip,
            "dst_ip": self._g._ip()[0],
            "src_port": 8333,
            "dst_port": 8333,
            "txid": txid,
            "input_addresses": list(ins),
            "output_addresses": list(outs),
            "input_amounts": in_amts,
            "output_amounts": out_amts,
            "fee": round(sum(in_amts) - sum(out_amts), SATOSHI_DP),
            "script_type": "p2wpkh",
            "geo_country": country,
            "asn": asn,
        })
        return txid

    def _note(self, action: str, detail: str) -> None:
        self.log.append({"action": action, "detail": detail, "balance": round(self.balance, 8)})

    def _fee(self) -> float:
        return round(self.rng.uniform(2e-6, 4e-5), SATOSHI_DP)

    # ---------- manual actions ----------

    def buy(self, amount: float) -> None:
        """Exchange pays into this wallet, creating one new unspent output."""
        fee = self._fee()
        ts = self._tick(self.rng.uniform(0.5, 6) * HOUR)
        txid = self._row(ts, [self.exchange], [amount + fee], [self.address], [amount])
        self._credit(txid, amount, ts)
        self._note("buy", f"{amount:.4f} BTC from exchange")

    def sell(self, amount: float) -> None:
        """This wallet pays back out to the exchange, keeping the remainder as change."""
        self._spend(amount, self.exchange, "sell", f"{amount:.4f} BTC to exchange")

    def send(self, amount: float, dest: str | None = None) -> str:
        """Ordinary payment to a counterparty. `dest` None picks a random one."""
        if dest is not None and not ADDRESS_RE.fullmatch(dest):
            raise ValueError(f"{dest!r} is not a Bitcoin address")
        dest = dest or self._g._addr()
        self._spend(amount, dest, "send", f"{amount:.4f} BTC to {dest[:12]}...")
        return dest

    def _spend(self, amount: float, dest: str, action: str, detail: str,
               ip=None, log: bool = True, change_to: str | None = None) -> None:
        """Fund `amount` from the coins on hand, paying the remainder back as change."""
        if amount > self.balance:
            raise ValueError(f"balance {self.balance:.8f} cannot cover {amount:.8f}")
        fee = self._fee()
        chosen = self._select(round(amount + fee, SATOSHI_DP))
        funded = round(sum(u["amount"] for u in chosen), SATOSHI_DP)
        # The coins may cover the payment but not the payment plus the fee. A real
        # wallet then simply pays a smaller fee rather than failing, and _row derives
        # the fee from what the inputs do not pay out, so this needs no special case.
        change = round(funded - amount - fee, SATOSHI_DP)
        outs, amts = [dest], [amount]
        if change > 0:
            # `change_to` reuses a given address instead of deriving one. Only geo_hop
            # asks for that, because address reuse is what makes its legs one wallet.
            outs.append(change_to or self._fresh())
            amts.append(change)
        ts = self._tick(self.rng.uniform(0.5, 6) * HOUR)
        # Coins may sit on several of this wallet's addresses, and spending them together
        # is precisely the co-spend that features.entity_clusters uses to put the wallet
        # back together from the outside.
        txid = self._row(ts, [u["address"] for u in chosen],
                         [u["amount"] for u in chosen], outs, amts, ip=ip)
        for u in chosen:
            self.utxos.remove(u)
        if change > 0:
            self._credit(txid, change, ts, outs[-1])
        if log:
            self._note(action, detail)

    # ---------- preset behaviours ----------

    def rapid_passthrough(self, n: int = 4) -> None:
        """Receive several payments, forward everything within the hour. Keeps nothing."""
        for _ in range(n):
            amt = round(self.rng.uniform(0.4, 2.5), SATOSHI_DP)
            fee = self._fee()
            ts = self._tick(self.rng.uniform(6, 40) * 60)
            txid = self._row(ts, [self._g._addr()], [amt + fee], [self.address], [amt])
            self._credit(txid, amt, ts)
        # Forwarding sweeps every coin into one transaction, which is exactly what a
        # consolidating pass-through looks like on chain.
        held = list(self.utxos)
        out_fee = self._fee()
        moved = round(self.balance - out_fee, SATOSHI_DP)
        self._row(self._tick(self.rng.uniform(4, 30) * 60),
                  [u["address"] for u in held], [u["amount"] for u in held],
                  [self._g._addr()], [moved])
        self.utxos.clear()
        self._note("rapid pass-through", f"{n} receipts forwarded in under an hour")

    def peeling_chain(self, hops: int = PEEL_HOPS) -> None:
        """Peel a small payment off at each hop, carry the remainder to a fresh address.

        Each hop is one input and exactly two outputs with the remainder above 60% of the
        funded value, which is the edge features._peel_edges follows.
        """
        held = round(max(self.balance, 4.0), SATOSHI_DP)
        if held > self.balance:                # top up so the chain has something to peel
            self.buy(round(held - self.balance, SATOSHI_DP))
        coins = list(self.utxos)
        current, held = self.address, self.balance
        ins0 = [u["address"] for u in coins]
        for hop in range(hops):
            fee = self._fee()
            peel = round(held * self.rng.uniform(0.08, 0.16), SATOSHI_DP)
            remainder = round(held - peel - fee, SATOSHI_DP)
            nxt = self._g._addr("p2wpkh")
            # Only the first hop spends this wallet's coins; every hop after it spends
            # the single remainder output the hop before created.
            ins = ins0 if hop == 0 else [current]
            amts = [u["amount"] for u in coins] if hop == 0 else [held]
            self._row(self._tick(self.rng.uniform(20, 90) * 60),
                      ins, amts, [self._g._addr(), nxt], [peel, remainder])
            current, held = nxt, remainder
        self.utxos.clear()
        self._note("peeling chain", f"{hops} hops, remainder now at {current[:12]}...")

    def fanin_collection(self, n: int = FANIN_RECEIPTS) -> None:
        """Many near-identical payments from unrelated payers inside one day."""
        base = round(self.rng.uniform(0.05, 0.4), SATOSHI_DP)
        for _ in range(n):
            amt = round(base * self.rng.uniform(0.97, 1.03), SATOSHI_DP)
            fee = self._fee()
            ts = self._tick(self.rng.uniform(20, 100) * 60)
            txid = self._row(ts, [self._g._addr()], [amt + fee], [self.address], [amt])
            self._credit(txid, amt, ts)
        self._note("fan-in collection", f"{n} near-identical receipts of ~{base:.4f} BTC")

    def geo_hop(self, legs: int = GEOHOP_LEGS) -> None:
        """Broadcast this wallet's spends from several countries inside a day."""
        if self.balance < 0.5:
            self.buy(1.0)
        nets = self.rng.sample(NETWORKS, legs)
        for net in nets:
            if self.balance <= 0:
                break
            amt = round(self.balance * 0.3, SATOSHI_DP)
            self._spend(amt, self._g._addr(), "geo hop", "", ip=self._g._ip(net),
                        log=False, change_to=self.address)
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
    from .features import entity_clusters, wallet_features
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

    # Coins, not a running total: two receipts are two spendable outputs, a payment that
    # needs both spends both, and the leftover comes back as exactly one change coin.
    coins = Wallet(start_ts=start, seed=9)
    coins.buy(0.5)
    coins.buy(0.4)
    assert len(coins.utxos) == 2, f"two buys made {len(coins.utxos)} coins"
    coins.send(0.7)                       # neither coin covers this alone
    spent = coins.records[-1]
    assert len(spent["input_amounts"]) == 2, "payment did not consume both coins"
    assert len(coins.utxos) == 1, f"change left {len(coins.utxos)} coins"
    assert abs(coins.balance - sum(u["amount"] for u in coins.utxos)) < 1e-9
    assert coins.balance < 0.2, f"change of {coins.balance} is too large"

    # The change coin lands on a *fresh* address this wallet owns, not on the one that
    # just spent -- and co-spending it later is what lets the common-input heuristic put
    # the pieces back together from outside.
    change_addr = spent["output_addresses"][1]
    assert change_addr in coins.addresses, "change left the wallet"
    assert change_addr not in spent["input_addresses"], "change reused the spending address"
    coins.buy(0.3)
    coins.send(0.4)                       # needs the change coin and the new one
    assert len(coins.records[-1]["input_addresses"]) == 2, "did not co-spend two addresses"
    merged = entity_clusters(pd.DataFrame(coins.records))
    assert merged[coins.address] == merged[change_addr], "co-spend did not re-merge them"
    fanin = Wallet(start_ts=start, seed=10)
    fanin.fanin_collection()
    assert len(fanin.utxos) == FANIN_RECEIPTS, "a fan-in should leave one coin per payer"
    wallets["coins"] = coins

    # A named destination is paid, and a mistyped one is refused rather than quietly
    # becoming a node in the link graph.
    payee = Wallet(start_ts=start, seed=11)
    named = Wallet(start_ts=start, seed=12)
    named.buy(1.0)
    named.send(0.25, payee.address)
    assert payee.address in named.records[-1]["output_addresses"], "named payee not paid"
    for junk in ("", "aayush", "bc1q!!!", "4" + "x" * 30, payee.address + "z" * 40):
        try:
            named.send(0.1, junk)
        except ValueError:
            continue
        raise AssertionError(f"{junk!r} was accepted as an address")
    wallets["named"] = named

    records = [r for w in wallets.values() for r in w.records]
    path = save_records(records, Path(tempfile.mkdtemp()) / "live.csv")

    good, bad = validate(load(path))
    assert not len(bad), f"emitted invalid rows: {bad['reject_reason'].tolist()[:3]}"
    assert len(good) == len(records), f"{len(good)} of {len(records)} rows survived"

    tags = typologies(wallet_features(good))
    ordinary = {"clean", "named", "coins"}   # manual trading, so nothing should fire
    for tag, w in wallets.items():
        got = tags.get(w.address, [])
        if tag in ordinary:
            assert not got, f"{tag} buy/sell wallet was tagged {got}"
        else:
            assert tag in got, f"{tag} preset produced {got or 'no typology'}"
    print(f"PASS  presets fire their typologies, addresses validated, "
          f"{len(records)} rows, 0 rejected")


if __name__ == "__main__":
    demo()
