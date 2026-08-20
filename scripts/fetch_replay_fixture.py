"""Fetch a fixed historical block range and freeze it as a replay fixture.

DEV TOOLING ONLY — never part of a production processing pipeline (DOC-011
§ Supporting Directories: scripts/ is dev/ops tooling only; DOC-010: Bash /
scripts never part of a production pipeline).

The fixture stores RAW provider shapes (block headers + logs), not
normalized facts — the replay test must push them through the live pipeline
(normalizer + fact processor) so a regression in the transformation is what
gets caught (ADR-006 Principle 2, DOC-010 § Replay Tests).

observed_at / ingested_at are PINNED constants in the fixture: replay
determinism requires them fixed, because the live pipeline stamps them from
an injected clock (ADR-006 Principle 2: identical inputs → identical
outputs; Milestone1-ExecutionPlan § Build Order step 23).

Usage:
    uv run python scripts/fetch_replay_fixture.py [--rpc-url URL]
"""

import argparse
import asyncio
import json
from datetime import UTC
from pathlib import Path

from onchain_platform.acquisition.providers.local_node import LocalNodeProvider

CHAIN_ID = 8453
FROM_BLOCK = 13_500_000
TO_BLOCK = 13_500_024
FACTORY_ADDRESS = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
DEFAULT_RPC_URL = "https://mainnet.base.org"

# Pinned clock values for deterministic replay (see module docstring).
PINNED_OBSERVED_AT = "2026-01-01T00:00:00Z"
PINNED_INGESTED_AT = "2026-01-01T00:00:00Z"

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "replay"
    / "fixtures"
    / f"base_pair_created_{FROM_BLOCK}_{TO_BLOCK}.json"
)


async def fetch(rpc_url: str) -> dict[str, object]:
    provider = LocalNodeProvider(rpc_url)
    try:
        chain_id = await provider.get_chain_id()
        if chain_id != CHAIN_ID:
            raise SystemExit(f"RPC endpoint serves chain {chain_id}, expected {CHAIN_ID}")

        blocks: dict[str, dict[str, str]] = {}
        for block_number in range(FROM_BLOCK, TO_BLOCK + 1):
            meta = await provider.get_block_metadata(block_number)
            blocks[str(block_number)] = {
                "number": str(meta.number),
                "hash": meta.hash,
                "parentHash": meta.parent_hash,
                # Canonical serialization: ISO-8601 Z-suffix (DOC-012 §
                # Conventions).
                "timestamp": meta.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }

        logs = await provider.get_logs(
            from_block=FROM_BLOCK,
            to_block=TO_BLOCK,
            address=FACTORY_ADDRESS,
            topics=[PAIR_CREATED_TOPIC],
        )
        return {
            "fixture_version": "1.0",
            "chain_id": CHAIN_ID,
            "from_block": FROM_BLOCK,
            "to_block": TO_BLOCK,
            "factory_address": FACTORY_ADDRESS,
            "event_topic": PAIR_CREATED_TOPIC,
            "observed_at": PINNED_OBSERVED_AT,
            "ingested_at": PINNED_INGESTED_AT,
            "blocks": blocks,
            "logs": [log.model_dump() for log in logs],
        }
    finally:
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=DEFAULT_RPC_URL)
    args = parser.parse_args()

    fixture = asyncio.run(fetch(args.rpc_url))
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    log_count = len(fixture["logs"])  # type: ignore[arg-type]
    print(f"wrote {FIXTURE_PATH} ({log_count} logs, blocks {FROM_BLOCK}..{TO_BLOCK})")
    if log_count == 0:
        raise SystemExit("fixture contains no logs — range selection is wrong")


if __name__ == "__main__":
    main()
