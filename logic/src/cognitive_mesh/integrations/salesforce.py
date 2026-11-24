"""The exact Salesforce contact actions that can be requested, and their payload rules.

Tool slugs and inputs were read from Composio's live Salesforce schemas (version 20261119_00).
Salesforce does not guarantee unique contact emails, so a search that cannot be read completely
stops the command. A contact's company is a linked Account, so it is resolved to one exact Account
and never created here. Deleting and merging are deliberately not available.
"""

import re
from typing import Any

from cognitive_mesh.integrations.gmail import ADDRESS
from cognitive_mesh.integrations.hubspot import (
    MAX_FIELD_CHARS,
    MAX_NOTE_CHARS,
    InvalidCrmActionError,
)

TOOLKIT = "salesforce"
CREATE_ACTION = "crm.salesforce.create_contact"
UPDATE_ACTION = "crm.salesforce.update_contact"
NOTE_ACTION = "crm.salesforce.create_note"
SEARCH_TOOL = "SALESFORCE_SEARCH_CONTACTS"
ACCOUNT_SEARCH_TOOL = "SALESFORCE_SEARCH_ACCOUNTS"
CREATE_TOOL = "SALESFORCE_CREATE_CONTACT"
UPDATE_TOOL = "SALESFORCE_UPDATE_CONTACT"
NOTE_TOOL = "SALESFORCE_CREATE_NOTE"
WRITE_SCOPES = ("api",)
NOTE_TITLE = "Note from Cognitive Mesh"
SEARCH_LIMIT = 200

FIELD_ARGUMENTS = {
    "firstname": "first_name", "lastname": "last_name", "email": "email", "phone": "phone",
    "jobtitle": "title", "city": "mailing_city", "country": "mailing_country",
}
CONTACT_FIELDS = (*FIELD_ARGUMENTS, "company")
_SEARCH_FIELDS = (
    "Id,FirstName,LastName,Email,Phone,Title,AccountId,Account.Name,MailingCity,MailingCountry"
)
_EMAIL = re.compile(rf"^{ADDRESS}$")
_CONTACT_ID = re.compile(r"^003[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$")
_ACCOUNT_ID = re.compile(r"^001[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def _check_email(email: Any) -> None:
    if not isinstance(email, str) or email != email.lower() or not _EMAIL.match(email):
        raise InvalidCrmActionError("A valid lowercase contact email is required")


def _check_contact_id(contact_id: Any) -> None:
    if not isinstance(contact_id, str) or not _CONTACT_ID.match(contact_id):
        raise InvalidCrmActionError("A valid Salesforce contact id is required")


def _check_properties(properties: Any) -> None:
    if not isinstance(properties, dict) or not properties:
        raise InvalidCrmActionError("At least one contact field is required")
    for name, value in properties.items():
        if name not in CONTACT_FIELDS:
            raise InvalidCrmActionError(f"The field {name} is not allowed")
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_CHARS:
            raise InvalidCrmActionError(f"The value for {name} is not valid")


def _check_extras(properties: dict[str, str], account: Any, skipped: Any) -> None:
    if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
        raise InvalidCrmActionError("Unexpected skipped fields")
    if account is None:
        if "company" in properties:
            raise InvalidCrmActionError("A company must be linked to one exact Account")
        return
    if not isinstance(account, dict) or set(account) != {"id", "name"}:
        raise InvalidCrmActionError("Unexpected Account")
    if not isinstance(account["id"], str) or not _ACCOUNT_ID.match(account["id"]):
        raise InvalidCrmActionError("A valid Salesforce Account id is required")
    if properties.get("company") != account["name"]:
        raise InvalidCrmActionError("The Account must match the approved company")


def _arguments(properties: dict[str, str], account: dict[str, str] | None) -> dict[str, str]:
    arguments = {
        FIELD_ARGUMENTS[name]: value for name, value in properties.items() if name != "company"
    }
    if account is not None:
        arguments["account_id"] = account["id"]
    return arguments


def validate_create(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "properties", "account", "skipped"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, properties = payload["contact"], payload["properties"]
    if not isinstance(contact, dict) or contact.get("id") is not None:
        raise InvalidCrmActionError("A new contact cannot have an id")
    _check_email(contact.get("email"))
    _check_properties(properties)
    _check_extras(properties, payload["account"], payload["skipped"])
    if properties.get("email") != contact["email"] or not properties.get("lastname"):
        raise InvalidCrmActionError("A new contact needs its approved email and a last name")
    if payload["arguments"] != _arguments(properties, payload["account"]):
        raise InvalidCrmActionError("The write must match the approved contact")


def validate_update(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "changes", "mode", "account", "skipped"}:
        raise InvalidCrmActionError("Unexpected contact fields")
    contact, changes = payload["contact"], payload["changes"]
    if not isinstance(contact, dict) or payload["mode"] not in ("fill", "change"):
        raise InvalidCrmActionError("Unexpected update")
    _check_contact_id(contact.get("id"))
    _check_email(contact.get("email"))
    if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
        raise InvalidCrmActionError("Unexpected changes")
    properties = {c.get("field"): c.get("value") for c in changes}
    _check_properties(properties)
    _check_extras(properties, payload["account"], payload["skipped"])  # type: ignore[arg-type]
    if "email" in properties:
        raise InvalidCrmActionError("The update must match the approved contact")
    expected = {"contact_id": contact["id"], **_arguments(properties, payload["account"])}  # type: ignore[arg-type]
    if payload["arguments"] != expected:
        raise InvalidCrmActionError("The write must match the approved changes")
    if payload["mode"] == "fill" and any(str(c.get("previous") or "").strip() for c in changes):
        raise InvalidCrmActionError("Automatic updates may only fill empty fields")


def validate_note(payload: dict[str, Any]) -> None:
    if set(payload) != {"contact", "arguments", "note"}:
        raise InvalidCrmActionError("Unexpected note fields")
    contact, note = payload["contact"], payload["note"]
    if not isinstance(contact, dict):
        raise InvalidCrmActionError("A contact is required")
    _check_contact_id(contact.get("id"))
    _check_email(contact.get("email"))
    if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_CHARS:
        raise InvalidCrmActionError("The note must be text within the size limit")
    expected = {"parent_id": contact["id"], "title": NOTE_TITLE, "body": note}
    if payload["arguments"] != expected:
        raise InvalidCrmActionError("The write must match the approved note")


def build_create_payload(
    properties: dict[str, str], account: dict[str, str] | None, skipped: tuple[str, ...]
) -> dict[str, Any]:
    payload = {
        "contact": {"id": None, "email": properties.get("email")},
        "arguments": _arguments(properties, account),
        "properties": dict(properties),
        "account": account,
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
    account: dict[str, str] | None,
    skipped: tuple[str, ...],
) -> dict[str, Any]:
    properties = {c["field"]: c["value"] for c in changes}
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": {"contact_id": contact_id, **_arguments(properties, account)},
        "changes": changes,
        "mode": mode,
        "account": account,
        "skipped": list(skipped),
    }
    validate_update(payload)
    return payload


def build_note_payload(*, contact_id: str, email: str, note: str) -> dict[str, Any]:
    payload = {
        "contact": {"id": contact_id, "email": email},
        "arguments": {"parent_id": contact_id, "title": NOTE_TITLE, "body": note},
        "note": note,
    }
    validate_note(payload)
    return payload


def search_arguments(*, email: str | None = None, query: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"limit": SEARCH_LIMIT, "fields": _SEARCH_FIELDS}
    if email:
        arguments["email"] = email
    else:
        arguments["name"] = query or ""
    return arguments


def account_search_arguments(name: str) -> dict[str, Any]:
    return {"name": name, "limit": SEARCH_LIMIT, "fields": "Id,Name"}


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("response_data"), dict):
        return data["response_data"]
    return data


def _records(data: Any, *, complete: bool) -> list[dict[str, Any]]:
    """Fails closed: an unreadable result must never look like "no match"."""
    body = _unwrap(data)
    rows = body.get("records") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise InvalidCrmActionError("Salesforce returned a search result I couldn't read")
    total = body.get("totalSize")
    if complete and isinstance(total, int) and total > len(rows):
        raise InvalidCrmActionError("Salesforce returned too many matches to check safely")
    return rows


def contacts_from(data: Any, *, complete: bool = True) -> list[dict[str, Any]]:
    contacts = []
    for row in _records(data, complete=complete):
        contact_id = str(row.get("Id") or "")
        if not _CONTACT_ID.match(contact_id):
            raise InvalidCrmActionError("Salesforce returned a contact I couldn't read")
        account = row.get("Account") if isinstance(row.get("Account"), dict) else {}
        contacts.append(
            {
                "id": contact_id,
                "firstname": str(row.get("FirstName") or ""),
                "lastname": str(row.get("LastName") or ""),
                "email": str(row.get("Email") or ""),
                "phone": str(row.get("Phone") or ""),
                "company": str(account.get("Name") or ""),
                "jobtitle": str(row.get("Title") or ""),
                "website": "",
                "city": str(row.get("MailingCity") or ""),
                "country": str(row.get("MailingCountry") or ""),
            }
        )
    return contacts


def accounts_from(data: Any) -> list[dict[str, str]]:
    accounts = []
    for row in _records(data, complete=True):
        account_id, name = str(row.get("Id") or ""), str(row.get("Name") or "")
        if not _ACCOUNT_ID.match(account_id):
            raise InvalidCrmActionError("Salesforce returned an Account I couldn't read")
        accounts.append({"id": account_id, "name": name})
    return accounts


def result_id(data: Any) -> str | None:
    body = _unwrap(data)
    value = (body.get("id") or body.get("Id")) if isinstance(body, dict) else None
    return value if isinstance(value, str) and _RECORD_ID.match(value) else None


def no_url(_data: Any, _record_id: str | None) -> str | None:
    return None  # the org's instance URL is not guaranteed in these responses
