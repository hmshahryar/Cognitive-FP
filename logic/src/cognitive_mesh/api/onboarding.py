"""Onboarding answers for the caller's own workspace.

The workspace always comes from the verified session (CurrentAccountDep); the request body
never names a user or workspace.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from cognitive_mesh.db.base import utcnow
from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.models.onboarding import WorkspaceProfile

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

TeamSize = Literal["just_me", "2_10", "11_50", "51_plus"]
Goal = Literal["email", "calendar", "crm", "docs", "social", "design"]


class OnboardingOut(BaseModel):
    workspace_name: str
    team_size: TeamSize | None
    goals: list[Goal]
    completed: bool
    completed_at: datetime | None


class OnboardingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_name: str = Field(min_length=1, max_length=80)
    team_size: TeamSize | None = None
    goals: list[Goal] = Field(default_factory=list, max_length=6)
    complete: bool = False

    @field_validator("workspace_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise ValueError("Workspace name can't be empty")
        if any(ord(c) < 32 for c in name):
            raise ValueError("Workspace name has invalid characters")
        return name

    @field_validator("goals")
    @classmethod
    def _unique_goals(cls, value: list[Goal]) -> list[Goal]:
        return list(dict.fromkeys(value))


async def _profile(session: SessionDep, account: CurrentAccountDep) -> WorkspaceProfile | None:
    return await session.scalar(
        select(WorkspaceProfile).where(WorkspaceProfile.workspace_id == account.workspace.id)
    )


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns stored times without a zone; they were written in UTC."""
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _out(account: CurrentAccountDep, profile: WorkspaceProfile | None) -> OnboardingOut:
    return OnboardingOut(
        workspace_name=account.workspace.name,
        team_size=profile.team_size if profile else None,  # type: ignore[arg-type]
        goals=list(profile.goals) if profile else [],  # type: ignore[arg-type]
        completed=bool(profile and profile.onboarded_at),
        completed_at=_utc(profile.onboarded_at) if profile else None,
    )


@router.get("", response_model=OnboardingOut)
async def get_onboarding(account: CurrentAccountDep, session: SessionDep) -> OnboardingOut:
    """The caller's onboarding answers, and whether onboarding is finished."""
    return _out(account, await _profile(session, account))


@router.put("", response_model=OnboardingOut)
async def save_onboarding(
    body: OnboardingIn, account: CurrentAccountDep, session: SessionDep
) -> OnboardingOut:
    """Save answers for the caller's own workspace. `complete` marks onboarding finished;
    once finished it stays finished."""
    profile = await _profile(session, account)
    if profile is None:
        profile = WorkspaceProfile(workspace_id=account.workspace.id, goals=[])
        session.add(profile)
    account.workspace.name = body.workspace_name
    profile.team_size = body.team_size
    profile.goals = list(body.goals)
    if body.complete and profile.onboarded_at is None:
        profile.onboarded_at = utcnow()
    await session.commit()
    return _out(account, profile)
