"""
escalation.py — пакет эскалации на оператора (задача 2.6).

Реализует п. 5 РП-ОБ-005:
  - EscalationPayload — состав передаваемых данных (п. 5.1);
  - build_summary — структурированная сводка ≤ 500 символов (п. 5.2);
  - build_escalation_payload — собрать пакет из состояния графа;
  - simulate_handoff — учебная симуляция передачи оператору (лог);
  - client_notification — уведомление клиента о переключении (п. 5.3).

Триггеры (п. 4): intent, negative, human_request, out_of_competence,
suspicious, technical.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from agent.state import AgentState, latest_client_text, messages_to_history

logger = logging.getLogger("agent.escalation")

SUMMARY_MAX_CHARS = 500

# Человекочитаемые названия продуктов для сводки.
PRODUCT_NAMES = {
    "BUSINESS_OBOROT": "Бизнес-Оборот",
    "BUSINESS_RAZVITIE": "Бизнес-Развитие",
    "BUSINESS_LIMIT": "Бизнес-Лимит",
    "BUSINESS_START": "Бизнес-Старт",
    "BUSINESS_PEREZAGRUZKA": "Бизнес-Перезагрузка",
}


class EscalationTrigger(str, Enum):
    """Триггеры эскалации по разделу 4 РП-ОБ-005."""

    INTENT = "intent"                    # 4.2 намерение оформить продукт
    NEGATIVE = "negative"                # 4.3 негатив клиента
    HUMAN_REQUEST = "human_request"      # 4.3.3 прямая просьба «к человеку»
    OUT_OF_COMPETENCE = "out_of_competence"  # 4.4.1 вне компетенции
    SUSPICIOUS = "suspicious"            # 4.4.2 / 7.3 подозрительное обращение
    TECHNICAL = "technical"              # 4.4.3 технический сбой


# Соответствие категории классификатора → триггеру (если classify не дал триггер явно).
_CATEGORY_TO_TRIGGER = {
    "escalation_sales": EscalationTrigger.INTENT,
    "escalation_negative": EscalationTrigger.NEGATIVE,
}


@dataclass
class EscalationPayload:
    """
    Пакет данных, передаваемый оператору (п. 5.1 РП-ОБ-005).

    Поля строго по регламенту: идентификатор сессии, клиент (если есть),
    категория, триггер, структурированная сводка (≤500), полная история диалога,
    метаданные.
    """

    session_id: str
    client_id: Optional[str]
    category: Optional[str]
    trigger: str
    summary: str
    dialog_history: list[dict]
    metadata: dict
    negative_markers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "client_id": self.client_id,
            "category": self.category,
            "trigger": self.trigger,
            "summary": self.summary,
            "dialog_history": self.dialog_history,
            "metadata": self.metadata,
            "negative_markers": self.negative_markers,
        }


def resolve_trigger(state: AgentState) -> EscalationTrigger:
    """
    Определить триггер эскалации. Берём явный из classify; если его нет —
    выводим из категории. По умолчанию (нераспознанный случай) — negative.
    """
    explicit = state.get("escalation_trigger")
    if explicit:
        try:
            return EscalationTrigger(explicit)
        except ValueError:
            logger.warning("Неизвестный триггер %r — трактуем как negative.", explicit)
            return EscalationTrigger.NEGATIVE

    category = state.get("category")
    return _CATEGORY_TO_TRIGGER.get(category, EscalationTrigger.NEGATIVE)


def build_summary(state: AgentState, trigger: EscalationTrigger) -> str:
    """
    Структурированная сводка обращения (п. 5.2), не длиннее 500 символов.

    Содержит: суть запроса; продукт (если определён); действие (для намерения);
    источник недовольства (для негатива); уже выясненные факты.
    """
    blocks: list[str] = []

    # Суть запроса.
    essence = latest_client_text(state).strip()
    if essence:
        blocks.append(f"Суть: {essence}")

    # Продукт / договор.
    product_code = state.get("detected_product")
    if product_code:
        product_name = PRODUCT_NAMES.get(product_code, product_code)
        blocks.append(f"Продукт: {product_name}")

    # Действие (для намерения оформить).
    if trigger == EscalationTrigger.INTENT:
        blocks.append("Действие: клиент намерен оформить/инициировать операцию.")

    # Источник недовольства (для негатива).
    if trigger in (EscalationTrigger.NEGATIVE, EscalationTrigger.HUMAN_REQUEST):
        markers = state.get("negative_markers", [])
        if markers:
            blocks.append(f"Недовольство: {', '.join(markers)}")
        elif trigger == EscalationTrigger.HUMAN_REQUEST:
            blocks.append("Недовольство: прямая просьба переключить на человека.")

    # Уже выясненные факты.
    facts: list[str] = []
    if state.get("client_id"):
        facts.append(f"клиент авторизован ({state['client_id']})")
    turns = len([m for m in messages_to_history(state) if m["role"] == "client"])
    if turns > 1:
        facts.append(f"реплик клиента в диалоге: {turns}")
    if facts:
        blocks.append("Выяснено: " + "; ".join(facts))

    summary = " | ".join(blocks)

    # Гарантируем лимит длины (п. 5.1 — до 500 символов).
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary


def build_escalation_payload(state: AgentState) -> EscalationPayload:
    """Собрать полный пакет эскалации из состояния графа (п. 5.1)."""
    trigger = resolve_trigger(state)
    now = datetime.datetime.now().isoformat(timespec="seconds")

    metadata = {
        "channel": state.get("channel"),
        "dialog_start_time": state.get("dialog_start_time"),
        "escalation_time": now,
        "language": state.get("language", "ru"),
    }

    return EscalationPayload(
        session_id=state.get("session_id", "unknown-session"),
        client_id=state.get("client_id"),
        category=state.get("category"),
        trigger=trigger.value,
        summary=build_summary(state, trigger),
        dialog_history=messages_to_history(state),
        metadata=metadata,
        negative_markers=state.get("negative_markers", []),
    )


def client_notification(trigger: EscalationTrigger) -> str:
    """
    Текст уведомления клиента о переключении (п. 5.3 + порядок из 4.2.4 / 4.3.4).
    Без формальных извинений и без попыток «удержать» клиента.
    """
    if trigger == EscalationTrigger.INTENT:
        return ("Чтобы оформить это, переключаю вас на менеджера — он продолжит работу "
                "по вашему обращению.")
    if trigger == EscalationTrigger.HUMAN_REQUEST:
        return "Конечно, переключаю вас на специалиста."
    if trigger == EscalationTrigger.NEGATIVE:
        return ("Понимаю вас. Переключаю на специалиста, который разберётся с вашим "
                "вопросом.")
    if trigger == EscalationTrigger.OUT_OF_COMPETENCE:
        return "Этот вопрос вне моей компетенции — передаю его профильному специалисту."
    if trigger == EscalationTrigger.SUSPICIOUS:
        # Нейтральная форма, без раскрытия факта классификации (п. 4.4.2).
        return "Передаю ваше обращение специалисту для дальнейшей обработки."
    if trigger == EscalationTrigger.TECHNICAL:
        return ("Возникла техническая сложность — переключаю вас на оператора, чтобы "
                "не задерживать решение.")
    return "Переключаю вас на специалиста."


def simulate_handoff(payload: EscalationPayload, log_path: Optional[str] = None) -> dict:
    """
    Учебная симуляция передачи обращения оператору.

    В реальной системе здесь была бы постановка в очередь оператора с контекстом.
    В проекте — логируем структурированный пакет и (опционально) дописываем в JSONL.

    Returns:
        Пакет в виде словаря (для записи в состояние / проверок).
    """
    payload_dict = payload.to_dict()
    logger.info(
        "ЭСКАЛАЦИЯ session=%s trigger=%s client=%s category=%s",
        payload.session_id, payload.trigger, payload.client_id, payload.category,
    )
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload_dict, ensure_ascii=False) + "\n")
    return payload_dict
