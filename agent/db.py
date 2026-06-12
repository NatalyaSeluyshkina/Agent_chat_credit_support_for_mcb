"""
db.py — доступ к базе клиентов (clients.sqlite) в режиме «только чтение».

Принцип минимальных привилегий: соединение открывается read-only (mode=ro),
поэтому ни один инструмент физически не может изменить данные банка, даже при
ошибке в SQL. Это поддерживает п. 2.3.2 РП-ОБ-005 (Помощник не выполняет
действий, изменяющих состояние).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Union

# Путь к БД по умолчанию: data/clients/clients.sqlite относительно корня репозитория.
DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "clients" / "clients.sqlite"
)


def get_connection(db_path: Optional[Union[Path, str]] = None) -> sqlite3.Connection:
    """
    Открыть соединение с БД клиентов в режиме «только чтение».

    Строки возвращаются как sqlite3.Row — к полям можно обращаться по имени
    (row["client_id"]) и легко превращать строку в обычный словарь.

    Args:
        db_path: путь к файлу базы. Если None — берётся DEFAULT_DB_PATH.

    Returns:
        sqlite3.Connection в режиме read-only.

    Raises:
        FileNotFoundError: если файл базы не существует.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"База клиентов не найдена: {path}")

    # mode=ro -> read-only. uri=True обязательно, иначе строка подключения
    # воспринимается как обычное имя файла, а не как URI.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """Превратить строку результата в обычный словарь (или вернуть None)."""
    if row is None:
        return None
    return dict(row)
