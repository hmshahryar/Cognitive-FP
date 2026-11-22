"""Typed multi-step plans and safe references between their steps.

A later step may need an id that does not exist yet, such as a new contact's id. Its approved
payload stores `{"$ref": "steps.<key>.external_id"}`. Only an id returned by a step it depends
on can fill that place, so a model or outside content can never choose the value.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cognitive_mesh.capabilities import CAPABILITIES, RiskLevel
from cognitive_mesh.integrations.actions import get_action

REF_KEY = "$ref"
MAX_STEPS = 50
MAX_WAIT_SECONDS = 30 * 24 * 60 * 60
_STEP_KEY = r"^[a-z][a-z0-9_]{0,63}$"
_REF = re.compile(r"^steps\.([a-z][a-z0-9_]{0,63})\.external_id$")
_SAFE_OUTPUT = re.compile(r"^[A-Za-z0-9_:.\-]{1,255}$")


class PlanInvalidError(ValueError):
    """The plan is not safe or complete enough to show for approval."""


class PlanReferenceError(ValueError):
    """A reference could not be filled with a safe value."""


def references_in(value: Any) -> set[str]:
    """The step keys a payload refers to. Malformed references are rejected."""
    if isinstance(value, dict):
        if REF_KEY in value:
            target = value[REF_KEY]
            match = _REF.match(target) if isinstance(target, str) else None
            if len(value) != 1 or match is None:
                raise PlanInvalidError("A reference must be exactly steps.<key>.external_id")
            return {match.group(1)}
        return set().union(*(references_in(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(references_in(item) for item in value)) if value else set()
    return set()


def resolve_references(value: Any, outputs: dict[str, str]) -> Any:
    """Copy `value` with every reference replaced by its dependency's returned id."""
    if isinstance(value, dict):
        if REF_KEY in value:
            key = _REF.match(value[REF_KEY]).group(1)  # type: ignore[union-attr]
            output = outputs.get(key)
            if output is None or not _SAFE_OUTPUT.match(output):
                raise PlanReferenceError(f"Step {key} returned no usable id")
            return output
        return {name: resolve_references(item, outputs) for name, item in value.items()}
    if isinstance(value, list):
        return [resolve_references(item, outputs) for item in value]
    return value


def is_safe_output(value: str) -> bool:
    return bool(_SAFE_OUTPUT.match(value))


def _risk(action_type: str) -> RiskLevel | None:
    return next((c.risk for c in CAPABILITIES if c.action_type == action_type), None)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=_STEP_KEY)
    action_type: str | None = None
    payload: dict[str, Any] | None = None
    depends_on: tuple[str, ...] = ()
    gate: int = Field(default=0, ge=0)
    run_after_failure: bool = False  # for steps such as logging a failure
    wait_seconds: int | None = Field(default=None, ge=1, le=MAX_WAIT_SECONDS)
    wait_event: str | None = Field(default=None, pattern=_STEP_KEY)

    @property
    def is_wait(self) -> bool:
        return self.action_type is None

    @model_validator(mode="after")
    def _check_shape(self) -> "PlanStep":
        if self.is_wait:
            if self.payload is not None or (self.wait_seconds is None and self.wait_event is None):
                raise ValueError("A wait step needs a timer or event, and no payload")
        elif self.payload is None or self.wait_seconds is not None or self.wait_event is not None:
            raise ValueError("An action step needs a payload and no wait settings")
        if self.run_after_failure and not self.depends_on:
            raise ValueError("run_after_failure needs a step to follow")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Dependencies must be unique")
        return self


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    playbook: str = Field(pattern=_STEP_KEY)
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _check_order(self) -> "WorkflowPlan":
        seen: set[str] = set()
        expected_gate = 0
        for step in self.steps:
            if step.key in seen:
                raise ValueError(f"Duplicate step: {step.key}")
            if not set(step.depends_on) <= seen:
                raise ValueError(f"{step.key} must come after the steps it depends on")
            if step.gate not in (expected_gate, expected_gate + 1) or (
                step.gate == expected_gate + 1 and not seen
            ):
                raise ValueError("Approval gates must start at 0 and rise one at a time")
            expected_gate = step.gate
            seen.add(step.key)
            if step.is_wait:
                continue
            action = get_action(step.action_type or "")
            if action is None:
                raise ValueError(f"Unknown action: {step.action_type}")
            references = references_in(step.payload)
            if not references <= set(step.depends_on):
                raise ValueError(f"{step.key} can only reference steps it depends on")
            if references and step.run_after_failure:
                raise ValueError("A step that runs after failure cannot need its result")
            if not references:
                action.validate(step.payload or {})
        self._check_spend_gates()
        return self

    def _check_spend_gates(self) -> None:
        """Anything that can spend money gets its own, separate human approval."""
        spend = {s.gate for s in self.steps if s.action_type and _risk(s.action_type) is RiskLevel.SPEND}
        for step in self.steps:
            is_spend = bool(step.action_type) and _risk(step.action_type) is RiskLevel.SPEND
            if step.gate in spend and not is_spend and not step.is_wait:
                raise ValueError("Spending steps need their own approval gate")

    @property
    def last_gate(self) -> int:
        return max(step.gate for step in self.steps)
