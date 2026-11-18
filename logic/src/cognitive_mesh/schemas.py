import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clerk_user_id: str
    email: str | None
    first_name: str | None
    last_name: str | None
    image_url: str | None
    created_at: datetime


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    workspace: WorkspaceOut


# ---------- Connector Hub ----------
class ConnectorOut(BaseModel):
    slug: str
    name: str
    logo_url: str | None
    description: str | None
    category: str
    available: bool
    connected: bool
    status: str | None
    display_name: str | None
    connected_at: datetime | None
    # Consent record: what the user allowed on the app's own consent screen.
    granted_scopes: list[str]
    permissions: list[str]
    consented_at: datetime | None


class ConnectorListOut(BaseModel):
    connectors: list[ConnectorOut]
    # True when Composio was unreachable and stored state is shown instead.
    degraded: bool


class ConnectStartOut(BaseModel):
    redirect_url: str


class ConnectCallbackIn(BaseModel):
    attempt_id: uuid.UUID
    # Echoed from Composio's redirect; only checked against our record, never trusted.
    connected_account_id: str | None = None


class ConnectCallbackOut(BaseModel):
    outcome: str  # connected | pending | failed
    connector: ConnectorOut
