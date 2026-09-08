"""Live capture: real transactions, from the real Bitcoin network.

This speaks the Bitcoin p2p protocol directly. It resolves peers from the DNS seeds,
performs the version/verack handshake with a handful of them, and records every `inv`
message announcing a transaction -- which host announced which txid, and when we heard
it. That is exactly the observation the rest of btctrace is built on, and it is the one
thing no public API will give you: the chain records what happened, never who said it.

No blockchain download is involved. A node hears the whole mempool from its peers within
seconds of connecting, so a one-minute capture on a laptop sees hundreds of real
transactions announced by real hosts.

The announcement carries only a txid, so the amounts and addresses are then fetched from
a public Esplora mirror and the peer IPs are resolved to country and ASN. Both are
labelled in the output: what we observed ourselves, and what we looked up.

    python -m btctrace.live --seconds 60 --peers 8 --limit 25

writes data/raw/live_net.csv, which ingest already picks up alongside everything else.

Honest limits, worth saying out loud before a judge says it for you: the first peer to
announce a transaction to us is not necessarily its author -- it is the first hop we
happened to observe -- and Dandelion++ and Tor-routed transactions are designed to make
that attribution wrong. This is a real measurement of relay, not proof of origin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import socket
import struct
import threading
import time
import urllib.request
from pathlib import Path

from .generate import write_csv
from .schema import SATOSHI_DP

MAGIC = b"\xf9\xbe\xb4\xd9"          # mainnet message prefix
PROTOCOL = 70016
PORT = 8333
MSG_TX = 1
USER_AGENT = b"/btctrace:0.1/"
SEEDS = ("seed.bitcoin.sipa.be", "dnsseed.bluematt.me", "seed.bitcoinstats.com",
         "seed.bitcoin.jonasschnelli.ch", "seed.btc.petertodd.net", "dnsseed.emzy.de")
# Esplora mirrors. mempool.space itself and blockstream.info are unreachable from some
# networks (India blocks a number of explorer domains at the TLS layer), so the mirrors
# are tried in order rather than one host being assumed.
MIRRORS = ("https://mempool.emzy.de/api", "https://mempool.ninja/api",
           "https://mempool.space/api")
GEO_API = "http://ip-api.com/batch"   # free tier is http-only; public data, no key
LIVE_CSV = Path("data/raw/live_net.csv")
SEEN_CSV = Path("data/live/announcements.csv")
# Real data that is not canonical-schema observations lives outside data/raw, where
# ingest would otherwise try to read it as traffic.
REAL_TXS = Path("data/live/transactions.json")
REAL_NODES = Path("data/live/nodes.csv")
UA = {"User-Agent": "btctrace/0.1 (research)"}


# ---------- wire format ----------

def _varint(buf: bytes, at: int) -> tuple[int, int]:
    """Read a Bitcoin variable-length integer, returning (value, bytes consumed)."""
    n = buf[at]
    if n < 0xFD:
        return n, 1
    width = {0xFD: 2, 0xFE: 4, 0xFF: 8}[n]
    fmt = {2: "<H", 4: "<I", 8: "<Q"}[width]
    return struct.unpack_from(fmt, buf, at + 1)[0], 1 + width


def _message(command: bytes, payload: bytes) -> bytes:
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return MAGIC + command.ljust(12, b"\0") + struct.pack("<I", len(payload)) + checksum + payload


def _version(peer_ip: str) -> bytes:
    """The handshake. relay=1 is the point of the whole exercise: it asks the peer to
    announce transactions to us, which is what we are here to observe."""
    def addr(ip: str) -> bytes:
        packed = b"\x00" * 10 + b"\xff\xff" + socket.inet_aton(ip)
        return struct.pack("<Q", 0) + packed + struct.pack(">H", PORT)

    return (struct.pack("<iQq", PROTOCOL, 0, int(time.time()))
            + addr(peer_ip) + addr("0.0.0.0")
            + struct.pack("<Q", random.getrandbits(64))
            + bytes([len(USER_AGENT)]) + USER_AGENT
            + struct.pack("<i", 0) + b"\x01")


def _read_exact(sock: socket.socket, n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        block = sock.recv(min(65536, n - got))
        if not block:
            raise ConnectionError("peer closed")
        chunks.append(block)
        got += len(block)
    return b"".join(chunks)


def _read_message(sock: socket.socket) -> tuple[str, bytes]:
    head = _read_exact(sock, 24)
    if not head.startswith(MAGIC):
        raise ConnectionError("bad magic")
    command = head[4:16].rstrip(b"\0").decode("ascii", "replace")
    length = struct.unpack_from("<I", head, 16)[0]
    if length > 4_000_000:
        raise ConnectionError(f"oversized {command} payload")
    return command, _read_exact(sock, length)


# ---------- the listener ----------

def _listen(peer_ip: str, deadline: float, seen: list, lock: threading.Lock) -> None:
    """Handshake with one peer and record its transaction announcements until deadline.

    Nothing is requested and nothing is relayed onward: this connects, says hello, and
    listens. That is an ordinary node's behaviour and it is all the observation needs.
    """
    sock = socket.socket()
    sock.settimeout(5)
    try:
        sock.connect((peer_ip, PORT))
        sock.sendall(_message(b"version", _version(peer_ip)))
        local_ip, local_port = sock.getsockname()[:2]
        while time.time() < deadline:
            sock.settimeout(max(1.0, min(5.0, deadline - time.time())))
            command, payload = _read_message(sock)
            if command == "version":
                sock.sendall(_message(b"verack", b""))
            elif command == "ping":
                # A peer that gets no pong hangs up on us, and the capture goes quiet.
                sock.sendall(_message(b"pong", payload))
            elif command == "inv":
                count, at = _varint(payload, 0)
                now = time.time()
                rows = []
                for _ in range(count):
                    kind = struct.unpack_from("<I", payload, at)[0]
                    # Hashes go over the wire little-endian; a txid is the reverse.
                    txid = payload[at + 4:at + 36][::-1].hex()
                    at += 36
                    if kind == MSG_TX:
                        rows.append({"ts": now, "peer_ip": peer_ip, "txid": txid,
                                     "local_ip": local_ip, "local_port": local_port})
                if rows:
                    with lock:
                        seen.extend(rows)
    except Exception:
        return                      # a peer that drops us is normal; the others carry on
    finally:
        sock.close()


def peers(want: int) -> list[str]:
    """Addresses from the DNS seeds, the same way a fresh node bootstraps itself."""
    found: set[str] = set()
    for host in random.sample(SEEDS, len(SEEDS)):
        try:
            found.update(ai[4][0] for ai in socket.getaddrinfo(host, PORT, socket.AF_INET))
        except OSError:
            continue
        if len(found) >= want * 4:
            break
    return random.sample(sorted(found), min(want, len(found)))


def watch(seconds: int, want: int) -> list[dict]:
    """Every transaction announcement heard from every peer, in the order we heard it."""
    hosts = peers(want)
    if not hosts:
        raise RuntimeError("no peers from the DNS seeds -- is the network up?")
    seen: list[dict] = []
    lock = threading.Lock()
    deadline = time.time() + seconds
    threads = [threading.Thread(target=_listen, args=(ip, deadline, seen, lock), daemon=True)
               for ip in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=seconds + 10)
    return sorted(seen, key=lambda r: r["ts"])


# ---------- enrichment ----------

def _get(url: str, timeout: int = 20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def fetch_tx(txid: str) -> dict | None:
    """Full transaction from the first mirror that answers."""
    for base in MIRRORS:
        try:
            return _get(f"{base}/tx/{txid}")
        except Exception:
            continue
    return None


def geolocate(ips: list[str]) -> dict:
    """Country and ASN per IP, in batches, from a free service that needs no key."""
    out: dict = {}
    for i in range(0, len(ips), 100):
        chunk = ips[i:i + 100]
        try:
            req = urllib.request.Request(
                GEO_API, data=json.dumps([{"query": ip, "fields": "status,countryCode,as,query"}
                                          for ip in chunk]).encode(),
                headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                for rec in json.loads(r.read()):
                    asn = (rec.get("as") or "AS0").split()[0].removeprefix("AS")
                    out[rec.get("query")] = (rec.get("countryCode") or "??",
                                             int(asn) if asn.isdigit() else 0)
        except Exception:
            pass
        time.sleep(1.5)             # the free tier allows 15 batches a minute
    return {ip: out.get(ip, ("??", 0)) for ip in ips}


SCRIPTS = {"v0_p2wpkh": "p2wpkh", "v1_p2tr": "p2tr", "v0_p2wsh": "p2wsh",
           "p2pkh": "p2pkh", "p2sh": "p2sh"}


def to_record(tx: dict, first: dict, geo: tuple[str, int]) -> dict | None:
    """One canonical row: our observation, plus the transaction it named."""
    if any(v.get("is_coinbase") for v in tx["vin"]):
        return None                 # newly mined coin has no funding inputs to record
    ins, in_amts = [], []
    for v in tx["vin"]:
        prev = v.get("prevout") or {}
        if prev.get("value") is None:
            return None             # cannot balance a row whose input value is unknown
        ins.append(prev.get("scriptpubkey_address") or f'script:{prev.get("scriptpubkey_type")}')
        in_amts.append(prev["value"] / 1e8)
    outs, out_amts = [], []
    for v in tx["vout"]:
        if not v.get("value") and not v.get("scriptpubkey_address"):
            continue                # OP_RETURN carries no value, so dropping it balances
        outs.append(v.get("scriptpubkey_address") or f'script:{v.get("scriptpubkey_type")}')
        out_amts.append(v["value"] / 1e8)
    if not ins or not outs:
        return None
    country, asn = geo
    # The fee is derived rather than taken from the API, so the row balances by
    # construction and ingest's funding check cannot fail on a rounding difference.
    fee = round(sum(in_amts) - sum(out_amts), SATOSHI_DP)
    kind = next((SCRIPTS.get(v.get("scriptpubkey_type"), v.get("scriptpubkey_type", "unknown"))
                 for v in tx["vout"] if v.get("scriptpubkey_type") != "op_return"), "unknown")
    return {
        "timestamp": int(first["ts"]),
        "src_ip": first["peer_ip"],
        "dst_ip": first["local_ip"],
        "src_port": PORT,
        "dst_port": int(first["local_port"]),
        "txid": tx["txid"],
        "input_addresses": ins,
        "output_addresses": outs,
        "input_amounts": [round(a, SATOSHI_DP) for a in in_amts],
        "output_amounts": [round(a, SATOSHI_DP) for a in out_amts],
        "fee": fee,
        "script_type": kind,
        "geo_country": country,
        "asn": asn,
    }


# ---------- capture ----------

def capture(seconds: int = 60, want_peers: int = 8, limit: int = 25,
            out: Path = LIVE_CSV) -> dict:
    """Listen, enrich, and write canonical rows the rest of the pipeline already reads."""
    heard = watch(seconds, want_peers)
    if not heard:
        raise RuntimeError("no announcements heard -- peers may be unreachable on this network")

    firsts: dict = {}
    for row in heard:               # sorted by time, so the first entry wins
        firsts.setdefault(row["txid"], row)
    chosen = list(firsts.values())[:limit]

    geo = geolocate(sorted({r["peer_ip"] for r in chosen}))
    records, missed = [], 0
    for first in chosen:
        tx = fetch_tx(first["txid"])
        rec = to_record(tx, first, geo[first["peer_ip"]]) if tx else None
        if rec is None:
            missed += 1
            continue
        records.append(rec)
        time.sleep(0.15)            # be a polite client of a free mirror

    if not records:
        raise RuntimeError("announcements were heard but no transaction could be fetched")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(records, out)

    # The full announcement log is not canonical-schema data, so it lives outside
    # data/raw where ingest would try to read it as observations.
    SEEN_CSV.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with SEEN_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", "peer_ip", "txid", "local_ip",
                                                "local_port"])
        writer.writeheader()
        writer.writerows(heard)

    return {
        "peers_polled": want_peers,
        "announcements_heard": len(heard),
        "distinct_txids": len(firsts),
        "rows_written": len(records),
        "unfetchable": missed,
        "countries": sorted({r["geo_country"] for r in records}),
        "csv": str(out),
        "announcements": str(SEEN_CSV),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Pull real Bitcoin data into btctrace")
    p.add_argument("--mode", default="chain",
                   choices=("chain", "nodes", "p2p", "probe", "all"),
                   help="chain: real transactions from a mirror; nodes: real node "
                        "addresses; p2p: capture announcements directly (needs an "
                        "unfiltered network); probe: test whether this network allows it")
    p.add_argument("--seconds", type=int, default=60, help="p2p: how long to listen")
    p.add_argument("--peers", type=int, default=8, help="p2p: how many peers to connect to")
    p.add_argument("--limit", type=int, default=30, help="transactions or nodes to fetch")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    if a.mode == "probe":
        out = probe()
    elif a.mode == "nodes":
        out = real_nodes(a.limit, a.out or REAL_NODES)
    elif a.mode == "p2p":
        out = capture(a.seconds, a.peers, a.limit, a.out or LIVE_CSV)
    elif a.mode == "all":
        out = {"chain": real_transactions(a.limit), "nodes": real_nodes(a.limit)}
    else:
        out = real_transactions(a.limit, a.out or REAL_TXS)
    print(json.dumps(out, indent=2))



# ---------- real data without the p2p protocol ----------
# Some networks reset any connection carrying the Bitcoin protocol magic (probe it with
# `python -m btctrace.live --mode probe`). The chain itself is still reachable through an
# Esplora mirror, and the DNS seeds still resolve, so the transactions and the nodes are
# real even where the announcements cannot be observed.

def real_transactions(limit: int = 30, out: Path = REAL_TXS) -> dict:
    """Live mainnet transactions, straight from the mempool of a public Esplora mirror."""
    for base in MIRRORS:
        try:
            recent = _get(f"{base}/mempool/recent", timeout=25)
            break
        except Exception:
            continue
    else:
        raise RuntimeError(f"no Esplora mirror reachable (tried {len(MIRRORS)})")

    # /mempool/recent is only the last handful. The rest come from the full mempool,
    # sampled rather than taken in order, so a capture is not all one miner's backlog.
    ids = [e["txid"] for e in recent]
    if len(ids) < limit * 2:
        try:
            pool = _get(f"{base}/mempool/txids", timeout=40)
            ids += random.sample(pool, min(limit * 3, len(pool)))
        except Exception:
            pass

    txs = []
    for entry in ({"txid": t} for t in ids):
        if len(txs) >= limit:
            break
        tx = fetch_tx(entry["txid"])
        if tx is None or any(v.get("is_coinbase") for v in tx["vin"]):
            continue
        if any((v.get("prevout") or {}).get("value") is None for v in tx["vin"]):
            continue
        txs.append({
            "txid": tx["txid"],
            "seen_at": int(time.time()),
            "fee": tx["fee"] / 1e8,
            "size": tx["size"],
            "weight": tx["weight"],
            "confirmed": bool(tx["status"].get("confirmed")),
            "inputs": [[v["prevout"].get("scriptpubkey_address")
                        or f'script:{v["prevout"].get("scriptpubkey_type")}',
                        v["prevout"]["value"] / 1e8] for v in tx["vin"]],
            "outputs": [[v.get("scriptpubkey_address") or f'script:{v.get("scriptpubkey_type")}',
                         v["value"] / 1e8] for v in tx["vout"]],
        })
        time.sleep(0.12)            # a free mirror is a courtesy, not an entitlement
    if not txs:
        raise RuntimeError("mirror reachable but no usable transaction came back")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(txs, indent=1), encoding="utf-8")
    return {"transactions": len(txs), "file": str(out),
            "total_btc": round(sum(sum(a for _, a in t["outputs"]) for t in txs), 8)}


def real_nodes(limit: int = 40, out: Path = REAL_NODES) -> dict:
    """Addresses of live Bitcoin nodes from the DNS seeds, with country and ASN resolved.

    These are the machines actually running the network right now. Resolving the seeds is
    how every node finds its first peers, and it is plain DNS, so it survives networks
    that block the protocol itself.
    """
    hosts = peers(limit)
    if not hosts:
        raise RuntimeError("DNS seeds returned no peers")
    geo = geolocate(hosts)
    out.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ip", "country", "asn"])
        w.writerows([ip, *geo[ip]] for ip in hosts)
    return {"nodes": len(hosts), "countries": len({geo[ip][0] for ip in hosts}),
            "file": str(out)}


def probe(sample: int = 3) -> dict:
    """Can this network speak the Bitcoin protocol at all?

    Connects to real peers and compares two sends: arbitrary bytes, then a real `version`
    message. A network that accepts the first and resets the second is filtering Bitcoin
    by signature, which is worth knowing before blaming the code.
    """
    results = []
    for ip in peers(sample):
        row = {"peer": ip}
        for label, payload in (("noise", bytes(range(100))),
                               ("version", _message(b"version", _version(ip)))):
            sock = socket.socket()
            sock.settimeout(6)
            try:
                sock.connect((ip, PORT))
                sock.sendall(payload)
                try:
                    row[label] = "reply" if sock.recv(64) else "closed"
                except socket.timeout:
                    row[label] = "held open"
            except Exception as exc:
                row[label] = type(exc).__name__
            finally:
                sock.close()
        results.append(row)
    blocked = all(r.get("version") == "ConnectionResetError" for r in results) and results
    return {"peers": results,
            "verdict": ("this network resets Bitcoin protocol traffic -- p2p capture needs "
                        "a different link (mobile hotspot or VPN)" if blocked
                        else "the Bitcoin protocol passes; --mode p2p should work")}
if __name__ == "__main__":
    main()
