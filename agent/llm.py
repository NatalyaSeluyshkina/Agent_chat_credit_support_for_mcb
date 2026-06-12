"""
llm.py — «боевые» реализации classify_fn / generate_fn на GigaChat.

Подключаются в граф через те же точки, что и оффлайн-заглушки (agent/stubs.py):
  - make_gigachat_classifier() читает prompts/classify.md, просит structured output;
  - make_gigachat_generator()  читает prompts/generate.md + правило коллизий
    Участника 1 (rag/prompts/collision_priority.md);
  - make_gigachat_deps()       собирает GraphDeps с реальным Retriever Участника 1.

Требует GIGACHAT_CREDENTIALS (см. .env.example). При сбое разбора ответа
классификатор откатывается на baseline (agent/stubs.rule_based_classify), чтобы
граф не падал.

ВНИМАНИЕ: модуль не запускается в песочнице без доступа к GigaChat — тестируется
на стороне, где есть ключ. Импорт langchain_gigachat ленивый (внутри функций).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agent.nodes import GraphDeps
from agent.state import AgentState, latest_client_text, messages_to_history
from agent.stubs import rule_based_classify, template_generate

logger = logging.getLogger("agent.llm")

PROMPTS_DIR = Path(__file__).parent / "prompts"
COLLISION_PROMPT_PATH = Path(__file__).parent.parent / "rag" / "prompts" / "collision_priority.md"

_VALID_CATEGORIES = {
    "info", "transactional", "escalation_sales", "escalation_negative",
    "edge_no_data", "edge_conflict", "edge_manipulation", "offtopic",
}

# Триггеры эскалации (раздел 4). Всё, что не отсюда, считаем отсутствием триггера.
_VALID_TRIGGERS = {
    "intent", "negative", "human_request",
    "out_of_competence", "suspicious", "technical",
}

# Значения, которые LLM иногда присылает вместо настоящего null.
_NULLISH = {"", "null", "none", "нет", "n/a", "na", "-"}


def _clean(value):
    """Привести «null»-подобные строки к настоящему None."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _NULLISH:
        return None
    return value


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_collision_rule() -> str:
    """Достать блок правила (первый ``` ... ```) из collision_priority.md Участника 1."""
    if not COLLISION_PROMPT_PATH.exists():
        return ""
    text = _read(COLLISION_PROMPT_PATH)
    parts = text.split("```")
    return parts[1].strip() if len(parts) >= 3 else text.strip()


def _strip_fences(text: str) -> str:
    """Убрать markdown-ограждения ```json ... ``` из ответа LLM."""
    return text.replace("```json", "").replace("```", "").strip()


def build_gigachat_chat(model: str = "GigaChat", temperature: float = 0.0):
    """
    Создать чат-клиент GigaChat. Берёт ключ из GIGACHAT_CREDENTIALS, область — из
    GIGACHAT_SCOPE (по умолчанию GIGACHAT_API_PERS).
    """
    from langchain_gigachat import GigaChat  # ленивый импорт

    credentials = os.getenv("GIGACHAT_CREDENTIALS")
    if not credentials:
        raise ValueError(
            "GIGACHAT_CREDENTIALS не установлены. Скопируй .env.example в .env."
        )
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    return GigaChat(
        credentials=credentials,
        scope=scope,
        model=model,
        temperature=temperature,
        verify_ssl_certs=False,
    )


def _format_dialog(state: AgentState) -> str:
    """Текстовое представление диалога для подачи в LLM."""
    lines = []
    for turn in messages_to_history(state):
        role = "Клиент" if turn["role"] == "client" else "Помощник"
        lines.append(f"{role}: {turn['text']}")
    return "\n".join(lines)


def make_gigachat_classifier(chat=None) -> Callable[[AgentState], dict]:
    """
    Классификатор на GigaChat. Возвращает функцию для GraphDeps.classify_fn.
    При ошибке разбора JSON откатывается на rule_based_classify.
    """
    chat = chat or build_gigachat_chat(temperature=0.0)
    system_prompt = _read(PROMPTS_DIR / "classify.md")

    def classify(state: AgentState) -> dict:
        user_prompt = (
            f"Канал обращения: {state.get('channel')}\n"
            f"Клиент идентифицирован: {'да' if state.get('client_id') else 'нет'}\n\n"
            f"Диалог:\n{_format_dialog(state)}\n\n"
            "Верни ТОЛЬКО JSON по схеме из инструкции, без markdown и пояснений."
        )
        try:
            response = chat.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            data = json.loads(_strip_fences(response.content))
        except Exception as exc:  # noqa: BLE001 — любой сбой → безопасный откат
            logger.warning("classify: сбой GigaChat/JSON (%s) — откат на baseline.", exc)
            return rule_based_classify(state)

        category = data.get("category")
        if category not in _VALID_CATEGORIES:
            logger.warning("classify: неизвестная категория %r — откат на baseline.", category)
            return rule_based_classify(state)

        # Чистим триггер: «null»-строки → None, и принимаем только валидные значения.
        trigger = _clean(data.get("escalation_trigger"))
        if trigger is not None and trigger not in _VALID_TRIGGERS:
            logger.warning("classify: неизвестный триггер %r — обнуляю.", trigger)
            trigger = None

        return {
            "category": category,
            "escalation_trigger": trigger,
            "needs_db": bool(data.get("needs_db", False)),
            "needs_rag": bool(data.get("needs_rag", False)),
            "detected_product": _clean(data.get("detected_product")),
            "negative_markers": data.get("negative_markers", []) or [],
        }

    return classify


def make_gigachat_generator(chat=None) -> Callable[[AgentState], str]:
    """
    Генератор ответа на GigaChat. Возвращает функцию для GraphDeps.generate_fn.
    Подмешивает правило разрешения коллизий Участника 1.
    """
    chat = chat or build_gigachat_chat(temperature=0.2)
    system_prompt = _read(PROMPTS_DIR / "generate.md")
    collision_rule = load_collision_rule()

    def generate(state: AgentState) -> str:
        context = state.get("retrieved_context", "")
        tool_results = state.get("tool_results", {})

        user_parts = [f"Вопрос клиента: {latest_client_text(state)}"]
        if collision_rule:
            user_parts.append(f"\nПравило приоритета при коллизиях:\n{collision_rule}")
        if context:
            user_parts.append(f"\n{context}")
        if tool_results and not tool_results.get("access_denied"):
            user_parts.append(f"\nДанные клиента:\n{json.dumps(tool_results, ensure_ascii=False)}")
        if tool_results.get("access_denied"):
            user_parts.append("\nДанные клиента недоступны (анонимный канал). "
                              "Предложи авторизоваться, не выдумывай данные.")
        user_parts.append("\nОтветь кратко и точно, цитируя источники (документ#пункт).")

        try:
            response = chat.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content="\n".join(user_parts)),
            ])
            return response.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("generate: сбой GigaChat (%s) — откат на шаблон.", exc)
            return template_generate(state)

    return generate


def make_gigachat_deps(
    index_dir: str = "rag/index",
    db_path: Optional[str] = None,
    top_k: int = 5,
    escalation_log_path: Optional[str] = None,
) -> GraphDeps:
    """
    Собрать GraphDeps для прода: реальный Retriever Участника 1 + GigaChat-узлы.
    Один чат-клиент переиспользуется для classify и generate.
    """
    from rag import Retriever  # ленивый импорт (тянет chromadb)

    retriever = Retriever.load(index_dir)
    chat = build_gigachat_chat()
    return GraphDeps(
        retriever=retriever,
        classify_fn=make_gigachat_classifier(chat),
        generate_fn=make_gigachat_generator(chat),
        db_path=db_path,
        top_k=top_k,
        escalation_log_path=escalation_log_path,
    )


def make_stub_deps(escalation_log_path: Optional[str] = None) -> GraphDeps:
    """GraphDeps на заглушках (без GigaChat) — для оффлайн-прогона и тестов."""
    from agent.stubs import FakeRetriever

    return GraphDeps(
        retriever=FakeRetriever(),
        classify_fn=rule_based_classify,
        generate_fn=template_generate,
        escalation_log_path=escalation_log_path,
    )
