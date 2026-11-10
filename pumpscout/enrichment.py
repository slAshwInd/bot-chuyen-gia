"""S2 enrichment: read everything, from independent sources, fail-closed.

Sources: Solana RPC directly (mint authorities, largest holders, the
bonding-curve account itself), the pump.fun public API (coin metadata,
recent trades), RugCheck as a second opinion only. Every source sets a
health flag; anything unread stays None in the Analysis (= blind).

Known P1 limitation (recorded in README): sell_simulated_ok is a
curve-math exit proxy, not a real simulateTransaction - that needs a
signable sell instruction and lands with the P2 paper executor.
"""
from __future__ import annotations

import hashlib
import struct
import time

import requests

from .models import Analysis, HolderSlice, Trade

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
LAMPORTS = 1_000_000_000
TOKEN_DECIMALS = 1_000_000

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + (out or "1" * (1 if not raw else 0))


def _on_curve(point: bytes) -> bool:
    """ed25519 compressed-point validity check (for PDA derivation)."""
    p = 2**255 - 19
    d = (-121665 * pow(121666, p - 2, p)) % p
    y = int.from_bytes(point, "little") & (2**255 - 1)
    if y >= p:
        return False
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p) % p
    return pow(x2, (p - 1) // 2, p) in (0, 1)


def find_program_address(seeds: list[bytes], program_id: str) -> str:
    pid = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256()
        for s in seeds:
            h.update(s)
        h.update(bytes([bump]))
        h.update(pid)
        h.update(b"ProgramDerivedAddress")
        digest = h.digest()
        if not _on_curve(digest):
            return b58encode(digest)
    raise ValueError("no viable PDA bump")


def bonding_curve_address(mint: str) -> str:
    return find_program_address([b"bonding-curve", b58decode(mint)], PUMP_PROGRAM)


class Rpc:
    def __init__(self, url: str, timeout: float = 4.0):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, params: list):
        r = self.session.post(self.url, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        }, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(str(body["error"]))
        return body["result"]

    def account_data(self, address: str) -> tuple[bytes, str] | None:
        res = self.call("getAccountInfo", [address, {"encoding": "base64"}])
        val = res.get("value") if res else None
        if not val:
            return None
        import base64
        return base64.b64decode(val["data"][0]), val["owner"]

    def multiple_accounts(self, addresses: list[str]) -> list[tuple[bytes, str] | None]:
        """One RPC request for several accounts (quota: mint+curve = 1 call)."""
        import base64
        res = self.call("getMultipleAccounts",
                        [addresses, {"encoding": "base64"}])
        out: list[tuple[bytes, str] | None] = []
        for val in (res.get("value") if res else []) or []:
            if not val:
                out.append(None)
            else:
                out.append((base64.b64decode(val["data"][0]), val["owner"]))
        return out


def parse_mint_account(data: bytes) -> tuple[str | None, str | None]:
    """SPL mint layout: COption<Pubkey> mint_authority, supply, decimals,
    initialised, COption<Pubkey> freeze_authority."""
    if len(data) < 82:
        raise ValueError("mint account too short")
    def coption(off: int) -> str | None:
        tag = struct.unpack_from("<I", data, off)[0]
        if tag == 0:
            return None
        return b58encode(data[off + 4: off + 36])
    return coption(0), coption(46)


def _metadata_url(uri: str) -> str | None:
    """Normalize a token metadata URI to a fixed-gateway URL, or None.

    Allowed: ipfs:// (via ipfs.io), ar:// (via arweave.net), any URL
    whose path carries /ipfs/<cid> (rewritten to ipfs.io), and direct
    https URLs already on ipfs.io or arweave.net. Everything else is
    refused - the caller treats it as blind, never as a fetch error."""
    from urllib.parse import urlparse
    u = (uri or "").strip()
    if u.startswith("ipfs://"):
        path = u[len("ipfs://"):]
        if path.startswith("ipfs/"):
            path = path[len("ipfs/"):]
        return "https://ipfs.io/ipfs/" + path
    if u.startswith("ar://"):
        return "https://arweave.net/" + u[len("ar://"):]
    parsed = urlparse(u)
    if parsed.scheme in ("http", "https"):
        if "/ipfs/" in parsed.path:
            cid_path = parsed.path.split("/ipfs/", 1)[1]
            return "https://ipfs.io/ipfs/" + cid_path
        if parsed.hostname in ("ipfs.io", "arweave.net"):
            return u
    return None


class Enricher:
    def __init__(self, rpc_url: str, timeout: float = 4.0, budget=None):
        self.rpc = Rpc(rpc_url, timeout)
        self.rpc_url = rpc_url
        self.budget = budget
        self.session = requests.Session()
        self.timeout = timeout

    def _api_key_query(self) -> str | None:
        """The api-key query fragment of the configured RPC URL, if any.

        With a surrogate-resolved Helius URL this is the hsurr:* token -
        never the real key - and the egress layer swaps it per request.
        """
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.rpc_url).query)
        vals = qs.get("api-key") or []
        return f"api-key={vals[0]}" if vals else None

    def enrich(self, mint: str, creator: str, uri: str = "") -> Analysis:
        now = time.time()
        health: list[str] = []
        a: dict = {"mint": mint, "ts": now}

        # --- RPC: mint authorities + bonding curve in ONE request
        # (getMultipleAccounts; the curve layout was verified live in P1)
        curve_api_sol = None
        try:
            vals = self.rpc.multiple_accounts([mint, bonding_curve_address(mint)])
            if vals[0] is None:
                raise RuntimeError("mint account missing")
            data, owner = vals[0]
            ma, fa = parse_mint_account(data)
            a.update(mint_authority=ma, freeze_authority=fa,
                     authorities_read=True,
                     token_program_ok=owner in (TOKEN_PROGRAM, TOKEN2022_PROGRAM))
            health.append("rpc_mint:ok")
            if vals[1] is None:
                raise RuntimeError("curve account missing")
            data, _owner = vals[1]
            vt, vs, rt, rs, supply, complete = struct.unpack_from("<QQQQQB", data, 8)
            a.update(virtual_sol=vs / LAMPORTS, virtual_token=vt / TOKEN_DECIMALS,
                     real_sol=rs / LAMPORTS, real_token=rt / TOKEN_DECIMALS,
                     complete=bool(complete),
                     supply_tokens=supply / TOKEN_DECIMALS)
            health.append("rpc_curve:ok")
        except Exception:
            if "rpc_mint:ok" not in health:
                health.append("rpc_mint:fail")
            health.append("rpc_curve:fail")

        # --- pump.fun public API: metadata, socials, cross-check price
        try:
            r = self.session.get(f"https://frontend-api-v3.pump.fun/coins/{mint}",
                                 timeout=self.timeout)
            r.raise_for_status()
            coin = r.json()
            curve_api_sol = (coin.get("virtual_sol_reserves") or 0) / LAMPORTS
            a.update(has_socials=bool(coin.get("twitter") or coin.get("telegram")
                                      or coin.get("website")),
                     reply_count=coin.get("reply_count"))
            if a.get("virtual_sol") and curve_api_sol:
                div = abs(a["virtual_sol"] - curve_api_sol) / a["virtual_sol"]
                health.append("price_xcheck:ok" if div <= 0.02
                              else "price_xcheck:diverged")
            health.append("pumpapi:ok")
        except Exception:
            health.append("pumpapi:fail")

        # --- pump.fun public API: recent trades
        try:
            r = self.session.get(
                f"https://frontend-api-v3.pump.fun/trades/all/{mint}",
                params={"limit": 50, "offset": 0, "minimumSize": 0},
                timeout=self.timeout)
            r.raise_for_status()
            raw = r.json()
            a.update(trades=tuple(
                Trade(wallet=t["user"], is_buy=bool(t["is_buy"]),
                      sol_amount=(t.get("sol_amount") or 0) / LAMPORTS,
                      token_amount=(t.get("token_amount") or 0) / TOKEN_DECIMALS,
                      ts=float(t.get("timestamp") or 0),
                      slot=int(t.get("slot") or 0))
                for t in raw), trades_read=True)
            health.append("trades:ok")
        except Exception:
            health.append("trades:fail")

        # --- Fallback when the pump.fun frontend API is unreachable
        # (it 403s from some egress IPs): socials from the token's own
        # metadata JSON. Trade history is NOT fetched here any more:
        # it lives in the late phase (enrich_trades), because the
        # enhanced API is the priciest Helius resource and fetching it
        # for every analysed token burned the monthly quota.
        if a.get("has_socials") is None and uri:
            socials = self._metadata_socials(uri)
            if socials is not None:
                a["has_socials"] = socials
                health.append("socials:metadata")

        # --- RugCheck: second opinion only
        try:
            r = self.session.get(
                f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary",
                timeout=self.timeout)
            r.raise_for_status()
            rep = r.json()
            norm = rep.get("score_normalised")
            if norm is not None:
                a["rugcheck_score"] = min(1.0, max(0.0, norm / 100.0))
            a["rugcheck_extreme"] = any(
                risk.get("level") == "danger" for risk in rep.get("risks", []))
            health.append("rugcheck:ok")
        except Exception:
            health.append("rugcheck:fail")

        # Sell-exit proxy (documented P1 limitation, see module docstring).
        # The proxy is "the curve state was read, so an exit quote exists";
        # an empty curve (real_sol == 0) is a liquidity rejection at S4,
        # not blindness - marking it blind here would hide that signal.
        if a.get("real_sol") is not None:
            a["sell_simulated_ok"] = True

        a["health"] = tuple(health)
        return Analysis(**a)

    def enrich_trades(self, a: Analysis) -> Analysis:
        """Late-phase trade-history read (1 enhanced-API call).

        Deliberately separate from enrich(): the enhanced API is the
        most expensive Helius resource per call, and the pump.fun
        frontend API - which would make it unnecessary - 403s from
        this egress, so fetching it for every analysed token (most of
        them vetoed junk) burned the monthly credit quota in days
        (2026-11-10 diagnosis). The pipeline calls this only for
        tokens that survived every cheaper veto and charges the
        credit-weighted api cost itself."""
        import dataclasses
        if a.trades_read:
            return a
        got = self._helius_trades(a.mint)
        if got is None:
            return a
        return dataclasses.replace(a, trades=got, trades_read=True,
                                   health=a.health + ("trades:helius",))

    def _helius_trades(self, mint: str) -> tuple[Trade, ...] | None:
        """Recent trades reconstructed from Helius parsed transactions
        on the bonding-curve account. Direction: tokens leaving the
        curve to a user = buy; tokens returning to the curve = sell.
        SOL amount from the user's native balance change (includes the
        small network/priority fees - acceptable for flow heuristics).
        Costs one enhanced-API call; the pipeline budgets it at the
        credit-weighted api cost. Returns None when unavailable."""
        key = self._api_key_query()
        if key is None:
            return None
        try:
            curve = bonding_curve_address(mint)
            r = self.session.get(
                f"https://api.helius.xyz/v0/addresses/{curve}/transactions?{key}",
                params={"limit": 50}, timeout=self.timeout + 4)
            r.raise_for_status()
            txs = r.json()
        except Exception:
            return None
        out: list[Trade] = []
        for tx in txs:
            changes = {c.get("account"): (c.get("nativeBalanceChange") or 0)
                       for c in tx.get("accountData", [])}
            for tr in tx.get("tokenTransfers", []):
                if tr.get("mint") != mint:
                    continue
                frm, to = tr.get("fromUserAccount"), tr.get("toUserAccount")
                if to == curve and frm and frm != curve:
                    wallet, is_buy = frm, False
                elif frm == curve and to and to != curve:
                    wallet, is_buy = to, True
                else:
                    continue
                change = changes.get(wallet, 0) / LAMPORTS
                out.append(Trade(
                    wallet=wallet, is_buy=is_buy,
                    sol_amount=abs(change),
                    token_amount=float(tr.get("tokenAmount") or 0),
                    ts=float(tx.get("timestamp") or 0),
                    slot=int(tx.get("slot") or 0)))
        return tuple(out)

    def _metadata_socials(self, uri: str) -> bool | None:
        """Social presence from the token metadata JSON itself.

        Fetches go ONLY through fixed gateways (see _metadata_url):
        token creators can point metadata at arbitrary hosts, and an
        unbounded fetch set is both a prompt/approval nuisance and an
        SSRF-shaped risk. Unresolvable URIs simply stay blind."""
        url = _metadata_url(uri)
        if url is None:
            return None
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            meta = r.json()
        except Exception:
            return None

        def hunt(node) -> bool:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k.lower() in ("twitter", "telegram", "website", "web"):
                        if isinstance(v, str) and v.strip():
                            return True
                    if hunt(v):
                        return True
            elif isinstance(node, list):
                return any(hunt(v) for v in node)
            return False

        return hunt(meta)

    def enrich_holders(self, a: Analysis) -> Analysis:
        """Late-phase holder read (2 RPC requests).

        Deliberately separate from enrich(): it is the most fragile and
        most rate-limited call, so the pipeline only pays for it after a
        token has passed the cheap contract/liquidity vetoes.

        Shares are fractions of TOTAL SUPPLY (the HolderSlice contract),
        resolved to owner wallets: getTokenLargestAccounts returns
        token-account addresses, so a second read parses each account's
        owner field (bytes 32:64 of the SPL token-account layout) and
        its amount (bytes 64:72). The bonding curve's own token account
        (owner = curve PDA) is excluded - it is the unsold supply, not
        a holder. Accounts sharing one owner are aggregated, so the
        creator veto in S5 compares real wallets. Any unreadable piece
        fails the whole read (blind is not pass).
        """
        import dataclasses
        try:
            supply = a.supply_tokens
            if supply is None or supply <= 0:
                raise RuntimeError("supply unknown")
            res = self.rpc.call("getTokenLargestAccounts", [a.mint])
            vals = res["value"]
            if not vals:
                raise RuntimeError("empty largest accounts")
            addresses = [v["address"] for v in vals]
            accounts = self.rpc.multiple_accounts(addresses)
            if len(accounts) != len(addresses):
                raise RuntimeError("holder account read mismatch")
            curve_owner = bonding_curve_address(a.mint)
            by_owner: dict[str, float] = {}
            for item in accounts:
                if item is None:
                    raise RuntimeError("holder account missing")
                data, _program = item
                if len(data) < 72:
                    raise RuntimeError("token account too short")
                owner = b58encode(data[32:64])
                if owner == curve_owner:
                    continue
                amount = struct.unpack_from("<Q", data, 64)[0] / TOKEN_DECIMALS
                by_owner[owner] = by_owner.get(owner, 0.0) + amount
            holders = tuple(
                HolderSlice(wallet, amount / supply)
                for wallet, amount in sorted(
                    by_owner.items(), key=lambda kv: kv[1], reverse=True))
            return dataclasses.replace(
                a,
                top_holders=holders,
                holders_read=True,
                health=a.health + ("rpc_holders:ok",))
        except Exception:
            return dataclasses.replace(a, health=a.health + ("rpc_holders:fail",))
