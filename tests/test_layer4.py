"""
Дымовой тест слоя 4 (эскалация: EscalationPayload + сводка + симуляция).
Запуск: python -m tests.test_layer4
"""

import json
import os
import tempfile

from agent.escalation import (
    SUMMARY_MAX_CHARS,
    EscalationTrigger,
    build_escalation_payload,
    build_summary,
    resolve_trigger,
)
from agent.graph import build_graph
from agent.nodes import GraphDeps
from agent.state import make_initial_state
from agent.stubs import FakeRetriever, rule_based_classify, template_generate


def section(t):
    print(f"\n=== {t} ===")


def test_payload_fields_intent():
    section("Пакет эскалации: намерение (intent)")
    state = make_initial_state(
        channel="chat_intern", session_client_id="C-000001",
        question="Хочу оформить кредит Бизнес-Оборот на оборудование",
    )
    state.update(rule_based_classify(state))  # проставить категорию/триггер/продукт

    payload = build_escalation_payload(state)
    d = payload.to_dict()
    for key in ("session_id", "client_id", "category", "trigger",
                "summary", "dialog_history", "metadata"):
        assert key in d, f"нет поля {key}"
    print("поля п.5.1:", list(d.keys()))
    print("trigger:", d["trigger"])
    print("summary:", d["summary"])
    print("metadata:", d["metadata"])

    assert d["trigger"] == "intent"
    assert d["client_id"] == "C-000001"
    assert "Продукт: Бизнес-Оборот" in d["summary"]
    assert "Действие:" in d["summary"]
    assert set(d["metadata"]) == {"channel", "dialog_start_time", "escalation_time", "language"}
    print("OK: пакет по намерению собран корректно.")


def test_payload_negative_multiturn():
    section("Пакет эскалации: негатив, multi-turn")
    history = [
        {"role": "client", "text": "Почему по моей заявке так долго нет решения?"},
        {"role": "assistant", "text": "Стандартный срок рассмотрения — до 3 рабочих дней."},
    ]
    state = make_initial_state(
        channel="chat_intern", session_client_id="C-000016",
        question="Это безобразие, буду жаловаться в Центробанк!",
        history=history,
    )
    state.update(rule_based_classify(state))

    payload = build_escalation_payload(state)
    d = payload.to_dict()
    print("trigger:", d["trigger"])
    print("маркеры:", d["negative_markers"])
    print("история (реплик):", len(d["dialog_history"]))
    print("summary:", d["summary"])

    assert d["trigger"] == "negative"
    assert d["negative_markers"], "должны быть маркеры негатива"
    assert len(d["dialog_history"]) == 3  # 2 из истории + текущая
    assert "Недовольство:" in d["summary"]
    assert "реплик клиента в диалоге" in d["summary"]
    print("OK: пакет по негативу с историей собран корректно.")


def test_summary_limit():
    section("Лимит сводки ≤ 500 символов")
    long_text = "Очень длинная жалоба. " * 60  # ~1300 символов
    state = make_initial_state(
        channel="chat_intern", session_client_id="C-000001",
        question=long_text,
    )
    state.update(rule_based_classify(state))
    summary = build_summary(state, resolve_trigger(state))
    print(f"длина сводки: {len(summary)} (лимит {SUMMARY_MAX_CHARS})")
    assert len(summary) <= SUMMARY_MAX_CHARS
    assert summary.endswith("…")
    print("OK: сводка обрезается до лимита.")


def test_graph_handoff_log():
    section("Прогон через граф + лог передачи оператору")
    log_path = os.path.join(tempfile.gettempdir(), "escalations_test.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)

    deps = GraphDeps(
        retriever=FakeRetriever(),
        classify_fn=rule_based_classify,
        generate_fn=template_generate,
        escalation_log_path=log_path,
    )
    graph = build_graph(deps)

    state = make_initial_state(
        channel="chat_intern", session_client_id="C-000004",
        question="Переключите меня на человека",
    )
    final = graph.invoke(state, config={"configurable": {"thread_id": "esc-1"}})

    print("outcome:", final["outcome_type"])
    print("ответ клиенту:", final["answer"])
    assert final["outcome_type"] == "escalation"
    assert final["escalation"]["trigger"] == "human_request"

    # Лог записан.
    with open(log_path, encoding="utf-8") as f:
        logged = [json.loads(line) for line in f]
    assert len(logged) == 1
    assert logged[0]["trigger"] == "human_request"
    print(f"лог: {len(logged)} запись, trigger={logged[0]['trigger']}")
    print("OK: эскалация прошла через граф и записана в лог.")


if __name__ == "__main__":
    test_payload_fields_intent()
    test_payload_negative_multiturn()
    test_summary_limit()
    test_graph_handoff_log()
    print("\nВсе проверки слоя 4 пройдены.")
