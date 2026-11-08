"""Resolve an rpc_url that carries a credential placeholder.

Runtime configs reference the Helius key as a placeholder, never as a
value:

    rpc_url = "https://mainnet.helius-rpc.com/?api-key={helius}"

At startup the placeholder is exchanged for a short-lived surrogate
(hsurr:*) via the local credential service; the real key is swapped in
by the egress layer on requests to the registered host and never exists
on this machine, in this repo, or in any log. If the exchange fails the
caller falls back to the public RPC and records the failure - visible,
never silent.
"""
from __future__ import annotations

import sys

HELPER_DIR = "/opt/muse/skills/custom-connector-setup/bin"
PLACEHOLDER = "{helius}"
HELIUS_HOST = "mainnet.helius-rpc.com"


def resolve_rpc_url(url: str) -> tuple[str, str]:
    """Return (resolved_url, status). status: plain | surrogate | failed."""
    if PLACEHOLDER not in url:
        return url, "plain"
    base = url.split("?")[0]
    try:
        if HELPER_DIR not in sys.path:
            sys.path.insert(0, HELPER_DIR)
        from dynamic_credentials import url_with_surrogate_query_param
        resolved = url_with_surrogate_query_param(
            base, "custom.helius", allowed_hosts=[HELIUS_HOST])
        return resolved, "surrogate"
    except Exception:
        return "https://api.mainnet-beta.solana.com", "failed"
