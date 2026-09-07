"""End-to-end checks for btctrace.

Runs standalone (`python tests/test_pipeline.py`) with no test framework installed, and
also collects under pytest if it happens to be available.

The last check is the one that matters: it regenerates a labelled dataset, runs the whole
pipeline, and fails if detection quality drops below a floor. Everything above it exists to
localise the failure when that happens.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btctrace import generate as gen  # noqa: E402
from btctrace.detect import detect, evaluate, fit, attribute, explain  # noqa: E402
from btctrace.features import entity_clusters, wallet_features  # noqa: E402
from btctrace.ingest import load, validate  # noqa: E402

# Detection floors. Set below the observed run so ordinary variation does not fail the
# build, but high enough that a real regression does.
MIN_ROC_AUC = 0.88
MIN_OPERATION_RECALL = 0.90
MIN_PRECISION_AT_100 = 0.85


def test_formats_converge():
    """CSV, JSON and XML must parse to byte-identical canonical frames."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        gen.generate(out, seed=7, scale=0.4, days=10)
        frames = {f: load(out / "raw" / f"traffic.{f}") for f in ("csv", "json", "xml")}
        assert len(frames["csv"]) > 100, "generator produced too little data to be meaningful"
        pd.testing.assert_frame_equal(frames["csv"], frames["json"])
        pd.testing.assert_frame_equal(frames["csv"], frames["xml"])


def test_validation_quarantines_bad_rows():
    """Every corruption class is rejected with its own reason, and nothing is dropped."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        gen.generate(out, seed=7, scale=0.4, days=10)
        df = load(out / "raw" / "traffic.csv").head(40).copy()

        df.at[0, "src_ip"] = "999.1.2.3"
        df.at[1, "txid"] = "nothex"
        df.at[2, "fee"] = df.at[2, "fee"] + 0.5
        df.at[3, "input_amounts"] = [-1.0]
        df.at[4, "src_port"] = 99999
        df.at[5, "output_addresses"] = []
        df.at[6, "input_amounts"] = [1.0, 2.0]
        df.at[7, "timestamp"] = pd.NaT

        good, bad = validate(df)
        assert len(good) + len(bad) == len(df), "rows vanished instead of being quarantined"
        assert len(bad) == 8, f"expected 8 rejects, got {len(bad)}"
        assert bad["reject_reason"].nunique() == 8, "reasons are not specific enough to act on"
        assert not good.index.duplicated().any()


def test_geoip_enrichment_and_fallback():
    """Resolved countries override the dataset; unresolved ones fall back to it.

    Exercised against a stub reader rather than the real 120 MB DB-IP file, so the parsing
    contract (MaxMind's country.iso_code layout, which DB-IP follows) and the fallback both
    stay covered without a download in the loop.
    """
    import contextlib

    import maxminddb

    from btctrace import ingest as ingest_mod

    df = _fixture()
    df["geo_country"] = ["ZZ", "ZZ", "ZZ"]

    # No database installed at all -- the dataset's own country must survive untouched.
    plain = ingest_mod.enrich_geoip(df, Path("does/not/exist.mmdb"))
    assert list(plain["geo_country"]) == ["ZZ", "ZZ", "ZZ"]
    assert set(plain["geo_source"]) == {"dataset"}

    class StubReader:
        def get(self, ip):
            if ip == "10.0.0.1":
                return {"country": {"iso_code": "NL"}}
            if ip == "10.0.0.2":
                return {}  # present in the database but carrying no country
            raise ValueError("invalid address")

    @contextlib.contextmanager
    def fake_open(_path):
        yield StubReader()

    real_open, real_exists = maxminddb.open_database, Path.exists
    maxminddb.open_database = fake_open
    Path.exists = lambda self: True
    try:
        out = ingest_mod.enrich_geoip(df, Path("pretend.mmdb"))
    finally:
        maxminddb.open_database, Path.exists = real_open, real_exists

    # 10.0.0.1 resolved to NL; 10.0.0.2 had no country, so the dataset value stands.
    assert list(out["geo_country"]) == ["NL", "NL", "ZZ"]
    assert list(out["geo_source"]) == ["mmdb", "mmdb", "dataset"]


def _fixture() -> pd.DataFrame:
    """Three hand-built transactions with known structure.

    tx1: A (2.0) -> B (0.05) + remainder A2 (1.9)   a real peel: 95% carries forward
    tx2: A2 (1.9) -> C (1.85)                        A2 sweeps out, retention 0
    tx3: A + D co-spent -> E                         co-spend links A and D into one entity

    The peel economics matter: the remainder has to clear REMAINDER_FRACTION for the hop
    to count as a chain link at all.
    """
    ts = pd.to_datetime(
        ["2025-01-01T00:00:00Z", "2025-01-01T02:00:00Z", "2025-01-02T00:00:00Z"], utc=True
    )
    return pd.DataFrame({
        "timestamp": ts,
        "src_ip": ["10.0.0.1", "10.0.0.1", "10.0.0.2"],
        "dst_ip": ["10.9.9.9"] * 3,
        "src_port": [8333, 8333, 8333],
        "dst_port": [8333] * 3,
        "txid": ["a" * 64, "b" * 64, "c" * 64],
        "input_addresses": [["A"], ["A2"], ["A", "D"]],
        "output_addresses": [["B", "A2"], ["C"], ["E"]],
        "input_amounts": [[2.0], [1.9], [0.5, 0.5]],
        "output_amounts": [[0.05, 1.9], [1.85], [0.9]],
        "fee": [0.05, 0.05, 0.1],
        "script_type": ["p2wpkh"] * 3,
        "geo_country": ["US", "US", "DE"],
        "asn": [16509, 16509, 24940],
    })


def test_graph_features_on_known_fixture():
    df = _fixture()
    f = wallet_features(df)

    assert f.loc["A", "n_spends"] == 2 and f.loc["A", "n_receives"] == 0
    assert f.loc["A2", "n_receives"] == 1 and f.loc["A2", "n_spends"] == 1
    assert f.loc["B", "total_received"] == 0.05

    # A2 received 1.9 and sent 1.9 -- keeps nothing.
    assert abs(f.loc["A2", "retention_ratio"]) < 1e-9
    # B received and never spent -- keeps everything.
    assert abs(f.loc["B", "retention_ratio"] - 1.0) < 1e-9

    # tx1 carries 95% of A's input to A2, so A -> A2 is one chain link. Depth counts the
    # chain *through* a wallet, so both ends of that link report length 1.
    assert f.loc["A", "peel_chain_depth"] == 1
    assert f.loc["A2", "peel_chain_depth"] == 1
    assert f.loc["B", "peel_chain_depth"] == 0, "a peel payout is not part of the chain"

    # A2 spent 2 h after receiving.
    assert abs(f.loc["A2", "hop_latency_h"] - 2.0) < 1e-6

    # A and D were co-spent in tx3, so they are one entity; B was not.
    clusters = entity_clusters(df)
    assert clusters["A"] == clusters["D"]
    assert "B" not in clusters

    # The IP is credited to spenders only -- B never broadcast anything.
    assert f.loc["A", "n_ips"] == 2, "A spent from two different hosts"
    assert f.loc["B", "n_ips"] == 0, "a recipient must not inherit its payer's IP"


def test_explanations_are_faithful():
    """Attribution must point at the feature that actually drove the score."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        gen.generate(out, seed=11, scale=1.0, days=20)
        df = load(out / "raw" / "traffic.csv")
        f = wallet_features(df)
        truth = pd.read_csv(out / "ground_truth.csv")

        peels = truth[truth["typology"] == "peeling_chain"]["address"]
        subjects = f.index.intersection(peels)
        assert len(subjects) > 0

        model, X, raw = fit(f)
        rows = f.index.get_indexer(subjects[:25])
        contribs = attribute(model, X, rows)
        items = explain(model, raw.loc[subjects[:25]], contribs, subjects[:25])

        # Contributions are shares of the positive total, so they cannot exceed 1.
        for ev in items:
            assert all(0.0 <= e["contribution"] <= 1.0 for e in ev)

        # Across known peeling chains, chain depth should surface as a top-5 driver more
        # often than chance -- otherwise the explanation is decorative, not diagnostic.
        named = [{e["feature"] for e in ev} for ev in items if ev]
        hits = sum("peel_chain_depth" in s for s in named)
        assert hits >= len(named) * 0.5, (
            f"peel_chain_depth explained only {hits}/{len(named)} known peeling chains"
        )


def test_detection_quality_floor():
    """The whole pipeline, scored against planted ground truth."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        gen.generate(out, seed=1337, scale=2.0, days=30)
        df = load(out / "raw" / "traffic.csv")
        good, bad = validate(df)
        assert len(bad) == 0, "the generator emitted rows its own validator rejects"

        f = wallet_features(good)
        alerts = detect(good, f, top_k=200)
        report = evaluate(alerts, pd.read_csv(out / "ground_truth.csv"))

        assert report["roc_auc"] >= MIN_ROC_AUC, report
        assert report["precision@100"] >= MIN_PRECISION_AT_100, report
        assert report["operation_recall@1000"] >= MIN_OPERATION_RECALL, report
        # Every typology must be found, not just the easy ones on average.
        for typ, stats in report["by_typology"].items():
            assert stats["detected@1000"] > 0, f"{typ} was never surfaced at all"
        return report


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            result = t()
            extra = ""
            if isinstance(result, dict):
                extra = (f"  (AUC {result['roc_auc']}, P@100 {result['precision@100']}, "
                         f"op-recall@1000 {result['operation_recall@1000']})")
            print(f"PASS  {t.__name__}{extra}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
