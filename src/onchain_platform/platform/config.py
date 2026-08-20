"""Settings — Pydantic Settings loading from a git-ignored local .env
(DOC-010 § Security).

Never raw os.environ access in Capability code; and no Capability module may
import a configured Settings instance as a global — main.py constructs ONE
Settings instance at startup and threads it through everything else
(DOC-013 § Dependency & Composition).
"""

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # RPC endpoint for the Base collector. Any JSON-RPC endpoint works —
    # the BlockchainProvider interface (ADR-006 § Provider Abstraction) is
    # what makes this choice cost nothing to change later.
    rpc_url: str = "https://mainnet.base.org"

    # PostgreSQL (TimescaleDB) DSN — must match docker-compose.yml.
    postgres_dsn: str = "postgresql+asyncpg://onchain@localhost:5433/onchain_platform"

    # Redis (event transport / state cache) — not wired until later
    # milestones; kept here so .env.example stays complete.
    redis_url: str = "redis://localhost:6379/0"

    # Milestone 1 collector configuration (ImplementationPlan § Milestone 1:
    # one fact type, one chain, one factory). The factory is the live-
    # verified Uniswap V2 factory on Base (Milestone1-ExecutionPlan § Open
    # Decisions; dex attribution confirmed per planning Q1).
    chain_id: int = 8453
    factory_address: str = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
    dex: str = "uniswap_v2"
    poll_interval_seconds: float = 2.0  # Base: ~2s blocks (DOC-006)

    # Milestone 2: confirmation depth configuration (ADR-006 § Configurable
    # Confirmation Depth: "The platform must not hardcode confirmation rules").
    confirmation_depth_path: Path = Path("config/confirmation_depth.yaml")

    def load_confirmation_depths(self) -> dict[int, int]:
        """Load per-chain confirmation depths from YAML.

        Returns {chain_id: depth}. ADR-006 § Configurable Confirmation
        Depth: 'Future chains may define different thresholds without
        changing application logic.'
        """
        raw = yaml.safe_load(self.confirmation_depth_path.read_text())
        # YAML keys are strings; convert to int chain_ids.
        return {int(k): int(v) for k, v in raw["confirmation_depth"].items()}
