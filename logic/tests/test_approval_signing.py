import dataclasses
import uuid

import pytest

from cognitive_mesh.security.approval_signing import (
    ApprovalBinding,
    ApprovalSigner,
    canonical_json,
    payload_hash,
)

KEY = "k" * 40


def _binding(**overrides) -> ApprovalBinding:  # type: ignore[no-untyped-def]
    base = ApprovalBinding(
        approval_id=uuid.UUID(int=1),
        workspace_id=uuid.UUID(int=2),
        task_id=uuid.UUID(int=3),
        execution_id=uuid.UUID(int=4),
        requested_by=uuid.UUID(int=5),
        action_type="linkedin.publish_post",
        toolkit_slug="linkedin",
        tool_slug="LINKEDIN_CREATE_LINKED_IN_POST",
        connection_id=uuid.UUID(int=6),
        target_account_id="ca_abc",
        payload_hash=payload_hash({"commentary": "Hello"}),
        content_version=1,
    )
    return dataclasses.replace(base, **overrides)


def test_canonical_json_ignores_key_order() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert payload_hash({"b": 1, "a": 2}) == payload_hash({"a": 2, "b": 1})


def test_signature_verifies_for_identical_binding() -> None:
    signer = ApprovalSigner(KEY)
    sig = signer.sign(_binding())
    assert signer.verify(_binding(), sig)


@pytest.mark.parametrize(
    "change",
    [
        {"payload_hash": payload_hash({"commentary": "Hello!"})},  # content edited
        {"content_version": 2},
        {"target_account_id": "ca_other"},  # different LinkedIn account
        {"connection_id": uuid.UUID(int=99)},
        {"tool_slug": "LINKEDIN_DELETE_POST"},  # different action
        {"action_type": "linkedin.delete_post"},
        {"workspace_id": uuid.UUID(int=77)},
        {"approval_id": uuid.UUID(int=88)},  # can't transplant a signature
    ],
)
def test_any_change_breaks_the_signature(change: dict) -> None:
    signer = ApprovalSigner(KEY)
    sig = signer.sign(_binding())
    assert not signer.verify(_binding(**change), sig)


def test_different_key_cannot_forge() -> None:
    sig = ApprovalSigner("x" * 40).sign(_binding())
    assert not ApprovalSigner(KEY).verify(_binding(), sig)


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError):
        ApprovalSigner("short")


def test_idempotency_key_is_stable_and_binding_specific() -> None:
    assert _binding().idempotency_key() == _binding().idempotency_key()
    assert _binding().idempotency_key() != _binding(content_version=2).idempotency_key()
