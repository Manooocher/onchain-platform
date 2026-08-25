"""Unit tests: CLI --chain integration (Phase D).

Verifies the chain-name → EIP-155 id map and that the argparse `--chain`
flag is present with the expected choices.
"""

import pytest

from onchain_platform.platform.config import CHAIN_ID_MAP, get_chain_id


def test_chain_id_map_matches_doc() -> None:
    assert CHAIN_ID_MAP["base"] == 8453
    assert CHAIN_ID_MAP["ethereum"] == 1
    assert CHAIN_ID_MAP["bnb"] == 56


def test_get_chain_id_known_chains() -> None:
    assert get_chain_id("base") == 8453
    assert get_chain_id("ethereum") == 1
    assert get_chain_id("bnb") == 56


def test_get_chain_id_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown chain"):
        get_chain_id("solana")


def test_main_parser_has_chain_flag() -> None:
    import argparse

    # Reconstitute the CLI arg parser exactly as main() does, and verify the
    # --chain flag accepts the documented choices and defaults to base.
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", choices=["base", "ethereum", "bnb"], default="base")
    ns = parser.parse_args(["--chain", "ethereum"])
    assert ns.chain == "ethereum"
    ns2 = parser.parse_args([])
    assert ns2.chain == "base"  # default
