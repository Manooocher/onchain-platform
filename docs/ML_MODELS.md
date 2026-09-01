# ML Foundation — Initial Models

> **Honest scope note:** these are the model *candidates* and their evaluation templates. With the current cohort (~8 durable pairs), any numeric result is a **pipeline smoke number**, not validated model capability. The target metrics below are stated as goals and explicitly caveated; a result that does not meet them with this data is an honest outcome, not a failure of the plan.

## Shared Inputs

- **Features (5):** `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`, `volume_quote_delta_1h`, `honeypot_detected_score`, `liquidity_usd_delta_1h`
- **Labels:** Outcome types RUG_PULL / SUCCESSFUL_LAUNCH / DEAD_TOKEN × observation windows (1h, 24h), versioned `label_definition` / `label_definition_version`
- **Splits:** strictly time-based, grouped by `entity_id`; scalers fit on train only
- **Confidence filter:** drop rows with `liquidity_usd_confidence < 0.5` before training
- **Tracking:** every run in MLflow with a `live` registry pointer

## Model Cards

---

### Model 1: Rug Pull Predictor

| Field | Value |
|-------|-------|
| **Task** | Binary classification |
| **Label** | `RUG_PULL` outcome (`label_value`), window 1h **or** 24h (choose per run based on which has measurable positive class) |
| **Features** | All 5 |
| **Algorithms** | LogisticRegression, RandomForest, XGBClassifier |
| **Target metric** | AUC-ROC ≥ 0.75 ⚠️ *likely not achievable with ~8 durable pairs — treat as pipeline smoke* |
| **Baseline** | Random classifier + rule heuristic (e.g. `liquidity_growth_pct_1h` / `liquidity_usd_delta_1h` threshold) |
| **Use case** | Flag high-risk newly-launched pairs for research follow-up (never trade execution) |
| **Limitations** | Sparse positive class unknown; honeypot insight is already a strong rule signal — the model must beat it, not just rediscover it |
| **Data version** | feature set v1 (5 features); label definition version per run |
| **label_definition_version** | `outcome_rules.OUTCOME_RULES_VERSION` (= "1.0") at label time |

---

### Model 2: Liquidity Forecaster

| Field | Value |
|-------|-------|
| **Task** | Regression |
| **Target** | `liquidity_usd` at T+24h (from snapshot history) |
| **Features** | All 5 (esp. `liquidity_usd_delta_1h`, `liquidity_growth_pct_1h`) |
| **Algorithms** | Ridge/Lasso, GradientBoostingRegressor, XGBRegressor |
| **Target metric** | MAE ≤ 20% of mean `liquidity_usd` ⚠️ *with tiny data, MAE will be dominated by a few rows — report honestly* |
| **Baseline** | Mean-value regressor + naive "carry forward last liquidity" heuristic |
| **Use case** | Project liquidity persistence for newly-launched pair screening |
| **Limitations** | Only priced (non-exotic) pools have `liquidity_usd`; confidence < 0.5 rows dropped; few labeled T+24h targets exist |
| **Data version** | feature set v1 (5 features) |
| **label_definition_version** | n/a (no label; supervised by future snapshot) |

---

### Model 3: Momentum Ranker

| Field | Value |
|-------|-------|
| **Task** | Ranking |
| **Target** | `price_momentum_zscore_1h` ordering (predict which pair has higher momentum) |
| **Features** | All 5 (esp. `volume_quote_delta_1h`, `price_momentum_zscore_1h`) |
| **Algorithms** | scalar-score ranking (LTR-style, or pairwise scorer via sklearn) |
| **Target metric** | NDCG@10 ≥ 0.6, MRR ⚠️ *with a handful of pairs, NDCG is noisy* |
| **Baseline** | Random order + "sort by current momentum" heuristic |
| **Use case** | Order research attention across candidate pairs |
| **Limitations** | Ranking quality bounded by pair count; fixture-only validation for now |
| **Data version** | feature set v1 (5 features) |

---