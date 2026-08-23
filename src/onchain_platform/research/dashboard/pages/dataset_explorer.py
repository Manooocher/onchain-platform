"""Page 3 — Dataset Explorer.

Fetch the assembled research dataset (pair + bars + features + outcomes) for
a pair and display it as tables + a raw JSON download (Q4 resolution: raw
JSON now; Polars pivot deferred to Phase 7+). Reads only through the API
client (DOC-015 § Dashboard).
"""

import json

import streamlit as st

from onchain_platform.research.dashboard.api_client import OnchainPlatformClient


def render(client: OnchainPlatformClient) -> None:
    st.subheader("Dataset Explorer")

    pair_id = st.text_input("Pair canonical ID", value="eip155:8453/pair:0x", key="ds_pair")
    col1, col2, col3 = st.columns(3)
    interval = col1.selectbox("Interval", ["1m", "5m", "15m", "1h"], index=3)
    start = col2.text_input("start (UTC)", value="2026-08-01T00:00:00Z")
    end = col3.text_input("end (UTC)", value="2026-08-02T00:00:00Z")
    feature_names = st.text_input("feature_names (comma-separated, optional)", value="")

    if st.button("Assemble dataset", type="primary"):
        try:
            data = client.get_dataset(
                pair_id,
                interval,
                start,
                end,
                feature_names or None,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Dataset error: {exc}")
            return

        st.success("Dataset assembled via the API.")
        st.json(data.get("pair", {}))

        st.markdown("#### Bars")
        bars = data.get("bars", {})
        st.caption(f"interval={bars.get('interval')}, {len(bars.get('items', []))} bars")
        st.dataframe(bars.get("items", []))

        st.markdown("#### Features (vertical)")
        features = data.get("features", [])
        st.dataframe(features)

        st.markdown("#### Outcomes")
        st.dataframe(data.get("outcomes", []))

        st.download_button(
            "Download raw JSON",
            data=json.dumps(data, indent=2),
            file_name=f"dataset_{pair_id.split(':')[-1]}.json",
            mime="application/json",
        )
