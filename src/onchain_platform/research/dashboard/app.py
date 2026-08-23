"""Streamlit entrypoint — sidebar navigation to the three pages.

The dashboard is a thin API client (DOC-015 § Dashboard). It reads data only
through research/dashboard/api_client.py (HTTPX), never persistence/.
"""

import streamlit as st

from onchain_platform.research.dashboard.api_client import OnchainPlatformClient
from onchain_platform.research.dashboard.pages import dataset_explorer, pair_detail, pairs_list

st.set_page_config(page_title="onchain_platform Research", layout="wide")

st.sidebar.title("onchain_platform")
st.sidebar.caption("Research Platform (DOC-015)")

# Base URL configurable for local dev / tests.
base_url = st.sidebar.text_input("API base URL", value="http://localhost:8000")

client: OnchainPlatformClient | None = None
try:
    client = OnchainPlatformClient(base_url=base_url)
    health = client.get_health()
    st.sidebar.success(f"API ok — {health.get('status', '?')}")
except Exception as exc:  # noqa: BLE001 — surface API connectivity in the UI
    st.sidebar.error(f"API unreachable: {exc}")
    client = None


def get_client() -> OnchainPlatformClient | None:
    """Return the shared client for pages (or None if unreachable)."""
    return client


if client is None:
    st.error("Cannot reach the Research API. Is the FastAPI server running?")
    st.stop()


st.title("onchain_platform Research")
st.markdown(
    "Ask the platform a question using only the API: browse pairs, inspect "
    "bars + features, and explore assembled research datasets."
)

# Sidebar navigation to the three pages (each reads only via the API client).
page = st.sidebar.radio(
    "Navigate",
    ["Pairs List", "Pair Detail", "Dataset Explorer"],
)

if page == "Pairs List":
    pairs_list.render(client)
elif page == "Pair Detail":
    pair_detail.render(client)
else:
    dataset_explorer.render(client)
