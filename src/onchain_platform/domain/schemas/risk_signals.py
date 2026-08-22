"""RiskSignals schema — extracted from GoPlus Security API response.

All GoPlus fields are stored as `str | None` (GoPlus returns strings).
Computed fields: risk_score (float, 0.0–1.0), risk_indicators (list[str]),
risk_rules_version (str, versioned for reproducibility).

Frozen per DOC-013 § Immutability & State Modeling.
"""

from pydantic import BaseModel, ConfigDict, Field


class RiskSignals(BaseModel):
    """Contract security + trading security + info security signals
    extracted from GoPlus Token Security API, plus computed risk score."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"

    # --- Contract security (all str — GoPlus returns strings) ---
    is_open_source: str | None = None
    is_proxy: str | None = None
    is_mintable: str | None = None
    owner_address: str | None = None
    can_take_back_ownership: str | None = None
    owner_change_balance: str | None = None
    hidden_owner: str | None = None
    selfdestruct: str | None = None
    external_call: str | None = None

    # --- Trading security ---
    is_in_dex: str | None = None
    buy_tax: str | None = None
    sell_tax: str | None = None
    transfer_tax: str | None = None
    is_honeypot: str | None = None
    cannot_buy: str | None = None
    cannot_sell_all: str | None = None
    transfer_pausable: str | None = None
    is_blacklisted: str | None = None
    is_whitelisted: str | None = None
    slippage_modifiable: str | None = None
    trading_cooldown: str | None = None

    # --- Info security ---
    holder_count: str | None = None
    total_supply: str | None = None
    creator_address: str | None = None
    creator_percent: str | None = None
    owner_percent: str | None = None
    is_airdrop_scam: str | None = None
    trust_list: str | None = None
    fake_token: str | None = None
    other_potential_risks: str | None = None

    # --- Computed ---
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_indicators: list[str] = Field(default_factory=list)
    risk_rules_version: str = "1.0"
