"""Metadata Service — stub implementation (DOC-011 § domain_management/).

ImplementationPlan § Open Decisions: "Metadata enrichment can start as a
stub (empty/UNVERIFIED for every token) — real provider integration is a
Day-1-of-this-milestone decision, not a blocker to starting it."

This stub creates Metadata rows with verification_status=UNVERIFIED and
empty website/social_links/logo_url/description. Real provider integration
is deferred to a future milestone.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.entities.metadata import Metadata
from onchain_platform.domain.enums import VerificationStatus
from onchain_platform.persistence.postgres.entity_repositories import save_metadata


async def create_stub_metadata(session: AsyncSession, entity_id: str) -> None:
    """Create a stub Metadata row for an entity.

    verification_status=UNVERIFIED, all enrichment fields empty.
    Idempotent: ON CONFLICT DO UPDATE on entity_id (the metadata may
    already exist from a previous replay).
    """
    metadata = Metadata(
        entity_id=entity_id,
        verification_status=VerificationStatus.UNVERIFIED,
        last_updated=datetime.now(UTC),
    )
    await save_metadata(session, metadata)
