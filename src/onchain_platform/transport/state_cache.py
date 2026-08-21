"""Redis-backed StateProjection store (DOC-012 § B.2, DOC-011 § transport/).

StateProjection is "the live, mutable, continuously-recomputed read model.
Never persisted as its own historical table — served from Redis cache
(DOC-010) and can always be rebuilt by replaying Facts" (DOC-012 § B.2).

Serialization: JSON via Pydantic .model_dump_json() with schema_version.
Key format: state:{chain_id}:{pool_address} (human-readable, debuggable).
TTL: none (state is rebuilt from facts on restart, but keeping it in Redis
avoids cold-start latency).

All Redis errors → TransportError (DOC-013 § Exception Hierarchy).
"""

import redis.asyncio as redis

from onchain_platform.domain.exceptions import TransportError
from onchain_platform.domain.schemas.state_projection import StateProjection


def _state_key(chain_id: int, pool_address: str) -> str:
    """Redis key for a pool's StateProjection."""
    return f"state:{chain_id}:{pool_address.lower()}"


async def save_state(r: redis.Redis, projection: StateProjection) -> None:
    """Store a StateProjection in Redis. Overwrites any existing value."""
    key = _state_key(projection.chain_id, projection.entity_id.split(":")[-1])
    try:
        await r.set(key, projection.model_dump_json())
    except redis.RedisError as exc:
        raise TransportError(f"failed to save StateProjection to Redis: {exc}") from exc


async def load_state(r: redis.Redis, chain_id: int, pool_address: str) -> StateProjection | None:
    """Load a StateProjection from Redis. Returns None if key doesn't exist."""
    key = _state_key(chain_id, pool_address)
    try:
        data = await r.get(key)
    except redis.RedisError as exc:
        raise TransportError(f"failed to load StateProjection from Redis: {exc}") from exc
    if data is None:
        return None
    return StateProjection.model_validate_json(data)


async def delete_state(r: redis.Redis, chain_id: int, pool_address: str) -> None:
    """Remove a StateProjection from Redis (used on reorg)."""
    key = _state_key(chain_id, pool_address)
    try:
        await r.delete(key)
    except redis.RedisError as exc:
        raise TransportError(f"failed to delete StateProjection from Redis: {exc}") from exc


async def list_state_keys(r: redis.Redis) -> list[str]:
    """List all state:* keys in Redis (used by snapshot scheduler)."""
    try:
        keys = []
        async for key in r.scan_iter(match="state:*"):
            keys.append(key.decode() if isinstance(key, bytes) else key)
        return keys
    except redis.RedisError as exc:
        raise TransportError(f"failed to list StateProjection keys: {exc}") from exc
