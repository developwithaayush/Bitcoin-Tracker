# btctrace — technical write-up

SIH 26146 · *AI-Powered Monitoring & Analysis of Bitcoin Transaction Traffic* · NTRO

---

## 1. Problem

Bitcoin's pseudonymity protects criminal cash flow, but it is not anonymity. Two
independent traces exist: the **blockchain layer** (wallets, TXIDs, amounts) and the
**network layer** (which host broadcast which transaction, when, from where). Each alone is
weak. A mixer looks like an exchange on-chain; a busy node looks like a criminal relay on
the wire. Correlated, they are far more informative than the sum of their parts.

The system ingests bulk metadata in CSV/JSON/XML, builds an entity graph across both
layers, and produces a ranked, explainable worklist of investigative leads.

---

## 2. Pipeline

```
CSV / JSON / XML
      │  parse, validate, quarantine bad rows, GeoIP enrich
      ▼
canonical.parquet        one row per observed transaction
      │  explode to transaction legs; build wallet/tx/IP graph
      ▼
28-feature wallet matrix value-flow · temporal · graph · network
      │  IsolationForest (score) + DBSCAN (cohorts) + motifs (evidence)
      ▼
ranked alerts + per-alert attribution
```

Runtime is ~35 s end to end for 17,358 transactions / 34,483 wallets on a laptop (~30 s of that is ingest + feature extraction + detection; the rest is generating the dataset). Everything is single-machine and CPU-only.

---

## 3. Ingestion

One reader per format converging on a single canonical schema, dispatched by extension.
XML is parsed with the standard library's `ElementTree` (no `lxml` dependency) from a
genuinely nested `<observation><inputs><input>` structure, not a flattened table.

Amounts are 8-decimal fixed point, rounded at emission. This is what lets decimal text
(CSV) and float repr (JSON) round-trip to the *identical* double, so all three formats
parse to frames that compare exactly equal — verified by a test, not assumed.

**Validation is a trust boundary.** Malformed timestamps, invalid IPs, out-of-range ports,
non-hex TXIDs, negative amounts, address/amount length mismatches, and transactions whose
inputs do not fund outputs plus fee are each quarantined to `rejected.csv` **with their
specific reason**. An investigative tool that quietly discards malformed evidence is worse
than one that refuses it loudly.

**GeoIP** uses DB-IP City Lite (free, CC BY 4.0, no account) through `maxminddb`, cached per
unique IP. If no database is installed, country and ASN fall back to the dataset's own
fields and a `geo_source` column records which happened — so an analyst can always tell a
resolved country from an asserted one.

---

## 4. Entity graph and features

A heterogeneous directed graph over three node types — wallets, transactions and IPs —
with `wallet→tx` (spend), `tx→wallet` (receive) and `ip→tx` (broadcast) edges.

**Entity resolution** uses the common-input-ownership heuristic: addresses co-spent as
inputs to one transaction are controlled by one actor, merged via connected components.
This is the standard opening move in blockchain forensics, and it is a heuristic, not a
proof — CoinJoin deliberately violates it (see §8).

28 numeric features per wallet, in four families:

| Family | Examples |
|---|---|
| Value flow | retention ratio, total/mean received, amount uniformity, round-amount fraction |
| Temporal | hop latency, burst count in any 24 h window, inter-arrival variability, night fraction |
| Graph | peel-chain length, entity size, max transaction fan-in / fan-out, upstream funder activity |
| Network | distinct IPs / countries / ASNs, geo-hop rate, shared-host cohort, host cohort fragmentation |

Network features enter the **same matrix** the model scores. That is what makes the
network/blockchain correlation a modelled signal rather than a rule bolted on afterwards.

### Three feature-design decisions that mattered

These are documented because each one was a measured correction, not a first guess.

**Attribute the source IP to the spender only.** The observed broadcasting host belongs to
whoever *sent* the transaction. Initially the IP was credited to output addresses too,
which hands every payment recipient its payer's network footprint — inventing an
association never observed and inflating every wallet's shared-host cohort until the sybil
signal drowned. Fixing it *lowered* the headline score (R-precision 0.72 → 0.59), because
the earlier number was flattered by a leak that artificially clustered illicit wallets.
The corrected, lower number is the honest one.

**Measure uniformity, not variance.** A ransomware collector receives dozens of
near-identical ransom payments — coefficient of variation ≈ 0.011, genuinely rare. But over
half of all wallets receive exactly once and have *no* defined variation. Encoding both as
0.0 buried the signal; encoding "undefined" as a sentinel merely moved it to mid-
distribution, where a rank-based transform renders it unremarkable. Mapping to
`1/(1+cv)`, with too-few-samples pinned at 0, puts "many receipts, all alike" at the *top*
of the range — the only place an isolation forest can see it.

**Measure chain length through a wallet, not ahead of it.** Forward-only depth credits the
head of a 20-hop peeling chain with 20 and its successors with progressively less, down to
0 at the tail — yet every one is equally a link in that chain, and the tail is where the
money is about to leave. Summing the longest path in each direction fixed both detection
(AUC 0.950 → 0.967) and explanation quality.

---

## 5. Models

**Primary — IsolationForest** (300 trees) over the quantile-transformed matrix, scores
rank-normalised to [0,1]. Chosen because:

- The operational reality is **unlabelled**. Real intercepted traffic arrives with no
  ground truth, so a supervised classifier has nothing to train on. Anything requiring
  labels would be a demo, not a deployable system.
- Isolation forests target *rarity* directly rather than modelling normality, which suits
  a domain where the interesting behaviour is 3 % of the population.
- Linear in samples, trivially parallel, no GPU — it runs on an investigator's laptop.
- The quantile transform makes it robust to the heavy-tailed, wildly different scales that
  Bitcoin features naturally have (satoshis to hundreds of BTC, seconds to weeks).

**Secondary — DBSCAN** on an 8-component PCA projection, for behavioural cohorts. Plain
DBSCAN in 28 dimensions is defeated by distance concentration, hence the reduction.

**Typology motifs** — peeling chain, mixer layering, ransomware fan-in, rapid pass-through,
sybil broadcast, geo-hopping. Thresholds are *percentiles of the observed population*, not
hard-coded constants, so they travel to a differently-scaled dataset without retuning.
These do not drive the score; they attach a **name** to an alert. Loose rules were actively
harmful — an early ransomware rule also matched pass-through and mixer wallets, so alerts
arrived carrying a typology that contradicted their own evidence. Tightening them raised
precision@100 from 0.95 to 1.000.

**Fusion.** `risk = 0.65 · model_score + 0.35 · min(1, tags/3)`, with confidence measuring
*agreement* across three independent evidence families (model, on-chain motif, network
correlation) rather than magnitude — a wallet three signals point at is a safer lead than
one an extreme score alone singles out. Two principled alternatives were tested and
rejected on measurement: noisy-OR fusion (R-precision 0.67 → 0.30) and graph risk
propagation, which lifted ransomware cash-outs but flooded every change address with false
taint (0.74 → 0.37).

---

## 6. Explainability

Every alert carries ranked, plain-English evidence:

> **11.4 %** — this wallet sits on a 12-hop peeling chain; population median 0.00 (percentile 70)
> **10.8 %** — this wallet makes 100 % of its transactions between 00:00–06:00 UTC (percentile 94)

**Method: two-endpoint Shapley approximation.** For each feature, two marginal
contributions are measured and averaged —

- *occlusion*: from the real wallet, reset the feature to the population baseline and
  measure the anomaly lost;
- *inclusion*: from an all-baseline wallet, set that one feature to its real value and
  measure the anomaly gained.

Occlusion alone — the obvious implementation, and the one built first — is actively
misleading under correlated features, which these thoroughly are. A peeling-chain wallet is
simultaneously deep in a chain, high-value and high-degree; neutralise any one and the
forest isolates it just as easily via the others, so every genuinely damning feature scores
near zero. Measured: chain depth appeared in the top-5 explanation for only **2 of 25**
known peeling chains. Averaging the two endpoints — the standard cheap approximation of the
Shapley value SHAP estimates — fixed it, at two forest passes instead of a sampling loop.

**Why not SHAP.** It computes a better-approximated version of the same quantity, but needs
a compiled extension with no Python 3.14 wheel at the time of writing. On an offline,
air-gapped target, a dependency that may fail to build is a worse trade than a
15-line exact-at-the-endpoints alternative that always works. All variants are scored in
batched calls, so explaining 300 alerts costs two forest passes, not 300 × 28.

---

## 7. Evaluation

Ground truth is planted by the generator, which also records an **operation ID** grouping
every wallet in one criminal operation. Two units are reported, because neither alone is
honest:

- **Wallet level** — how clean is the worklist. ROC-AUC **0.968**, precision@100 **1.000**,
  precision@500 **0.904**.
- **Operation level** — did the system surface each criminal operation *at all*.
  **93.5 %** within the top 500 alerts, **100 %** within the top 1000.

Operation-level recall is the question an investigator actually asks. One good lead into a
mixer opens an investigation; ranking all 26 of its disposable single-use intermediates
highly is not the goal, and averaging them into a wallet-level recall understates the
system by treating throwaway addresses as separate targets. Wallet-level numbers are
reported alongside so the trade is visible rather than hidden.

| Typology | Operations | Detected @500 | Detected @1000 | Median best rank |
|---|---|---|---|---|
| rapid_passthrough | 30 | 30 | 30 | 14 |
| geo_hopping | 24 | 24 | 24 | 51 |
| ransomware_fanin | 18 | 18 | 18 | 74 |
| mixer_layering | 15 | 15 | 15 | 84 |
| peeling_chain | 24 | 24 | 24 | 217 |
| sybil_broadcast | 12 | 4 | 12 | 821 |

The evaluation is only as honest as its negatives, so the generator plants **hard
negatives** — exchange hot wallets and high-volume merchants. Benign wallets reach degree
251 while illicit ones top out at 54, so transaction volume alone cannot separate the
classes; retention, dwell time, chain structure and network correlation have to do the
work.

---

## 8. Limitations

Stated plainly, because a system that overstates itself is not usable as evidence.

1. **Synthetic data.** Every number here is measured on data this project generated. It
   demonstrates the method works against the modelled typologies; it is **not** a claim
   about real-world performance. Real traffic is messier, adversaries adapt, and the
   typologies here are the ones we thought to plant.
2. **Sybil detection is the weak point** — median best rank 821, only 4 of 12 operations in
   the top 500. The host-fragmentation feature separates cleanly in isolation, but one
   strong feature is diluted across 28 dimensions by an unsupervised global detector.
   Operationally, filtering by the `sybil_broadcast` typology tag surfaces them
   immediately; ranking them by risk alone does not.
3. **Consolidation cash-out wallets are near-undetectable in isolation** — one receipt,
   keeps everything, structurally identical to a savings address. They are reachable only
   through their funding relationship, which the one-hop upstream feature captures
   partially.
4. **Common-input ownership breaks on CoinJoin** and other collaborative spends, by design
   of those protocols. Entity clusters are investigative leads, not proof of control.
5. **Unsupervised anomaly ≠ criminality.** The model finds the unusual. A new exchange, a
   mining pool payout, or an airdrop is unusual and innocent. Alerts are leads for a human
   investigator, never conclusions — which is precisely why every one ships with its
   evidence attached.
6. **GeoIP is approximate.** City-level free databases are frequently wrong at city
   granularity and VPN/Tor exit nodes misattribute wholesale. Country and ASN are used as
   weak signals, never as attribution.
7. **The mmdb reader path is covered with a stub**, matching MaxMind's `country.iso_code`
   layout that DB-IP follows; it has not been run against the full 120 MB database in CI.

---

## 9. Reproducing

```bash
pip install -r requirements.txt
make demo      # ~15 s: generate -> ingest -> detect -> evaluate
make dashboard
make test      # 6 checks including a detection-quality floor
```

The generator is seeded, so the reported figures reproduce exactly. `tests/test_pipeline.py`
fails the build if ROC-AUC drops below 0.88, precision@100 below 0.85, or operation
recall@1000 below 0.90 — so a regression in any of the above surfaces as a failing check
rather than a quietly worse worklist.
