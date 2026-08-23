"""Page 2 — Pair Detail.

Shows a single pair's metadata, its Market Bars (line chart), and its latest
features. Reads only through the API client (DOC-015 § Dashboard).
"""

import streamlit as st

from onchain_platform.research.dashboard.api_client import OnchainPlatformClient


def render(client: OnchainPlatformClient) -> None:
    st.subheader("Pair Detail")

    pair_id = st.text_input("Pair canonical ID", value="eip155:8453/pair:0x")

    if not pair_id.startswith("eip155:"):
        st.info("Enter an eip155: canonical pair ID to inspect.")
        return

    try:
        detail = client.get_pair(pair_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Pair not found / API error: {exc}")
        return

    st.json(detail.get("pair", {}))
    if detail.get("liquidity_pool"):
        st.caption(f"LiquidityPool: {detail['liquidity_pool']}")
    if detail.get("metadata"):
        st.caption(f"Metadata: {detail['metadata']}")

    st.divider()
    st.markdown("#### Market Bars (1h)")
    col1, col2 = st.columns(2)
    start = col1.text_input("start", value="2026-08-01T00:00:00Z")
    end = col2.text_input("end", value="2026-08-31T00:00:00Z")

    if st.button("Load bars"):
        try:
            bars_data = client.get_bars(pair_id, "1h", start, end)
            items = bars_data.get("items", [])
            if items:
                chart_data = [
                    {"bar_start_time": b["bar_start_time"], "close": float(b["close"])}
                    for b in items
                ]
                st.line_chart(chart_data, x="bar_start_time", y="close")
                st.dataframe(items)
            else:
                st.caption("No bars in this range.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Bars error: {exc}")

    st.divider()
    st.markdown("#### Latest Features")
    if st.button("Load features"):
        try:
            feats = client.get_features(pair_id)
            st.dataframe(
                [
                    {
                        "feature_name": f["feature_name"],
                        "value": f["value"],
                        "as_of_timestamp": f["as_of_timestamp"],
                    }
                    for f in feats.get("items", [])
                ]
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Features error: {exc}")
