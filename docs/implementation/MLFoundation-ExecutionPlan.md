# ML Foundation Execution Plan — Phase 4 (Base Chain Only)

> **ML Foundation Goal:** train the first machine-learning models on platform data so the platform's research is *predictive*, not just descriptive — starting with a pragmatic, honest minimal version on the data that actually exists.
>
> **Strategy decision (approved):** pursue Phase 4 with **Base chain only**, prioritizing speed to a working result over multi-chain breadth. Data limitations are accepted and documented, not hidden.
>
> **Definition of Done (realistic, given ~8 durable pairs):** the dataset → training → evaluation → serving pipeline works **end-to-end** on available data, ≥3 models are trained and honestly compared against baselines, and every result is tracked in MLflow. It is NOT "production-quality model" — with the current cohort that is explicitly out of reach and would be dishonest to claim.
>
> This is **planning only**. No implementation code is written here.

---

## 0. Pre-Flight Status

Verified against the committed tree at `HEAD 7e9fac2` (branch `master`, clean, pushed, 0 ahead of origin/master). Gates re-run for this plan on the day of writing:

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | All M1–M10 + Phase 0 gates still pass | ✅ | `make lint` PASS (196 files), `make typecheck` PASS (115 files, 0 issues), `make import-check` PASS (8/8 KEPT), `make test` **331 passed** (2 env-gated skips), `make test-replay` **7 passed** |
| 2 | Phase 0 Step 1 (Feature Expansion) done | ✅ | 3 new PIT-correct features committed (`283c15e`); 5 feature functions total in `analytics/feature_engine.py` |
| 3 | Phase 0 Step 2 (24h window) done | ✅ | `ACTIVE_OBSERVATION_WINDOWS = ("1h", "24h")` + parameterized thresholds committed (`7955cc7`) |
| 4 | Phase 0 Step 3 (cohort) — **partial** | ⚠️ | Tooling committed (`7e9fac2`); see Cohort status subsection |
| 5 | Feature catalogue | ✅ | **5 features**: `liquidity_growth_pct_1h`, `price_momentum_zscore_1h`, `volume_quote_delta_1h`, `honeypot_detected_score`, `liquidity_usd_delta_1h` |
| 6 | Outcome catalogue | ✅ | RUG_PULL / SUCCESSFUL_LAUNCH / DEAD_TOKEN × (1h, 24h), versioned label definitions |
| 7 | ML libraries installed | ❌ | scikit-learn, xgboost, mlflow, numpy NOT yet in `pyproject.toml` — Phase A prerequisite |
| 8 | `ml/` package + import contract | ❌ | package does not exist; new import-linter contract must be added |

### Data Cohort Status (honest — measured at plan time)

- **Durable pairs: ~8** (from the Step 3 session record) vs the **200-pair target (≈4%)**.
- **Critical caveat (must not be ignored):** the shared local TimescaleDB is **truncated by the integration/replay test suite** (`clean_entities`, `_clear_timescale`, replay reset). As measured immediately after a gate run, the DB held **1 pair / 3 facts / 0 features / 3 fixture outcomes**. This means the Step 3 cohort is **not durable in this sandbox** — it persists on a long-lived VM, not here.
- **Output of this reality:** every model-training run in this planning phase must treat the current in-DB cohort as near-empty and rely on **seeded/fixture data for pipeline validation**, exactly as the ML Foundation plan's own tests will.

### Known Limitations (explicit)

1. **Data:** ~8 durable pairs vs 200 target (4%). Only 1 pair is currently in-DB after gate truncation.
2. **Class balance unknown:** the RUG_PULL positive rate has not been measured because the outcome job has not produced new labels from freshly-ingested pairs (all 3 current outcomes are fixture, all `False` for RUG_PULL).
3. **No dedicated server:** Ubuntu on VMware only.
4. **Process limit:** long-lived processes are terminated after ~4.7 minutes, so cohort ingestion is chunked/resumable only.
5. **Model performance cannot be validated honestly:** with this cohort, any AUC-ROC/NDCG number is a pipeline smoke number, not a real capability claim.

---

## 1. Open Decisions — Resolved

| # | Decision | Resolution | Rationale |
|---|----------|-----------|-----------|
| D1 | **ML framework** | **scikit-learn + XGBoost** (tabular, low-dim, interpretable). No deep learning in this phase. | Features are tabular; interpretability matters (DOC-001); PyTorch deferred to a later phase. XGBoost covers boosted-tree strength; sklearn LR/RF/GBM serve as baselines. |
| D2 | **Experiment tracking** | **MLflow, self-hosted, sqlite backend** (`mlruns.db`), with a `NONE` backend toggle so unit tests don't need a server. W&B rejected (SaaS). | Open-source, sklearn/xgboost autologging, native registry. Toggle keeps tests hermetic. |
| D3 | **Model serving** | **FastAPI `POST /v1/models/{model_name}/predict`** + local filesystem registry (`registry.json` naming the `live` model+version). | Reuses `create_app(extra_router=...)`; ML owns its router (never imports `research/`). |
| D4 | **Dataset split** | **Strictly time-based (chronological)**, split on `outcome.evaluation_timestamp`/`feature.as_of_timestamp`; **grouped by `entity_id`** to prevent pair leakage. | Avoids lookahead bias (PIT, DOC-013) and cross-pair leakage. |
| D5 | **Baseline models** | **Mandatory**: random classifier/regressor + rule-based heuristic per task. A model "wins" only if it beats the baseline on held-out test. | Proves ML adds value over simple heuristics; with tiny data the baseline may win — that must be reported, not hidden. |
| D6 | **Feature selection** | **Auto top-K by importance (cap K=20)** with a **minimum-viable-features guard (<3 features → refuse to train and log)**. | We have exactly 5 features; guard prevents silently overfitting a degenerate model. |
| D7 | **Cohort strategy** | **Start with available pairs + fixture data for pipeline validation; continue chunked ingestion incrementally.** | Speed-to-result: validate the pipeline now, grow the cohort in parallel. |
| D8 | **Liquidity-confidence filtering** | Drop rows with `liquidity_usd_confidence < 0.5` before training; use `liquidity_usd_source` as a documented feature-or-metadata column. | `liquidity_usd` is only defensible at high confidence (ARCHITECTURE.md § Liquidity USD). |
| D9 | **`ml/` import contract** | `ml/` MAY import `domain, analytics, persistence, platform, transport`; MAY NOT import `acquisition, processing, domain_management, intelligence, research, strategy`. | See DOC-011 update (§ this plan + the doc change). Must be verifiable via `make import-check` 9/9. |

---

## 2. Build Order (Sequential)

One phase = one commit with green gates. Do NOT proceed past a failing gate. **Phase D is gated on data availability** — if fewer than ~18 durable closed-window pairs exist (below the nominal minimum validation cohort), Phase D reports a pipeline smoke result and stops; it does not fabricate model capability.

### Phase 0 — ML Prerequisites (dependency install)
1. Add dev/prod deps: `scikit-learn`, `xgboost`, `mlflow`, `numpy` to `pyproject.toml` (main or dev group). `uv lock` + `uv sync`.
2. Add `onchain_platform.ml` to `[tool.importlinter]` layers (above `analytics`) + a `forbidden` contract for `ml/`. Add the contract alongside the empty package in Phase A (so `make import-check` stays 8/8 until then or becomes 9/9 only when `ml/` code lands).
3. Install and lock deps; confirm `uv run python -c "import sklearn, xgboost, mlflow, numpy"` succeeds.
   - **Gate 0** green. Commit.

### Phase A — Dataset Infrastructure
1. **`ml/datasets/assembler.py`** — build `(entity_id, as_of, feature_vector[], label)` rows from `persistence` (features + outcomes), `polars`/numpy. Decimal/str→float conversion **only at this boundary** (DOC-012/013). Drop `liquidity_usd_confidence < 0.5` rows. `dataset_quality_report()` set (= counts, null rates, class balance).
2. **`ml/datasets/splits.py`** — time-based train/val/test with `entity_id` gating; time-indexed CV-folds. Nolookahead unit test.
3. **`ml/datasets/normalization.py`** — fit-on-train scalers (standard/min-max), versioned for inference.
4. **Integration tests** — assembler + splits + normalization over seeded/ fixture data. **Use fixture/needed data, not the empty live DB** — this is an explicit accommodation of the current cohort state.
   - **Gate A** green. Commit `feat(ml): dataset assembly infrastructure`.

### Phase B — Experiment Tracking
5. **`ml/tracking/experiment.py`** — thin MLflow wrapper (`start_run`, param/metric/artifact logging), `NONE` backend toggle.
6. **`ml/tracking/metrics.py`** — AUC-ROC, precision/recall; MAE, RMSE, MAPE; NDCG@k, MRR. Pure functions, unit-tested.
7. **`ml/tracking/registry.py`** — local `registry.json` (`run_id`, version, metric, `live` pointer). `promote(version)`, `get_live()`.
   - **Gate B** green. Commit `feat(ml): experiment tracking with MLflow`.

### Phase C — Model Training Pipeline
8. **`ml/models/base.py`** — `BaseModel` protocol: `fit`, `predict`, `predict_proba` (clf), `feature_importances`, `config`.
9. **`ml/models/classifiers.py`** — LogisticRegression, RandomForest, XGBClassifier.
10. **`ml/models/regressors.py`** — Ridge/Lasso, GradientBoostingRegressor, XGBRegressor.
11. **`ml/training/trainer.py`** — MLflow autologging, time-indexed CV, early stopping (XGB), `train_model(model_type, dataset, hyperparams) -> TrainedModel`.
12. **`ml/training/tune.py`** — capped grid/random search (`GridSearchCV`), no unbounded searches.
13. **Integration tests** — train each on seeded/fixture data; artifact + run logged.
    - **Gate C** green. Commit `feat(ml): model training pipeline`.

### Phase D — Initial Models (gated on data)
14. **Pre-flight check:** requires ≥5 features (done — we have 5) AND a validation cohort ≥ ~18 closed-window pairs (likely NOT satisfied). If not satisfied, run on seeded/fixture data and label the result **pipeline smoke**, not capability.
15. **Model 1 — Rug Pull Predictor** (binary; label = `RUG_PULL` outcome, window 1h or 24h). Metric AUC-ROC vs baseline.
16. **Model 2 — Liquidity Forecaster** (regression; target = `liquidity_usd` at T+24h). Metric MAE vs baseline.
17. **Model 3 — Momentum Ranker** (ranking; target = `price_momentum_zscore_1h` ordering). Metric NDCG@10, MRR vs baseline.
18. Model cards created per model. Comparison vs baseline committed as an evaluation report.
    - **Gate D** green. Commit `feat(ml): initial models with evaluation`.

### Phase E — Model Serving
19. **`ml/serving/predictor.py`** — load live model + normalizer; `predict()` returns prediction + version + feature importances.
20. **`ml/serving/api.py`** — `build_ml_router()` with `POST /v1/models/{model_name}/predict`; **own session dependency** (DB via `ml/` → persistence, never import `research/`).
21. **Composition root** — mount into `create_app(extra_router=...)` in `main.py`; B008 ruff allowance for `ml/**`.
22. **Integration tests** — predict real features; registry `live` version; 200/404; latency < 100 ms (in-process).
    - **Gate E** green. Commit `feat(ml): model serving API`.

### Phase F — Documentation & Reports
23. Model cards + experiment log (`docs/ML_MODELS.md`, `docs/ML_DATA_COHORT.md` already created — this commits any updates).
24. Update DOC-005 (Phase 4 Base-only rationale), DOC-009 (ML capability row), DOC-011 (`ml/` package + contract), ARCHITECTURE.md (data flow).
25. `docs/implementation/ImplementationPlan.md` — mark ML Foundation phase + note data-cohort dependency.
26. **Final commit + push** to `origin/master`.

---

## 3. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **Overfitting to ~8 pairs** | High | High | Regularization, early stopping, strict time-CV, minimum-3-features guard, honest reporting. Never claim model quality with 8 pairs. |
| Model worse than baseline | Medium | High | Mandatory baseline comparison; if the heuristic wins, ship the heuristic and document — a valid outcome for Phase 4's pipeline goal. |
| Insufficient positive RUG_PULL class | High | High | Measure class balance first (Phase A quality report). If too imbalanced, treat RUG_PULL as anomaly detection (isolation forest) and document. |
| Feature leakage / lookahead | Medium | High | Strict time-based splits, entity_id gating, fit-on-train scalers, leak regression test. |
| Import-contract break for `ml/` | Low | High | New import-linter contract (D9) + `make import-check` 9/9 at every gate. |
| Cohort not durable (test truncation) | High | Medium | Train on seeded/fixture data in CI; treat live cohort as a VM-only asset. |

## 4. Success Criteria Matrix

| Criterion | Realistic target | How verified | Gate |
|-----------|------------------|--------------|------|
| Dataset assembler produces train/val/test | Produced from available+fixture data | Integration test | A |
| ≥3 models trained | 3 (rug, liquidity, momentum) | Run records | D |
| All models compared against baselines | Every model has a baseline column | Eval report | D |
| Serving API latency | < 100 ms | Integration/benchmark | E|
| All experiments tracked in MLflow | Every run | registry | B–D |
| ✅ Honest caveat | With ~8 pairs, success = **pipeline works end-to-end**, NOT production-quality model | Documentation | all |

## 5. Out-of-Scope Confirmation

Explicitly **NOT** in this phase (reflected in the import/serving boundaries):
- Deep learning (CNN/transformer/GNN — later)
- Multi-chain models (Base only)
- Real-time / online model updates
- Autonomous trading / portfolio
- Production monitoring / alerting / drift
- A/B testing framework beyond a registry `live` pointer

## 6. Questions / Blockers
1. **Durable cohort (BLOCKER for real-model quality).** The sandbox DB is truncated by tests; ~8 pairs were ingested but are not durable here. Decision needed: (a) run ingestion on a long-lived VM, or (b) proceed with fixture/seeded validation only (recommended for speed, with an honest "pipeline smoke" label).
2. **Class balance unknown.** Need to run the outcome job over ingested pairs on a durable DB to measure RUG_PULL positive rate before setting a training threshold / anomaly-detection decision.
3. **`ml/` contract placement.** DOC-011 guiding principle says new top-level packages belong in DOC-009/DOC-010 first. This plan adds the doc updates in Phase A + F — confirm that's acceptable, or if a separate ADR is wanted.
4. **RUG_PULL learning target ambiguity.** With a single (1h) vs (24h) window, which is the label? Recommend training on whichever window has a measurable positive class; document the choice per run.