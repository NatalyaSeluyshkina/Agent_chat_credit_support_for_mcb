"""
Дымовой тест слоя 1 (tools + auth). Запуск: python -m tests.test_layer1
Не требует GigaChat — работает на локальном clients.sqlite.
"""

from agent.auth import (
    AccessDenied,
    IdentificationLevel,
    ensure_self_access,
    resolve_client,
)
import datetime

from agent.tools import (
    CLIENT_TOOLS,
    get_active_loans,
    get_applications,
    get_client_info,
    get_eligible_products,
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_auth() -> None:
    section("Авторизация по каналу (resolve_client)")

    cases = [
        ("chat_intern", "C-000001"),   # авторизован с id
        ("mobile", "C-000007"),        # авторизован с id
        ("chat_intern", None),         # авторизован, но id не передан -> аноним
        ("chat_site", None),           # аноним
        ("chat_site", "C-000001"),     # АНОМАЛИЯ: id на анонимном канале -> игнор
        ("contact_center", "C-000002"),# аноним (id игнорируем)
        ("telegram", "C-000003"),      # неизвестный канал -> аноним
    ]
    for channel, sid in cases:
        identity = resolve_client(channel, sid)
        print(
            f"{channel:<15} sid={str(sid):<10} -> "
            f"level={identity.level.value:<9} client_id={identity.client_id} "
            f"authorized={identity.is_authorized}"
        )

    # Проверки-утверждения.
    assert resolve_client("chat_intern", "C-000001").client_id == "C-000001"
    assert resolve_client("chat_site", "C-000001").client_id is None  # подмену не пускаем
    assert resolve_client("chat_intern", None).level == IdentificationLevel.ANONYMOUS
    assert resolve_client("telegram", "C-1").client_id is None
    print("OK: авторизация ведёт себя по регламенту.")


def test_access_control() -> None:
    section("Контроль доступа (ensure_self_access)")

    authorized = resolve_client("chat_intern", "C-000001")
    anonymous = resolve_client("chat_site", None)

    # Свои данные — можно.
    assert ensure_self_access(authorized, "C-000001") == "C-000001"
    print("OK: авторизованный читает свои данные.")

    # Чужие данные — нельзя даже авторизованному.
    try:
        ensure_self_access(authorized, "C-000002")
        raise AssertionError("Должно было выбросить AccessDenied (чужой клиент).")
    except AccessDenied as exc:
        print(f"OK: чужой client_id отклонён -> {exc}")

    # Аноним — нельзя ничего персонального.
    try:
        ensure_self_access(anonymous, "C-000001")
        raise AssertionError("Должно было выбросить AccessDenied (аноним).")
    except AccessDenied as exc:
        print(f"OK: анонимный доступ отклонён -> {exc}")


def test_tools() -> None:
    section("Инструменты БД на реальных данных")

    # C-000001: есть профиль и 1 кредит.
    profile = get_client_info("C-000001")
    assert profile is not None
    print(
        f"C-000001: {profile['legal_form']}, {profile['industry']}, "
        f"скоринг={profile['credit_score']}, счёт={profile['has_account_in_bank']}"
    )

    loans = get_active_loans("C-000001")
    print(f"C-000001 кредитов: {len(loans)}")
    if loans:
        loan = loans[0]
        print(
            f"  {loan['contract_id']} {loan['product_code']} остаток="
            f"{loan['principal_outstanding']} платёж={loan['next_payment_amount']} "
            f"@ {loan['next_payment_date']} просрочка={loan['overdue_days']}д"
        )

    # C-000016: есть заявки.
    apps = get_applications("C-000016")
    print(f"C-000016 заявок: {len(apps)}")
    for app in apps:
        print(
            f"  {app['application_id']} {app['product_code']} "
            f"статус={app['status']} решение={app['decision']}"
        )

    # Несуществующий клиент — честный пустой результат, без падения.
    assert get_client_info("C-999999") is None
    assert get_active_loans("C-999999") == []
    assert get_applications("C-999999") == []
    print("OK: несуществующий клиент -> None / пустые списки (без галлюцинаций).")


def test_langchain_tools() -> None:
    section("LangChain-обёртки (описания для LLM)")
    for t in CLIENT_TOOLS:
        first_line = t.description.strip().splitlines()[0]
        print(f"{t.name}: {first_line}")
    # Инструмент вызывается через .invoke со словарём аргументов.
    result = CLIENT_TOOLS[0].invoke({"client_id": "C-000001"})
    assert result is not None and result["client_id"] == "C-000001"
    print("OK: client_info_tool.invoke вернул профиль.")


def test_eligible_products() -> None:
    section("Детерминированный подбор продуктов (get_eligible_products)")
    today = datetime.date(2026, 6, 12)

    def eligible_codes(client_id: str) -> set[str]:
        res = get_eligible_products(client_id, today=today)
        return {p["code"] for p in res["products"] if p["eligible"]}

    # C-000011 (ИП, выручка 5 млн < 6 млн): Бизнес-Оборот недоступен, Бизнес-Старт — да.
    codes = eligible_codes("C-000011")
    assert "BUSINESS_OBOROT" not in codes, "Оборот не должен проходить при выручке < 6 млн"
    assert "BUSINESS_START" in codes, "Бизнес-Старт должен подходить"

    # C-000007 (основной ОКВЭД 64 — финансы): Бизнес-Старт закрыт по отрасли (п. 2.4.4).
    assert "BUSINESS_START" not in eligible_codes("C-000007"), "Старт закрыт для ОКВЭД 64"

    # Скоринг НЕ должен утекать в причины (п. 6.2): ни буквы рейтинга, ни «скоринг=».
    dump = str(get_eligible_products("C-000020", today=today))
    assert "скоринг=" not in dump.lower() and "credit_score" not in dump
    print("OK: подбор по порогам корректен, значение скоринга не раскрывается.")


if __name__ == "__main__":
    test_auth()
    test_access_control()
    test_tools()
    test_langchain_tools()
    test_eligible_products()
    print("\nВсе проверки слоя 1 пройдены.")
