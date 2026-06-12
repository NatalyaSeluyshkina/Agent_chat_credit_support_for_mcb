"""
state.py — состояние графа агента (задача 2.3) + плумбинг multi-turn (задача 2.5).

AgentState — единый словарь, который течёт через узлы графа. Каждое поле имеет
явного «владельца» — узел, который его заполняет (см. комментарии). Диалог
хранится в `messages` с редьюсером add_messages — это нативный для LangGraph
способ накапливать историю между ходами (работает вместе с checkpointer'ом).

Помимо самой схемы здесь — хелперы построения и конвертации состояния:
  - make_initial_state(...) — собрать стартовое состояние (резолвит канал → доступ);
  - latest_client_text(state) — текущая реплика клиента;
  - to_rag_case(state) — привести диалог к формату {question, history}, который
    понимают rag.build_query_from_history / rag.detect_product Участника 1.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from agent.auth import resolve_client


class AgentState(TypedDict, total=False):
    """
    Состояние графа. total=False — узлы дописывают поля по мере прохождения,
    не обязаны заполнять всё сразу.
    """

    # --- Диалог (multi-turn). Источник истины, накапливается add_messages. ---
    messages: Annotated[list[BaseMessage], add_messages]

    # --- Идентификация. Заполняет intake/resolve_client. ---
    channel: str
    session_client_id: Optional[str]   # «заявленный» id из сессии канала
    client_id: Optional[str]           # авторизованный id или None (аноним)
    identification_level: str          # anonymous | basic

    # --- Классификация. Заполняет узел classify (тело промпта — Участник 3). ---
    category: Optional[str]            # info|transactional|escalation_*|edge_*|offtopic
    escalation_trigger: Optional[str]  # intent|negative|human_request|out_of_competence|suspicious|None
    needs_db: bool                     # нужен ли доступ к БД клиента
    needs_rag: bool                    # нужен ли поиск по нормативным документам
    detected_product: Optional[str]    # код продукта (для product_filter в RAG)
    negative_markers: list[str]        # маркеры негатива (в пакет эскалации)

    # --- RAG. Заполняет узел retrieve_rag (retriever Участника 1). ---
    retrieved_context: str             # отформатированный контекст для LLM
    scope_tags: list[dict]             # источники: source/scope/score/product_code
    retrieved_count: int

    # --- БД. Заполняет узел query_db. ---
    tool_results: dict                 # {"profile":..., "loans":[...], "applications":[...]}

    # --- Эскалация. Заполняет узел escalate. ---
    escalation: Optional[dict]         # EscalationPayload в виде словаря

    # --- Финал. Заполняет generate_answer / escalate. ---
    outcome_type: Optional[str]        # info|calculation|escalation|rejection|clarification
    answer: Optional[str]              # ответ клиенту
    sources: list[dict]                # источники, на которые опирается ответ

    # --- Метаданные сессии (нужны для пакета эскалации, п. 5.1 РП-ОБ-005). ---
    session_id: str
    dialog_start_time: str             # ISO-время начала диалога
    language: str                      # язык общения (по умолчанию "ru")


def _history_to_messages(history: Optional[list[dict]]) -> list[BaseMessage]:
    """
    Преобразовать историю в формате qa.jsonl ([{role, text}, ...]) в сообщения
    LangChain. role="client" → HumanMessage, role="assistant" → AIMessage.
    Пустые реплики пропускаются.
    """
    messages: list[BaseMessage] = []
    for turn in history or []:
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        role = turn.get("role")
        if role == "client":
            messages.append(HumanMessage(text))
        elif role == "assistant":
            messages.append(AIMessage(text))
    return messages


def make_initial_state(
    channel: str,
    session_client_id: Optional[str] = None,
    question: Optional[str] = None,
    history: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
    language: str = "ru",
) -> AgentState:
    """
    Собрать стартовое состояние для нового обращения / хода диалога.

    Идентификация выполняется сразу: канал резолвится в уровень доступа и
    авторизованный client_id (или None). Историю и текущую реплику клиента
    кладём в messages.

    Args:
        channel: канал обращения.
        session_client_id: id из сессии канала (на анонимном канале будет проигнорирован).
        question: текущая реплика клиента (если есть).
        history: предыдущие реплики в формате qa.jsonl (для multi-turn).
        session_id: идентификатор сессии Помощника (если None — генерируется).
        language: язык общения.

    Returns:
        AgentState с заполненными полями идентификации и метаданных.
    """
    identity = resolve_client(channel, session_client_id)

    messages = _history_to_messages(history)
    if question and question.strip():
        messages.append(HumanMessage(question.strip()))

    now = datetime.datetime.now().isoformat(timespec="seconds")

    return AgentState(
        messages=messages,
        channel=identity.channel,
        session_client_id=session_client_id,
        client_id=identity.client_id,
        identification_level=identity.level.value,
        category=None,
        escalation_trigger=None,
        needs_db=False,
        needs_rag=False,
        detected_product=None,
        negative_markers=[],
        retrieved_context="",
        scope_tags=[],
        retrieved_count=0,
        tool_results={},
        escalation=None,
        outcome_type=None,
        answer=None,
        sources=[],
        session_id=session_id or f"sess-{uuid.uuid4().hex[:12]}",
        dialog_start_time=now,
        language=language,
    )


def latest_client_text(state: AgentState) -> str:
    """Вернуть текст последней реплики клиента (последний HumanMessage)."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def to_rag_case(state: AgentState) -> dict:
    """
    Привести диалог к формату {question, history}, который понимают хелперы
    Участника 1 (build_query_from_history, detect_product).

    question — последняя реплика клиента; history — все остальные реплики в
    хронологическом порядке (роли client/assistant, как в qa.jsonl).
    """
    messages = state.get("messages", [])

    # Индекс последней реплики клиента — она пойдёт в question.
    last_client_index: Optional[int] = None
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_client_index = index

    question = ""
    history: list[dict] = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            role = "client"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue
        content = message.content
        text = content if isinstance(content, str) else str(content)

        if index == last_client_index:
            question = text
        else:
            history.append({"role": role, "text": text})

    return {"question": question, "history": history}


def messages_to_history(state: AgentState) -> list[dict]:
    """
    Полная история диалога в хронологическом порядке: [{role, text}, ...].
    В отличие от to_rag_case, включает ВСЕ реплики (в т.ч. последнюю клиентскую).
    Нужна для пакета эскалации (п. 5.1 РП-ОБ-005 — история передаётся целиком).
    """
    history: list[dict] = []
    for message in state.get("messages", []):
        if isinstance(message, HumanMessage):
            role = "client"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue
        content = message.content
        text = content if isinstance(content, str) else str(content)
        history.append({"role": role, "text": text})
    return history
