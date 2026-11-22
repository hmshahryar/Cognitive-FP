"""Cryptographic binding of an approval to one exact action.

The signature covers every field that defines *what* will happen and *where*:
approval/workspace/task/execution ids, action type, toolkit + tool, target
connection + Composio account, the SHA-256 of the canonical payload, the
content version and the requesting user. Change any of them and verification
fails, so the old approval can't authorize the new action.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class ApprovalBinding:
    approval_id: uuid.UUID
    workspace_id: uuid.UUID
    task_id: uuid.UUID
    execution_id: uuid.UUID
    requested_by: uuid.UUID
    action_type: str
    toolkit_slug: str
    tool_slug: str
    connection_id: uuid.UUID
    target_account_id: str
    payload_hash: str
    content_version: int

    def message(self) -> bytes:
        return canonical_json({"v": 1, **{k: str(v) for k, v in asdict(self).items()}})

    def idempotency_key(self) -> str:
        """Same binding -> same key; DB uniqueness stops duplicate approvals/executions."""
        return hashlib.sha256(b"idem:" + self.message()).hexdigest()


@dataclass(frozen=True)
class PlanBinding:
    """Everything one multi-step plan is allowed to do, in order."""

    run_id: uuid.UUID
    workspace_id: uuid.UUID
    requested_by: uuid.UUID
    playbook: str
    playbook_version: int
    title: str  # shown to the user when they approve
    last_gate: int
    plan_hash: str
    # key, gate, action, tool, connection, account, payload hash and dependencies per step
    steps: tuple[tuple[str, ...], ...]

    def message(self) -> bytes:
        # A different version label keeps plan and single-action signatures from overlapping.
        data = asdict(self)
        steps = data.pop("steps")
        return canonical_json({"v": "plan-1", **{k: str(v) for k, v in data.items()}, "steps": steps})


class ApprovalSigner:
    def __init__(self, key: str) -> None:
        if len(key) < 32:
            raise ValueError("APPROVAL_SIGNING_KEY must be at least 32 characters")
        self._key = key.encode("utf-8")

    def sign(self, binding: ApprovalBinding | PlanBinding) -> str:
        return hmac.new(self._key, binding.message(), hashlib.sha256).hexdigest()

    def verify(self, binding: ApprovalBinding | PlanBinding, signature: str) -> bool:
        return hmac.compare_digest(self.sign(binding), signature)
