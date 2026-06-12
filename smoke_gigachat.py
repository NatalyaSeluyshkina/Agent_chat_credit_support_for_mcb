"""
smoke_gigachat.py — быстрый боевой смоук агента на GigaChat.

Запуск из корня репозитория:
    python smoke_gigachat.py

Требует:
  - .env с GIGACHAT_CREDENTIALS (см. .env.example);
  - собранный индекс rag/index (он есть в архиве).

Прогоняет несколько представительных обращений разных типов через граф и
печатает категорию, outcome и ответ. Это ручной спот-чек, не полная E2E-оценка.
"""

from agent.graph import build_graph
from agent.llm import make_gigachat_deps
from agent.state import make_initial_state

# (описание, канал, client_id, вопрос)
CASES = [
    ("info / аноним", "chat_site", None,
     "Какие кредиты вы предлагаете малому бизнесу?"),
    ("transactional / авторизован", "chat_intern", "C-000001",
     "Какой остаток по моему кредиту и когда следующий платёж?"),
    ("transactional / аноним → отказ", "chat_site", None,
     "Сколько я должен по кредиту?"),
    ("эскалация: намерение", "chat_intern", "C-000004",
     "Хочу оформить кредит на оборудование"),
    ("эскалация: к человеку", "chat_intern", "C-000004",
     "Переключите меня на человека"),
    ("офтоп", "chat_site", None,
     "Расскажи анекдот про программистов"),
    ("коллизия: продуктовое > общее", "chat_intern", "C-000001",
     "Какой лимит долговой нагрузки действует для кредита Бизнес-Развитие?"),
]


def main() -> None:
    print("Собираю граф на GigaChat (это займёт пару секунд)...")
    graph = build_graph(make_gigachat_deps(index_dir="rag/index"))
    print("Готово.\n")

    for i, (name, channel, client_id, question) in enumerate(CASES, 1):
        state = make_initial_state(
            channel=channel, session_client_id=client_id, question=question,
        )
        out = graph.invoke(state, config={"configurable": {"thread_id": f"smoke-{i}"}})

        print("=" * 70)
        print(f"[{i}] {name}")
        print(f"    канал={channel} client_id={client_id}")
        print(f"    вопрос: {question}")
        print(f"    категория={out.get('category')} | outcome={out.get('outcome_type')}")
        print(f"    ответ: {out.get('answer')}")

        # Диагностика: что подтянул RAG и были ли данные клиента.
        sources = out.get("sources", [])
        if sources:
            tags = ", ".join(f"{s['source']} [{s['scope']}]" for s in sources[:5])
            print(f"    [debug] источники RAG: {tags}")
        tr = out.get("tool_results", {})
        if tr:
            if tr.get("access_denied"):
                print(f"    [debug] БД: доступ отклонён ({tr.get('reason')})")
            else:
                loans = len(tr.get("loans", []))
                apps = len(tr.get("applications", []))
                has_profile = tr.get("profile") is not None
                print(f"    [debug] БД: профиль={has_profile} кредитов={loans} заявок={apps}")
        print()


if __name__ == "__main__":
    main()
