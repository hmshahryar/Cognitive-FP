"""Read a CRM and prepare one exact contact change for the user to approve.

Contact content is untrusted and never reaches a model here. Nothing in this module writes.
If the duplicate check cannot run, the command stops, because creating blindly is unsafe.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cognitive_mesh.integrations import salesforce
from cognitive_mesh.integrations.crm_providers import CrmExtras, CrmProvider
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.workflows.crm_commands import CrmCommand

_LABELS = {
    "firstname": "First name", "lastname": "Last name", "email": "Email", "phone": "Phone",
    "company": "Company", "jobtitle": "Job title", "website": "Website", "city": "City",
    "country": "Country",
}


class CrmStop(Exception):
    """The command can't continue. `message` tells the user what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedCrmAction:
    action_type: str
    payload: dict[str, Any]
    text: str


def _read_failed(provider: CrmProvider) -> str:
    return (
        f"I couldn't read {provider.name} just now, so nothing was changed. "
        f"Press Retry, or reconnect {provider.name} on the Connectors page."
    )


async def _search(
    gateway: IntegrationGateway,
    user_id: str,
    provider: CrmProvider,
    *,
    email: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    try:
        data = await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=provider.toolkit, tool_slug=provider.search_tool,
            arguments=provider.search_arguments(email=email, query=query),
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise CrmStop(_read_failed(provider)) from exc
    contacts = provider.contacts_from(data, complete=bool(email))
    if email:  # only an exact address can identify the same person
        wanted = email.casefold()
        return [
            c for c in contacts
            if wanted in {e.strip().casefold() for e in (c["email"], *c.get("other_emails", ()))}
        ]
    return contacts


async def _resolve_fields(
    gateway: IntegrationGateway, user_id: str, provider: CrmProvider, fields: dict[str, str]
) -> tuple[dict[str, str], CrmExtras]:
    """Keep what this CRM can store. Anything left out is shown on the approval card."""
    kept = dict(fields)
    skipped = [
        f"{_LABELS[field]} ({kept.pop(field)}): {reason}"
        for field, reason in provider.unsupported_fields
        if field in kept
    ]
    account = None
    if provider.company_is_account and "company" in kept:
        name = kept["company"]
        try:
            data = await gateway.execute_read_only(
                user_id=user_id, toolkit_slug=provider.toolkit,
                tool_slug=salesforce.ACCOUNT_SEARCH_TOOL,
                arguments=salesforce.account_search_arguments(name),
            )
        except (IntegrationUnavailableError, ToolExecutionError) as exc:
            raise CrmStop(_read_failed(provider)) from exc
        matches = [
            a for a in salesforce.accounts_from(data)
            if a["name"].strip().casefold() == name.strip().casefold()
        ]
        if len(matches) > 1:
            raise CrmStop(
                f"More than one {provider.name} Account is named {name}. "
                "Fix the duplicate there first."
            )
        if matches:
            account = matches[0]
            kept["company"] = account["name"]
        else:
            del kept["company"]
            skipped.append(
                f"Company ({name}): no {provider.name} Account has this exact name, so it won't "
                "be linked."
            )
    return kept, CrmExtras(account=account, skipped=tuple(skipped))


def _describe(contact: dict[str, Any]) -> str:
    name = " ".join(p for p in (contact["firstname"], contact["lastname"]) if p) or "(no name)"
    details = [contact["email"] or "(no email)", contact["company"], contact["phone"]]
    return f"- {name} · " + " · ".join(d for d in details if d)


async def answer_find(
    gateway: IntegrationGateway, user_id: str, provider: CrmProvider, command: CrmCommand
) -> str:
    contacts = await _search(gateway, user_id, provider, email=command.email, query=command.query)
    if not contacts:
        return f"I couldn't find a matching contact in {provider.name}."
    lines = "\n".join(_describe(c) for c in contacts[:5])
    return f"Matching {provider.name} contacts:\n{lines}"


async def _one_contact(
    gateway: IntegrationGateway, user_id: str, provider: CrmProvider, email: str
) -> dict[str, Any] | None:
    matches = await _search(gateway, user_id, provider, email=email)
    if len(matches) > 1:
        raise CrmStop(
            f"More than one {provider.name} contact has this email. "
            f"Fix the duplicate in {provider.name} first."
        )
    return matches[0] if matches else None


async def _prepare_create(
    gateway: IntegrationGateway,
    user_id: str,
    provider: CrmProvider,
    command: CrmCommand,
    recent_emails: frozenset[str],
) -> PreparedCrmAction:
    email = command.email or ""
    wanted = dict(command.fields)
    existing = await _one_contact(gateway, user_id, provider, email)
    if existing is None:
        if email.casefold() in recent_emails:
            raise CrmStop(
                f"{email} was just added or is waiting for your approval, so I didn't create a "
                f"duplicate. {provider.name} search can take a few minutes to show new contacts."
            )
        if provider.requires_lastname and not wanted.get("lastname"):
            raise CrmStop(
                f"{provider.name} requires a last name. Send the command again with the full name."
            )
        fields, extras = await _resolve_fields(gateway, user_id, provider, wanted)
        payload = provider.build_create(fields, extras)
        return PreparedCrmAction(
            provider.create_action, payload, f"Create {provider.name} contact: {email}"
        )
    # Existing sales data is never overwritten by a create command.
    empty = {
        field: value
        for field, value in wanted.items()
        if field != "email" and not existing[field].strip()
    }
    fields, extras = await _resolve_fields(gateway, user_id, provider, empty)
    changes = [{"field": f, "previous": "", "value": value} for f, value in fields.items()]
    kept = [
        _LABELS[field]
        for field, value in wanted.items()
        if field != "email" and existing[field].strip() and existing[field].strip() != value
    ]
    if not changes:
        extra = f" I kept its current {', '.join(kept)}." if kept else ""
        skipped = "".join(f" {item}" for item in extras.skipped)
        raise CrmStop(
            f"{email} is already in {provider.name}, so I didn't create a duplicate.{extra}{skipped}"
        )
    payload = provider.build_update(
        contact_id=existing["id"], email=email, changes=changes, mode="fill", extras=extras
    )
    return PreparedCrmAction(provider.update_action, payload, f"Fill empty fields for {email}")


async def _prepare_update(
    gateway: IntegrationGateway, user_id: str, provider: CrmProvider, command: CrmCommand
) -> PreparedCrmAction:
    email = command.email or ""
    existing = await _one_contact(gateway, user_id, provider, email)
    if existing is None:
        raise CrmStop(f"I couldn't find that contact in {provider.name}, so nothing was changed.")
    fields, extras = await _resolve_fields(gateway, user_id, provider, dict(command.fields))
    changes = [
        {"field": field, "previous": existing[field], "value": value}
        for field, value in fields.items()
        if existing[field].strip() != value
    ]
    if not changes:
        if extras.skipped:
            raise CrmStop("Nothing was changed. " + " ".join(extras.skipped))
        raise CrmStop("That contact already has those details, so nothing needs to change.")
    if extras.account is not None and not any(c["field"] == "company" for c in changes):
        extras = CrmExtras(account=None, skipped=extras.skipped)  # already linked to this Account
    payload = provider.build_update(
        contact_id=existing["id"], email=email, changes=changes, mode="change", extras=extras
    )
    return PreparedCrmAction(
        provider.update_action, payload, f"Update {provider.name} contact: {email}"
    )


async def _prepare_note(
    gateway: IntegrationGateway,
    user_id: str,
    provider: CrmProvider,
    command: CrmCommand,
    now: datetime | None,
) -> PreparedCrmAction:
    email = command.email or ""
    existing = await _one_contact(gateway, user_id, provider, email)
    if existing is None:
        raise CrmStop(f"I couldn't find that contact in {provider.name}, so no note was added.")
    moment = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = provider.build_note(
        contact_id=existing["id"], email=email, note=command.note or "", timestamp=moment
    )
    return PreparedCrmAction(provider.note_action, payload, f"Add a note to {email}")


async def prepare_change(
    gateway: IntegrationGateway,
    user_id: str,
    provider: CrmProvider,
    command: CrmCommand,
    now: datetime | None = None,
    recent_emails: frozenset[str] = frozenset(),
) -> PreparedCrmAction:
    if command.kind == "create":
        return await _prepare_create(gateway, user_id, provider, command, recent_emails)
    if command.kind == "update":
        return await _prepare_update(gateway, user_id, provider, command)
    return await _prepare_note(gateway, user_id, provider, command, now)
