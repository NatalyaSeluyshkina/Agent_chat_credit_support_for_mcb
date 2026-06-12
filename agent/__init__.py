"""
agent — слой Участника 2: инструменты БД, идентификация, состояние, граф LangGraph.

Слой 1 (готов): доступ к БД, инструменты, идентификация по каналу.
"""

from agent.auth import (
    AccessDenied,
    IdentificationLevel,
    ResolvedIdentity,
    ensure_self_access,
    resolve_client,
)
from agent.state import (
    AgentState,
    latest_client_text,
    make_initial_state,
    to_rag_case,
)
from agent.graph import build_graph
from agent.nodes import GraphDeps
from agent.escalation import (
    EscalationPayload,
    EscalationTrigger,
    build_escalation_payload,
    simulate_handoff,
)
from agent.tools import (
    CLIENT_TOOLS,
    get_active_loans,
    get_applications,
    get_client_info,
)

__all__ = [
    # auth
    "resolve_client",
    "ensure_self_access",
    "ResolvedIdentity",
    "IdentificationLevel",
    "AccessDenied",
    # tools
    "get_client_info",
    "get_active_loans",
    "get_applications",
    "CLIENT_TOOLS",
    # state
    "AgentState",
    "make_initial_state",
    "latest_client_text",
    "to_rag_case",
    # graph
    "build_graph",
    "GraphDeps",
    # escalation
    "EscalationPayload",
    "EscalationTrigger",
    "build_escalation_payload",
    "simulate_handoff",
]
