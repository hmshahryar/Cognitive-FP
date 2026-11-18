from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from cognitive_mesh.auth import get_app_settings
from cognitive_mesh.config import Settings
from cognitive_mesh.deps import CurrentAccountDep, SessionDep
from cognitive_mesh.integrations.catalog import get_connector
from cognitive_mesh.integrations.gateway import IntegrationGateway, IntegrationUnavailableError
from cognitive_mesh.schemas import (
    ConnectCallbackIn,
    ConnectCallbackOut,
    ConnectorListOut,
    ConnectorOut,
    ConnectStartOut,
)
from cognitive_mesh.services import connectors as svc

router = APIRouter(prefix="/connectors", tags=["connectors"])


def get_gateway(request: Request) -> IntegrationGateway:
    return request.app.state.integration_gateway


GatewayDep = Annotated[IntegrationGateway, Depends(get_gateway)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]

_ERRORS: dict[type[Exception], tuple[int, str]] = {
    svc.ConnectorNotFoundError: (status.HTTP_404_NOT_FOUND, "Unknown connector"),
    svc.ConnectorUnavailableError: (status.HTTP_409_CONFLICT, "This connector is coming soon"),
    svc.AttemptNotFoundError: (status.HTTP_404_NOT_FOUND, "Connection attempt not found"),
    svc.AttemptExpiredError: (status.HTTP_410_GONE, "Connection attempt expired; try again"),
    svc.CallbackVerificationError: (
        status.HTTP_400_BAD_REQUEST,
        "Could not verify this connection",
    ),
    IntegrationUnavailableError: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The integration service is unavailable; try again shortly",
    ),
}


def _http_error(exc: Exception) -> HTTPException:
    code, detail = _ERRORS[type(exc)]
    return HTTPException(code, detail)


def _permissions(view: svc.ConnectorView) -> list[str]:
    spec = get_connector(view.slug)
    return spec.describe_scopes(view.granted_scopes) if spec else list(view.granted_scopes)


def _out(view: svc.ConnectorView) -> ConnectorOut:
    return ConnectorOut(
        slug=view.slug,
        name=view.name,
        logo_url=view.logo_url,
        description=view.description,
        category=view.category,
        available=view.available,
        connected=view.connected,
        status=view.status.value if view.status else None,
        display_name=view.display_name,
        connected_at=view.connected_at,
        granted_scopes=list(view.granted_scopes),
        permissions=_permissions(view),
        consented_at=view.consented_at,
    )


@router.get("", response_model=ConnectorListOut)
async def list_connectors(
    account: CurrentAccountDep, session: SessionDep, gateway: GatewayDep
) -> ConnectorListOut:
    views, degraded = await svc.list_connectors(session, account, gateway)
    return ConnectorListOut(connectors=[_out(v) for v in views], degraded=degraded)


@router.post("/{slug}/connect", response_model=ConnectStartOut)
async def start_connection(
    slug: str,
    account: CurrentAccountDep,
    session: SessionDep,
    gateway: GatewayDep,
    settings: SettingsDep,
) -> ConnectStartOut:
    try:
        url = await svc.start_connection(
            session,
            account,
            gateway,
            slug=slug,
            frontend_url=settings.frontend_url,
            ttl_minutes=settings.connection_attempt_ttl_minutes,
        )
    except (svc.ConnectorError, IntegrationUnavailableError) as exc:
        raise _http_error(exc) from exc
    return ConnectStartOut(redirect_url=url)


@router.post("/callback", response_model=ConnectCallbackOut)
async def complete_connection(
    body: ConnectCallbackIn,
    account: CurrentAccountDep,
    session: SessionDep,
    gateway: GatewayDep,
) -> ConnectCallbackOut:
    try:
        result = await svc.complete_connection(
            session,
            account,
            gateway,
            attempt_id=body.attempt_id,
            reported_account_id=body.connected_account_id,
        )
    except (svc.ConnectorError, IntegrationUnavailableError) as exc:
        raise _http_error(exc) from exc
    return ConnectCallbackOut(outcome=result.outcome, connector=_out(result.connector))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    slug: str, account: CurrentAccountDep, session: SessionDep, gateway: GatewayDep
) -> None:
    try:
        await svc.disconnect(session, account, gateway, slug=slug)
    except (svc.ConnectorError, IntegrationUnavailableError) as exc:
        raise _http_error(exc) from exc
