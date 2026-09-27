# CRM for Me

Личный Telegram-бот-CRM для одного владельца. Бот ведёт клиентов, проекты,
задачи, счета и оплаты. Вся бизнес-логика вынесена в стабильное ядро
(`app/services`, `app/repositories`, `app/models`), а aiogram-слой
(`app/handlers`, `app/keyboards`, `app/states`, `app/middlewares`) служит
тонкой обвязкой поверх него.

## Возможности

- **Клиенты** — карточка клиента, кастомные поля, заметки, список.
- **Проекты** — проект клиента с полями и задачами.
- **Задачи** — статусы `NEW → IN_PROGRESS → DONE` (или `CANCELLED`).
  Завершённые задачи автоматически попадают в черновик счёта за текущий месяц.
- **Пересылка сообщений** — пересланное сообщение от клиента превращается в
  задачу; повторная обработка одного и того же сообщения защищена от дублей
  (`dedup_key`).
- **Биллинг** — месячные периоды, idempotent-сверка позиций, выставление счёта,
  частичная/полная оплата, защита от переплаты.
- **Повторяющиеся начисления** — ежемесячные услуги, учитываемые в черновике.
- **Дашборд** — статистика за месяц (начислено / выставлено / оплачено / долг).
- **Поиск** — по клиентам, проектам и задачам.

## Стек

- Python 3.12+
- [aiogram 3.x](https://github.com/aiogram/aiogram)
- SQLAlchemy 2.x (async, asyncpg) + Alembic
- PostgreSQL 16
- pydantic-settings

## Структура

```
app/
  main.py            # точка входа бота (polling, middleware, логирование)
  config.py          # настройки из окружения
  states.py          # FSM-состояния
  keyboards/         # inline-клавиатуры
  middlewares/       # OwnerOnlyMiddleware (доступ только владельцу)
  handlers/          # aiogram-обработчики (common, client, task, forward, billing)
  services/          # бизнес-логика (ядро)
  repositories/      # SQLAlchemy-репозитории
  models/            # ORM-модели
  db/                # engine, session_factory, Base, session_scope
alembic/             # миграции
tests/               # pytest (ядро) + регрессионные тесты
```

## Настройка

1. Скопируйте `.env.example` в `.env` и заполните:

   ```dotenv
   BOT_TOKEN=...
   OWNER_TELEGRAM_ID=<ваш telegram user id>
   DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm
   ALEMBIC_DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm
   TIMEZONE=Europe/Moscow
   DEFAULT_CURRENCY=RUB
   LOG_LEVEL=INFO
   ```

2. Установите зависимости:

   ```bash
   py -3.14 -m pip install -e .[dev]
   ```

## Запуск (локально)

```bash
docker compose up -d db          # только PostgreSQL
alembic upgrade head             # применить миграции
py -3.14 -m app.main             # запустить бота
```

Полный стек в контейнерах:

```bash
docker compose up --build
```

## Миграции

```bash
alembic upgrade head             # накатить
alembic downgrade -1             # откатить на один шаг
alembic revision --autogenerate -m "..."   # новая миграция
```

Миграции выполняются через асинхронный драйвер `asyncpg` (тот же, что и
приложение).

## Тесты

```bash
py -3.14 -m pytest -q
```

Тесты ядра используют in-memory SQLite. Интеграция с PostgreSQL и живой
Telegram (smoke) остаётся ручной проверкой в контейнерной среде.

## Команды бота

- `/start` — главное меню.
- Кнопки меню: клиенты, дашборд, поиск.
- Пересланное сообщение — создание клиента/задачи из пересылки.

## Примечания по безопасности

- Доступ к боту разрешён только `OWNER_TELEGRAM_ID` (проверяется middleware).
- Секреты хранятся только в окружении и никогда не попадают в логи, код или БД.
