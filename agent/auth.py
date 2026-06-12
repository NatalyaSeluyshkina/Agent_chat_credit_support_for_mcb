"""
auth.py — идентификация клиента по каналу обращения (задача 2.2).

Главное правило: уровень доступа определяется КАНАЛОМ, а не тем, назвал ли
клиент какой-то client_id. Это защищает от попыток выдать себя за другого
клиента на анонимном канале (см. п. 2.3.1 и раздел 7 РП-ОБ-005).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class IdentificationLevel(str, Enum):
    """Уровни идентификации по п. 2.3.1 РП-ОБ-005."""

    ANONYMOUS = "anonymous"  # только общие информационные запросы
    BASIC = "basic"          # авторизован в ИБ/мобайле: чтение СВОИХ договоров/заявок
    EXTENDED = "extended"    # подтверждение по SMS/коду; агент этот уровень НЕ использует


# Каналы, где клиент идентифицирован по факту авторизации (уровень «базовый»).
AUTHORIZED_CHANNELS = {"chat_intern", "mobile"}

# Анонимные каналы: персональные данные не выдаём.
ANONYMOUS_CHANNELS = {"chat_site", "contact_center"}


@dataclass
class ResolvedIdentity:
    """Результат идентификации обращения."""

    channel: str
    level: IdentificationLevel
    client_id: Optional[str]  # авторизованный client_id или None

    @property
    def is_authorized(self) -> bool:
        """True, если доступны персональные данные клиента (уровень не анонимный)."""
        return self.client_id is not None and self.level != IdentificationLevel.ANONYMOUS


def resolve_client(
    channel: str,
    session_client_id: Optional[str] = None,
) -> ResolvedIdentity:
    """
    Определить, кто обращается и какой у него уровень доступа.

    Args:
        channel: канал обращения (chat_intern / mobile / chat_site / contact_center).
        session_client_id: client_id, пришедший из авторизованной сессии канала.
            На анонимных каналах сюда может прийти «заявленный» id — ему мы
            НЕ доверяем.

    Returns:
        ResolvedIdentity: канал, уровень, авторизованный client_id (или None).
    """
    channel_normalized = (channel or "").strip().lower()

    if channel_normalized in AUTHORIZED_CHANNELS:
        # Авторизованный канал: доверяем client_id из сессии.
        if not session_client_id:
            # Канал авторизованный, но id не передан — трактуем как аноним, без данных.
            logger.warning(
                "Авторизованный канал %r без session_client_id — доступ как аноним.",
                channel,
            )
            return ResolvedIdentity(channel_normalized, IdentificationLevel.ANONYMOUS, None)
        return ResolvedIdentity(channel_normalized, IdentificationLevel.BASIC, session_client_id)

    if channel_normalized in ANONYMOUS_CHANNELS:
        # Анонимный канал: даже если id «прилетел», игнорируем его.
        if session_client_id:
            logger.warning(
                "На анонимном канале %r получен client_id %r — игнорируем "
                "(возможная попытка подмены).",
                channel,
                session_client_id,
            )
        return ResolvedIdentity(channel_normalized, IdentificationLevel.ANONYMOUS, None)

    # Неизвестный канал — по умолчанию максимально строго: аноним.
    logger.warning("Неизвестный канал %r — доступ как аноним.", channel)
    return ResolvedIdentity(channel_normalized, IdentificationLevel.ANONYMOUS, None)


class AccessDenied(Exception):
    """Доступ к данным клиента запрещён правилами идентификации/конфиденциальности."""


def ensure_self_access(identity: ResolvedIdentity, requested_client_id: str) -> str:
    """
    Проверить, что обращающийся вправе получить данные по requested_client_id.

    Разрешено только чтение СВОИХ данных авторизованным клиентом. Запрос данных
    другого клиента (даже на авторизованном канале) запрещён — это п. 7.2 РП-ОБ-005
    (защита данных третьих лиц) и должно вести к отказу + эскалации.

    Args:
        identity: результат resolve_client.
        requested_client_id: чей профиль/кредиты/заявки запрашиваются.

    Returns:
        Авторизованный client_id (для удобного вызова инструмента).

    Raises:
        AccessDenied: если канал анонимный или запрошен чужой client_id.
    """
    if not identity.is_authorized:
        raise AccessDenied("Персональные данные недоступны на анонимном канале.")
    if requested_client_id and requested_client_id != identity.client_id:
        raise AccessDenied("Запрос данных другого клиента запрещён (п. 7.2 РП-ОБ-005).")
    return identity.client_id
