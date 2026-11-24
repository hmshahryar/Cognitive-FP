"""Wire the command workflow.

    prepare --(calendar)----> calendar_action --------+  (reads finish here)
       |----(sheets)--------> sheets_action ----------+
       |----(email)---------> gmail_action -----------+
       |----(post as-is)----> use_provided_text ------+
       |----(write)-----------------+                 |
       +----(research/unclear)-> research -> write -> request_approval --interrupt()--> decide
                                               ^                                          |
                                               +------------ changes requested -----------+
    decide: APPROVED -> publish (verified, at most once) -> end | REJECTED -> end
            INVALIDATED -> fail | still PENDING -> request_approval (pause again)

Rules:
- One graph thread per execution; checkpoints use durability="sync" in their own SQLite file.
- Nodes re-run on resume and after crashes, so each one is idempotent.
- The decision is always read from the approvals table. The resume value is only a wake-up.
- App records (tasks, drafts, approvals, audit) stay the source of truth.
- Node names and state fields are part of the checkpoint format. Do not rename them casually.
"""

from langgraph.graph import END, START, StateGraph

from cognitive_mesh.workflows.command_graph.context import GraphDeps, NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.nodes.approval_nodes import build_approval_nodes
from cognitive_mesh.workflows.command_graph.nodes.calendar_node import build_calendar_nodes
from cognitive_mesh.workflows.command_graph.nodes.content_nodes import build_content_nodes
from cognitive_mesh.workflows.command_graph.nodes.crm_node import build_crm_nodes
from cognitive_mesh.workflows.command_graph.nodes.email_node import build_email_nodes
from cognitive_mesh.workflows.command_graph.nodes.notion_node import build_notion_nodes
from cognitive_mesh.workflows.command_graph.nodes.publish_node import build_publish_nodes
from cognitive_mesh.workflows.command_graph.nodes.routing_node import build_routing_nodes
from cognitive_mesh.workflows.command_graph.nodes.sheets_node import build_sheets_nodes
from cognitive_mesh.workflows.command_graph.nodes.social_node import build_social_nodes
from cognitive_mesh.workflows.command_graph.state import TERMINAL_OUTCOMES, GraphState


def build_graph(deps: GraphDeps) -> StateGraph:
    ctx = NodeContext(deps)
    nodes: dict[str, NodeFunction] = {
        **build_routing_nodes(ctx),
        **build_content_nodes(ctx),
        **build_email_nodes(ctx),
        **build_calendar_nodes(ctx),
        **build_sheets_nodes(ctx),
        **build_crm_nodes(ctx),
        **build_notion_nodes(ctx),
        **build_social_nodes(ctx),
        **build_approval_nodes(ctx),
        **build_publish_nodes(ctx),
    }

    def stop_or(next_node: str):  # type: ignore[no-untyped-def]
        def route(state: GraphState) -> str:
            return END if state.get("outcome") in TERMINAL_OUTCOMES else next_node
        return route

    def after_decide(state: GraphState) -> str:
        outcome = state.get("outcome")
        if outcome == "changes":
            return {
                "gmail": "gmail_action", "calendar": "calendar_action", "sheets": "sheets_action",
                "crm": "crm_action", "notion": "notion_action", "social": "social_action",
            }.get(
                state.get("mode", ""), "write"
            )
        if outcome == "awaiting":
            return "request_approval"
        if outcome == "approved":
            return "publish"
        return END  # rejected | cancelled | failed

    def after_prepare(state: GraphState) -> str:
        if state.get("outcome") in TERMINAL_OUTCOMES:
            return END
        nodes = {
            "post_as_is": "use_provided_text",
            "write": "write",
            "gmail": "gmail_action",
            "calendar": "calendar_action",
            "sheets": "sheets_action",
            "crm": "crm_action",
            "notion": "notion_action",
            "social": "social_action",
        }
        return nodes.get(state["mode"], "research")

    graph = StateGraph(GraphState)
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges(
        "prepare",
        after_prepare,
        [
            "use_provided_text", "gmail_action", "calendar_action", "sheets_action", "crm_action",
            "notion_action", "social_action", "write", "research", END,
        ],
    )
    graph.add_conditional_edges(
        "social_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "crm_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "notion_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "sheets_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "calendar_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "gmail_action", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges(
        "use_provided_text", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges("research", stop_or("write"), ["write", END])
    graph.add_conditional_edges("write", stop_or("request_approval"), ["request_approval", END])
    graph.add_conditional_edges("request_approval", stop_or("decide"), ["decide", END])
    graph.add_conditional_edges(
        "decide",
        after_decide,
        [
            "write", "gmail_action", "calendar_action", "sheets_action", "crm_action",
            "notion_action", "social_action", "request_approval", "publish", END,
        ],
    )
    graph.add_edge("publish", END)
    return graph
