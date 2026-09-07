# ML Models — Phase 4

## Status: REORIENTED (2026-09-06)

Original plan had Rug Pull Predictor as Model 1.
Deep data audit revealed ZERO positive RUG_PULL samples in current cohort.
Plan revised to focus on models with sufficient data.

## Model 1: Successful Launch Predictor ✅ ACTIVE

- **Status**: Ready for training
- **Target**: AUC-ROC ≥ 0.70
- **Algorithm**: XGBoost (scikit-learn wrapper)
- **Target Variable**: outcome_type='SUCCESSFUL_LAUNCH', window='24h', label_value
- **Positive Samples**: ~38 (37%)
- **Negative Samples**: ~62 (63%)
- **Features**:
  - liquidity_growth_pct_1h (requires liquidity_usd)
  - price_momentum_zscore_1h
  - volume_quote_delta_1h
- **Dataset**: 100 pairs with outcomes
- **Split**: Time-based (train: first 70%, test: last 30%)
- **Preprocessing**: RobustScaler, log1p for volume, median imputation
- **Explainability**: SHAP values + feature importance

## Model 2: Dead Token Predictor ✅ ACTIVE

- **Status**: Ready for training
- **Target**: AUC-ROC ≥ 0.70
- **Algorithm**: XGBoost
- **Target Variable**: outcome_type='DEAD_TOKEN', window='24h', label_value
- **Positive Samples**: ~52 (52%)
- **Negative Samples**: ~48 (48%)
- **Features**: Same as Model 1
- **Dataset**: 100 pairs with outcomes
- **Split**: Time-based
- **Note**: Best class balance of all three models

## Model 3: Rug Pull Predictor 🔴 DEFERRED

- **Status**: BLOCKED — Zero positive samples
- **Original Target**: AUC-ROC ≥ 0.75
- **Root Cause**: 11-hour random window (blocks 50.4M-50.55M) had no rug events
- **Unblocked By**:
  1. Expanding block range to 500K-1M blocks
  2. OR finding a period with known rug activity
- **Estimated Data Need**: ≥100 confirmed rug events
- **Workaround**: NONE — do not fabricate labels
- **Interim Alternative**: Anomaly detection on reserve drops (future work)

## Model 4: Liquidity Forecaster ⚠️ AT RISK

- **Status**: LIMITED DATA
- **Target**: MAE ≤ 20%
- **Constraint**: Only ~47 pairs have liquidity_usd (74% lack it)
- **Recommendation**: DEFER until feature expansion provides alternatives
- **Alternative**: Predict liquidity_usd from reserve0/reserve1 + token metadata

## Data Quality Notes

- Zero-leakage dataset builder: analytics/dataset_builder.py
- Volume normalization: divided by quote token decimals (fixed in 0bc893b)
- Oracle: MultiPriceOracle with StaticEthPriceProvider for WETH
  (src/onchain_platform/intelligence/oracles.py)
- Exotic pairs: 74% lack liquidity_usd — documented limitation