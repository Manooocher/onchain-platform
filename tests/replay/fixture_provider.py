"""FixtureProvider — replays a committed JSON fixture through the REAL
BlockchainProvider interface.

Replay is the primary regression test for the platform's central guarantee:
it re-processes a fixed, known slice of historical blockchain data through
the LIVE pipeline (DOC-010 § Replay Tests, ADR-006 § Single Processing
Path — historical and live data share exactly one implementation).

The fixture stores raw provider shapes (headers + logs); this provider
serves them as RawLog/BlockMetadata — the same canonical primitives a live
provider emits — so nothing downstream knows or cares that the source is a
file (ADR-006 § Replay: "The only difference is the event source").

observed_at is pinned by the fixture (ADR-006 Principle 2: deterministic
inputs); it is consumed by the collector's injected clock, never read here.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from onchain_platform.acquisition.providers.base import (
    BlockchainProvider,
    BlockMetadata,
    RawLog,
)
from onchain_platform.domain.exceptions import AcquisitionError

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _parse_utc(value: str) -> datetime:
    # Fixtures store ISO-8601 with a Z suffix (DOC-012 § Conventions).
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class FixtureProvider(BlockchainProvider):
    """BlockchainProvider backed by a committed fixture JSON file."""

    def __init__(self, fixture_path: Path) -> None:
        raw = json.loads(fixture_path.read_text())
        self.chain_id: int = int(raw["chain_id"])
        self.observed_at: datetime = _parse_utc(raw["observed_at"])
        self.ingested_at: datetime = _parse_utc(raw["ingested_at"])
        self.factory_address: str = raw["factory_address"]
        self.event_topic: str = raw["event_topic"]
        self.from_block: int = int(raw["from_block"])
        self.to_block: int = int(raw["to_block"])
        self._blocks: dict[int, BlockMetadata] = {
            int(number): BlockMetadata(
                number=int(meta["number"]),
                hash=meta["hash"],
                parent_hash=meta["parentHash"],
                timestamp=_parse_utc(meta["timestamp"]),
            )
            for number, meta in raw["blocks"].items()
        }
        self._logs: list[RawLog] = [RawLog.model_validate(entry) for entry in raw["logs"]]
        # Swap logs (Milestone 3 extension).
        self._swap_logs: list[RawLog] = [
            RawLog.model_validate(entry) for entry in raw.get("swap_logs", [])
        ]

    async def get_chain_id(self) -> int:
        return self.chain_id

    async def get_chain_head(self) -> int:
        return self.to_block

    async def get_block_metadata(self, block_number: int) -> BlockMetadata:
        try:
            return self._blocks[block_number]
        except KeyError as exc:
            raise AcquisitionError(f"fixture has no block {block_number}") from exc

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[RawLog]:
        topic0 = topics[0] if topics else None
        # Search both PairCreated logs and Swap logs.
        all_logs = self._logs + self._swap_logs
        result = [
            log
            for log in all_logs
            if from_block <= log.block_number <= to_block
            and (address is None or log.address == address)
            and (topic0 is None or (log.topics and log.topics[0] == topic0))
        ]
        # Canonical order regardless of storage order (base.py contract).
        result.sort(key=lambda log: (log.block_number, log.log_index))
        return result

    async def close(self) -> None:
        return None
