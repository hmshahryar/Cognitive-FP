"""The exact Zoho CRM contact actions that can be requested, and their payload rules.

Tool slugs and inputs were read from Composio's live Zoho schemas (version 20261119_00), and
scopes and response shapes from Zoho's CRM API v8 docs. Zoho's search index can lag behind a new
record, so the CRM flow also checks this app's recent approvals. Writes skip Zoho automations,
because a workflow could send an email the user never approved. Linked Accounts are outside this
connection's permissions. Deleting and merging are deliberately not available.
"""

import re
from typing import Any

from cognitive_mesh.integrations.gmail import ADDRESS
from cognitive_mesh.integrations.hubspot import (
    MAX_FIELD_CHARS,
    MAX_NOTE_CHARS,
    InvalidCrmActionError,
)

TOOLKIT = "zoho"
MODULE = "Contacts"
CREATE_ACTION = "crm.zoho.create_contact"
UPDATE_ACTION = "crm.zoho.update_contact"
NOTE_ACTION = "crm.zoho.create_note"
SEARCH_TOOL = "ZOHO_SEARCH_CONTACTS"
CREATE_TOOL = "ZOHO_CREATE_CONTACT"
UPDATE_TOOL = "ZOHO_UPDATE_ZOHO_RECORD"
NOTE_TOOL = "ZOHO_CREATE_NOTE"
CREATE_SCOPES = ("ZohoCRM.modules.contacts.CREATE",)
UPDATE_SCOPES = ("ZohoCRM.modules.contacts.UPDATE",)
NOTE_SCOPES = ("ZohoCRM.modules.notes.CREATE",)
NOTE_TITLE = "Note from Cognitive Mesh"
SEARCH_LIMIT = 200

_CREATE_ARGUMENTS = {
    "firstname": "first_name", "lastname": "last_name", "email": "email", "phone": "phone",
    "jobtitle": "title", "city": "mailing_city", "country": "mailing_country",
}
_API_FIELDS = {
    "firstname": "First_Name", "lastname": "Last_Name", "email": "Email", "phone": "Phone",
    "jobtitle": "Title", "city": "Mailing_City", "country": "Mailing_Country",
}
_SEARCH_FIELDS = (
    "First_Name,Last_Name,Email,Secondary_Email,Phone,Title,Account_Name,Mailing_City,"
    "Mailing_Country"
)
_EMAIL = re.compile(rf"^{ADDRESS}$")
_RECORD_ID = re.compile(r"^[0-9]{1,32}$")


def _check_email(email: Any) -> None:
    if not isinstance(email, str) or email != email.lower() or not _EMAIL.match(email):
        raise InvalidCrmActionError("A valid lowercase contact email is required")


def _check_id(contact_id: Any) -> None:
    if not isinstance(contact_id, str) or not _RECORD_ID.match(contact_id):
        raise InvalidCrmActionError("A valid Zoho contact id is required")


def _check_properties(properties: Any) -> None:
    if not isinstance(properties, dict) or not properties:
        raise InvalidCrmActionError("At least one contact field is required")
    for name, value in properties.items():
        if name not in _API_FIELDS:
            raise InvalidCrmActionError(f"The field {name} is not allowed")
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_CHARS:
            raise InvalidCrmActionError(f"The value for {name} is not valid")


def _check_skipped(skipped: Any) -> None:
    if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
        raise InvalidCrmActionError("Unexpected skipped fields")


def _create_arguments(properties: dict[str, str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {_CREATE_ARGUMENTS[k]: v for k, v in properties.items()}
    arguments["trigger"] = []
    return arguments


def _update_arguments(contact_id: str, properties: dict[str, str]) -> dict[str, Any]:
    record = {"id": contact_id, **{_API_FIELDS[k]: v for k, v in properties.items()}}
    return {"module_api_name": MODULE, "data": [record], "trigger": []}


def validate_create(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "properties", "skipped"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, properties = payload["contact"], payload["properties"]
    if not isinstance(contact, dict) or contact.get("id") is not None:
        raise InvalidCrmActionError("A new contact cannot have an id")
    _check_email(contact.get("email"))
    _check_properties(properties)
    _check_skipped(payload["skipped"])
    if properties.get("email") != contact["email"] or not properties.get("lastname"):
        raise InvalidCrmActionError("A new contact needs its approved email and a last name")
    if payload["arguments"] != _create_arguments(properties):
        raise InvalidCrmActionError("The write must match the approved contact")


def validate_update(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "changes", "mode", "skipped"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, changes = payload["contact"], payload["changes"]
    if not isinstance(contact, dict) or payload["mode"] not in ("fill", "change"):
        raise InvalidCrmActionError("Unexpected update")
    _check_id(contact.get("id"))
    _check_email(contact.get("email"))
    _check_skipped(payload["skipped"])
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        raise InvalidCrmActionError("Unexpected changes")
    properties = {c.get("field"): c.get("value") for c in changes}
    _check_properties(properties)
    if "email" in properties:
        raise InvalidCrmActionError("The update must match the approved contact")
    if payload["arguments"] != _update_arguments(contact["id"], properties):  # type: ignore[arg-type]
        raise InvalidCrmActionError("The write must match the approved changes")
    if payload["mode"] == "fill" and any(str(c.get("previous") or "").strip() for c in changes):
        raise InvalidCrmActionError("Automatic updates may only fill empty fields")


def validate_note(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "note"}:
        raise InvalidCrmActionError("Unexpected note fields")
    contact, note = payload["contact"], payload["note"]
    if not isinstance(contact, dict):
        raise InvalidCrmActionError("A contact is required")
    _check_id(contact.get("id"))
    _check_email(contact.get("email"))
    if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_CHARS:
        raise InvalidCrmActionError("The note must be text within the size limit")
    expected = {
        "module_api_name": MODULE, "record_id": contact["id"],
        "Note_Title": NOTE_TITLE, "Note_Content": note,
    }
    if payload["arguments"] != expected:
        raise InvalidCrmActionError("The write must match the approved note")


def build_create_payload(properties: dict[str, str], skipped: tuple[str, ...]) -> dict[str, Any]:
    payload = {
        "contact": {"id": None, "email": properties.get("email")},
        "arguments": _create_arguments(properties),
        "properties": dict(properties),
        "skipped": list(skipped),
    }
    validate_create(payload)
    return payload


def build_update_payload(
    *,
    contact_id: str,
    email: str,
    changes: list[dict[str, str]],
    mode: str,
    skipped: tuple[str, ...],
) -> dict[str, Any]:
    properties = {c["field"]: c["value"] for c in changes}
    _check_properties(properties)
    if "email" in properties:
        raise InvalidCrmActionError("The update must match the approved contact")
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": _update_arguments(contact_id, properties),
        "changes": changes,
        "mode": mode,
        "skipped": list(skipped),
    }
    validate_update(payload)
    return payload


def build_note_payload(*, contact_id: str, email: str, note: str) -> dict[str, Any]:
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": {
            "module_api_name": MODULE, "record_id": contact_id,
            "Note_Title": NOTE_TITLE, "Note_Content": note,
        },
        "note": note,
    }
    validate_note(payload)
    return payload


def search_arguments(*, email: str | None = None, query: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"per_page": SEARCH_LIMIT, "fields": _SEARCH_FIELDS}
    if email:
        arguments["email"] = email
    else:
        arguments["word"] = query or ""
    return arguments


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("response_data"), dict):
        return data["response_data"]
    return data


def contacts_from(data: Any, *, complete: bool = True) -> list[dict[str, Any]]:
    """Fails closed: an unreadable result must never look like "no match"."""
    body = _unwrap(data)
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InvalidCrmActionError("Zoho returned a search result I couldn't read")
    info = body.get("info")
    if complete and isinstance(info, dict) and info.get("more_records") is True:
        raise InvalidCrmActionError("Zoho returned too many matches to check safely")
    contacts = []
    for row in rows:
        contact_id = str(row.get("id") or "")
        if not _RECORD_ID.match(contact_id):
            raise InvalidCrmActionError("Zoho returned a contact I couldn't read")
        account = row.get("Account_Name") if isinstance(row.get("Account_Name"), dict) else {}
        secondary = str(row.get("Secondary_Email") or "")
        contacts.append(
            {
                "id": contact_id,
                "firstname": str(row.get("First_Name") or ""),
                "lastname": str(row.get("Last_Name") or ""),
                "email": str(row.get("Email") or ""),
                "other_emails": (secondary,) if secondary else (),
                "phone": str(row.get("Phone") or ""),
                "company": str(account.get("name") or ""),
                "jobtitle": str(row.get("Title") or ""),
                "website": "",
                "city": str(row.get("Mailing_City") or ""),
                "country": str(row.get("Mailing_Country") or ""),
            }
        )
    return contacts


def result_id(data: Any) -> str | None:
    """The record id only when Zoho reports success for exactly this one record."""
    body = _unwrap(data)
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    value = details.get("id")
    if row.get("status") != "success" or not isinstance(value, str):
        return None
    return value if _RECORD_ID.match(value) else None


def no_url(_data: Any, _record_id: str | None) -> str | None:
    return None  # the record link depends on the user's Zoho organization and region
