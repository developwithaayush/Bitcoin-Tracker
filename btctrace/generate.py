"""Synthetic Bitcoin P2P traffic generator with planted ground-truth typologies.

Produces the same set of records in CSV, JSON and XML, plus a ground_truth.csv naming
every wallet that belongs to a planted illicit pattern.

The point of the planted labels is measurement: without them "the model flags suspicious
wallets" is unfalsifiable. With them we can report precision and recall (see cli evaluate).

Benign actors deliberately include *hard negatives* -- exchange hot wallets and busy
merchants whose in/out degree overlaps the illicit actors. Degree alone must not separate
the classes, otherwise the reported precision measures nothing but the generator.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .schema import COLUMNS, LIST_SEP, SATOSHI_DP, SCRIPT_TYPES

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
HEX = "0123456789abcdef"

SCRIPT_WEIGHTS = [0.18, 0.14, 0.55, 0.13]

# (country, asn, asn_name) -- plausible hosting/consumer networks a node might sit behind.
NETWORKS = [
    ("US", 16509, "AMAZON-02"), ("US", 14061, "DIGITALOCEAN-ASN"),
    ("DE", 24940, "HETZNER-AS"), ("DE", 3320, "DTAG"),
    ("NL", 60781, "LEASEWEB-NL"), ("FR", 16276, "OVH"),
    ("GB", 2856, "BT-UK-AS"), ("RU", 12389, "ROSTELECOM-AS"),
    ("CN", 4134, "CHINANET-BACKBONE"), ("JP", 2497, "IIJ"),
    ("SG", 9299, "IPG-PLDT"), ("IN", 55836, "RELIANCE-JIO"),
    ("IN", 9829, "BSNL-NIB"), ("BR", 28573, "CLARO-BR"),
    ("UA", 15895, "KSNET-AS"), ("SC", 202425, "INT-NETWORK"),
    ("PA", 51852, "PLI-AS"), ("MD", 39798, "MIVOCLOUD"),
]
# Networks favoured by actors trying to obscure origin -- bulletproof / anonymising hosts.
EVASIVE = [n for n in NETWORKS if n[1] in (202425, 51852, 39798, 15895)]

DAY = 86400
START_TS = 1_754_006_400  # 2025-08-01T00:00:00Z, fixed so runs are reproducible

# Diurnal weighting: real P2P traffic is not uniform across the clock.
HOUR_WEIGHTS = [0.4, 0.3, 0.25, 0.22, 0.25, 0.35, 0.6, 0.9, 1.2, 1.5, 1.7, 1.8,
                1.75, 1.7, 1.65, 1.6, 1.5, 1.4, 1.3, 1.2, 1.05, 0.9, 0.7, 0.5]


@dataclass
class Actor:
    """A logical owner of one or more wallets, broadcasting from its own IP pool."""

    kind: str
    wallets: list = field(default_factory=list)
    ips: list = field(default_factory=list)  # list of (ip, country, asn)


class Generator:
    def __init__(self, seed: int = 1337, days: int = 30):
        self.rng = random.Random(seed)
        self.days = days
        self.records: list = []
        # wallet -> (typology, operation_id). The operation id groups every wallet
        # belonging to one criminal operation, so evaluation can ask the question an
        # investigator actually cares about -- "did we get a lead into this operation?" --
        # instead of only "did we rank every last intermediate address highly?".
        self.labels: dict = {}
        self._op_counter = 0
        self._addr_script: dict = {}

    # ---------- primitives ----------

    def _addr(self, script_type=None) -> str:
        r = self.rng
        st = script_type or r.choices(SCRIPT_TYPES, weights=SCRIPT_WEIGHTS)[0]
        if st == "p2pkh":
            a = "1" + "".join(r.choices(B58, k=33))
        elif st == "p2sh":
            a = "3" + "".join(r.choices(B58, k=33))
        elif st == "p2wpkh":
            a = "bc1q" + "".join(r.choices(B32, k=38))
        else:
            a = "bc1p" + "".join(r.choices(B32, k=58))
        self._addr_script[a] = st
        return a

    def _txid(self) -> str:
        return "".join(self.rng.choices(HEX, k=64))

    def _ip(self, net=None):
        r = self.rng
        country, asn, _ = net or r.choice(NETWORKS)
        ip = ".".join(str(r.randint(1, 254)) for _ in range(4))
        return ip, country, asn

    def _time(self, day=None) -> int:
        """Pick a timestamp for an *independent* event, with a realistic diurnal shape.

        The diurnal hour is redrawn on every call, so this must not be used to place two
        events a known short interval apart -- the hour jitter would swamp the interval.
        Use the returned value as a base and add explicit seconds instead (see _after).
        """
        r = self.rng
        d = r.uniform(0, self.days) if day is None else day
        hour = r.choices(range(24), weights=HOUR_WEIGHTS)[0]
        return int(START_TS + d * DAY + hour * 3600 + r.randint(0, 3599))

    def _after(self, base: int, lo_s: float, hi_s: float) -> int:
        """A timestamp a controlled number of seconds after base.

        Dwell time is the signal that separates a layering hop from a savings wallet, so
        the interval has to survive into the data exactly as intended.
        """
        return int(base + self.rng.uniform(lo_s, hi_s))

    def _amt(self, lo: float, hi: float, lognormal: bool = True) -> float:
        """Draw an amount in [lo, hi].

        The lognormal is fitted to the requested range (centred on its geometric mean,
        sigma set so ~95% of mass lands inside) rather than drawn from a fixed
        distribution and clamped. Clamping collapsed every range whose lower bound sat
        above the fixed median onto a constant, which handed benign actors the uniform
        amounts that are supposed to be a mixer tell.
        """
        import math
        r = self.rng
        if lognormal:
            mu = math.log(math.sqrt(lo * hi))
            sigma = max(1e-6, math.log(hi / lo) / 4.0)
            v = min(hi, max(lo, r.lognormvariate(mu, sigma)))
        else:
            v = r.uniform(lo, hi)
        return round(v, SATOSHI_DP)

    def _actor(self, kind, n_wallets, n_ips=1, evasive=False) -> Actor:
        nets = EVASIVE if evasive else NETWORKS
        return Actor(
            kind=kind,
            wallets=[self._addr() for _ in range(n_wallets)],
            ips=[self._ip(self.rng.choice(nets)) for _ in range(n_ips)],
        )

    def _label(self, wallets, typology: str) -> None:
        self._op_counter += 1
        op = f"{typology}-{self._op_counter:03d}"
        for w in wallets:
            self.labels[w] = (typology, op)

    # ---------- record emission ----------

    def _emit(self, ts, actor, in_addrs, in_amts, out_addrs, out_amts, ip=None, port=None):
        """Append one network observation of one transaction.

        Inputs fund outputs plus fee; the fee is whatever is left over, which is how a real
        transaction works and what ingest validates on the way back in.
        """
        r = self.rng
        fee = round(sum(in_amts) - sum(out_amts), SATOSHI_DP)
        if ip is not None:
            src_ip, country, asn = ip
        elif actor.ips:
            src_ip, country, asn = actor.ips[r.randrange(len(actor.ips))]
        else:
            src_ip, country, asn = self._ip()
        dst_ip, _, _ = self._ip()
        self.records.append({
            "timestamp": int(ts),
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "src_port": port or r.choice([8333] * 12 + [18333, 9001, 49152, 51413]),
            "dst_port": 8333,
            "txid": self._txid(),
            "input_addresses": list(in_addrs),
            "output_addresses": list(out_addrs),
            "input_amounts": [round(v, SATOSHI_DP) for v in in_amts],
            "output_amounts": [round(v, SATOSHI_DP) for v in out_amts],
            "fee": fee,
            "script_type": self._addr_script.get(in_addrs[0], "p2wpkh"),
            "geo_country": country,
            "asn": asn,
        })

    def _fee(self, n_in: int, n_out: int) -> float:
        """Fee scaled to transaction weight, as a real wallet would estimate it."""
        vsize = 10 + 68 * n_in + 31 * n_out
        return round(vsize * self.rng.uniform(2e-8, 3.5e-7), SATOSHI_DP)

    def _payment(self, actor: Actor, ts, src_wallet=None, dest=None):
        """A normal payment: one to three inputs, a payment output and a change output."""
        r = self.rng
        src = src_wallet or r.choice(actor.wallets)
        n_in = r.choices([1, 2, 3], weights=[0.7, 0.22, 0.08])[0]
        ins = [src] + [r.choice(actor.wallets) for _ in range(n_in - 1)]
        in_amts = [self._amt(1e-4, 40.0) for _ in ins]
        fee = self._fee(len(ins), 2)
        total = round(sum(in_amts) - fee, SATOSHI_DP)
        pay = round(total * r.uniform(0.05, 0.9), SATOSHI_DP)
        change = round(total - pay, SATOSHI_DP)
        if pay <= 0 or change <= 0:
            return
        change_addr = self._addr()
        actor.wallets.append(change_addr)
        self._emit(ts, actor, ins, in_amts, [dest or self._addr(), change_addr], [pay, change])

    # ---------- benign actors ----------

    def benign(self, n_actors: int, tx_each) -> None:
        for _ in range(n_actors):
            a = self._actor("benign", self.rng.randint(1, 4), n_ips=1)
            for _ in range(self.rng.randint(*tx_each)):
                self._payment(a, self._time())

    def exchange(self, n_actors: int) -> None:
        """HARD NEGATIVE: huge fan-in and fan-out, reused hot wallets, global user base.

        Looks structurally like a mixer on degree alone. What separates it: addresses are
        reused for months, amounts are heterogeneous, and it broadcasts from a stable ASN.
        """
        for _ in range(n_actors):
            a = self._actor("exchange", 3, n_ips=self.rng.randint(2, 4))
            hot = a.wallets[0]
            for _ in range(self.rng.randint(60, 140)):  # deposits: many senders -> hot wallet
                amt = self._amt(1e-3, 30.0)
                fee = self._fee(1, 2)
                change = round(max(1e-5, amt * self.rng.uniform(0.1, 0.6)), SATOSHI_DP)
                self._emit(self._time(), a, [self._addr()],
                           [round(amt + fee + change, SATOSHI_DP)],
                           [hot, self._addr()], [amt, change])
            for _ in range(self.rng.randint(50, 120)):  # withdrawals: hot wallet -> many
                amt = self._amt(1e-3, 25.0)
                fee = self._fee(2, 2)
                change = self._amt(0.5, 60.0)
                self._emit(self._time(), a, [hot, a.wallets[1]],
                           [round(amt + fee, SATOSHI_DP), change],
                           [self._addr(), a.wallets[2]], [amt, change])

    def merchant(self, n_actors: int) -> None:
        """HARD NEGATIVE: high fan-in from customers, regular sweeps to a treasury."""
        for _ in range(n_actors):
            a = self._actor("merchant", 2, n_ips=1)
            till, treasury = a.wallets[0], a.wallets[1]
            for _ in range(self.rng.randint(40, 90)):
                amt = self._amt(5e-4, 2.0)
                fee = self._fee(1, 2)
                self._emit(self._time(), a, [self._addr()], [round(amt + fee + 0.0001, SATOSHI_DP)],
                           [till, self._addr()], [amt, 0.0001])
            for d in range(self.days // 3):  # periodic sweep -- regular, not bursty
                fee = self._fee(1, 1)
                v = self._amt(1.0, 20.0)
                self._emit(self._time(d * 3), a, [till], [round(v + fee, SATOSHI_DP)],
                           [treasury], [v])

    # ---------- illicit typologies ----------

    def ransomware(self, n_actors: int) -> None:
        """Many first-seen victim wallets pay a near-identical amount into one collector
        inside a tight window, then the collector consolidates and cashes out."""
        for _ in range(n_actors):
            a = self._actor("ransomware", 2, n_ips=self.rng.randint(1, 2), evasive=True)
            collector, cashout = a.wallets[0], a.wallets[1]
            ransom = round(self.rng.choice([0.05, 0.1, 0.25, 0.5]), SATOSHI_DP)
            day0 = self.rng.uniform(0, max(1.0, self.days - 8))
            n_victims = self.rng.randint(18, 55)
            last_payment = 0
            for _ in range(n_victims):
                ts = self._time(day0 + self.rng.uniform(0, 5))  # tight burst window
                last_payment = max(last_payment, ts)
                amt = round(ransom * self.rng.uniform(0.98, 1.02), SATOSHI_DP)
                fee = self._fee(1, 2)
                change = self._amt(0.01, 3.0)
                self._emit(ts, a, [self._addr()], [round(amt + fee + change, SATOSHI_DP)],
                           [collector, self._addr()], [amt, change])
            fee = self._fee(1, 1)
            gross = round(ransom * n_victims - fee, SATOSHI_DP)
            # Cash out after the last victim has paid -- anchored to the observed maximum
            # so the collector can never spend funds it has not yet received.
            self._emit(self._after(last_payment, 6 * 3600, 48 * 3600), a,
                       [collector], [round(gross + fee, SATOSHI_DP)], [cashout], [gross])
            self._label([collector, cashout], "ransomware_fanin")

    def peeling_chain(self, n_actors: int) -> None:
        """A large balance walks down a chain of fresh addresses, shedding a small
        constant payout at each hop and carrying the remainder forward."""
        for _ in range(n_actors):
            a = self._actor("peel", 0, n_ips=self.rng.randint(1, 3), evasive=True)
            balance = round(self.rng.uniform(8.0, 60.0), SATOSHI_DP)
            cur = self._addr()
            chain = [cur]
            ts = self._time(self.rng.uniform(0, max(1.0, self.days - 10)))
            peel = round(balance * self.rng.uniform(0.01, 0.04), SATOSHI_DP)
            for _ in range(self.rng.randint(12, 28)):
                fee = self._fee(1, 2)
                nxt = self._addr()
                remainder = round(balance - peel - fee, SATOSHI_DP)
                if remainder <= peel:
                    break
                ts = self._after(ts, 600, 21600)  # hops 10 minutes to 6 hours apart
                self._emit(ts, a, [cur], [balance], [self._addr(), nxt], [peel, remainder])
                cur, balance = nxt, remainder
                chain.append(cur)
            self._label(chain, "peeling_chain")

    def mixer(self, n_actors: int) -> None:
        """Fan out to single-use intermediates in uniform slices, then recombine.

        Overlaps an exchange on degree; differs in address reuse (zero), amount
        uniformity (high) and dwell time (minutes).
        """
        for _ in range(n_actors):
            a = self._actor("mixer", 1, n_ips=self.rng.randint(2, 5), evasive=True)
            source = a.wallets[0]
            n_hops = self.rng.randint(10, 26)
            slice_amt = round(self.rng.uniform(0.2, 2.0), SATOSHI_DP)
            t0 = self._time(self.rng.uniform(0, max(1.0, self.days - 5)))
            funding_fee = self._fee(1, n_hops)
            inters = [self._addr() for _ in range(n_hops)]
            self._emit(t0, a, [source],
                       [round(slice_amt * n_hops + funding_fee, SATOSHI_DP)],
                       inters, [slice_amt] * n_hops)
            sink = self._addr()
            for i in inters:  # each intermediate is used once and abandoned
                fee = self._fee(1, 1)
                out = round(slice_amt - fee, SATOSHI_DP)
                # short dwell: value rests in the intermediate for minutes, not days
                self._emit(self._after(t0, 180, 7200), a, [i], [slice_amt], [sink], [out])
            self._label([source, sink, *inters], "mixer_layering")

    def passthrough(self, n_actors: int) -> None:
        """Funds arrive and leave within minutes; near-zero balance retention."""
        for _ in range(n_actors):
            a = self._actor("passthrough", 1, n_ips=1, evasive=True)
            w = a.wallets[0]
            for _ in range(self.rng.randint(6, 18)):
                amt = self._amt(0.5, 12.0, lognormal=False)
                fee_in = self._fee(1, 1)
                t_in = self._time(self.rng.uniform(0, max(1.0, self.days - 1)))
                self._emit(t_in, a, [self._addr()],
                           [round(amt + fee_in, SATOSHI_DP)], [w], [amt])
                fee_out = self._fee(1, 1)
                # out again within minutes -- the defining trait of a pass-through
                self._emit(self._after(t_in, 45, 420), a,
                           [w], [amt], [self._addr()], [round(amt - fee_out, SATOSHI_DP)])
            self._label([w], "rapid_passthrough")

    def sybil_broadcast(self, n_actors: int) -> None:
        """NETWORK-LAYER: one host broadcasts for many wallets that share no on-chain
        link. Invisible to blockchain-only analysis -- this is the correlation payoff."""
        for _ in range(n_actors):
            ip = self._ip(self.rng.choice(EVASIVE))
            controlled = []
            for _ in range(self.rng.randint(12, 30)):
                w = self._addr()
                controlled.append(w)
                sub = Actor(kind="sybil", wallets=[w], ips=[ip])
                for _ in range(self.rng.randint(2, 6)):
                    self._payment(sub, self._time(), src_wallet=w)
            self._label(controlled, "sybil_broadcast")

    def geo_hop(self, n_actors: int) -> None:
        """NETWORK-LAYER: one wallet broadcasts from many countries/ASNs within hours."""
        for _ in range(n_actors):
            a = self._actor("geohop", 1, n_ips=0)
            w = a.wallets[0]
            ts = self._time(self.rng.uniform(0, max(1.0, self.days - 2)))
            nets = self.rng.sample(NETWORKS, self.rng.randint(6, 11))
            for net in nets:
                sub = Actor(kind="geohop", wallets=[w], ips=[self._ip(net)])
                self._payment(sub, ts, src_wallet=w)
                ts = self._after(ts, 900, 5400)  # continent-hopping within hours
            self._label([w], "geo_hopping")

    # ---------- orchestration ----------

    def run(self, scale: float = 1.0) -> None:
        """Plant illicit patterns first, then pad with benign traffic to bury them."""
        s = lambda n: max(1, int(round(n * scale)))
        self.ransomware(s(6))
        self.peeling_chain(s(8))
        self.mixer(s(5))
        self.passthrough(s(10))
        self.sybil_broadcast(s(4))
        self.geo_hop(s(8))
        self.exchange(s(6))
        self.merchant(s(10))
        self.benign(s(400), (2, 12))
        self.records.sort(key=lambda r: r["timestamp"])


# ---------- writers ----------

def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_csv(records, path: Path) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in records:
            row = dict(r)
            row["timestamp"] = _iso(r["timestamp"])
            for c in ("input_addresses", "output_addresses"):
                row[c] = LIST_SEP.join(r[c])
            for c in ("input_amounts", "output_amounts"):
                row[c] = LIST_SEP.join("%.8f" % v for v in r[c])
            row["fee"] = "%.8f" % r["fee"]
            w.writerow(row)


def write_json(records, path: Path) -> None:
    out = []
    for r in records:
        d = {c: r[c] for c in COLUMNS}
        d["timestamp"] = _iso(r["timestamp"])
        out.append(d)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")


def write_xml(records, path: Path) -> None:
    """Nested elements, not flattened attributes -- the parser has to do real work."""
    root = ET.Element("traffic")
    scalars = [c for c in COLUMNS if c not in
               ("input_addresses", "output_addresses", "input_amounts", "output_amounts")]
    for r in records:
        tx = ET.SubElement(root, "observation")
        for c in scalars:
            if c == "timestamp":
                text = _iso(r[c])
            elif c == "fee":
                text = "%.8f" % r[c]
            else:
                text = str(r[c])
            ET.SubElement(tx, c).text = text
        ins = ET.SubElement(tx, "inputs")
        for a, v in zip(r["input_addresses"], r["input_amounts"]):
            e = ET.SubElement(ins, "input")
            ET.SubElement(e, "address").text = a
            ET.SubElement(e, "amount").text = "%.8f" % v
        outs = ET.SubElement(tx, "outputs")
        for a, v in zip(r["output_addresses"], r["output_amounts"]):
            e = ET.SubElement(outs, "output")
            ET.SubElement(e, "address").text = a
            ET.SubElement(e, "amount").text = "%.8f" % v
    ET.indent(root, space=" ")
    ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)


def write_ground_truth(labels: dict, path: Path) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "typology", "operation_id"])
        for addr, (typ, op) in sorted(labels.items()):
            w.writerow([addr, typ, op])


def generate(out_dir: Path, seed: int = 1337, scale: float = 1.0, days: int = 30) -> dict:
    g = Generator(seed=seed, days=days)
    g.run(scale=scale)
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    write_csv(g.records, raw / "traffic.csv")
    write_json(g.records, raw / "traffic.json")
    write_xml(g.records, raw / "traffic.xml")
    write_ground_truth(g.labels, out_dir / "ground_truth.csv")
    wallets = set()
    for r in g.records:
        wallets.update(r["input_addresses"])
        wallets.update(r["output_addresses"])
    return {
        "transactions": len(g.records),
        "wallets": len(wallets),
        "labelled_wallets": len(g.labels),
        "typologies": sorted({t for t, _ in g.labels.values()}),
        "operations": len({op for _, op in g.labels.values()}),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Generate synthetic Bitcoin traffic")
    p.add_argument("--out", type=Path, default=Path("data"))
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--scale", type=float, default=1.0, help="multiplier on actor counts")
    p.add_argument("--days", type=int, default=30)
    a = p.parse_args(argv)
    print(json.dumps(generate(a.out, seed=a.seed, scale=a.scale, days=a.days), indent=2))


if __name__ == "__main__":
    main()
