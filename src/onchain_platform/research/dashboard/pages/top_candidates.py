"""Page 4 — Top Candidates.

Shows the deterministic research ranking (DOC-009 Strategy): a table of
candidates with score and explainable factor contributions. Reads only
through the API client (DOC-015 § Dashboard).
"""

import streamlit as st

from onchain_platform.research.dashboard.api_client import OnchainPlatformClient


def render(client: OnchainPlatformClient) -> None:
    st.subheader("Top Candidates")
    st.caption(
        "Deterministic research ranking from Features + risk/outcome signals "
        "(DOC-009 Strategy). Strategy recommends, it does not act."
    )

    col1, col2, col3 = st.columns(3)
    chain_id = col1.number_input("Chain ID", value=8453, step=1)
    dex = col2.text_input("DEX (optional)", value="")
    limit = col3.number_input("Limit", value=50, min_value=1, max_value=100, step=1)

    if st.button("Load rankings", type="primary"):
        try:
            data = client.get_rankings(
                chain_id=int(chain_id) if chain_id else None,
                dex=dex or None,
                limit=int(limit),
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Rankings error: {exc}")
            return

        items = data if isinstance(data, list) else data.get("items", [])
        if not items:
            st.caption("No candidates meet the minimum feature threshold yet.")
            return

        rows = []
        for item in items:
            factors = ", ".join(
                f"{f['name']}={f['contribution']:.3f}" for f in item.get("factors", [])
            )
            rows.append(
                {
                    "rank": item.get("rank"),
                    "score": item.get("score"),
                    "pair_id": item.get("pair_id"),
                    "factors": factors,
                }
            )
        st.dataframe(rows)
        st.json(items[:5])  # show the explainable detail for the top five
