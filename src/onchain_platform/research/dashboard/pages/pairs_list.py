"""Page 1 — Pairs List.

Lists trading pairs with chain_id/dex filters and cursor pagination. Reads
only through the API client (DOC-015 § Dashboard).
"""

from typing import Any

import streamlit as st

from onchain_platform.research.dashboard.api_client import OnchainPlatformClient


def render(client: OnchainPlatformClient) -> None:
    st.subheader("Pairs List")

    col1, col2, col3 = st.columns(3)
    chain_id = col1.number_input("Chain ID", value=8453, step=1)
    dex = col2.text_input("DEX", value="uniswap_v2")
    limit = col3.number_input("Per page", value=100, min_value=1, max_value=1000, step=1)

    record_key = "dashboard_pairs_cursor"

    def _fetch() -> dict[str, Any]:
        return client.get_pairs(
            chain_id=int(chain_id) if chain_id else None,
            dex=dex or None,
            limit=int(limit),
            cursor=st.session_state.get(record_key),
        )

    if st.button("Load pairs", type="primary") or "dashboard_pairs_data" not in st.session_state:
        data = _fetch()
        st.session_state["dashboard_pairs_data"] = data

    data = st.session_state["dashboard_pairs_data"]
    items = data.get("items", [])
    pagination = data.get("pagination", {})
    st.session_state[record_key] = pagination.get("next_cursor")

    st.write(f"{len(items)} pairs returned. has_more={pagination.get('has_more', False)}")

    if items:
        st.dataframe(
            [
                {
                    "canonical_id": p["canonical_id"],
                    "chain_id": p["chain_id"],
                    "dex": p["dex"],
                    "pool_address": p["pool_address"],
                    "creation_block": p["creation_block"],
                }
                for p in items
            ]
        )
    else:
        st.caption("No pairs match the filters.")
