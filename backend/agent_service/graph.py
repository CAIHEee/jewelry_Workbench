from __future__ import annotations

from typing import Any, TypedDict

from app.models.user import User
from app.schemas.agent import AgentAssetRef
from app.services.agent_service import AgentService


class AgentGraphState(TypedDict, total=False):
    conversation_id: str
    current_user: User
    content: str
    attachments: list[AgentAssetRef]
    reply: str
    action: Any
    memory_proposal: Any


async def _memory_node(state: AgentGraphState) -> AgentGraphState:
    return state


async def _planner_node(state: AgentGraphState) -> AgentGraphState:
    return state


async def _advisor_node(state: AgentGraphState) -> AgentGraphState:
    service = AgentService()
    reply, action, memory_proposal = await service.handle_user_message(
        conversation_id=state["conversation_id"],
        current_user=state["current_user"],
        content=state.get("content", ""),
        attachments=state.get("attachments", []),
    )
    state["reply"] = reply
    state["action"] = action
    state["memory_proposal"] = memory_proposal
    return state


async def _action_card_node(state: AgentGraphState) -> AgentGraphState:
    return state


async def _validator_node(state: AgentGraphState) -> AgentGraphState:
    return state


def build_agent_graph():
    try:
        from langgraph.graph import END, StateGraph
    except Exception:  # noqa: BLE001
        return None

    graph = StateGraph(AgentGraphState)
    graph.add_node("memory", _memory_node)
    graph.add_node("planner", _planner_node)
    graph.add_node("advisor", _advisor_node)
    graph.add_node("action_card", _action_card_node)
    graph.add_node("validator", _validator_node)
    graph.set_entry_point("memory")
    graph.add_edge("memory", "planner")
    graph.add_edge("planner", "advisor")
    graph.add_edge("advisor", "action_card")
    graph.add_edge("action_card", "validator")
    graph.add_edge("validator", END)
    return graph.compile()


class AgentGraphRunner:
    def __init__(self) -> None:
        self.graph = build_agent_graph()

    async def run(self, state: AgentGraphState) -> AgentGraphState:
        if self.graph is None:
            return await _advisor_node(state)
        return await self.graph.ainvoke(state)
