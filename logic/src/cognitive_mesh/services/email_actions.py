"""Turns an email command into one exact action for the user to approve.

Reads are limited to the mailbox address and the target email's summary. Email content is
never passed to a model, so a malicious email cannot instruct the AI or change a recipient.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cognitive_mesh.agents import prompts
from cognitive_mesh.agents.llm import LLM_PROVIDER
from cognitive_mesh.integrations import gmail
from cognitive_mesh.integrations.gateway import (
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.workflows.content_pipeline import ContentPipeline
from cognitive_mesh.workflows.email_commands import EmailCommand

READ_FAILED = (
    "I couldn't read your Gmail account just now, so nothing was changed. "
    "Press Retry, or reconnect Gmail on the Connectors page."
)


class EmailStop(Exception):
    """The command can't continue. `message` tells the user what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PreparedEmail:
    action_type: str
    payload: dict[str, Any]
    text: str  # what is stored as the draft
    model: str
    provider: str
    prompt_version: str


async def _read(
    gateway: IntegrationGateway, user_id: str, tool: str, arguments: dict[str, Any]
) -> Any:
    try:
        return await gateway.execute_read_only(
            user_id=user_id, toolkit_slug=gmail.TOOLKIT, tool_slug=tool, arguments=arguments
        )
    except (IntegrationUnavailableError, ToolExecutionError) as exc:
        raise EmailStop(READ_FAILED) from exc


async def _mailbox(gateway: IntegrationGateway, user_id: str) -> str:
    data = await _read(gateway, user_id, gmail.PROFILE_TOOL, {})
    address = data.get("emailAddress") if isinstance(data, dict) else None
    if not isinstance(address, str) or not address:
        raise EmailStop(READ_FAILED)
    return address


async def _target(
    gateway: IntegrationGateway, user_id: str, command: EmailCommand
) -> dict[str, Any]:
    data = await _read(
        gateway,
        user_id,
        gmail.SEARCH_TOOL,
        {"query": command.query, "max_results": 2, "verbose": False, "include_payload": False},
    )
    messages = gmail.parse_messages(data)
    if not messages:
        raise EmailStop("I couldn't find an email matching that. Nothing was changed.")
    if len(messages) > 1 and not command.latest:
        raise EmailStop(
            "More than one email matches. Say \"the latest email\" or add the exact subject."
        )
    return messages[0]  # Gmail returns newest first


def _summary(verb: str, message: dict[str, Any]) -> str:
    return f"{verb}: {message['subject']} (from {message['sender']})"


async def prepare(
    gateway: IntegrationGateway,
    build_pipeline: Callable[[str], ContentPipeline],
    *,
    user_id: str,
    command: EmailCommand,
    model_name: str,
    feedback: list[str],
    previous_text: str | None,
    should_stop: Callable[[], Awaitable[bool]],
    context: str = "",
    trusted_text: str = "",
) -> PreparedEmail:
    if feedback and command.kind in ("forward", "trash"):
        raise EmailStop("This action has no text to revise. Send a new command to change it.")
    account = await _mailbox(gateway, user_id)

    if command.kind == "forward":
        message = await _target(gateway, user_id, command)
        payload = gmail.build_forward_payload(
            account=account, message=message, to=list(command.to), note=command.note
        )
        return PreparedEmail(
            gmail.FORWARD_ACTION, payload, _summary("Forward", message), "none", "user", "none"
        )
    if command.kind == "trash":
        message = await _target(gateway, user_id, command)
        payload = gmail.build_trash_payload(account=account, message=message)
        return PreparedEmail(
            gmail.TRASH_ACTION, payload, _summary("Move to Trash", message), "none", "user", "none"
        )

    if command.kind == "send" and not feedback:
        subject, body = command.subject or "", command.body or ""
        written_by = ("none", "user", "none")
    else:
        request = command.topic or f"Subject: {command.subject}\n\n{command.body}"
        draft = await build_pipeline(user_id).write_email(
            request, feedback=feedback, previous_text=previous_text, should_stop=should_stop,
            context=context, trusted_text=trusted_text,
        )
        subject, body = draft.subject, draft.body
        written_by = (model_name, LLM_PROVIDER, prompts.EMAIL_PROMPT_VERSION)
    payload = gmail.build_send_payload(
        account=account,
        to=list(command.to),
        cc=list(command.cc),
        bcc=list(command.bcc),
        subject=subject,
        body=body,
    )
    return PreparedEmail(gmail.SEND_ACTION, payload, body, *written_by)
