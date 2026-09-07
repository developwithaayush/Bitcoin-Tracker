"""Bulk ingestion: CSV / JSON / XML -> one canonical DataFrame, with GeoIP enrichment.

All three formats converge on the identical DataFrame (schema.COLUMNS, in order). Amounts
are 8-decimal fixed point, so text and float encodings round-trip to the same double.

Rows that fail validation are never silently dropped -- they are quarantined to
rejected.csv with the reason, because an investigative tool that quietly discards
malformed evidence is worse than one that refuses it loudly.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

from .schema import AMOUNT_COLUMNS, COLUMNS, LIST_COLUMNS, LIST_SEP

TXID_RE = re.compile(r"^[0-9a-f]{64}$")
# Inputs must fund outputs plus fee. One satoshi of slack absorbs float representation
# error; anything wider is a malformed record, not rounding.
BALANCE_TOLERANCE = 1e-8


# ---------- parsers ----------

def read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in LIST_COLUMNS:
        parts = df[col].map(lambda s: s.split(LIST_SEP) if s else [])
        if col in AMOUNT_COLUMNS:
            parts = parts.map(lambda xs: [float(x) for x in xs])
        df[col] = parts
    return df


def read_json(path: Path) -> pd.DataFrame:
    records = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame.from_records(records)


def read_xml(path: Path) -> pd.DataFrame:
    """Parse the nested <observation><inputs><input> form back into list columns."""
    rows = []
    for obs in ET.parse(path).getroot().findall("observation"):
        row = {}
        for child in obs:
            if child.tag not in ("inputs", "outputs"):
                row[child.tag] = child.text
        for tag, addr_col, amt_col in (
            ("inputs", "input_addresses", "input_amounts"),
            ("outputs", "output_addresses", "output_amounts"),
        ):
            container = obs.find(tag)
            entries = list(container) if container is not None else []
            row[addr_col] = [e.findtext("address") for e in entries]
            row[amt_col] = [float(e.findtext("amount")) for e in entries]
        rows.append(row)
    return pd.DataFrame.from_records(rows)


READERS = {".csv": read_csv, ".json": read_json, ".xml": read_xml}


def load(path: Path) -> pd.DataFrame:
    """Read one file, dispatching on extension, and normalise to the canonical schema."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"unsupported input format {path.suffix!r} (want .csv/.json/.xml)")
    df = reader(path)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing required columns {missing}")
    df = df[COLUMNS].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for col in ("src_port", "dst_port", "asn"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["fee"] = pd.to_numeric(df["fee"], errors="coerce")
    for col in ("src_ip", "dst_ip", "txid", "script_type", "geo_country"):
        df[col] = df[col].astype(str)
    return df.reset_index(drop=True)


def load_dir(path: Path) -> pd.DataFrame:
    """Ingest a single file, or every supported file in a directory, as one frame.

    Directory mode reads one file per format so that a data drop containing the same
    records as .csv and .json does not double-count them; use explicit paths to
    concatenate genuinely different files.
    """
    if path.is_file():
        return load(path)
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in READERS)
    if not files:
        raise ValueError(f"no .csv/.json/.xml files under {path}")
    by_fmt = {}
    for f in files:
        by_fmt.setdefault(f.suffix.lower(), []).append(f)
    chosen = by_fmt.get(".csv") or by_fmt.get(".json") or by_fmt.get(".xml")
    return pd.concat([load(f) for f in chosen], ignore_index=True)


# ---------- validation ----------

def _valid_ip(v) -> bool:
    try:
        ipaddress.ip_address(str(v))
        return True
    except ValueError:
        return False


def _row_error(row) -> str | None:
    """Return a human-readable reason this row is unusable, or None if it is fine."""
    if pd.isna(row["timestamp"]):
        return "unparseable timestamp"
    if not _valid_ip(row["src_ip"]) or not _valid_ip(row["dst_ip"]):
        return "invalid IP address"
    for col in ("src_port", "dst_port"):
        port = row[col]
        if pd.isna(port) or not (1 <= int(port) <= 65535):
            return f"{col} out of range"
    if not TXID_RE.match(str(row["txid"])):
        return "malformed txid"
    ins, outs = row["input_addresses"], row["output_addresses"]
    in_amts, out_amts = row["input_amounts"], row["output_amounts"]
    if not ins or not outs:
        return "transaction with no inputs or no outputs"
    if len(ins) != len(in_amts) or len(outs) != len(out_amts):
        return "address/amount length mismatch"
    if any(a < 0 for a in in_amts) or any(a < 0 for a in out_amts):
        return "negative amount"
    fee = row["fee"]
    if pd.isna(fee) or fee < 0:
        return "missing or negative fee"
    if abs(sum(in_amts) - sum(out_amts) - fee) > BALANCE_TOLERANCE:
        return "inputs do not fund outputs plus fee"
    return None


def validate(df: pd.DataFrame):
    """Split a frame into (accepted, rejected-with-reason)."""
    reasons = df.apply(_row_error, axis=1)
    ok = reasons.isna()
    rejected = df[~ok].copy()
    rejected["reject_reason"] = reasons[~ok]
    return df[ok].reset_index(drop=True), rejected.reset_index(drop=True)


# ---------- GeoIP ----------

def enrich_geoip(df: pd.DataFrame, mmdb: Path | None) -> pd.DataFrame:
    """Resolve src_ip to a country via a local MaxMind-format database.

    Falls back to the country carried in the data when no database is installed, so the
    pipeline degrades rather than failing on an air-gapped box with no GeoIP download.
    Adds geo_source so an analyst can tell a resolved country from an asserted one.
    """
    df = df.copy()
    if mmdb is None or not Path(mmdb).exists():
        df["geo_source"] = "dataset"
        return df
    import maxminddb

    with maxminddb.open_database(str(mmdb)) as reader:
        cache: dict = {}

        def lookup(ip: str):
            if ip not in cache:
                try:
                    rec = reader.get(ip)
                except ValueError:
                    rec = None
                cache[ip] = (rec or {}).get("country", {}).get("iso_code")
            return cache[ip]

        resolved = df["src_ip"].map(lookup)
    df["geo_source"] = resolved.notna().map({True: "mmdb", False: "dataset"})
    df["geo_country"] = resolved.fillna(df["geo_country"])
    return df


# ---------- pipeline ----------

def ingest(src: Path, out_dir: Path, mmdb: Path | None = None) -> dict:
    df = load_dir(src)
    total = len(df)
    good, bad = validate(df)
    good = enrich_geoip(good, mmdb)
    out_dir.mkdir(parents=True, exist_ok=True)
    good.to_parquet(out_dir / "canonical.parquet", index=False)
    if len(bad):
        bad.to_csv(out_dir / "rejected.csv", index=False)
    return {
        "source": str(src),
        "rows_read": total,
        "rows_accepted": len(good),
        "rows_rejected": len(bad),
        "reject_reasons": bad["reject_reason"].value_counts().to_dict() if len(bad) else {},
        "geo_source": good["geo_source"].value_counts().to_dict() if len(good) else {},
        "output": str(out_dir / "canonical.parquet"),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Ingest bulk Bitcoin traffic metadata")
    p.add_argument("src", type=Path, help="file or directory of .csv/.json/.xml")
    p.add_argument("--out", type=Path, default=Path("data"))
    p.add_argument("--geoip", type=Path, default=Path("data/geoip/dbip-city-lite.mmdb"))
    a = p.parse_args(argv)
    print(json.dumps(ingest(a.src, a.out, a.geoip), indent=2, default=str))


if __name__ == "__main__":
    main()
