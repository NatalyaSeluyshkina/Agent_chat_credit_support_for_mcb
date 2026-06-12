"""
graph.py — сборка графа LangGraph и маршрутизация (задачи 2.4, 2.5).

Поток:

    START
      │
      ▼
   classify ──► escalate ──► END        (есть триггер эскалации; приоритет п.4.1)
      │
      ├──► query_db ──► (нужен RAG?) ──► retrieve_rag ──► generate_answer ──► END
      │                     │ нет
      │                     └──────────────────────────► generate_answer ──► END
      │
      ├──► retrieve_rag ──► generate_answer ──► END   (info / edge_conflict)
      │
      └──► generate_answer ──► END                    (offtopic / edge_no_data / манипуляции)

Multi-turn (2.5): компилируем с MemorySaver. История диалога живёт в state["messages"]
(редьюсер add_messages) и сохраняется по thread_id между ходами.
"""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    GraphDeps,
    classify_node,
    escalate_node,
    generate_answer_node,
    query_db_node,
    retrieve_rag_node,
)
from agent.escalation import EscalationTrigger
from agent.state import AgentState

# Категории, на которые срабатывает эскалация (помимо явного триггера).
_ESCALATION_CATEGORIES = {"escalation_sales", "escalation_negative"}

# Только распознанные триггеры ведут в эскалацию (защита от мусора вида "null").
_VALID_TRIGGER_VALUES = {trigger.value for trigger in EscalationTrigger}


def route_after_classify(state: AgentState) -> str:
    """Куда идти после классификации. Приоритет эскалации над всем остальным (п. 4.1)."""
    trigger = state.get("escalation_trigger")
    if trigger in _VALID_TRIGGER_VALUES or state.get("category") in _ESCALATION_CATEGORIES:
        return "escalate"
    if state.get("needs_db"):
        return "query_db"
    if state.get("needs_rag"):
        return "retrieve_rag"
    return "generate_answer"


def route_after_query_db(state: AgentState) -> str:
    """После БД: если нужен нормативный контекст — в RAG, иначе сразу к ответу."""
    # Если доступ к данным отклонён — нормативка не нужна, сразу к отказу.
    if state.get("tool_results", {}).get("access_denied"):
        return "generate_answer"
    if state.get("needs_rag"):
        return "retrieve_rag"
    return "generate_answer"


def build_graph(deps: GraphDeps, checkpointer: MemorySaver | None = None):
    """
    Собрать и скомпилировать граф агента.

    Args:
        deps: зависимости (ретривер, классификатор, генератор, путь к БД).
        checkpointer: хранилище состояния для multi-turn. Если None — новый MemorySaver.

    Returns:
        Скомпилированный граф (поддерживает .invoke с config={"configurable": {"thread_id": ...}}).
    """
    graph = StateGraph(AgentState)

    # Узлы. partial «привязывает» deps, оставляя сигнатуру (state) для LangGraph.
    graph.add_node("classify", partial(classify_node, deps=deps))
    graph.add_node("query_db", partial(query_db_node, deps=deps))
    graph.add_node("retrieve_rag", partial(retrieve_rag_node, deps=deps))
    graph.add_node("generate_answer", partial(generate_answer_node, deps=deps))
    graph.add_node("escalate", partial(escalate_node, deps=deps))

    # Рёбра.
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "escalate": "escalate",
            "query_db": "query_db",
            "retrieve_rag": "retrieve_rag",
            "generate_answer": "generate_answer",
        },
    )
    graph.add_conditional_edges(
        "query_db",
        route_after_query_db,
        {
            "retrieve_rag": "retrieve_rag",
            "generate_answer": "generate_answer",
        },
    )
    graph.add_edge("retrieve_rag", "generate_answer")
    graph.add_edge("generate_answer", END)
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
