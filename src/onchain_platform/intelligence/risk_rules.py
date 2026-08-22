"""Risk Rules Engine — deterministic rule-based scoring (Milestone 7).

DOC-009: "This capability enriches research. It never executes trades."
DOC-013 § Determinism Discipline: no wall-clock, no set iteration, no
unseeded randomness. Same inputs → same outputs, always.

All weights are hardcoded and versioned (RISK_RULES_VERSION) for
reproducibility. Weights are MVP baseline — iterate in future milestones.
"""

from onchain_platform.domain.schemas.risk_signals import RiskSignals

# Versioned for reproducibility — historical scores remain explainable.
RISK_RULES_VERSION = "1.0"

# Risk indicator weights (0.0–1.0). Sum capped at 1.0.
# Honeypot is an auto-fail (score=1.0 immediately).
_WEIGHTS: dict[str, float] = {
    "hidden_owner": 0.30,
    "owner_change_balance": 0.25,
    "is_mintable": 0.15,
    "sell_tax_high": 0.20,  # sell_tax > 0.2
    "buy_tax_high": 0.15,  # buy_tax > 0.2
    "is_proxy": 0.10,
    "selfdestruct": 0.10,
    "external_call": 0.05,
    "transfer_pausable": 0.10,
    "is_blacklisted": 0.60,
    "is_airdrop_scam": 0.85,
    "fake_token": 0.90,
    "cannot_buy": 0.70,
    "cannot_sell_all": 0.70,
    "slippage_modifiable": 0.15,
}


def extract_risk_signals(goplus_response: dict[str, object]) -> RiskSignals:
    """Parse GoPlus JSON response into RiskSignals.

    All GoPlus fields stored as str|None (GoPlus returns strings).
    Computed fields: risk_score, risk_indicators, risk_rules_version.
    """

    def _str(key: str) -> str | None:
        val = goplus_response.get(key)
        if val is None:
            return None
        return str(val)

    # Compute risk score and indicators.
    indicators = _identify_risk_indicators_from_raw(goplus_response)
    score = _compute_score_from_indicators(indicators)

    return RiskSignals(
        is_open_source=_str("is_open_source"),
        is_proxy=_str("is_proxy"),
        is_mintable=_str("is_mintable"),
        owner_address=_str("owner_address"),
        can_take_back_ownership=_str("can_take_back_ownership"),
        owner_change_balance=_str("owner_change_balance"),
        hidden_owner=_str("hidden_owner"),
        selfdestruct=_str("selfdestruct"),
        external_call=_str("external_call"),
        is_in_dex=_str("is_in_dex"),
        buy_tax=_str("buy_tax"),
        sell_tax=_str("sell_tax"),
        transfer_tax=_str("transfer_tax"),
        is_honeypot=_str("is_honeypot"),
        cannot_buy=_str("cannot_buy"),
        cannot_sell_all=_str("cannot_sell_all"),
        transfer_pausable=_str("transfer_pausable"),
        is_blacklisted=_str("is_blacklisted"),
        is_whitelisted=_str("is_whitelisted"),
        slippage_modifiable=_str("slippage_modifiable"),
        trading_cooldown=_str("trading_cooldown"),
        holder_count=_str("holder_count"),
        total_supply=_str("total_supply"),
        creator_address=_str("creator_address"),
        creator_percent=_str("creator_percent"),
        owner_percent=_str("owner_percent"),
        is_airdrop_scam=_str("is_airdrop_scam"),
        trust_list=_str("trust_list"),
        fake_token=_str("fake_token"),
        other_potential_risks=_str("other_potential_risks"),
        risk_score=score,
        risk_indicators=indicators,
        risk_rules_version=RISK_RULES_VERSION,
    )


def compute_risk_score(signals: RiskSignals) -> float:
    """Deterministic weighted scoring (0.0–1.0).

    Honeypot is an auto-fail (score=1.0 immediately).
    All weights hardcoded, versioned for reproducibility.
    """
    if signals.is_honeypot == "1":
        return 1.0

    indicators = _identify_risk_indicators_from_signals(signals)
    return _compute_score_from_indicators(indicators)


def identify_risk_indicators(signals: RiskSignals) -> list[str]:
    """Human-readable risk indicator list."""
    return _identify_risk_indicators_from_signals(signals)


def _is_truthy(val: object) -> bool:
    """GoPlus uses '1' for true, '0' for false."""
    return val == "1"


def _parse_tax(val: object) -> float:
    """Parse GoPlus tax string (0.0–1.0) to float."""
    if val is None:
        return 0.0
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return 0.0


def _identify_risk_indicators_from_raw(
    goplus_response: dict[str, object],
) -> list[str]:
    """Identify risk indicators from raw GoPlus response dict."""
    indicators: list[str] = []

    if _is_truthy(goplus_response.get("is_honeypot")):
        indicators.append("Honeypot Detected")
    if _is_truthy(goplus_response.get("hidden_owner")):
        indicators.append("Hidden Owner")
    if _is_truthy(goplus_response.get("owner_change_balance")):
        indicators.append("Owner Can Change Balance")
    if _is_truthy(goplus_response.get("is_mintable")):
        indicators.append("Mintable Token")
    if _is_truthy(goplus_response.get("is_proxy")):
        indicators.append("Upgradeable Proxy")
    if _is_truthy(goplus_response.get("selfdestruct")):
        indicators.append("Self-Destruct Function")
    if _is_truthy(goplus_response.get("external_call")):
        indicators.append("External Call Risk")
    if _is_truthy(goplus_response.get("transfer_pausable")):
        indicators.append("Transfer Pausable")
    if _is_truthy(goplus_response.get("is_blacklisted")):
        indicators.append("Blacklist Function")
    if _is_truthy(goplus_response.get("is_airdrop_scam")):
        indicators.append("Airdrop Scam")
    if _is_truthy(goplus_response.get("fake_token")):
        indicators.append("Fake Token")
    if _is_truthy(goplus_response.get("cannot_buy")):
        indicators.append("Cannot Buy")
    if _is_truthy(goplus_response.get("cannot_sell_all")):
        indicators.append("Cannot Sell All")
    if _is_truthy(goplus_response.get("slippage_modifiable")):
        indicators.append("Slippage Modifiable")

    sell_tax = _parse_tax(goplus_response.get("sell_tax"))
    if sell_tax > 0.2:
        indicators.append(f"High Sell Tax ({sell_tax:.0%})")

    buy_tax = _parse_tax(goplus_response.get("buy_tax"))
    if buy_tax > 0.2:
        indicators.append(f"High Buy Tax ({buy_tax:.0%})")

    return indicators


def _identify_risk_indicators_from_signals(signals: RiskSignals) -> list[str]:
    """Identify risk indicators from RiskSignals."""
    indicators: list[str] = []

    if _is_truthy(signals.is_honeypot):
        indicators.append("Honeypot Detected")
    if _is_truthy(signals.hidden_owner):
        indicators.append("Hidden Owner")
    if _is_truthy(signals.owner_change_balance):
        indicators.append("Owner Can Change Balance")
    if _is_truthy(signals.is_mintable):
        indicators.append("Mintable Token")
    if _is_truthy(signals.is_proxy):
        indicators.append("Upgradeable Proxy")
    if _is_truthy(signals.selfdestruct):
        indicators.append("Self-Destruct Function")
    if _is_truthy(signals.external_call):
        indicators.append("External Call Risk")
    if _is_truthy(signals.transfer_pausable):
        indicators.append("Transfer Pausable")
    if _is_truthy(signals.is_blacklisted):
        indicators.append("Blacklist Function")
    if _is_truthy(signals.is_airdrop_scam):
        indicators.append("Airdrop Scam")
    if _is_truthy(signals.fake_token):
        indicators.append("Fake Token")
    if _is_truthy(signals.cannot_buy):
        indicators.append("Cannot Buy")
    if _is_truthy(signals.cannot_sell_all):
        indicators.append("Cannot Sell All")
    if _is_truthy(signals.slippage_modifiable):
        indicators.append("Slippage Modifiable")

    sell_tax = _parse_tax(signals.sell_tax)
    if sell_tax > 0.2:
        indicators.append(f"High Sell Tax ({sell_tax:.0%})")

    buy_tax = _parse_tax(signals.buy_tax)
    if buy_tax > 0.2:
        indicators.append(f"High Buy Tax ({buy_tax:.0%})")

    return indicators


def _compute_score_from_indicators(indicators: list[str]) -> float:
    """Compute risk score from indicator list.

    Deterministic: same indicators → same score. No wall-clock, no
    randomness (DOC-013 § Determinism Discipline).
    """
    if "Honeypot Detected" in indicators:
        return 1.0

    score = 0.0
    indicator_to_weight = {
        "Hidden Owner": "hidden_owner",
        "Owner Can Change Balance": "owner_change_balance",
        "Mintable Token": "is_mintable",
        "Upgradeable Proxy": "is_proxy",
        "Self-Destruct Function": "selfdestruct",
        "External Call Risk": "external_call",
        "Transfer Pausable": "transfer_pausable",
        "Blacklist Function": "is_blacklisted",
        "Airdrop Scam": "is_airdrop_scam",
        "Fake Token": "fake_token",
        "Cannot Buy": "cannot_buy",
        "Cannot Sell All": "cannot_sell_all",
        "Slippage Modifiable": "slippage_modifiable",
    }

    for indicator in indicators:
        if indicator.startswith("High Sell Tax"):
            score += _WEIGHTS["sell_tax_high"]
        elif indicator.startswith("High Buy Tax"):
            score += _WEIGHTS["buy_tax_high"]
        else:
            weight_key = indicator_to_weight.get(indicator)
            if weight_key is not None:
                score += _WEIGHTS[weight_key]

    return min(score, 1.0)
