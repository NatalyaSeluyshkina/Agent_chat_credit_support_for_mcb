"""
Дымовой тест слоя 2 (AgentState + multi-turn плумбинг).
Запуск: python -m tests.test_layer2

Проверяет:
  - seed состояния из реального multi-turn кейса qa.jsonl;
  - корректность идентификации внутри состояния;
  - работу редьюсера add_messages (накопление истории между ходами);
  - стык с хелперами Участника 1 (build_query_from_history, detect_product).
"""

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import add_messages

from agent.state import (
    AgentState,
    latest_client_text,
    make_initial_state,
    to_rag_case,
)

# Хелперы Участника 1 — проверяем, что наш формат им подходит.
from rag import build_query_from_history, detect_product

QA_PATH = Path("data/qa/qa.jsonl")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def load_case(case_id: str) -> dict:
    for line in QA_PATH.open(encoding="utf-8"):
        case = json.loads(line)
        if case["id"] == case_id:
            return case
    raise LookupError(case_id)


def test_seed_multiturn() -> None:
    section("Seed состояния из multi-turn кейса (Q-043)")

    case = load_case("Q-043")  # chat_intern, C-000004, история про инвест-кредит
    state = make_initial_state(
        channel=case["channel"],
        session_client_id=case["client_id"],
        question=case["question"],
        history=case["history"],
    )

    print(f"канал={state['channel']} уровень={state['identification_level']} "
          f"client_id={state['client_id']}")
    print(f"сообщений в истории: {len(state['messages'])}")
    for m in state["messages"]:
        role = "client" if isinstance(m, HumanMessage) else "assistant"
        print(f"  [{role}] {m.content}")

    # Идентификация: авторизованный канал → доступ к своим данным.
    assert state["client_id"] == "C-000004"
    assert state["identification_level"] == "basic"
    # История (3 реплики) + текущая реплика = 4 сообщения.
    assert len(state["messages"]) == 4
    assert latest_client_text(state) == "Расскажите про комиссию."
    print("OK: состояние собрано, идентификация и история на месте.")


def test_anonymous_seed() -> None:
    section("Seed анонимного кейса (Q-001, chat_site)")

    case = load_case("Q-001")
    state = make_initial_state(
        channel=case["channel"],
        session_client_id=case["client_id"],
        question=case["question"],
    )
    print(f"канал={state['channel']} уровень={state['identification_level']} "
          f"client_id={state['client_id']}")
    assert state["client_id"] is None
    assert state["identification_level"] == "anonymous"
    print("OK: анонимный канал — без client_id в состоянии.")


def test_reducer() -> None:
    section("Редьюсер add_messages (накопление между ходами)")

    state = make_initial_state(channel="chat_intern", session_client_id="C-000004",
                               question="Здравствуйте")
    before = len(state["messages"])
    # Симулируем, что узел вернул ответ ассистента и следующую реплику клиента.
    update = {"messages": [AIMessage("Здравствуйте, чем помочь?"),
                           HumanMessage("Какая ставка по Бизнес-Развитие?")]}
    state["messages"] = add_messages(state["messages"], update["messages"])
    after = len(state["messages"])
    print(f"сообщений было {before}, стало {after}")
    assert after == before + 2
    assert latest_client_text(state) == "Какая ставка по Бизнес-Развитие?"
    print("OK: add_messages дописывает историю.")


def test_rag_seam() -> None:
    section("Стык с хелперами Участника 1 (to_rag_case → build_query_from_history)")

    case = load_case("Q-043")
    state = make_initial_state(
        channel=case["channel"],
        session_client_id=case["client_id"],
        question=case["question"],
        history=case["history"],
    )
    rag_case = to_rag_case(state)
    print(f"question: {rag_case['question']!r}")
    print(f"history turns: {len(rag_case['history'])}")

    # Формат принимается хелпером Участника 1 без ошибок.
    query = build_query_from_history(rag_case)
    print(f"build_query_from_history -> {query!r}")
    assert query == "Расскажите про комиссию."  # question непустой → берётся он

    # detect_product работает на тексте диалога (инвест-кредит = Бизнес-Развитие
    # распознаётся по явному упоминанию, здесь его нет — вернёт None, это ок).
    product = detect_product("Хочу узнать про Бизнес-Развитие")
    print(f"detect_product('...Бизнес-Развитие') -> {product}")
    assert product == "BUSINESS_RAZVITIE"
    print("OK: формат состояния совместим с RAG-слоем Участника 1.")


if __name__ == "__main__":
    test_seed_multiturn()
    test_anonymous_seed()
    test_reducer()
    test_rag_seam()
    print("\nВсе проверки слоя 2 пройдены.")
