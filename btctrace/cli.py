"""btctrace command line: generate | ingest | detect | evaluate | pipeline.

Everything runs offline. The only step that ever touches the network is the optional
GeoIP database download in scripts/fetch_geoip.sh, which is a one-time setup.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

DEFAULT_DATA = Path("data")
DEFAULT_GEOIP = Path("data/geoip/dbip-city-lite.mmdb")


def _load_features(data_dir: Path):
    from .features import wallet_features

    canonical = data_dir / "canonical.parquet"
    if not canonical.exists():
        sys.exit(f"error: {canonical} not found -- run 'btctrace ingest' first")
    df = pd.read_parquet(canonical)
    return df, wallet_features(df)


def cmd_generate(a) -> dict:
    from .generate import generate

    return generate(a.out, seed=a.seed, scale=a.scale, days=a.days)


def cmd_ingest(a) -> dict:
    from .ingest import ingest

    return ingest(a.src, a.out, a.geoip)


def cmd_detect(a) -> dict:
    from .detect import detect

    df, f = _load_features(a.data)
    alerts = detect(df, f, top_k=a.top)
    a.data.mkdir(parents=True, exist_ok=True)
    alerts.to_parquet(a.data / "alerts.parquet", index=False)
    f.to_parquet(a.data / "features.parquet")
    flagged = alerts[alerts["typologies"].astype(bool) & alerts["typologies"].ne("")]
    return {
        "wallets_scored": len(alerts),
        "explained_alerts": int(alerts["explanation"].ne("[]").sum()),
        "wallets_with_typology": len(flagged),
        "top_typologies": flagged["typologies"].value_counts().head(8).to_dict(),
        "output": str(a.data / "alerts.parquet"),
    }


def cmd_evaluate(a) -> dict:
    from .detect import evaluate

    alerts_path = a.data / "alerts.parquet"
    if not alerts_path.exists():
        sys.exit(f"error: {alerts_path} not found -- run 'btctrace detect' first")
    truth_path = a.data / "ground_truth.csv"
    if not truth_path.exists():
        sys.exit(f"error: {truth_path} not found -- evaluation needs labelled data")
    return evaluate(pd.read_parquet(alerts_path), pd.read_csv(truth_path))


def cmd_pipeline(a) -> dict:
    """generate (optional) -> ingest -> detect -> evaluate, for a one-command demo."""
    out = {}
    t0 = time.time()
    if not a.skip_generate:
        out["generate"] = cmd_generate(a)
    a.src = a.out / "raw" / f"traffic.{a.format}"
    out["ingest"] = cmd_ingest(a)
    a.data = a.out
    out["detect"] = cmd_detect(a)
    if (a.out / "ground_truth.csv").exists():
        out["evaluate"] = cmd_evaluate(a)
    out["elapsed_seconds"] = round(time.time() - t0, 1)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="btctrace", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="write a synthetic labelled dataset")
    g.add_argument("--out", type=Path, default=DEFAULT_DATA)
    g.add_argument("--seed", type=int, default=1337)
    g.add_argument("--scale", type=float, default=3.0)
    g.add_argument("--days", type=int, default=30)
    g.set_defaults(func=cmd_generate)

    i = sub.add_parser("ingest", help="parse CSV/JSON/XML into the canonical store")
    i.add_argument("src", type=Path)
    i.add_argument("--out", type=Path, default=DEFAULT_DATA)
    i.add_argument("--geoip", type=Path, default=DEFAULT_GEOIP)
    i.set_defaults(func=cmd_ingest)

    d = sub.add_parser("detect", help="score wallets and write a ranked alert list")
    d.add_argument("--data", type=Path, default=DEFAULT_DATA)
    d.add_argument("--top", type=int, default=300, help="how many alerts to explain")
    d.set_defaults(func=cmd_detect)

    e = sub.add_parser("evaluate", help="score the alert list against ground truth")
    e.add_argument("--data", type=Path, default=DEFAULT_DATA)
    e.set_defaults(func=cmd_evaluate)

    a = sub.add_parser("pipeline", help="run everything end to end")
    a.add_argument("--out", type=Path, default=DEFAULT_DATA)
    a.add_argument("--seed", type=int, default=1337)
    a.add_argument("--scale", type=float, default=3.0)
    a.add_argument("--days", type=int, default=30)
    a.add_argument("--top", type=int, default=300)
    a.add_argument("--geoip", type=Path, default=DEFAULT_GEOIP)
    a.add_argument("--format", choices=["csv", "json", "xml"], default="csv",
                   help="which emitted format to ingest (all three are equivalent)")
    a.add_argument("--skip-generate", action="store_true")
    a.set_defaults(func=cmd_pipeline)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    print(json.dumps(args.func(args), indent=2, default=str))


if __name__ == "__main__":
    main()
