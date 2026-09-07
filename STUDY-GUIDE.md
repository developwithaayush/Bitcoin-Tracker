# btctrace — Study Guide

Everything you need to understand before demoing this to judges: the vocabulary, how the
system actually works, why each design decision was made, and how to answer the hard
questions honestly.

Read §1–§3 to be able to *explain* it. Read §7–§9 to be able to *defend* it.

---

## 1. The 60-second pitch

> Bitcoin is pseudonymous, not anonymous. Every transaction leaves two separate traces: the
> **blockchain layer** — which wallet paid which wallet, how much — and the **network
> layer** — which computer broadcast that transaction, when, and from where.
>
> Investigators normally look at one or the other. Each alone is weak: a mixer looks like a
> legitimate exchange on-chain, and a busy node looks like a criminal relay on the wire.
>
> btctrace ingests both, builds a single graph linking wallets, transactions and IP
> addresses, and runs unsupervised machine learning over 28 features drawn from both layers
> at once. It outputs a ranked worklist where every alert says *why* it was flagged.
>
> On a synthetic dataset with 123 planted criminal operations hidden among 34,483 wallets,
> it surfaces **100% of the operations within the top 1000 alerts**, with **perfect
> precision in the top 100**. It runs completely offline.

**If you say nothing else, say this:** the novelty is *correlating the two layers inside one
model*, and every alert is explainable.

---

## 2. The pipeline in plain English

```
CSV / JSON / XML  ──►  canonical table  ──►  entity graph  ──►  28 features per wallet
                                                                        │
                        ranked, explained alerts  ◄──  ML scoring  ◄────┘
```

**Stage 1 — Ingest.** Read bulk metadata in any of three formats. Normalise to one table.
Validate every row: bad timestamps, invalid IPs, impossible ports, malformed transaction
IDs, negative amounts, and transactions where the money doesn't add up are *quarantined*
to `rejected.csv` **with a reason** — never silently dropped. Optionally enrich each IP
with a country via a local GeoIP database.

**Stage 2 — Graph.** Build a graph with three kinds of node — wallets, transactions, IP
addresses — and three kinds of edge: wallet→transaction (spending), transaction→wallet
(receiving), IP→transaction (broadcasting). Group addresses into *entities* using the
common-input-ownership heuristic.

**Stage 3 — Features.** For every wallet, compute 28 numbers describing how it behaves:
how much it keeps, how fast it moves money, how deep in a chain it sits, how many countries
it broadcast from, how many unrelated wallets share its host.

**Stage 4 — Detect.** An IsolationForest scores every wallet for anomalousness. DBSCAN
groups wallets into behavioural cohorts. Six typology detectors attach human-readable
*names* to patterns. These are fused into a risk score and a confidence score.

**Stage 5 — Explain.** For the top alerts, compute how much each feature contributed to the
score, and render it as English: *"sits on a 12-hop peeling chain — 11.4% of risk score."*

**Stage 6 — Present.** A Streamlit dashboard with a ranked alert table, per-alert evidence,
an interactive link-analysis graph, and model performance metrics.

---

## 3. Terminology

### A. Bitcoin fundamentals

| Term | Meaning |
|---|---|
| **UTXO** | Unspent Transaction Output. Bitcoin has no account balances — you hold *outputs* from previous transactions, and spending means consuming them entirely as new inputs. |
| **Input / Output** | A transaction consumes inputs and creates outputs. `sum(inputs) = sum(outputs) + fee`, always. Our validator enforces exactly this. |
| **Change address** | Because outputs are consumed whole, spending 0.1 BTC from a 2 BTC output sends 0.1 to the payee and ~1.9 back to a *new address you own*. This is why one person owns thousands of addresses. |
| **TXID** | Transaction ID — a 64-character hex hash uniquely naming a transaction. |
| **Satoshi** | The smallest unit: 0.00000001 BTC. Amounts have exactly 8 decimal places. |
| **Fee** | Paid to miners, implied by inputs minus outputs. Scales with transaction *size in bytes*, not value. |
| **vsize** | Virtual size in bytes — what fees are actually charged on. |
| **Pseudonymity** | Addresses aren't names, but every transaction is public and permanent forever. That's what makes forensics possible. |

**Address / script types** (you'll see all four in the data):

| Type | Full name | Looks like |
|---|---|---|
| **P2PKH** | Pay to Public Key Hash (legacy) | `1A1zP1eP...` |
| **P2SH** | Pay to Script Hash | `3J98t1Wp...` |
| **P2WPKH** | Pay to Witness Public Key Hash (native SegWit) | `bc1q...` |
| **P2TR** | Pay to Taproot (newest) | `bc1p...` |

### B. Crime typologies — the six we detect

These are the actual patterns. **Know all six cold** — judges will ask.

**1. Ransomware fan-in** (`ransomware_fanin`)
Many victims each pay a near-identical ransom into one collector wallet inside a short
window; the collector then consolidates and cashes out.
*Signature:* many receipts + near-identical amounts + concentrated in time.

**2. Peeling chain** (`peeling_chain`)
A large balance walks down a long chain of fresh addresses. At each hop it "peels" off a
small payment and carries the remainder forward to a brand-new address. Classic way to move
a big sum without one obvious large transfer.
*Signature:* long chain of single-use addresses each keeping ~95%+ of what it received.

**3. Mixer / layering** (`mixer_layering`)
Funds fan out into many single-use intermediates in uniform slices, then recombine
elsewhere. Deliberately destroys the link between source and destination.
*Signature:* wide fan-out + uniform amounts + short dwell + zero address reuse.

**4. Rapid pass-through** (`rapid_passthrough`)
Money arrives and leaves within minutes. The wallet is a conduit, not a destination.
*Signature:* near-zero retention + hop latency measured in minutes.

**5. Sybil broadcast** (`sybil_broadcast`) — *network layer*
One host broadcasts for many wallets that share no on-chain connection at all. Invisible to
blockchain-only analysis.
*Signature:* one IP, many mutually **unrelated** wallet groups.

**6. Geographic hopping** (`geo_hopping`) — *network layer*
One wallet broadcasts from many countries and network operators within hours — VPN/Tor
rotation or distributed infrastructure.
*Signature:* many distinct ASNs and countries per active day.

**General AML vocabulary judges may use:**

- **Placement / Layering / Integration** — the three classic money-laundering stages. Our
  mixer and peeling-chain detectors target *layering*.
- **Typology** — a named pattern of criminal behaviour. Standard AML term.
- **Cash-out** — converting crypto to fiat; the exit point of an operation.
- **Consolidation** — sweeping many small balances into one wallet.
- **Fan-in / fan-out** — many→one and one→many value flows.
- **Dwell time** — how long value sits in a wallet before moving on.

### C. Blockchain forensics

| Term | Meaning |
|---|---|
| **Common-input-ownership heuristic** | If two addresses are spent as inputs to the *same* transaction, one actor controls both — you need all their private keys to sign. The foundational move in address clustering. |
| **Entity / address clustering** | Merging many addresses into one real-world actor using the above. We do it with connected components. |
| **CoinJoin** | Multiple unrelated users deliberately co-sign one transaction, so the common-input heuristic produces a **false** cluster. This is the main documented limitation of our entity clustering. |
| **Taint analysis** | Tracking "dirtiness" downstream from a known-bad wallet. We **tested this and rejected it** — see §7. |
| **Link analysis** | Visual graph investigation — the dashboard's second tab. |

### D. Network layer

| Term | Meaning |
|---|---|
| **P2P broadcast** | To transact, you send it to peer nodes who relay it. Whoever observes this first sees the originating IP. |
| **Port 8333** | Bitcoin mainnet's default P2P port (18333 = testnet). Non-standard ports are mildly suspicious. |
| **ASN** | Autonomous System Number — identifies the *network operator* (AS16509 = Amazon, AS24940 = Hetzner). Far more stable and meaningful than an IP. |
| **GeoIP** | Mapping IP → country. We use **DB-IP City Lite** (free, CC BY 4.0, no account). |
| **.mmdb** | MaxMind DB — the compact binary format GeoIP databases ship in. |
| **Bulletproof hosting** | Providers that ignore abuse complaints; favoured by criminal infrastructure. |
| **Sybil node** | One operator presenting as many independent identities. |

### E. Machine learning — the important section

| Term | Meaning |
|---|---|
| **Supervised learning** | Training on labelled examples. **We can't use it** — real intercepted traffic has no labels. |
| **Unsupervised learning** | Finding structure with no labels. What we use. |
| **Anomaly / outlier detection** | Identifying rare, unusual data points. Our core task. |
| **Feature** | One measurable number describing an entity. We compute 28 per wallet. |
| **Feature matrix** | The table of wallets × features that the model consumes. |
| **Ground truth** | The known-correct answer, used only to *measure* — never shown to the model. |
| **Class imbalance** | Only ~3% of wallets are illicit. This is why plain accuracy is a useless metric. |

**IsolationForest — our primary model. Understand this properly.**

It builds many random decision trees. In each, it repeatedly picks a random feature and a
random split point, partitioning the data until every point is isolated.

The insight: **anomalies get isolated in fewer splits.** A wallet unusual in several
dimensions gets cut off from everything else almost immediately, while a typical wallet
sits deep in a crowded region needing many splits. The anomaly score is derived from the
average number of splits ("path length") needed across all 300 trees — shorter path = more
anomalous.

*Why we chose it:* needs no labels; targets rarity directly rather than modelling
normality; linear in dataset size; CPU-only, so it runs on an investigator's laptop.

**DBSCAN — our clustering model.**
Density-based clustering. A point is a "core point" if at least `min_samples` (12) other
points lie within distance `eps` (1.4); connected core points form a cluster. Unlike
k-means it needs no cluster count in advance, and it can label a point `-1` = **noise**,
belonging to no cluster — itself a mild oddity signal.

**PCA (Principal Component Analysis).**
Projects data onto the directions of greatest variance, reducing 28 dimensions to 8. We do
this *before* DBSCAN because of the **curse of dimensionality** — in high dimensions all
points become roughly equidistant ("distance concentration"), so density clustering fails.

**QuantileTransformer.**
Replaces each feature value with its **rank**, then reshapes to a normal distribution. This
makes wildly different scales comparable (satoshis vs. seconds vs. counts) and neutralises
extreme outliers.

⚠️ **This has a critical consequence you must understand** — because it's rank-based, a
value is only "extreme" to the model if it sits at the *top or bottom* of the ranking. A
genuinely rare value stuck in the middle of the distribution is invisible. This caused a
real bug we had to fix (see §7).

**Explainability (XAI)**

| Term | Meaning |
|---|---|
| **Shapley value** | From cooperative game theory: a feature's fair share of the outcome, defined as its average *marginal contribution* across all possible orderings of features. |
| **SHAP** | The popular library that approximates Shapley values. **We deliberately don't use it** — see §7. |
| **Occlusion / ablation** | Remove one feature (set it to a baseline), re-score, and attribute the drop to it. |
| **Marginal contribution** | How much the score changes when one feature is added to (or removed from) a set. |
| **Attribution** | The per-feature credit assigned to a single prediction. |

### F. Graph theory

| Term | Meaning |
|---|---|
| **Directed graph** | Nodes joined by one-way edges. Value flows in a direction. |
| **Heterogeneous graph** | A graph with *multiple node types* — ours has wallets, transactions and IPs. |
| **Degree** | How many edges touch a node. In-degree = receipts, out-degree = spends. |
| **Connected component** | A group of nodes reachable from one another. We use these for entity clustering. |
| **DAG** | Directed Acyclic Graph — no cycles. Transaction history is naturally a DAG. |
| **Longest path** | Used to measure peeling-chain length. |
| **Memoised DFS** | Depth-first search caching results, so longest-path is computed once per node. |
| **One-hop neighbourhood aggregation** | Summarising a node's immediate neighbours into a feature. This is the core idea behind Graph Neural Networks; we use one hop of it in `upstream_max_receives`. |

---

## 4. The 28 features, in plain English

**Value flow (10)** — `n_spends`, `n_receives`, `total_sent`, `total_received`,
`mean_received`, `degree`, `retention_ratio`, `uniformity_received`, `uniformity_sent`,
`round_amount_frac`

- `retention_ratio` — fraction of received value still held. **0 = pure conduit** (mixer,
  pass-through), **1 = accumulator** (savings, cash-out).
- `uniformity_received` — `1/(1+CV)`, high when a wallet receives many near-identical
  amounts. The ransomware-collector signature.

**Transaction shape (3)** — `max_tx_fanout`, `max_tx_fanin`, `n_counterparties`
Widest fan-out is the mixer tell; counterparty count separates hubs from ordinary wallets.

**Temporal (5)** — `lifetime_days`, `burst_max_24h`, `night_frac`, `interarrival_cv`,
`hop_latency_h`

- `hop_latency_h` — **hours between receiving and re-spending**. Minutes = layering hop;
  days = legitimate holding. One of our strongest features.
- `burst_max_24h` — most transactions in any 24h window. Catches ransom collection windows.

**Graph (3)** — `upstream_max_receives`, `peel_chain_depth`, `entity_size`

- `peel_chain_depth` — length of the peeling chain running **through** this wallet.
- `upstream_max_receives` — how busy the wallet that *funded* this one was. This is what
  makes an otherwise-invisible cash-out wallet detectable.

**Network (7)** — `n_ips`, `n_countries`, `n_asns`, `nonstd_port_frac`,
`shared_ip_cohort`, `ip_cohort_components`, `geo_hop_rate`

- `ip_cohort_components` — **the cleverest feature in the project.** How many *mutually
  unrelated* wallet groups broadcast from this wallet's host. A normal heavy user's
  addresses are all chained together by their own change outputs, so they collapse into
  **one** component. A sybil operator fronting for unrelated parties produces **many
  disjoint** components on one IP. This signal exists in *neither layer alone* — it only
  appears when you correlate them. **Lead with this one.**

> **Key talking point:** the network features live in the *same matrix* as the on-chain
> features. That's what makes the correlation a *modelled signal* rather than a rule bolted
> on afterwards.

---

## 5. Metrics — what each number means

| Metric | Our value | What it means |
|---|---|---|
| **ROC-AUC** | 0.968 | Pick one illicit and one benign wallet at random — the probability we rank the illicit one higher. 0.5 = coin flip, 1.0 = perfect. |
| **Precision@100** | 1.000 | Of the top 100 alerts, **all 100** are genuinely illicit. Zero wasted investigator time at the top of the list. |
| **Precision@500** | 0.904 | 90.4% of the top 500 are genuinely illicit. |
| **Operation recall@1000** | 100% | All 123 planted criminal operations have at least one wallet in the top 1000. |
| **Average precision** | — | Area under the precision-recall curve; a single summary of the whole ranking. |
| **R-precision** | — | Precision at k = the number of true positives. A standard balanced summary. |

**Precision vs recall** — memorise this:
- **Precision** = of what we flagged, how much was real. *(Are we wasting the investigator's time?)*
- **Recall** = of what was real, how much did we flag. *(Are we missing crimes?)*

### ⭐ The single most important idea in the project: two units of measurement

We report detection at **two levels**, and you must be able to explain why.

- **Wallet level** — how clean the worklist is.
- **Operation level** — did we surface each criminal *operation* at all?

**Why this matters:** a mixer has ~26 disposable single-use intermediate addresses. An
investigator needs **one** good lead to open the case on that mixer — ranking all 26
throwaway addresses highly is not the goal. Measuring only at wallet level would understate
the system by treating disposable addresses as if each were a separate target.

But reporting *only* operation-level recall would flatter us. So we report both. **Neither
number alone is honest.**

This is the kind of reasoning that separates a project that measured itself from one that
just printed a number.

---

## 6. The numbers you should have memorised

```
17,358 transactions   34,483 wallets   28 features   123 planted operations
ROC-AUC 0.968   Precision@100 1.000   Precision@500 0.904
Operations found: 93.5% @500   ·   100% @1000
Runtime ~35 s   ·   6/6 tests pass   ·   3 dependencies   ·   fully offline
```

Model settings: IsolationForest **300 trees** · DBSCAN **eps 1.4, min_samples 12** on
**8 PCA components** · risk = `0.65 × model_score + 0.35 × min(1, tags/3)`.

---

## 7. Design decisions you must be able to defend

Judges reward *measured* decisions over plausible ones. Each of these was tested, and
several rejected good-sounding ideas. **This is your strongest material.**

**① We attribute the source IP to the spender only.**
The broadcasting host belongs to whoever *sent* the transaction. Initially we credited it
to recipients too — which hands every payee its payer's network footprint, inventing an
association never observed. Fixing it **lowered** our headline score (R-precision
0.72 → 0.59), because the earlier number was inflated by a leak that artificially clustered
illicit wallets.
> *"We kept the correct, lower number. The earlier one was measuring our own bug."*

**② Uniformity, not variance.**
A ransomware collector's receipts have coefficient of variation ≈ 0.011 — genuinely rare.
But over half of all wallets receive exactly once and have *no defined* variation. Encoding
both as 0.0 buried the signal. Because QuantileTransformer is rank-based, even a distinct
"undefined" sentinel just moved it to mid-distribution where the model can't see it. Mapping
to `1/(1+CV)` puts "many receipts, all alike" at the **top** of the range — the only place
an isolation forest reacts.
> *This is the single best example of understanding **why** your model behaves as it does.*

**③ Chain length through a wallet, not ahead of it.**
Forward-only depth credits the *head* of a 20-hop peeling chain with 20 and its successors
with progressively less, down to 0 at the tail — yet every one is equally a link, and the
tail is where the money is about to leave. Measuring both directions: **AUC 0.950 → 0.967**.

**④ We tested taint propagation and rejected it.**
Spreading risk along value-flow edges lifted ransomware cash-outs (rank 5323 → 1791) but
**collapsed precision** (R-precision 0.74 → 0.37) by flooding every ordinary change address
with false taint. Rejected on evidence.

**⑤ We tested noisy-OR fusion and rejected it.**
A principled probabilistic alternative to our weighted sum. It scored worse on every metric
(R-precision 0.67 → 0.30), so we kept the weighted sum — now a *verified* choice rather than
an arbitrary one.

**⑥ Occlusion-only attribution was misleading; we fixed it.**
The obvious implementation — knock out one feature, measure the drop — fails badly under
**correlated features**. A peeling-chain wallet is simultaneously deep in a chain,
high-value and high-degree; neutralise any one and the forest isolates it just as easily via
the others, so every genuinely damning feature scores ≈ 0. Measured: chain depth appeared in
the top-5 explanation for only **2 of 25** known peeling chains.

Our fix measures the marginal contribution at **both ends**:
- *occlusion* — from the real wallet, reset one feature to baseline → anomaly lost
- *inclusion* — from an all-baseline wallet, set one feature to its real value → anomaly gained

Averaging these two is the standard **two-endpoint approximation of a Shapley value** — the
same quantity SHAP estimates, at two forest passes instead of a sampling loop.

**⑦ Why not SHAP?**
It approximates the same quantity better, but requires a compiled C extension with no
Python 3.14 wheel available. On an **air-gapped** target machine, a dependency that might
fail to build is a worse trade than 15 lines that always work.
> *"We chose deployability over brand name, and we can show the maths is the same
> quantity."*

**⑧ Tight typology rules.**
Loose rules were worse than none: an early ransomware rule also matched pass-through and
mixer wallets, so alerts arrived carrying a typology that **contradicted their own
evidence**. An investigator acts on that name. Tightening the rules took precision@100 from
0.95 → **1.000**.

---

## 8. Likely judge questions — and honest answers

**"Isn't this just rules with extra steps?"** ← *the question most likely to sink you*

Don't hand-wave this. We ran the ablation, and the answer is **neither component works
alone** — which is a better answer than "it's all the model":

| Configuration | ROC-AUC | P@100 | P@500 | Op recall@1000 |
|---|---|---|---|---|
| Model only (rules removed) | 0.926 | 0.310 | 0.392 | 82.1% |
| Rules only (model removed) | 0.814 | 0.900 | 0.910 | 93.5% |
| **Fused (shipped)** | **0.968** | **1.000** | 0.904 | **100%** |

How to say it:

> *"We measured exactly that. The model alone gets ROC-AUC 0.926 — it separates illicit
> from benign far above chance with no rule involved, and it finds operations we never
> described. But its top-of-list precision is only 0.31, because the most statistically
> anomalous wallets are often legitimately weird ones like exchange hot wallets.*
>
> *The rules alone get good precision but rank badly — 0.814 — because a rule is binary:
> it can't order the wallets it matches, and it only ever finds the six patterns we thought
> to name, missing 6.5% of operations entirely.*
>
> *Fused, we beat both: 0.968 AUC, perfect precision at 100, and 100% of operations found.
> The model supplies the ranking and the unnamed patterns; the rules supply precision at
> the top and a human-readable name."*

Note the one honest wrinkle: rules-only edges out the fused system at P@500 (0.910 vs
0.904). Say so if it comes up — it's within noise, and fused wins on every other measure.

**"Why unsupervised and not a neural network / XGBoost?"**
Because real intercepted traffic arrives with **no labels**. A supervised model would have
nothing to train on — it would be a demo, not a deployable system. We only have labels
because we generated the data, and we deliberately never show them to the model. The
labelled data is used *exclusively* for measurement.

**"Your data is synthetic — so what does this prove?"**
It proves the method detects the modelled typologies, and it lets us report real
precision/recall instead of asserting quality. We state plainly it is **not** a claim about
real-world performance. Crucially we planted **hard negatives** — exchange and merchant
wallets whose volume *exceeds* the criminals' (benign reach degree 251, illicit top out at
54) — so transaction volume alone cannot separate the classes. Without that, the precision
number would measure nothing but our own generator.

**"How would you validate on real data?"**
Ingestion already accepts any CSV/JSON/XML with the required fields. Validation would need
labelled seizure data or known-bad address lists (e.g. OFAC-sanctioned addresses) as a
partial ground truth, then a backtest against confirmed cases.

**"What's the false positive rate?"**
At the top 100, zero. At 500, 9.6%. Those false positives are mostly exchange hot wallets —
legitimately anomalous, which is why the system produces *leads for a human*, not verdicts.

**"How does it scale?"**
Feature extraction is `groupby` over transaction legs — linear in the number of legs.
IsolationForest is linear in samples and parallel. 17k transactions in ~35s single-threaded
on a laptop. The current bottleneck is graph construction, not the model.

**"Can criminals evade it?"**
Yes, and we should say so. Slowing dwell times below our thresholds, avoiding address reuse,
and using a fresh IP per broadcast would each defeat specific features. This raises the
adversary's cost and reduces their operational convenience, which is the realistic goal.
No detection system is evasion-proof.

**"Why an isolation forest specifically?"**
It targets *rarity* directly rather than modelling normality — right for a domain where the
interesting behaviour is 3% of the population. It needs no labels, no GPU, is linear in
samples, and handles the heavy-tailed scales Bitcoin data naturally has.

**"What does 'confidence' mean, versus risk?"**
Risk is how suspicious. Confidence is **agreement** — how many *independent* evidence
families point at this wallet (the model, an on-chain motif, a network correlation). A
wallet three independent signals agree on is a safer lead than one an extreme score alone
singles out.

---

## 9. Known weaknesses — say these before you're asked

Volunteering limitations reads as competence. Being caught hiding one reads as the opposite.

1. **Sybil detection is our weakest typology** — median rank 821, only 4 of 12 operations in
   the top 500. The host-fragmentation feature separates cleanly *in isolation*, but one
   strong feature gets diluted across 28 dimensions by an unsupervised global detector.
   *Workaround:* filtering by the `sybil_broadcast` tag surfaces them immediately.
2. **Consolidation cash-out wallets are near-undetectable alone** — one receipt, keeps
   everything, structurally identical to a savings address. Only reachable via their funding
   relationship (`upstream_max_receives`), and only partially.
3. **Common-input ownership breaks on CoinJoin** by design of that protocol. Entity clusters
   are investigative leads, not proof of control.
4. **Anomalous ≠ criminal.** A new exchange, a mining pool payout, an airdrop — all unusual
   and innocent. This is exactly why every alert ships with its evidence attached and why
   the output is a worklist for a human.
5. **GeoIP is approximate.** Free city-level databases are often wrong, and VPN/Tor exits
   misattribute wholesale. We use country/ASN as weak signals, never as attribution.
6. **Synthetic data** — see §8.

---

## 10. Demo script

Have `make demo` already run before you present. Then:

1. **Terminal, 10s** — `python -m btctrace.cli pipeline`. Show the JSON: rows ingested,
   0 rejected, metrics at the bottom. *"Whole thing, offline, 35 seconds."*
2. **Dashboard → Ranked alerts** — *"34,483 wallets scored, ranked by risk. Every row has a
   named typology and a count of independent signals."*
3. **Click a peeling-chain alert** — read the evidence aloud: *"sits on a 12-hop peeling
   chain, 11.4% of the risk score; population median 0, 70th percentile."* → **"This is the
   explainability requirement — not a black box score."**
4. **Link analysis tab** — *"Red is the subject wallet, grey are transactions, blue are
   counterparties, amber is the broadcasting host. This runs fully offline — the graph
   library is inlined, no CDN."*
5. **Model performance tab** — ROC-AUC 0.968, Precision@100 1.000, 123 operations, 100%
   operation recall. Explain the two-unit measurement from §5.
6. **Close with a limitation** (§9) — it lands as confidence, not weakness.

**If asked to prove it's real:** `python tests/test_pipeline.py` — 6 checks, including a
detection-quality floor that fails the build if ROC-AUC drops below 0.88.

---

## 11. One-page cheat sheet

**Pitch:** Correlates blockchain + network layers in one ML model; every alert explains
itself; runs fully offline.

**Six typologies:** ransomware fan-in · peeling chain · mixer layering · rapid pass-through
· sybil broadcast · geo-hopping

**Stack:** pandas · scikit-learn (IsolationForest, DBSCAN, PCA, QuantileTransformer) ·
networkx · pyvis · Streamlit. Three new dependencies total.

**Best feature to brag about:** `ip_cohort_components` — unrelated wallet groups per host.
Invisible to either layer alone.

**Best decision to brag about:** occlusion→two-endpoint Shapley attribution, after measuring
that occlusion alone explained only 2 of 25 known peeling chains.

**Best metric to brag about:** 100% operation recall @1000, precision@100 = 1.000.

**Best limitation to volunteer:** sybil ranking is weak (median 821); the tag catches them,
the ranking doesn't.

**Never claim:** that this is validated on real-world data, or that an alert proves
criminality. It produces *prioritised, explainable investigative leads* — that is exactly
what the problem statement asked for.
