"""The only workflow path to an external write."""

import uuid

from cognitive_mesh.integrations.actions import get_action
from cognitive_mesh.integrations.gateway import ActionRejectedError
from cognitive_mesh.models import PublicationStatus, TaskStatus
from cognitive_mesh.services import approvals as approval_svc
from cognitive_mesh.services import publishing
from cognitive_mesh.services.errors import ApprovalInvalidError, ConfigurationError, ConflictError
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.messages import PUBLISH_BLOCKED
from cognitive_mesh.workflows.command_graph.state import GraphState


def build_publish_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    load_task = ctx.load_task
    still_mine = ctx.still_mine
    fail = ctx.fail

    async def publish(state: GraphState) -> GraphState:
        """The only path to an external write. Runs at most once per approval."""
        if deps.signer is None:
            raise ConfigurationError("APPROVAL_SIGNING_KEY is not configured")
        task = await load_task(state)
        if not still_mine(task, state):
            return {"outcome": "cancelled"}
        async with db.sessionmaker() as s:
            approval = await approval_svc.latest_for_execution(s, uuid.UUID(state["execution_id"]))
            if approval is None:
                return await fail(state, PUBLISH_BLOCKED, "no approval found")
            existing = await publishing.for_approval(s, approval.id)
            if existing is not None:  # this node re-ran after a crash or restart
                if existing.status == PublicationStatus.SENDING:
                    await publishing.finish(
                        s, existing.id, PublicationStatus.NEEDS_CHECK,
                        error="interrupted while sending",
                    )
                    return {"outcome": "failed"}
                published = existing.status == PublicationStatus.PUBLISHED
                return {"outcome": "published" if published else "failed"}
            if task.status != TaskStatus.APPROVED:
                return {"outcome": "cancelled"}
            try:
                publication = await publishing.reserve(s, deps.signer, approval)
            except ApprovalInvalidError as exc:
                return await fail(state, PUBLISH_BLOCKED, f"approval check failed: {exc.detail}")
            except ConflictError:
                return {"outcome": "cancelled"}
            publication_id = publication.id
            target = approval.target_account_id
            action = get_action(approval.action_type)
            assert action is not None  # reserve() verified it
            arguments = action.arguments(approval.payload)
            approved_payload = dict(approval.payload)
            toolkit, tool = approval.toolkit_slug, approval.tool_slug

        status, post_id, url, error = PublicationStatus.PUBLISHED, None, None, None
        note: str | None = None
        try:
            if action.pre_check is not None:
                await action.pre_check(approved_payload)  # nothing has been sent yet
            data = await deps.gateway.execute_approved_action(
                user_id=state["user_id"], toolkit_slug=toolkit, tool_slug=tool,
                connected_account_id=target, arguments=arguments,
            )
            post_id = action.result_id(data)
            url = action.result_url(data, post_id)
            completes = action.follow_up_result_id is not None
            if post_id is None:
                status, error = PublicationStatus.NEEDS_CHECK, "success without a result id"
            elif action.follow_up is not None:
                # Part of the same approval. A failure here is reported and never retried.
                try:
                    step = action.follow_up(approved_payload, data)
                    if step is None and completes:
                        status, error = PublicationStatus.NEEDS_CHECK, "no follow-up step"
                    elif step is not None:
                        finished = await deps.gateway.execute_approved_action(
                            user_id=state["user_id"], toolkit_slug=toolkit,
                            tool_slug=step.tool_slug, connected_account_id=target,
                            arguments=step.arguments,
                        )
                        if completes:
                            assert action.follow_up_result_id is not None
                            post_id = action.follow_up_result_id(finished)
                            url = action.result_url(finished, post_id)
                            if post_id is None:
                                status = PublicationStatus.NEEDS_CHECK
                                error = "follow-up succeeded without a result id"
                except ActionRejectedError as exc:
                    if completes:  # the second step definitely didn't happen
                        status, post_id, url = PublicationStatus.FAILED, None, None
                    note, error = action.follow_up_failed, str(exc)
                except Exception as exc:  # noqa: BLE001 - the first step already succeeded
                    if completes:
                        status, post_id, url = PublicationStatus.NEEDS_CHECK, None, None
                    note, error = action.follow_up_failed, repr(exc)
        except ActionRejectedError as exc:
            status, error = PublicationStatus.FAILED, str(exc)
        except Exception as exc:  # noqa: BLE001 - after sending, any error means outcome unknown
            status, error = PublicationStatus.NEEDS_CHECK, repr(exc)
        async with db.sessionmaker() as s:
            await publishing.finish(
                s, publication_id, status, external_id=post_id, external_url=url, error=error,
                note=note,
            )
        return {"outcome": "published" if status == PublicationStatus.PUBLISHED else "failed"}

    return {"publish": publish}
