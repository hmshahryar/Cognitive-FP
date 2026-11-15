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
