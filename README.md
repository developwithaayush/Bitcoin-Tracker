# btctrace

**AI-powered monitoring and analysis of Bitcoin transaction traffic.**
Smart India Hackathon problem statement **26146** — National Technical Research Organisation (NTRO).

Ingests bulk Bitcoin P2P metadata (CSV / JSON / XML), correlates network-layer observations
(IP, port, timing, ASN) with blockchain-layer data (wallets, TXIDs, amounts), and applies
unsupervised machine learning to produce a **ranked, explainable** list of investigative leads.

Runs completely offline on Linux. No API keys, no external services, no network calls at run time.

---

## Results

Measured on a 17,358-transaction synthetic dataset containing **123 planted criminal
operations** across six typologies, hidden among 34,483 wallets:

| Metric | Value |
|---|---|
| ROC-AUC (wallet level) | **0.968** |
| Precision @ 100 alerts | **1.000** |
| Precision @ 500 alerts | **0.904** |
| Operations detected within top 500 | **93.5 %** (115 / 123) |
| Operations detected within top 1000 | **100 %** (123 / 123) |
| Full pipeline runtime | **~35 s** (generate + ingest + detect + evaluate) |

Reproduce with `make demo`. See [WRITEUP.md](WRITEUP.md) for method, and for the
limitations these numbers carry.

---

## Quick start

Needs Python 3.10+. Every command below runs from the project root.

```bash
python -m pip install -r requirements.txt   # once
python -m btctrace.cli pipeline --scale 3   # demo: generate -> ingest -> detect -> evaluate (~40 s)
python -m streamlit run app.py              # dashboard at http://localhost:8501 (run the demo first)
python tests/test_pipeline.py               # 6 checks, no test framework required
```

Run the demo before the dashboard — the dashboard reads `data/alerts.parquet`, which the
pipeline writes.

If you have `make` (Linux/macOS, or Git Bash with make installed), the same four steps are
`make setup`, `make demo`, `make dashboard`, `make test`. On Windows use the commands above;
`make` is not installed by default there.

## Live demo: wallet simulator

A second page in the dashboard, for showing the detector working in front of an audience.
It is a sandbox account that buys, sells and sends fake bitcoin; its transactions are
written to `data/raw/live.csv` and picked up by the ordinary ingest path, so nothing in
the pipeline treats them specially.

```bash
python -m btctrace.cli pipeline --scale 3   # build the baseline corpus first
python -m streamlit run app.py              # sidebar -> "Wallet simulator"
```

Buy and sell normally and the wallet stays unflagged. Run one of the preset behaviours and
it acts out a laundering pattern the detector is built to catch:

| Preset | What it emits | Typology it should trigger |
|---|---|---|
| Rapid pass-through | four receipts, all forwarded within the hour | `rapid_passthrough` |
| Peeling chain | eight hops, a small payment peeled off each | `peeling_chain` |
| Fan-in collection | twelve near-identical receipts in one day | `ransomware_fanin` |
| Geo hopping | spends broadcast from five countries in a day | `geo_hopping` |

**Analyse now** re-ingests and re-scores, then reports where your wallet ranked among all
~34,000. Measured against the standard corpus, the four presets land at ranks 138, 423,
239 and 10 with the correct typology attached, while a plain buy/sell wallet draws no
typology at all — that contrast is the demo.

The re-score takes about 30 seconds and there is no shortcut: an IsolationForest score is
a statement about a wallet's position in a population, so the population is scored with
it. Open the console in a second browser tab to watch the alert list change.

**Reset** clears the live feed and re-scores back to the baseline. Do that before quoting
the headline metrics — live wallets are not in `ground_truth.csv`, so while a live feed is
loaded the Model performance tab counts them as false positives.

Verify the simulator on its own with `python -m btctrace.wallet`, which checks that every
emitted row survives ingest validation and that each preset fires its intended typology.

---

Optional GeoIP enrichment (the only step that uses the network, and it is a one-time setup):

```bash
make geoip     # DB-IP City Lite, free, CC BY 4.0, no account
```

Without it, country and ASN are read from the dataset instead — the pipeline never fails
for want of a GeoIP database.

---

## Air-gapped install

On a connected machine:

```bash
make vendor                # downloads wheels into vendor/
sh scripts/fetch_geoip.sh  # optional GeoIP database
```

Copy the repository (with `vendor/` and `data/geoip/`) to the isolated host, then:

```bash
pip install --no-index --find-links=vendor/ -r requirements.txt
make demo && make dashboard
```

---

## Usage

```bash
python -m btctrace.cli generate --scale 3          # synthetic labelled dataset
python -m btctrace.cli ingest data/raw/traffic.xml # CSV, JSON or XML
python -m btctrace.cli detect --top 300            # score + explain
python -m btctrace.cli evaluate                    # score against ground truth
python -m btctrace.cli pipeline                    # all of the above
```

Point `ingest` at your own file or directory. Any CSV/JSON/XML carrying the required
fields works; rows that fail validation are quarantined to `data/rejected.csv` with a
reason rather than silently dropped.

**Required fields:** `timestamp, src_ip, dst_ip, src_port, dst_port, txid,
input_addresses[], output_addresses[], input_amounts[], output_amounts[], fee,
script_type, geo_country, asn`

---

## Dashboard

Three tabs, all rendering from local files:

- **Ranked alerts** — sortable worklist with risk, confidence, named typologies and the
  number of independent signals; select a row for its full evidence.
- **Link analysis** — interactive wallet / transaction / host graph around the selected
  address. The vis.js bundle is inlined, so it renders with no network access.
- **Model performance** — ROC-AUC, precision@k, and per-typology detection against
  ground truth.

---

## Layout

```
btctrace/
  schema.py     canonical field definitions shared by every stage
  generate.py   synthetic traffic + planted ground truth (CSV/JSON/XML)
  ingest.py     parsing, validation, GeoIP enrichment
  features.py   entity graph, co-spend clustering, 28-feature wallet matrix
  detect.py     IsolationForest + DBSCAN + typology motifs + attribution
  wallet.py     demo wallet simulator -> data/raw/live.csv
  ui.py         palette, stylesheet and HTML helpers shared by every page
  cli.py        command line
app.py          Streamlit dashboard: page config, navigation, console page
pages/          extra dashboard pages (wallet simulator)
tests/          six end-to-end checks
scripts/        GeoIP downloader
```

---

## Data

The dataset is **synthetic**. No real, seized, or intercepted Bitcoin traffic is used or
included, consistent with the problem statement. The generator plants known typologies —
ransomware collection, peeling chains, mixer layering, rapid pass-through, sybil
broadcasting and geographic hopping — and records which wallets belong to each, so
detection quality can be measured rather than asserted.

It also plants **hard negatives**: exchange hot wallets and busy merchants whose
transaction volume overlaps and exceeds the criminal actors'. Benign wallets reach
degree 251 while illicit ones top out at 54, so transaction volume alone cannot
separate the classes.

IP geolocation by [DB-IP](https://db-ip.com) (CC BY 4.0) when the optional database is
installed.
