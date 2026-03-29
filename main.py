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


def _norm_hex_addr(s: str) -> str:
    if not s.startswith("0x") or len(s) != 42:
        raise ValueError("address must be 0x + 40 hex chars")
    int(s[2:], 16)  # validate
    return s


def _norm_bytes32_hex(s: str) -> bytes:
    h = s[2:] if s.startswith("0x") else s
    if len(h) != 64:
        raise ValueError("bytes32 must be 64 hex chars")
    return bytes.fromhex(h)


def encode_abi_treasury_payload(to_addr: str, amount_wei: int, memo32: bytes) -> bytes:
    """Matches Solidity abi.encode(address,uint256,bytes32)."""
    addr = bytes.fromhex(to_addr[2:].lower())
    if len(addr) != 20:
        raise ValueError("bad address")
    if len(memo32) != 32:
        raise ValueError("memo must be 32 bytes")
    return (b"\x00" * 12 + addr) + amount_wei.to_bytes(32, "big") + memo32


def encode_abi_spawn_payload(parent_venture_id: int, manifest32: bytes) -> bytes:
    """Matches Solidity abi.encode(uint256,bytes32)."""
    if len(manifest32) != 32:
        raise ValueError("manifest must be 32 bytes")
    return parent_venture_id.to_bytes(32, "big") + manifest32


def payload_hash_treasury(to_addr: str, amount_wei: int, memo_hex: str) -> str:
    memo = _norm_bytes32_hex(memo_hex)
    _norm_hex_addr(to_addr)
    raw = encode_abi_treasury_payload(to_addr, amount_wei, memo)
    return "0x" + _keccak256(raw).hex()


def payload_hash_spawn(parent_venture_id: int, manifest_hex: str) -> str:
    m = _norm_bytes32_hex(manifest_hex)
    raw = encode_abi_spawn_payload(parent_venture_id, m)
    return "0x" + _keccak256(raw).hex()


@dataclass
class DevaVentureRow:
    venture_id: int
    lead: str
    phase: int
    milestone_cursor: int
    milestone_target: int
    blueprint: str
    updated_at: float


@dataclass
class DevaProposalRow:
    proposal_id: int
    p_class: int
    proposer: str
    yes_weight: int
    no_weight: int
    quorum_required: int
    executed: bool
    cancelled: bool
    payload_hash: str
    created_ts: float = 0.0
    voting_ends_ts: float = 0.0
    execute_after_ts: float = 0.0


@dataclass
class DevaLaneRow:
    lane_id: int
    venture_id: int
    buffer_cap_wei: int


@dataclass
class DevaApplicationRow:
    application_id: int
    applicant: str
    pitch_hash: str
    decided: bool
    accepted: bool


@dataclass
class DevaState:
    ventures: Dict[int, DevaVentureRow] = field(default_factory=dict)
    proposals: Dict[int, DevaProposalRow] = field(default_factory=dict)
    lanes: Dict[int, DevaLaneRow] = field(default_factory=dict)
    council: Dict[int, str] = field(default_factory=dict)
    applications: Dict[int, DevaApplicationRow] = field(default_factory=dict)
    proposal_votes: Dict[str, bool] = field(default_factory=dict)
    treasury_wei: int = 0
    notes: List[str] = field(default_factory=list)


def _vote_key(pid: int, voter: str) -> str:
    return f"{pid}:{voter.lower()}"


class Ap01XCore:
    VOTING_PERIOD_SEC = 3 * 24 * 3600
    TIMELOCK_PERIOD_SEC = 24 * 3600

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "ap01x_deva_state.json"
        self.state = DevaState()
        self._load()

    def _append_note(self, text: str) -> None:
        self.state.notes.append(f"{time.time():.3f} | {text}")

    def _load(self) -> None:
        if not self.path.exists():
            self._bootstrap()
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.state.treasury_wei = int(data.get("treasury_wei", 0))
        for k, v in data.get("ventures", {}).items():
            self.state.ventures[int(k)] = DevaVentureRow(**v)
        for k, v in data.get("proposals", {}).items():
            pv = dict(v)
            pv.setdefault("created_ts", 0.0)
            pv.setdefault("voting_ends_ts", 0.0)
            pv.setdefault("execute_after_ts", 0.0)
            self.state.proposals[int(k)] = DevaProposalRow(**pv)
        for k, v in data.get("lanes", {}).items():
            self.state.lanes[int(k)] = DevaLaneRow(**v)
        self.state.council = {int(k): str(v) for k, v in data.get("council", {}).items()}
        for k, v in data.get("applications", {}).items():
            self.state.applications[int(k)] = DevaApplicationRow(**v)
        self.state.proposal_votes = {
            str(k): bool(v) for k, v in data.get("proposal_votes", {}).items()
        }
        self.state.notes = list(data.get("notes", []))

    def _bootstrap(self) -> None:
        self._append_note("bootstrap: empty council (use council-add to mirror on-chain seats)")
        self._save()

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "treasury_wei": self.state.treasury_wei,
            "ventures": {str(k): asdict(v) for k, v in self.state.ventures.items()},
            "proposals": {str(k): asdict(v) for k, v in self.state.proposals.items()},
            "lanes": {str(k): asdict(v) for k, v in self.state.lanes.items()},
            "council": {str(k): v for k, v in self.state.council.items()},
            "applications": {str(k): asdict(v) for k, v in self.state.applications.items()},
            "proposal_votes": dict(self.state.proposal_votes),
            "notes": self.state.notes[-400:],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def note(self, text: str) -> None:
        self._append_note(text)
        self._save()

    def treasury_delta(self, wei: int) -> None:
        if self.state.treasury_wei + wei < 0:
            raise ValueError("treasury would underflow")
        self.state.treasury_wei += wei
        self._append_note(f"treasury {wei:+d} wei")
        self._save()

    def seed_venture(self, lead: str, blueprint: str, target: int) -> int:
        _norm_hex_addr(lead)
        _norm_bytes32_hex(blueprint)
        if not (1 <= target <= 96):
            raise ValueError("milestone target must be 1..96")
        vid = max(self.state.ventures.keys(), default=0) + 1
        self.state.ventures[vid] = DevaVentureRow(
            venture_id=vid,
            lead=lead,
            phase=2,
            milestone_cursor=0,
            milestone_target=target,
            blueprint=blueprint,
            updated_at=time.time(),
        )
        self._append_note(f"venture_seed {vid} lead={lead}")
        self._save()
        return vid

    def bind_lane(self, lane_id: int, venture_id: int, cap_wei: int) -> None:
        if lane_id in self.state.lanes:
            raise KeyError("lane already bound")
        if venture_id not in self.state.ventures:
            raise KeyError("unknown venture_id")
        if cap_wei <= 0:
            raise ValueError("cap_wei must be positive")
        self.state.lanes[lane_id] = DevaLaneRow(
            lane_id=lane_id, venture_id=venture_id, buffer_cap_wei=cap_wei
        )
        self._append_note(f"lane_bind {lane_id} -> venture {venture_id}")
        self._save()

    def council_add(self, seat_id: int, addr: str) -> None:
        if not (0 <= seat_id < 64):
            raise ValueError("seat_id must be 0..63")
        _norm_hex_addr(addr)
        if seat_id in self.state.council:
            raise KeyError("seat already filled")
        self.state.council[seat_id] = addr
        self._append_note(f"council_add seat={seat_id} {addr}")
        self._save()

    def council_clear(self, seat_id: int) -> None:
        if seat_id not in self.state.council:
            raise KeyError("seat empty")
        del self.state.council[seat_id]
        self._append_note(f"council_clear seat={seat_id}")
        self._save()

    def council_count(self) -> int:
        return len(self.state.council)

    def _quorum_required(self) -> int:
        n = self.council_count()
        if n == 0:
            return 1
        q = max(1, (n * 2) // 3)
        return q

    def proposal_create(self, p_class: int, proposer: str, payload_hash: str) -> int:
        _norm_hex_addr(proposer)
        ph = payload_hash.lower()
        if not ph.startswith("0x") or len(ph) != 66:
            raise ValueError("payload_hash must be bytes32 hex")
        int(ph[2:], 16)
        if proposer.lower() not in {a.lower() for a in self.state.council.values()}:
            raise ValueError("proposer must be a seated council member")
        pid = max(self.state.proposals.keys(), default=0) + 1
        now = time.time()
        self.state.proposals[pid] = DevaProposalRow(
            proposal_id=pid,
            p_class=p_class,
            proposer=proposer,
            yes_weight=0,
            no_weight=0,
            quorum_required=self._quorum_required(),
            executed=False,
            cancelled=False,
            payload_hash=payload_hash,
            created_ts=now,
            voting_ends_ts=now + self.VOTING_PERIOD_SEC,
            execute_after_ts=0.0,
        )
        self._append_note(f"proposal_create id={pid} class={p_class}")
        self._save()
        return pid

    def proposal_vote(self, proposal_id: int, voter: str, support: bool) -> None:
        if proposal_id not in self.state.proposals:
            raise KeyError("unknown proposal")
        p = self.state.proposals[proposal_id]
        if p.cancelled or p.executed:
            raise ValueError("proposal closed")
        now = time.time()
        if now > p.voting_ends_ts:
            raise ValueError("voting ended")
        vk = _vote_key(proposal_id, voter)
        if vk in self.state.proposal_votes:
            raise ValueError("already voted")
        if voter.lower() not in {a.lower() for a in self.state.council.values()}:
            raise ValueError("voter must be council")
        self.state.proposal_votes[vk] = True
        if support:
            p.yes_weight += 1
        else:
            p.no_weight += 1
        self._append_note(f"proposal_vote id={proposal_id} support={support}")
        self._save()

    def proposal_queue(self, proposal_id: int) -> None:
        if proposal_id not in self.state.proposals:
            raise KeyError("unknown proposal")
        p = self.state.proposals[proposal_id]
        if p.cancelled or p.executed:
            raise ValueError("proposal closed")
        now = time.time()
        if now <= p.voting_ends_ts:
            raise ValueError("voting still active")
        if p.yes_weight < p.quorum_required:
            raise ValueError("quorum not met")
        if p.yes_weight <= p.no_weight:
            raise ValueError("not passing")
        if p.execute_after_ts > 0:
            raise ValueError("already queued")
        p.execute_after_ts = now + self.TIMELOCK_PERIOD_SEC
        self._append_note(f"proposal_queue id={proposal_id} execute_after={p.execute_after_ts}")
        self._save()

    def application_apply(self, applicant: str, pitch_hash: str) -> int:
        _norm_hex_addr(applicant)
        _norm_bytes32_hex(pitch_hash)
        aid = max(self.state.applications.keys(), default=0) + 1
        self.state.applications[aid] = DevaApplicationRow(
            application_id=aid,
            applicant=applicant,
            pitch_hash=pitch_hash,
            decided=False,
            accepted=False,
        )
        self._append_note(f"incubator_apply id={aid}")
        self._save()
        return aid

    def application_decide(self, application_id: int, accepted: bool) -> None:
        if application_id not in self.state.applications:
            raise KeyError("unknown application")
        a = self.state.applications[application_id]
        if a.decided:
            raise ValueError("already decided")
        a.decided = True
        a.accepted = accepted
        self._append_note(f"incubator_decide id={application_id} accepted={accepted}")
        self._save()

    def deva_metric(self, n: int, x: int) -> int:
        """Deterministic mixing function (replaces deva_metric_0..N)."""
        if n < 0:
            raise ValueError("n must be non-negative")
        return int((x * (n + 3) + ATX_FIB_A) ^ ATX_FIB_B) & 0xFFFFFFFFFFFFFFFF

    def export_holss_sync(self) -> dict:
        """Shape compatible with Holss_Sync/index.html localStorage key 'holss_sync'."""
        ventures_out = {}
        for vid, v in self.state.ventures.items():
            ventures_out[str(vid)] = {
                "lead": v.lead,
                "target": v.milestone_target,
                "bp": v.blueprint,
                "t": int(v.updated_at * 1000),
            }
        lanes_out = {str(lid): row.venture_id for lid, row in self.state.lanes.items()}
        return {
            "ventures": ventures_out,
            "lanes": lanes_out,
            "treasury": self.state.treasury_wei,
            "notes": list(self.state.notes[-200:]),
        }

    def status_text(self) -> str:
        lines = [
            f"state_file: {self.path}",
            f"treasury_wei: {self.state.treasury_wei}",
            f"ventures: {len(self.state.ventures)}  lanes: {len(self.state.lanes)}",
            f"council_seats: {self.council_count()}  proposals: {len(self.state.proposals)}",
            f"applications: {len(self.state.applications)}",
            "immutable refs: GENESIS=" + ATX_ADDR_GENESIS,
        ]
        return "\n".join(lines)


def deva_cli() -> None:
    p = argparse.ArgumentParser(prog="Ap01X_Deva")
    p.add_argument("--root", default=".", help="directory for ap01x_deva_state.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="summary of local state")

    sp = sub.add_parser("note", help="append a note")
    sp.add_argument("text")

    sp = sub.add_parser("treasury", help="add wei to treasury (negative allowed if balance ok)")
    sp.add_argument("wei", type=int)

    sp = sub.add_parser("venture", help="seed a venture")
    sp.add_argument("lead")
    sp.add_argument("blueprint")
    sp.add_argument("target", type=int)

    sp = sub.add_parser("lane", help="bind cohort lane to venture")
    sp.add_argument("lane_id", type=int)
    sp.add_argument("venture_id", type=int)
    sp.add_argument("cap", type=int)

    sub.add_parser("dump", help="print full JSON state")

    sp = sub.add_parser("metric", help="deterministic mix: (x*(n+3)+A)^B mod 2^64")
    sp.add_argument("n", type=int)
    sp.add_argument("x", type=int)

    sp = sub.add_parser("council-add", help="assign council seat (0..63)")
    sp.add_argument("seat_id", type=int)
    sp.add_argument("addr")

    sp = sub.add_parser("council-clear", help="remove council seat")
    sp.add_argument("seat_id", type=int)

    sub.add_parser("council-list", help="list seated council")

    sp = sub.add_parser("proposal-create", help="new proposal (proposer must be council)")
    sp.add_argument("p_class", type=int, help="0=treasury 1=gov 2=incubator 3=spawn 4=emergency")
    sp.add_argument("proposer")
    sp.add_argument("payload_hash")

    sp = sub.add_parser("proposal-vote")
    sp.add_argument("proposal_id", type=int)
    sp.add_argument("voter")
    sp.add_argument("support", type=int, choices=(0, 1))
