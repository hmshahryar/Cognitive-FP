"""One adapter per CRM, so every CRM shares the same search, approval and audit flow.

To add a CRM: write `integrations/<crm>.py`, register an adapter here, then register its tools in
`capabilities/registry.py` and its actions in `integrations/actions.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cognitive_mesh.integrations import hubspot, salesforce, zoho


@dataclass(frozen=True)
class CrmExtras:
    account: dict[str, str] | None = None
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrmProvider:
    toolkit: str
    name: str
    create_action: str
    update_action: str
    note_action: str
    search_tool: str
    search_arguments: Callable[..., dict[str, Any]]
    contacts_from: Callable[..., list[dict[str, Any]]]
    build_create: Callable[[dict[str, str], CrmExtras], dict[str, Any]]
    build_update: Callable[..., dict[str, Any]]
    build_note: Callable[..., dict[str, Any]]
    requires_lastname: bool = False
    company_is_account: bool = False
    # (field, why it can't be saved), shown on the approval card.
    unsupported_fields: tuple[tuple[str, str], ...] = ()
    # Search may miss a brand-new record, so this app's recent approvals are checked too.
    search_can_lag: bool = False


def _hubspot_update(
    *, contact_id: str, email: str, changes: list[dict[str, str]], mode: str, extras: CrmExtras
) -> dict[str, Any]:
    del extras  # HubSpot stores every field directly on the contact
    return hubspot.build_update_payload(
        contact_id=contact_id, email=email, changes=changes, mode=mode
    )


def _hubspot_note(*, contact_id: str, email: str, note: str, timestamp: str) -> dict[str, Any]:
    return hubspot.build_note_payload(
        contact_id=contact_id, email=email, note=note, timestamp=timestamp
    )


def _hubspot_contacts(data: Any, *, complete: bool) -> list[dict[str, Any]]:
    del complete  # the exact email filter runs inside HubSpot's search
    return hubspot.contacts_from(data)


def _salesforce_update(
    *, contact_id: str, email: str, changes: list[dict[str, str]], mode: str, extras: CrmExtras
) -> dict[str, Any]:
    return salesforce.build_update_payload(
        contact_id=contact_id, email=email, changes=changes, mode=mode,
        account=extras.account, skipped=extras.skipped,
    )


def _salesforce_note(*, contact_id: str, email: str, note: str, timestamp: str) -> dict[str, Any]:
    del timestamp  # Salesforce records the note's creation time itself
    return salesforce.build_note_payload(contact_id=contact_id, email=email, note=note)


def _zoho_update(
    *, contact_id: str, email: str, changes: list[dict[str, str]], mode: str, extras: CrmExtras
) -> dict[str, Any]:
    return zoho.build_update_payload(
        contact_id=contact_id, email=email, changes=changes, mode=mode, skipped=extras.skipped
    )


def _zoho_note(*, contact_id: str, email: str, note: str, timestamp: str) -> dict[str, Any]:
    del timestamp  # Zoho records the note's creation time itself
    return zoho.build_note_payload(contact_id=contact_id, email=email, note=note)


PROVIDERS: dict[str, CrmProvider] = {
    hubspot.TOOLKIT: CrmProvider(
        toolkit=hubspot.TOOLKIT,
        name="HubSpot",
        create_action=hubspot.CREATE_ACTION,
        update_action=hubspot.UPDATE_ACTION,
        note_action=hubspot.NOTE_ACTION,
        search_tool=hubspot.SEARCH_TOOL,
        search_arguments=hubspot.search_arguments,
        contacts_from=_hubspot_contacts,
        build_create=lambda properties, _extras: hubspot.build_create_payload(properties),
        build_update=_hubspot_update,
        build_note=_hubspot_note,
    ),
    salesforce.TOOLKIT: CrmProvider(
        toolkit=salesforce.TOOLKIT,
        name="Salesforce",
        create_action=salesforce.CREATE_ACTION,
        update_action=salesforce.UPDATE_ACTION,
        note_action=salesforce.NOTE_ACTION,
        search_tool=salesforce.SEARCH_TOOL,
        search_arguments=salesforce.search_arguments,
        contacts_from=salesforce.contacts_from,
        build_create=lambda properties, extras: salesforce.build_create_payload(
            properties, extras.account, extras.skipped
        ),
        build_update=_salesforce_update,
        build_note=_salesforce_note,
        requires_lastname=True,
        company_is_account=True,
        unsupported_fields=(("website", "Salesforce contacts have no standard field for this."),),
    ),
    zoho.TOOLKIT: CrmProvider(
        toolkit=zoho.TOOLKIT,
        name="Zoho CRM",
        create_action=zoho.CREATE_ACTION,
        update_action=zoho.UPDATE_ACTION,
        note_action=zoho.NOTE_ACTION,
        search_tool=zoho.SEARCH_TOOL,
        search_arguments=zoho.search_arguments,
        contacts_from=zoho.contacts_from,
        build_create=lambda properties, extras: zoho.build_create_payload(
            properties, extras.skipped
        ),
        build_update=_zoho_update,
        build_note=_zoho_note,
        requires_lastname=True,
        unsupported_fields=(
            ("company", "Zoho links companies to Accounts, which this connection can't access."),
            ("website", "Zoho contacts have no standard field for this."),
        ),
        search_can_lag=True,
    ),
}
