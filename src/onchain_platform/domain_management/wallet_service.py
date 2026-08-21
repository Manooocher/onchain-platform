"""Wallet Service (DOC-011 § domain_management/, DOC-006 § Ownership table).

DOC-006 Ownership table: "Wallet Service" is distinct from "Entity
Resolution." In M4, the distinction is organizational — both do upserts,
but wallet_service owns the Wallet entity lifecycle.

Minimal scope: create Wallet entities when first seen in a fact. No
behavior analysis, no tagging, no Feature Engineering (deferred to M6).
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.wallet import Wallet
from onchain_platform.domain.ids import wallet_canonical_id
from onchain_platform.persistence.postgres.entity_repositories import save_wallet


async def ensure_wallet(
    session: AsyncSession,
    *,
    chain_id: int,
    address: str,
    first_seen_at: datetime,
) -> Wallet:
    """Create or update a Wallet entity.

    first_seen_at is updated only if the new value is earlier (idempotent
    replay — ADR-006 § Idempotency). Returns the Wallet domain object.
    """
    wallet = Wallet(
        canonical_id=wallet_canonical_id(chain_id, address),
        chain_id=chain_id,
        address=address,
        first_seen_at=first_seen_at,
    )
    await save_wallet(session, wallet)
    return wallet
