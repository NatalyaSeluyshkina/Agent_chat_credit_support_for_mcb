"""
tools.py — инструменты доступа к БД клиентов (задача 2.1).

Три read-only инструмента: профиль клиента, действующие кредиты, заявки.
Каждый оформлен дважды:
  1) как обычная функция (get_client_info / get_active_loans / get_applications) —
     её детерминированно вызывает узел query_db;
  2) как LangChain-инструмент с описанием для LLM (CLIENT_TOOLS) — на случай
     tool-calling и для соответствия требованию «tool-функции с описаниями для LLM».

Инструменты НЕ проверяют авторизацию сами — они принимают уже авторизованный
client_id. Проверку выполняет вызывающий код через auth.ensure_self_access, а
соединение открыто read-only (db.get_connection). Это разделение делает
инструменты простыми и тестируемыми, а правило доступа — единым (в auth.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from langchain_core.tools import tool

from agent.db import get_connection, row_to_dict

DbPath = Optional[Union[Path, str]]


def get_client_info(client_id: str, db_path: DbPath = None) -> Optional[dict]:
    """
    Вернуть профиль клиента по client_id.

    Returns:
        Словарь с полями таблицы clients, либо None, если клиент не найден.
    """
    connection = get_connection(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return row_to_dict(row)
    finally:
        connection.close()


def get_active_loans(client_id: str, db_path: DbPath = None) -> list[dict]:
    """
    Вернуть действующие кредитные договоры клиента (от свежих к старым).

    Все записи в credit_products — это действующие договоры, поэтому возвращаем
    их все.

    Returns:
        Список словарей (пустой, если кредитов нет).
    """
    connection = get_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM credit_products WHERE client_id = ? "
            "ORDER BY contract_date DESC",
            (client_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_applications(client_id: str, db_path: DbPath = None) -> list[dict]:
    """
    Вернуть историю заявок клиента (включая активные), от свежих к старым.

    Returns:
        Список словарей (пустой, если заявок нет).
    """
    connection = get_connection(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM applications WHERE client_id = ? "
            "ORDER BY application_date DESC",
            (client_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


# --- LangChain-обёртки с описаниями для LLM ------------------------------------
# Узел query_db вызывает функции выше напрямую. Обёртки ниже нужны для требования
# «описания для LLM» и на случай tool-calling-режима. Авторизация при этом
# по-прежнему обеспечивается выше по стеку (узел query_db + auth.ensure_self_access).


@tool
def client_info_tool(client_id: str) -> Optional[dict]:
    """Профиль авторизованного клиента: форма (ООО/ИП/Самозанятый), отрасль,
    выручка, скоринг, отношения с банком (счёт, зарплатный проект), история
    просрочек. Использовать ТОЛЬКО для собственных данных авторизованного клиента."""
    return get_client_info(client_id)


@tool
def active_loans_tool(client_id: str) -> list[dict]:
    """Действующие кредитные договоры авторизованного клиента: остаток долга,
    ставка, срок, дата и сумма следующего платежа, просрочка, обеспечение.
    Только для собственных данных авторизованного клиента."""
    return get_active_loans(client_id)


@tool
def applications_tool(client_id: str) -> list[dict]:
    """История заявок авторизованного клиента со статусами и решениями.
    Только для собственных данных авторизованного клиента."""
    return get_applications(client_id)


# Список инструментов для биндинга к LLM (если потребуется tool-calling).
CLIENT_TOOLS = [client_info_tool, active_loans_tool, applications_tool]
