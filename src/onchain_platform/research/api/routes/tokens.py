"""Token endpoints (DOC-015 Resource Model).

`/v1/tokens/{id}` — single Token with nested SmartContract + Metadata.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from onchain_platform.domain.ids import smart_contract_canonical_id
from onchain_platform.persistence.postgres import entity_repositories as repo
from onchain_platform.research.api.deps import get_session
from onchain_platform.research.api.schemas import TokenDetail

router = APIRouter()


@router.get(
    "/tokens/{token_id:path}",
    summary="Get a token",
    description="Single Token with its nested SmartContract and Metadata "
    "(DOC-015 Resource Model). 404 if the token does not exist. The canonical "
    "ID may contain '/' — hence the :path converter.",
    response_model=TokenDetail,
)
async def get_token(
    token_id: str,
    session: AsyncSession = Depends(get_session),
) -> TokenDetail:
    canonical = unquote(token_id)
    token = await repo.get_token(session, canonical)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    # The SmartContract is the token's own contract at <chain>/contract:<addr>,
    # distinct from the token canonical ID (eip155:<chain>/token:<addr>).
    contract_id = smart_contract_canonical_id(token.chain_id, token.contract_address)
    smart_contract = await repo.get_smart_contract(session, contract_id)
    metadata = await repo.get_metadata(session, canonical)
    return TokenDetail(token=token, smart_contract=smart_contract, metadata=metadata)
