"""The exact HubSpot actions that can be requested, and their payload rules.

Tool slugs and inputs were read from Composio's live HubSpot schemas (version 20261116_01).
HubSpot's note-to-contact association id, 202, comes from its Associations API guide.
A payload is what the user approves and what gets signed:

    {"contact": {id, email}, "arguments": <exact tool input>, ...what the user reviews}

Contact email is HubSpot's unique identifier, so the agent searches before creating. Deleting
and merging contacts are deliberately not available.
"""

import html
import re
from typing import Any

from cognitive_mesh.integrations.gmail import ADDRESS

TOOLKIT = "hubspot"
CREATE_ACTION = "crm.create_contact"
UPDATE_ACTION = "crm.update_contact"
NOTE_ACTION = "crm.create_note"
SEARCH_TOOL = "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA"
CREATE_TOOL = "HUBSPOT_CREATE_CONTACT"
UPDATE_TOOL = "HUBSPOT_UPDATE_CONTACT"
NOTE_TOOL = "HUBSPOT_CREATE_NOTE"
WRITE_SCOPES = ("crm.objects.contacts.write",)

CONTACT_FIELDS = (
    "firstname", "lastname", "email", "phone", "company", "jobtitle", "website", "city", "country",
)
NOTE_TO_CONTACT_TYPE = 202
MAX_FIELD_CHARS = 200
MAX_NOTE_CHARS = 5000

_EMAIL = re.compile(rf"^{ADDRESS}$")
_CONTACT_ID = re.compile(r"^[0-9]{1,32}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class InvalidCrmActionError(ValueError):
    pass


def _check_email(email: Any) -> None:
    if not isinstance(email, str) or email != email.lower() or not _EMAIL.match(email):
        raise InvalidCrmActionError("A valid lowercase contact email is required")


def _check_id(contact_id: Any) -> None:
    if not isinstance(contact_id, str) or not _CONTACT_ID.match(contact_id):
        raise InvalidCrmActionError("A valid HubSpot contact id is required")


def _check_properties(properties: Any) -> None:
    if not isinstance(properties, dict) or not properties:
        raise InvalidCrmActionError("At least one contact field is required")
    for name, value in properties.items():
        if name not in CONTACT_FIELDS:
            raise InvalidCrmActionError(f"The field {name} is not allowed")
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_CHARS:
            raise InvalidCrmActionError(f"The value for {name} is not valid")


def validate_create(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "properties"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, arguments = payload["contact"], payload["arguments"]
    if not isinstance(contact, dict) or contact.get("id") is not None:
        raise InvalidCrmActionError("A new contact cannot have an id")
    _check_email(contact.get("email"))
    _check_properties(arguments)
    if arguments != payload["properties"] or arguments.get("email") != contact["email"]:
        raise InvalidCrmActionError("The write must match the approved contact")


def validate_update(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "changes", "mode"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, arguments, changes = payload["contact"], payload["arguments"], payload["changes"]
    if not isinstance(contact, dict) or payload["mode"] not in ("fill", "change"):
        raise InvalidCrmActionError("Unexpected update")
    _check_id(contact.get("id"))
    _check_email(contact.get("email"))
    if not isinstance(arguments, dict) or set(arguments) != {"contactId", "properties"}:
        raise InvalidCrmActionError("Unexpected update fields")
    properties = arguments["properties"]
    _check_properties(properties)
    if arguments["contactId"] != contact["id"] or "email" in properties:
        raise InvalidCrmActionError("The update must match the approved contact")
    if not isinstance(changes, list) or {c.get("field"): c.get("value") for c in changes} != properties:
        raise InvalidCrmActionError("The write must match the approved changes")
    if payload["mode"] == "fill" and any(str(c.get("previous") or "").strip() for c in changes):
        raise InvalidCrmActionError("Automatic updates may only fill empty fields")


def validate_note(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "note"}:
        raise InvalidCrmActionError("Unexpected note fields")
    contact, arguments, note = payload["contact"], payload["arguments"], payload["note"]
    if not isinstance(contact, dict) or not isinstance(arguments, dict):
        raise InvalidCrmActionError("A contact is required")
    _check_id(contact.get("id"))
    _check_email(contact.get("email"))
    if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_CHARS:
        raise InvalidCrmActionError("The note must be text within the size limit")
    timestamp = arguments.get("hs_timestamp")
    if not isinstance(timestamp, str) or not _TIMESTAMP.match(timestamp):
        raise InvalidCrmActionError("A valid note time is required")
    expected = {
        "hs_note_body": html.escape(note),  # HubSpot renders note bodies as rich text
        "hs_timestamp": timestamp,
        "associations": [_note_association(contact["id"])],
    }
    if arguments != expected:
        raise InvalidCrmActionError("The write must match the approved note")


def _note_association(contact_id: str) -> dict[str, Any]:
    return {
        "to": {"id": contact_id},
        "types": [
            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_CONTACT_TYPE}
        ],
    }


def build_create_payload(properties: dict[str, str]) -> dict[str, Any]:
    payload = {
        "contact": {"id": None, "email": properties.get("email")},
        "arguments": dict(properties),
        "properties": dict(properties),
    }
    validate_create(payload)
    return payload


def build_update_payload(
    *, contact_id: str, email: str, changes: list[dict[str, str]], mode: str
) -> dict[str, Any]:
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": {
            "contactId": contact_id,
            "properties": {c["field"]: c["value"] for c in changes},
        },
        "changes": changes,
        "mode": mode,
    }
    validate_update(payload)
    return payload


def build_note_payload(*, contact_id: str, email: str, note: str, timestamp: str) -> dict[str, Any]:
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": {
            "hs_note_body": html.escape(note),
            "hs_timestamp": timestamp,
            "associations": [_note_association(contact_id)],
        },
        "note": note,
    }
    validate_note(payload)
    return payload


def search_arguments(*, email: str | None = None, query: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"limit": 5, "properties": list(CONTACT_FIELDS)}
    if email:
        arguments["filterGroups"] = [
            {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
        ]
    else:
        arguments["query"] = query or ""
    return arguments


def _unwrap(data: Any) -> Any:
    """Composio can nest the provider's response under `response_data`."""
    if isinstance(data, dict) and isinstance(data.get("response_data"), dict):
        return data["response_data"]
    return data


def contacts_from(data: Any) -> list[dict[str, Any]]:
    """Contacts as {id, <allowed fields>}. Anything else in the response is ignored."""
    body = _unwrap(data)
    rows = body.get("results") if isinstance(body, dict) else None
    contacts = []
    for row in rows or []:
        if not isinstance(row, dict) or not _CONTACT_ID.match(str(row.get("id") or "")):
            continue
        properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        contact = {name: str(properties.get(name) or "") for name in CONTACT_FIELDS}
        contacts.append({"id": str(row["id"]), **contact})
    return contacts


def result_id(data: Any) -> str | None:
    body = _unwrap(data)
    value = body.get("id") if isinstance(body, dict) else None
    text = str(value) if isinstance(value, str | int) else ""
    return text if _CONTACT_ID.match(text) else None


def no_url(_data: Any, _record_id: str | None) -> str | None:
    return None  # a record link needs the portal id, which these responses do not guarantee
