"""The exact Gmail actions that can be requested, and their payload rules.

Tool slugs, inputs and scopes were verified against Composio's Gmail toolkit. A payload is
what the user approves and what gets signed:

    {"account": <mailbox>, "arguments": <exact tool input>, "message": <target snapshot>}

"Delete" uses GMAIL_MOVE_TO_TRASH, which is recoverable. Permanent delete is not offered.
Signatures are off so the sent text is exactly the approved text.
"""

import re
from typing import Any

TOOLKIT = "gmail"
SEND_ACTION = "gmail.send_email"
FORWARD_ACTION = "gmail.forward_email"
TRASH_ACTION = "gmail.trash_email"
SEND_TOOL = "GMAIL_SEND_EMAIL"
FORWARD_TOOL = "GMAIL_FORWARD_MESSAGE"
TRASH_TOOL = "GMAIL_MOVE_TO_TRASH"
PROFILE_TOOL = "GMAIL_GET_PROFILE"
SEARCH_TOOL = "GMAIL_FETCH_EMAILS"

SCOPE_FULL = "https://mail.google.com/"
SCOPE_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SEND_SCOPES = (SCOPE_FULL, SCOPE_MODIFY, SCOPE_COMPOSE, SCOPE_SEND)
TRASH_SCOPES = (SCOPE_FULL, SCOPE_MODIFY)

ADDRESS = r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
_ADDRESS = re.compile(rf"^{ADDRESS}$")
_MESSAGE_ID = re.compile(r"^[0-9a-fA-F]{6,32}$")
MAX_RECIPIENTS = 20
MAX_BODY_CHARS = 50_000
MAX_SUBJECT_CHARS = 500
PREVIEW_CHARS = 400

_SEND_KEYS = {
    "recipient_email", "extra_recipients", "cc", "bcc", "subject", "body", "is_html",
    "include_signature",
}
_FORWARD_REQUIRED = {"message_id", "recipients", "include_signature"}
_MESSAGE_KEYS = {"id", "sender", "subject", "date", "preview"}


class InvalidEmailActionError(ValueError):
    pass


def _check_addresses(addresses: list[str]) -> None:
    if len(addresses) > MAX_RECIPIENTS:
        raise InvalidEmailActionError(f"At most {MAX_RECIPIENTS} recipients are allowed")
    for address in addresses:
        if not isinstance(address, str) or not _ADDRESS.match(address):
            raise InvalidEmailActionError("Every recipient must be a valid email address")


def _check_envelope(payload: dict[str, Any], expected: set[str]) -> dict[str, Any]:
    if set(payload) != expected:
        raise InvalidEmailActionError(f"Payload must have exactly {sorted(expected)}")
    if not isinstance(payload["account"], str) or not _ADDRESS.match(payload["account"]):
        raise InvalidEmailActionError("The Gmail account is required")
    arguments = payload["arguments"]
    if not isinstance(arguments, dict):
        raise InvalidEmailActionError("Arguments are required")
    return arguments


def _check_target(payload: dict[str, Any], message_id: Any) -> None:
    target = payload["message"]
    if not isinstance(target, dict) or set(target) != _MESSAGE_KEYS:
        raise InvalidEmailActionError("The target email snapshot is required")
    if not isinstance(message_id, str) or not _MESSAGE_ID.match(message_id):
        raise InvalidEmailActionError("A valid Gmail message id is required")
    if target["id"] != message_id:
        raise InvalidEmailActionError("The target email does not match the action")


def validate_send(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload, {"account", "arguments"})
    if set(arguments) != _SEND_KEYS:
        raise InvalidEmailActionError("Unexpected email fields")
    primary = arguments["recipient_email"]
    _check_addresses([primary, *arguments["extra_recipients"], *arguments["cc"], *arguments["bcc"]])
    subject, body = arguments["subject"], arguments["body"]
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_CHARS:
        raise InvalidEmailActionError("A subject is required")
    if "\r" in subject or "\n" in subject:
        raise InvalidEmailActionError("The subject must be one line")
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_BODY_CHARS:
        raise InvalidEmailActionError("An email body is required")
    if arguments["is_html"] is not False or arguments["include_signature"] is not False:
        raise InvalidEmailActionError("Only the exact approved plain text can be sent")


def validate_forward(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload, {"account", "arguments", "message"})
    if not _FORWARD_REQUIRED <= set(arguments) <= {*_FORWARD_REQUIRED, "additional_text"}:
        raise InvalidEmailActionError("Unexpected forward fields")
    _check_target(payload, arguments["message_id"])
    recipients = arguments["recipients"]
    if not isinstance(recipients, list) or not recipients:
        raise InvalidEmailActionError("A recipient is required")
    _check_addresses(recipients)
    if arguments["include_signature"] is not False:
        raise InvalidEmailActionError("Only the exact approved text can be forwarded")


def validate_trash(payload: dict[str, Any]) -> None:
    arguments = _check_envelope(payload, {"account", "arguments", "message"})
    if set(arguments) != {"message_id"}:
        raise InvalidEmailActionError("Unexpected trash fields")
    _check_target(payload, arguments["message_id"])


def build_send_payload(
    *, account: str, to: list[str], cc: list[str], bcc: list[str], subject: str, body: str
) -> dict[str, Any]:
    payload = {
        "account": account,
        "arguments": {
            "recipient_email": to[0],
            "extra_recipients": list(to[1:]),
            "cc": list(cc),
            "bcc": list(bcc),
            "subject": subject,
            "body": body,
            "is_html": False,
            "include_signature": False,
        },
    }
    validate_send(payload)
    return payload


def build_forward_payload(
    *, account: str, message: dict[str, Any], to: list[str], note: str | None
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "message_id": message["id"],
        "recipients": list(to),
        "include_signature": False,
    }
    if note:
        arguments["additional_text"] = note
    payload = {"account": account, "arguments": arguments, "message": message}
    validate_forward(payload)
    return payload


def build_trash_payload(*, account: str, message: dict[str, Any]) -> dict[str, Any]:
    payload = {"account": account, "arguments": {"message_id": message["id"]}, "message": message}
    validate_trash(payload)
    return payload


def parse_messages(data: Any) -> list[dict[str, Any]]:
    """Tool schema: data.messages[{messageId, sender, subject, messageTimestamp, preview}].

    Email content is untrusted. It is shown to the user and is never given to a model here.
    """
    rows = (data or {}).get("messages") if isinstance(data, dict) else None
    messages = []
    for row in rows or []:
        if not isinstance(row, dict) or not _MESSAGE_ID.match(str(row.get("messageId") or "")):
            continue
        preview = row.get("preview")
        if isinstance(preview, dict):
            preview = preview.get("body")
        messages.append(
            {
                "id": row["messageId"],
                "sender": str(row.get("sender") or "Unknown sender")[:300],
                "subject": str(row.get("subject") or "(no subject)")[:MAX_SUBJECT_CHARS],
                "date": str(row.get("messageTimestamp") or "")[:64],
                "preview": str(preview or "")[:PREVIEW_CHARS],
            }
        )
    return messages


def result_id(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    nested = data.get("response_data")
    value = data.get("id") or (nested.get("id") if isinstance(nested, dict) else None)
    return value if isinstance(value, str) and value else None


def result_url(data: Any, _result_id: str | None) -> str | None:
    if not isinstance(data, dict):
        return None
    nested = data.get("response_data")
    url = data.get("display_url") or (nested.get("display_url") if isinstance(nested, dict) else None)
    return url if isinstance(url, str) and url.startswith("https://mail.google.com/") else None
