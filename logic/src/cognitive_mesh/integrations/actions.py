"""Registry of consequential actions that can run after human approval.

To add an app action (Calendar, Drive, Sheets, WhatsApp Business, Slack, HubSpot, Salesforce):
1. Add its verified payload rules in `integrations/<app>.py`.
2. Register an `ActionSpec` here and add its write capability in `capabilities/registry.py`.
3. Enable the app in `catalog.py` and add its command workflow.
Approval signing, one-time use, execution records and recovery then apply automatically.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cognitive_mesh.integrations import gmail, google_calendar, google_sheets, linkedin


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    app: str
    toolkit: str
    tool_slug: str
    any_scopes: tuple[str, ...]  # at least one must be granted
    validate: Callable[[dict[str, Any]], None]
    arguments: Callable[[dict[str, Any]], dict[str, Any]]
    result_id: Callable[[Any], str | None]
    result_url: Callable[[Any, str | None], str | None]
    done_message: str
    failed_message: str
    unknown_message: str
    # Optional second call that completes this one approved action.
    follow_up: Callable[[dict[str, Any], Any], google_sheets.FollowUp | None] | None = None
    follow_up_failed: str = ""


def _email_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload["arguments"])


def _email_spec(
    action_type: str, tool: str, scopes: tuple[str, ...], done: str, check_where: str
) -> ActionSpec:
    validators = {
        gmail.SEND_ACTION: gmail.validate_send,
        gmail.FORWARD_ACTION: gmail.validate_forward,
        gmail.TRASH_ACTION: gmail.validate_trash,
    }
    return ActionSpec(
        action_type=action_type,
        app="Gmail",
        toolkit=gmail.TOOLKIT,
        tool_slug=tool,
        any_scopes=scopes,
        validate=validators[action_type],
        arguments=_email_arguments,
        result_id=gmail.result_id,
        result_url=gmail.result_url,
        done_message=done,
        failed_message=(
            "Gmail did not complete this action, so nothing changed. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            "I can't confirm whether Gmail completed this because the result was lost. "
            f"Check {check_where} before trying again. I will not repeat it automatically."
        ),
    )


def _calendar_spec(
    action_type: str,
    tool: str,
    validate: Callable[[dict[str, Any]], None],
    done: str,
    result_id: Callable[[Any], str | None] = google_calendar.result_id,
    result_url: Callable[[Any, str | None], str | None] = google_calendar.result_url,
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        app="Google Calendar",
        toolkit=google_calendar.TOOLKIT,
        tool_slug=tool,
        any_scopes=google_calendar.WRITE_SCOPES,
        validate=validate,
        arguments=_email_arguments,
        result_id=result_id,
        result_url=result_url,
        done_message=done,
        failed_message=(
            "Google Calendar did not complete this action, so nothing changed. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            "I can't confirm whether Google Calendar completed this because the result was "
            "lost. Check your calendar before trying again. I will not repeat it automatically."
        ),
    )


def _sheet_spec(
    action_type: str,
    tool: str,
    validate: Callable[[dict[str, Any]], None],
    done: str,
    **extra: Any,
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        app="Google Sheets",
        toolkit=google_sheets.TOOLKIT,
        tool_slug=tool,
        any_scopes=google_sheets.WRITE_SCOPES,
        validate=validate,
        arguments=_email_arguments,
        result_id=google_sheets.result_id,
        result_url=google_sheets.result_url,
        done_message=done,
        failed_message=(
            "Google Sheets did not complete this action, so nothing changed. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            "I can't confirm whether Google Sheets completed this because the result was lost. "
            "Check the sheet before trying again. I will not repeat it automatically."
        ),
        **extra,
    )


ACTIONS: dict[str, ActionSpec] = {
    spec.action_type: spec
    for spec in (
        ActionSpec(
            action_type=linkedin.ACTION_TYPE,
            app="LinkedIn",
            toolkit=linkedin.TOOLKIT,
            tool_slug=linkedin.POST_TOOL,
            any_scopes=(linkedin.REQUIRED_SCOPE,),
            validate=linkedin.validate_post_payload,
            arguments=dict,
            result_id=linkedin.post_id_from,
            result_url=lambda _data, post_id: linkedin.post_url(post_id),
            done_message="Published on LinkedIn",
            failed_message=(
                "LinkedIn did not publish this post, so nothing went live. "
                "Press Retry to prepare it for your approval again."
            ),
            unknown_message=(
                "I can't confirm whether this post went live because the result was lost after "
                "sending. Check your LinkedIn profile before posting it again. I will not resend "
                "it automatically."
            ),
        ),
        _email_spec(
            gmail.SEND_ACTION, gmail.SEND_TOOL, gmail.SEND_SCOPES, "Email sent", "your Sent folder"
        ),
        _email_spec(
            gmail.FORWARD_ACTION, gmail.FORWARD_TOOL, gmail.SEND_SCOPES, "Email forwarded",
            "your Sent folder",
        ),
        _email_spec(
            gmail.TRASH_ACTION, gmail.TRASH_TOOL, gmail.TRASH_SCOPES,
            "Moved to Gmail Trash, where you can restore it", "your Gmail Trash",
        ),
        _calendar_spec(
            google_calendar.CREATE_ACTION, google_calendar.CREATE_TOOL,
            google_calendar.validate_create, "Event scheduled",
        ),
        _calendar_spec(
            google_calendar.RESCHEDULE_ACTION, google_calendar.PATCH_TOOL,
            google_calendar.validate_reschedule, "Event rescheduled",
        ),
        _calendar_spec(
            google_calendar.CANCEL_ACTION, google_calendar.DELETE_TOOL,
            google_calendar.validate_cancel, "Event cancelled",
            result_id=google_calendar.deleted_id, result_url=google_calendar.no_url,
        ),
        _sheet_spec(
            google_sheets.CREATE_ACTION, google_sheets.CREATE_TOOL, google_sheets.validate_create,
            "Sheet created",
            follow_up=google_sheets.create_follow_up,
            follow_up_failed=(
                "The sheet was created, but I couldn't confirm that your rows were written. "
                "Open it and check before adding them again."
            ),
        ),
        _sheet_spec(
            google_sheets.APPEND_ACTION, google_sheets.APPEND_TOOL, google_sheets.validate_append,
            "Rows added",
        ),
        _sheet_spec(
            google_sheets.UPDATE_ACTION, google_sheets.UPDATE_TOOL, google_sheets.validate_update,
            "Cell updated",
        ),
    )
}
_BY_TOOL = {spec.tool_slug: spec for spec in ACTIONS.values()}


def get_action(action_type: str) -> ActionSpec | None:
    return ACTIONS.get(action_type)


def action_for_tool(tool_slug: str) -> ActionSpec | None:
    return _BY_TOOL.get(tool_slug)
