"""Registry of consequential actions that can run after human approval.

To add an app action (Calendar, Drive, Sheets, WhatsApp Business, Slack, HubSpot, Salesforce):
1. Add its verified payload rules in `integrations/<app>.py`.
2. Register an `ActionSpec` here and add its write capability in `capabilities/registry.py`.
3. Enable the app in `catalog.py` and add its command workflow.
Approval signing, one-time use, execution records and recovery then apply automatically.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cognitive_mesh.integrations import (
    facebook,
    gmail,
    google_calendar,
    google_sheets,
    hubspot,
    instagram,
    linkedin,
    notion,
    salesforce,
    zoho,
)


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
    # Only for providers with no OAuth scopes. An empty scope list alone never skips the check.
    provider_has_no_scopes: bool = False
    # Runs right before sending. Raising ActionRejectedError stops the action with nothing sent.
    pre_check: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    # When set, the follow-up completes the action: its result is the outcome, and its failure
    # means the action did not happen (or is unknown), never "done with a note".
    follow_up_result_id: Callable[[Any], str | None] | None = None

    def is_permitted(self, granted: set[str]) -> bool:
        return self.provider_has_no_scopes or bool(granted & set(self.any_scopes))


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


def _crm_spec(
    action_type: str,
    tool: str,
    validate: Callable[[dict[str, Any]], None],
    done: str,
    app: str = "HubSpot",
    module: Any = hubspot,
    scopes: tuple[str, ...] | None = None,
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        app=app,
        toolkit=module.TOOLKIT,
        tool_slug=tool,
        any_scopes=scopes or module.WRITE_SCOPES,
        validate=validate,
        arguments=_email_arguments,
        result_id=module.result_id,
        result_url=module.no_url,
        done_message=done,
        failed_message=(
            f"{app} did not complete this action, so nothing changed. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            f"I can't confirm whether {app} completed this because the result was lost. "
            f"Check the contact in {app} before trying again. I will not repeat it automatically."
        ),
    )


def _notion_spec(
    action_type: str,
    tool: str,
    validate: Callable[[dict[str, Any]], None],
    done: str,
    result_id: Callable[[Any], str | None],
    result_url: Callable[[Any, str | None], str | None],
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        app="Notion",
        toolkit=notion.TOOLKIT,
        tool_slug=tool,
        any_scopes=(),
        validate=validate,
        arguments=_email_arguments,
        result_id=result_id,
        result_url=result_url,
        done_message=done,
        failed_message=(
            "Notion did not complete this action, so nothing changed. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            "I can't confirm whether Notion completed this because the result was lost. "
            "Check the page in Notion before trying again. I will not repeat it automatically."
        ),
        provider_has_no_scopes=True,
    )


def _social_spec(
    action_type: str,
    app: str,
    module: Any,
    tool: str,
    validate: Callable[[dict[str, Any]], None],
    result_id: Callable[[Any], str | None],
    result_url: Callable[[Any, str | None], str | None],
    **extra: Any,
) -> ActionSpec:
    return ActionSpec(
        action_type=action_type,
        app=app,
        toolkit=module.TOOLKIT,
        tool_slug=tool,
        any_scopes=module.WRITE_SCOPES,
        validate=validate,
        arguments=_email_arguments,
        result_id=result_id,
        result_url=result_url,
        done_message=f"Published on {app}",
        failed_message=(
            f"{app} did not publish this post, so nothing went live. "
            "Press Retry to prepare it for your approval again."
        ),
        unknown_message=(
            f"I can't confirm whether this post went live on {app} because the result was lost. "
            f"Check {app} before posting it again. I will not resend it automatically."
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
        _crm_spec(
            hubspot.CREATE_ACTION, hubspot.CREATE_TOOL, hubspot.validate_create, "Contact created"
        ),
        _crm_spec(
            hubspot.UPDATE_ACTION, hubspot.UPDATE_TOOL, hubspot.validate_update, "Contact updated"
        ),
        _crm_spec(hubspot.NOTE_ACTION, hubspot.NOTE_TOOL, hubspot.validate_note, "Note added"),
        _crm_spec(
            salesforce.CREATE_ACTION, salesforce.CREATE_TOOL, salesforce.validate_create,
            "Contact created", "Salesforce", salesforce,
        ),
        _crm_spec(
            salesforce.UPDATE_ACTION, salesforce.UPDATE_TOOL, salesforce.validate_update,
            "Contact updated", "Salesforce", salesforce,
        ),
        _crm_spec(
            salesforce.NOTE_ACTION, salesforce.NOTE_TOOL, salesforce.validate_note,
            "Note added", "Salesforce", salesforce,
        ),
        _crm_spec(
            zoho.CREATE_ACTION, zoho.CREATE_TOOL, zoho.validate_create, "Contact created",
            "Zoho CRM", zoho, zoho.CREATE_SCOPES,
        ),
        _crm_spec(
            zoho.UPDATE_ACTION, zoho.UPDATE_TOOL, zoho.validate_update, "Contact updated",
            "Zoho CRM", zoho, zoho.UPDATE_SCOPES,
        ),
        _crm_spec(
            zoho.NOTE_ACTION, zoho.NOTE_TOOL, zoho.validate_note, "Note added",
            "Zoho CRM", zoho, zoho.NOTE_SCOPES,
        ),
        _notion_spec(
            notion.CREATE_ACTION, notion.CREATE_TOOL, notion.validate_create, "Page created",
            notion.create_result_id, notion.create_result_url,
        ),
        _notion_spec(
            notion.APPEND_ACTION, notion.APPEND_TOOL, notion.validate_append, "Content added",
            notion.append_result_id, notion.no_url,
        ),
        _social_spec(
            facebook.POST_ACTION, "Facebook", facebook, facebook.POST_TOOL,
            facebook.validate_post, facebook.post_result_id, facebook.result_url,
        ),
        _social_spec(
            facebook.PHOTO_ACTION, "Facebook", facebook, facebook.PHOTO_TOOL,
            facebook.validate_photo, facebook.photo_result_id, facebook.result_url,
            pre_check=facebook.check_photo_unchanged,
        ),
        _social_spec(
            instagram.PHOTO_ACTION, "Instagram", instagram, instagram.CONTAINER_TOOL,
            instagram.validate_photo, instagram.container_id, instagram.no_url,
            pre_check=instagram.check_photo_unchanged,
            follow_up=instagram.publish_step,
            follow_up_result_id=instagram.media_id,
            follow_up_failed=(
                "Instagram prepared the post but didn't confirm publishing it. "
                "Check your Instagram profile before trying again."
            ),
        ),
    )
}
_BY_TOOL = {spec.tool_slug: spec for spec in ACTIONS.values()}


def get_action(action_type: str) -> ActionSpec | None:
    return ACTIONS.get(action_type)


def action_for_tool(tool_slug: str) -> ActionSpec | None:
    return _BY_TOOL.get(tool_slug)
