# btctrace — Terminology to Study

Study list for the team before the judging round. One line per term: if you can say that
line out loud in your own words, you know it well enough. Depth and defence answers live in
[STUDY-GUIDE.md](STUDY-GUIDE.md); this file is the flashcard set.

**Rule for the room:** never say a term you cannot define. Judges follow up on jargon.

---

## Split it up — who studies what

Everyone learns §1, §2, §3 and §9. Then split the rest so each block has an owner:

| Owner | Sections |
|---|---|
| Person A — domain | §4 Forensics & AML (plus depth on §1, §3) |
| Person B — ML | §6 Machine learning, §7 Explainability, §8 Metrics |
| Person C — systems | §5 Network layer, §10 Stack & pipeline |

Every person must still be able to answer §9 alone.

---

## 1. Bitcoin fundamentals — everyone

| Term | One-line answer |
|---|---|
| **UTXO** | Unspent Transaction Output — Bitcoin has no balances, you hold outputs and spend them whole. |
| **Input / Output** | A transaction consumes inputs and creates outputs; `sum(inputs) = sum(outputs) + fee`, always. |
| **Change address** | The leftover from spending a whole output, sent back to a fresh address you own — why one person owns thousands of addresses. |
| **TXID** | 64-character hex hash naming a transaction. |
| **Satoshi** | Smallest unit, 1e-8 BTC — amounts carry exactly 8 decimal places. |
| **Fee** | Inputs minus outputs, paid to miners; scales with size in bytes, not value. |
| **vsize** | Virtual size in bytes — what the fee is actually charged on. |
| **Pseudonymity** | Addresses are not names, but every transaction is public and permanent — this is what makes forensics possible at all. |
| **P2PKH / P2SH / P2WPKH / P2TR** | The four script types in our data: legacy `1...`, script hash `3...`, SegWit `bc1q...`, Taproot `bc1p...`. |
| **P2P broadcast** | You hand a transaction to peer nodes to relay; the first observer sees the originating IP. |

## 2. Project vocabulary — everyone

| Term | One-line answer |
|---|---|
| **Wallet vs entity** | A wallet is one address; an entity is the cluster of addresses we believe one actor controls. |
| **Operation** | One planted criminal scheme in the dataset — may span dozens of wallets. |
| **Lead / alert** | One ranked row we hand an investigator. |
| **Risk score** | `0.65 × model_score + 0.35 × min(1, tags/3)` — the number the list is sorted by. |
| **Tag** | A rule-based typology label attached to a wallet; feeds the 0.35 term above. |
| **Ground truth** | The known-correct labels — used only to measure, never shown to the model. |
| **Canonical store** | The normalised Parquet tables everything downstream reads. |
| **Rejected rows** | Input rows that failed validation, written to `data/rejected.csv` rather than silently dropped. |
| **Offline / air-gapped** | No network calls at run time; GeoIP is a one-time optional download. |

## 3. The six crime typologies — everyone, cold

| Typology | Signature to recite |
|---|---|
| **Ransomware fan-in** | Many victims, near-identical amounts, one collector, short window. |
| **Peeling chain** | Big balance walks a long chain of fresh addresses, peeling a small payment at each hop. |
| **Mixer / layering** | Wide fan-out into uniform single-use intermediates, then recombination. |
| **Rapid pass-through** | In and out within minutes — a conduit, not a destination. |
| **Sybil broadcast** | One host broadcasting for many *unrelated* wallet groups — network layer only. |
| **Geo hopping** | One wallet broadcasting from many countries and ASNs within hours. |

## 4. Forensics and AML

| Term | One-line answer |
|---|---|
| **Common-input-ownership heuristic** | Addresses spent as inputs to the same transaction share one owner — the basis of clustering. |
| **Entity / address clustering** | Merging addresses into one actor; we do it with connected components. |
| **CoinJoin** | Unrelated users deliberately co-sign one transaction, breaking the heuristic above — our main clustering limitation. |
| **Taint analysis** | Tracking dirtiness downstream from a known-bad wallet; we tested it and rejected it. |
| **Link analysis** | Visual graph investigation — the dashboard's second tab. |
| **Placement / layering / integration** | The three money-laundering stages; our mixer and peel detectors target layering. |
| **Typology** | A named pattern of criminal behaviour — standard AML word. |
| **Fan-in / fan-out** | Many-to-one and one-to-many value flows. |
| **Dwell time** | How long value sits in a wallet before moving on. |
| **Consolidation / cash-out** | Sweeping many balances into one; converting to fiat at the exit. |

## 5. Network layer

| Term | One-line answer |
|---|---|
| **ASN** | Autonomous System Number — identifies the network operator, far more stable than an IP. |
| **GeoIP** | IP to country lookup; we use DB-IP City Lite, free, CC BY 4.0, no account. |
| **.mmdb** | MaxMind DB — the binary format GeoIP databases ship in. |
| **Port 8333** | Bitcoin mainnet's P2P port (18333 = testnet); non-standard ports are mildly suspicious. |
| **Sybil node** | One operator presenting as many independent identities. |
| **Bulletproof hosting** | Providers that ignore abuse complaints. |
| **Correlation** | Network features sitting in the *same* feature matrix as on-chain ones — a modelled signal, not a rule bolted on afterwards. |

## 6. Machine learning — the section judges push on

| Term | One-line answer |
|---|---|
| **Supervised vs unsupervised** | Labelled vs unlabelled training; real intercepted traffic has no labels, so we are unsupervised. |
| **Anomaly detection** | Finding rare, unusual points — our core task. |
| **Feature / feature matrix** | One number describing a wallet; the wallets × 28 features table the model consumes. |
| **Class imbalance** | Only ~3% of wallets are illicit — why plain accuracy is a useless metric here. |
| **IsolationForest** | 300 random trees; anomalies get isolated in fewer splits, so shorter average path length = more anomalous. |
| **DBSCAN** | Density clustering — core points within `eps` 1.4 with `min_samples` 12; needs no cluster count and can label a point `-1` = noise. |
| **PCA** | Projects 28 dimensions onto the 8 directions of greatest variance, run before DBSCAN. |
| **Curse of dimensionality** | In high dimensions all points become roughly equidistant, so density clustering fails — the reason for PCA. |
| **QuantileTransformer** | Replaces each value with its rank, then reshapes to normal — makes satoshis, seconds and counts comparable. |
| **Rank-based blind spot** | Because it is rank-based, a rare value sitting mid-distribution is invisible to the model; this caused a real bug we fixed. |

**Feature names worth knowing by heart:** `retention_ratio` (0 = conduit, 1 = accumulator),
`hop_latency_h` (minutes = layering hop, days = holding), `peel_chain_depth`,
`upstream_max_receives`, and **`ip_cohort_components`** — how many mutually unrelated wallet
groups share this wallet's host. Lead with that last one: it exists in neither layer alone.

## 7. Explainability

| Term | One-line answer |
|---|---|
| **XAI** | Explainable AI — why the model scored this wallet, not just that it did. |
| **Shapley value** | A feature's fair share of the outcome: its average marginal contribution across all feature orderings. |
| **SHAP** | The library that approximates Shapley values — we deliberately do not use it. |
| **Occlusion / ablation** | Set one feature to a baseline, re-score, attribute the drop to it — what we do instead. |
| **Attribution** | The per-feature credit assigned to a single prediction. |

## 8. Metrics

| Term | One-line answer |
|---|---|
| **Precision** | Of what we flagged, how much was real — are we wasting investigator time? |
| **Recall** | Of what was real, how much did we flag — are we missing crimes? |
| **ROC-AUC** | Probability a random illicit wallet outranks a random benign one; 0.5 = coin flip. |
| **Precision@k** | Precision within the top k alerts — the number that matches how a worklist is actually used. |
| **Average precision** | Area under the precision-recall curve; one summary of the whole ranking. |
| **R-precision** | Precision at k = the number of true positives. |
| **Wallet level vs operation level** | The two units we report — worklist cleanliness vs. did we surface the operation at all. **Neither number alone is honest.** |

## 9. Numbers and answers everyone must have memorised

```
17,358 transactions   34,483 wallets   28 features   123 planted operations
ROC-AUC 0.968   Precision@100 1.000   Precision@500 0.904
Operations found: 93.5% @500  ·  100% @1000
Runtime ~35 s  ·  6/6 tests pass  ·  fully offline
IsolationForest 300 trees  ·  DBSCAN eps 1.4, min_samples 12, on 8 PCA components
risk = 0.65 × model_score + 0.35 × min(1, tags/3)
```

Three answers to have ready in one sentence each:

- **Why unsupervised?** Real intercepted traffic arrives with no labels.
- **Why is this not just blockchain analysis?** `ip_cohort_components` — a signal that exists
  in neither the network nor the chain layer alone.
- **Why two levels of measurement?** A mixer's 26 disposable addresses are one case, not 26.

## 10. Stack and pipeline

| Term | One-line answer |
|---|---|
| **Pipeline stages** | generate → ingest → detect → evaluate; `pipeline` runs all four. |
| **Ingestion** | Parses CSV / JSON / XML into the canonical store, validating `sum(inputs) = sum(outputs) + fee`. |
| **Parquet** | Columnar file format for the canonical store; read via **pyarrow**. |
| **pandas / numpy** | Dataframes and numeric arrays — the feature matrix lives here. |
| **scikit-learn** | Supplies IsolationForest, DBSCAN, PCA and QuantileTransformer. |
| **networkx** | Builds the entity graph and finds connected components. |
| **pyvis** | Renders the offline link-analysis graph (vis.js inlined, no CDN). |
| **maxminddb** | Reads the `.mmdb` GeoIP database. |
| **Streamlit / Altair** | The dashboard and its charts. |
| **Seed 1337** | Fixed RNG seed — why every run reproduces the same numbers. |
| **Connected components** | Groups of mutually reachable nodes; our entity clustering primitive. |
| **DAG / longest path / memoised DFS** | Transaction history is acyclic; longest path measures peel-chain depth, cached per node. |
