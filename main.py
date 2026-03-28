"""
Ap01X_Deva — local operator console for AppaTimoX (ventures, lanes, council, proposals, incubator).

State file: <root>/ap01x_deva_state.json

Examples:
  python Ap01X_Deva.py --root . status
  python Ap01X_Deva.py --root . venture 0xabc... 0x0000...01 6
  python Ap01X_Deva.py --root . treasury 1000000000000000000
  python Ap01X_Deva.py --root . payload-treasury 0x... 1ether_as_wei 0x0000...
  python Ap01X_Deva.py --root . export-holss
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# AppaTimoX immutables (reference; must match deployed contract if you verify on-chain)
ATX_ADDR_GENESIS = "0x263e10eAA37F82E9C625251554aB36395bb7ff34"
ATX_ADDR_TREASURY = "0x0532D65B16f768b0dCFEd74eC6bc563AD28eb117"
ATX_ADDR_COUNCIL = "0x3d822AFEdFfB6096A2e49DD08B4D85B5473cf4B7"
ATX_ADDR_ORACLE = "0xa755D68ED8154022642B53Ea3671C3c91D149e09"
ATX_ADDR_BEACON = "0xB9E279f8C4500311EE7C6E6F1188B358483d6a59"
ATX_ADDR_AUDIT = "0x423177606F4a321569B18a308BEF9E7fD98F3B8C"
ATX_ADDR_GRANT = "0xFC1fee146ea70647be58e0C49A9Eef4149C98E41"
ATX_ADDR_TIMELOCK = "0x50AB304E52718158CbBd163797CD074116651011"

ATX_FIB_A = 0x9E3779B97F4A7C15
ATX_FIB_B = 0x85EBCA77C2B2AD63


def _keccak256(data: bytes) -> bytes:
    try:
        from Crypto.Hash import keccak

        k = keccak.new(digest_bits=256)
        k.update(data)
        return k.digest()
    except Exception:
        try:
            import sha3

            k = sha3.keccak_256()
            k.update(data)
            return k.digest()
        except Exception as exc:
            raise RuntimeError(
                "Install pycryptodome or pysha3 for keccak256: pip install pycryptodome"
            ) from exc


def deva_hash_topic(*parts: Any) -> str:
    enc = json.dumps(parts, sort_keys=True, default=str).encode()
    return _keccak256(enc).hex()


def deva_rand_addr() -> str:
    hx = secrets.token_hex(20)
    h = _keccak256(hx.encode("ascii")).hex()
    out = []
    for i, ch in enumerate(hx):
        if ch in "0123456789":
            out.append(ch)
        else:
            out.append(ch.upper() if int(h[i], 16) >= 8 else ch)
    return "0x" + "".join(out)

