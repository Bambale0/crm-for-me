# AGENTS.md — crm-for-me

## 1. Назначение проекта

crm-for-me — личный Telegram CRM-бот владельца для учёта заказчиков, проектов, разовых задач и дозаказов, регулярных услуг, аренды серверов, месячных начислений, выставленных счетов, оплат и задолженности.

Это не универсальная CRM и не публичный SaaS. Основная ценность продукта — минимальное количество действий для фиксации нового дозаказа и надёжный финансовый учёт по каждому заказчику.

Критический пользовательский сценарий:

~~~text
Переслать сообщение заказчика
→ определить заказчика
→ если заказчика нет — создать новую карточку
→ выбрать или создать проект
→ создать дозаказ
→ указать цену
→ выполнить
→ сумма попала в текущий расчётный месяц
→ регулярные услуги добавились в этот же период
→ сформировать счёт по требованию
→ выставить счёт
→ внести частичную или полную оплату
→ долг стал 0
~~~

Если изменение не улучшает или не поддерживает этот сценарий, оно не должно попадать в MVP без отдельного согласования.

## 2. Обязательное правило для любого агента

Перед изменениями:

1. Прочитать этот AGENTS.md полностью.
2. Изучить текущую структуру репозитория, README.md, pyproject.toml, миграции, тесты и существующие архитектурные решения.
3. Найти существующий источник конфигурации и существующие abstractions, прежде чем добавлять новые.
4. Не придумывать состояние production, результаты тестов, состояние CI, логи или содержимое файлов.
5. Если репозиторий уже содержит реализацию, сначала воспроизвести текущий сценарий и понять execution path, затем менять код.

Инструкции этого файла обязательны для всего репозитория, если более вложенный AGENTS.md явно не задаёт более специфичные правила для подкаталога.

## 3. Git workflow

После первоначальной инициализации репозитория запрещено напрямую писать в main.

Для каждой задачи:

~~~text
main
→ отдельная feature/fix ветка
→ изменения
→ тесты
→ self-review
→ PR
→ CI/review
→ merge только после зелёных обязательных проверок
~~~

Имена веток:

~~~text
feat/<short-description>
fix/<short-description>
refactor/<short-description>
docs/<short-description>
test/<short-description>
~~~

Не смешивать несвязанные изменения в одном PR.

Предпочитать небольшие, проверяемые PR вместо широкого переписывания проекта.

Не делать force-push в общие ветки без явной необходимости.

Не удалять историю и не переписывать уже опубликованные миграции без отдельного согласования.

Если auto-merge доступен и все обязательные проверки зелёные, разрешается включать auto-merge.

## 4. TDD и порядок исправления багов

Когда это практически возможно, работать через:

~~~text
reproduce
→ failing test
→ root cause
→ minimal fix
→ tests
→ regression verification
→ smoke
~~~

Для бага:

1. Воспроизвести исходный пользовательский сценарий.
2. Найти root cause.
3. Добавить regression test, который падает на старом поведении.
4. Внести минимальное исправление.
5. Запустить релевантные unit/integration tests.
6. Повторно проверить исходный сценарий.
7. Только после этого считать работу исправленной.

Запрещено менять тест только для того, чтобы скрыть неправильное поведение.

Зелёный CI сам по себе не является доказательством исправления пользовательского сценария.

## 5. Предпочтительный стек

Базовый стек проекта:

~~~text
Python 3.12+
aiogram 3.x
PostgreSQL
SQLAlchemy 2.x
Alembic
Pydantic Settings
pytest
pytest-asyncio
Docker / Docker Compose
~~~

Допустимо менять конкретную библиотеку только при наличии технического обоснования и без ухудшения поддержки проекта.

Бизнес-логика не должна находиться непосредственно в Telegram handlers.

Предпочтительная структура:

~~~text
app/
  bot/
  handlers/
  keyboards/
  middlewares/
  services/
  repositories/
  models/
  schemas/
  db/
  config/
tests/
alembic/
~~~

Не создавать слои ради слоёв. Архитектура должна оставаться простой и тестируемой.

## 6. Owner-only security model

Бот личный и должен работать только для владельца.

Конфигурация:

~~~env
OWNER_TELEGRAM_ID=
~~~

Требования:

- любой Telegram update должен проходить owner authorization;
- посторонние пользователи не должны получать доступ к данным;
- публичная регистрация отсутствует;
- нельзя доверять данным callback/FSM без повторной серверной проверки владельца;
- чувствительные операции должны проверять owner ID независимо от UI.

## 7. Работа с заказчиками

### Новый заказчик

Основной entry point — пересланное сообщение.

Если Telegram предоставляет исходный telegram_user_id:

~~~text
lookup client by telegram_user_id
~~~

Если клиент существует — использовать существующую карточку.

Если клиента нет — предложить создать новую карточку.

Минимальная карточка:

~~~text
display_name
telegram_user_id nullable
telegram_username nullable
created_at
updated_at
~~~

Дополнительные данные заполняются владельцем вручную.

### Если sender ID скрыт

Имя или username нельзя считать надёжным уникальным ID.

Если Telegram не отдаёт пригодный ID:

~~~text
→ предложить выбрать существующего клиента
или
→ создать нового клиента вручную
~~~

Запрещено генерировать фиктивный Telegram ID.

### Username

telegram_username не является primary identity. Username может измениться.

Если telegram_user_id известен, именно он является стабильным идентификатором Telegram-пользователя.

## 8. Карточка клиента

Основные данные:

~~~text
Client
- id
- telegram_user_id nullable
- telegram_username nullable
- display_name
- company_name nullable
- comment nullable
- created_at
- updated_at
- archived_at nullable
~~~

Произвольные данные — через отдельные поля:

~~~text
ClientField
- id
- client_id
- name
- value
- sort_order
- created_at
- updated_at
~~~

Примеры: телефон, email, номер договора, reference на Bitwarden, внутренняя заметка.

Не превращать карточку в обязательную многостраничную анкету.

## 9. Проекты

Один клиент может иметь несколько проектов.

~~~text
Project
- id
- client_id
- name
- description nullable
- status
- created_at
- updated_at
- archived_at nullable
~~~

Статусы:

~~~text
ACTIVE
PAUSED
ARCHIVED
~~~

Технические данные проекта вводятся владельцем вручную.

Не пытаться автоматически угадывать repo, IP, domain, server из сообщений клиента.

Для произвольных технических данных использовать ProjectField.

Примеры:

~~~text
Server → happy-fox
IP → 1.2.3.4
Domain → example.ru
Repo → github.com/...
Production → https://...
Secrets → Bitwarden → Project / Production
~~~

## 10. Секреты

Запрещено хранить в CRM:

- API keys;
- пароли;
- access/refresh tokens;
- private keys;
- SSH private keys;
- recovery codes.

Можно хранить только reference, например:

~~~text
Secrets: Bitwarden → Foxgen Production
~~~

Никогда не логировать секреты.

Не добавлять секреты в fixtures, тесты, README, .env.example или git history.

## 11. Задачи и дозаказы

Основная сущность:

~~~text
Task
- id
- client_id
- project_id nullable
- title
- description nullable
- amount
- currency
- status
- created_at
- started_at nullable
- completed_at nullable
- cancelled_at nullable
~~~

Статусы:

~~~text
NEW
IN_PROGRESS
DONE
CANCELLED
~~~

Основные переходы:

~~~text
NEW → IN_PROGRESS
NEW → DONE
NEW → CANCELLED
IN_PROGRESS → DONE
IN_PROGRESS → CANCELLED
~~~

При DONE необходимо атомарно зафиксировать completed_at.

Повторное нажатие DONE не должно создавать вторую финансовую позицию.

## 12. Исходное сообщение заказчика

Исходный текст нельзя терять после редактирования задачи.

Использовать отдельный source record, например:

~~~text
TaskSource
- id
- task_id
- source_type
- telegram_chat_id nullable
- telegram_message_id nullable
- forwarded_user_id nullable
- original_text nullable
- created_at
~~~

Отредактированное описание задачи и оригинальное сообщение — разные данные.

Если одно и то же сообщение переслано повторно, по возможности определить duplicate и не создавать задачу молча второй раз.

## 13. UX создания дозаказа

Критический быстрый flow:

~~~text
forward message
→ client resolved
→ [Создать дозаказ]
→ project selected
→ message text prefilled as description
→ enter amount
→ task created
~~~

Цель: типичный дозаказ должен фиксироваться за несколько действий.

Не добавлять обязательные поля, которые не нужны для финансового или рабочего учёта.

## 14. Деньги

Никогда не использовать float для денег.

Использовать Decimal.

В PostgreSQL использовать NUMERIC.

В MVP основная валюта RUB, но поле currency должно существовать в модели.

## 15. Регулярные услуги

Регулярные начисления нужны минимум для:

- аренды сервера;
- сопровождения;
- хостинга;
- поддержки;
- других ежемесячных услуг.

Пример модели:

~~~text
RecurringCharge
- id
- client_id
- project_id nullable
- title
- amount
- currency
- frequency
- active_from
- active_until nullable
- is_active
- created_at
- updated_at
~~~

Для MVP достаточно frequency = MONTHLY.

Генерация регулярных начислений должна быть идемпотентной.

Повторный запуск и следующий счёт в том же месяце не могут начислить аренду сервера дважды.

## 16. Расчётные периоды

По требованию владельца счета выставляются в любой момент, несколько раз в месяц.
Месяц сохраняется только как расчётная группировка начислений и отчётности.
У клиента может быть несколько выставленных счетов и максимум один открытый
черновик на расчётный месяц. Завершение новой задачи после выставления предыдущего
счёта должно добавлять её в новый черновик, не изменяя старый snapshot.

Модель (историческое имя BillingPeriod):

~~~text
BillingPeriod
- id
- client_id
- year
- month
- status
- created_at
- issued_at nullable
- closed_at nullable
~~~

DB constraint:

~~~text
UNIQUE INDEX(client_id, year, month) WHERE status = 'DRAFT'
~~~

Статусы:

~~~text
DRAFT
ISSUED
PARTIALLY_PAID
PAID
CANCELLED
SUPERSEDED
~~~

Выполненная задача относится к месяцу по completed_at.

## 17. Позиции счёта и snapshot

Использовать отдельный snapshot:

~~~text
InvoiceItem
- id
- billing_period_id
- source_type
- source_id nullable
- description
- quantity
- unit_price
- amount
- created_at
~~~

Типы source:

~~~text
TASK
RECURRING_CHARGE
MANUAL
ADJUSTMENT
~~~

До выставления счёта DRAFT может пересобираться.

После перехода DRAFT → ISSUED позиции и цены являются историческим snapshot.

Изменение Task.amount или RecurringCharge.amount после выставления не должно менять старый счёт.

Это критическое финансовое правило.

По явному запросу владельца название и сумма позиции могут корректироваться,
включая частично оплаченные счета. Исходный InvoiceItem не перезаписывается:
InvoiceItemCorrection хранит предыдущие и новые значения, время и уникальный
ключ операции. Для итогов используется последняя корректировка каждой позиции.
Общая сумма не может стать меньше уже внесённых оплат. Корректировки и платежи
сериализуются одной блокировкой клиента. Ручная корректировка черновика имеет
приоритет над последующей пересборкой из источников. Отменённые и заменённые
счета корректировать нельзя.

По запросу владельца разрешено переносить выбранные позиции между неоплаченными
счетами одного клиента через явное подтверждение. Исходные счета и позиции
сохраняются без изменения сумм; исходные счета помечаются SUPERSEDED.
Создаются общий счёт и счета на остатки, с origin_item_id для каждой позиции.
SUPERSEDED не входит в начисления и долг и не принимает оплаты. Перенос сохраняет
цены всех затронутых позиций, включая исходные черновики. Статус и расчётный месяц
общего счёта берутся из счёта назначения, остатки сохраняют исходные значения.
Регулярные начисления сверяются также с SUPERSEDED, чтобы не начислить услугу повторно.
Операция атомарна, идемпотентна и сериализована с платежами блокировкой клиента.

## 18. Оплаты

Оплата не является статусом задачи.

~~~text
Payment
- id
- billing_period_id
- amount
- currency
- paid_at
- comment nullable
- created_at
~~~

Один счёт может иметь несколько платежей.

Расчёты:

~~~text
invoice_total = SUM(invoice_items.amount)
paid_total = SUM(payments.amount)
debt = invoice_total - paid_total
~~~

Пример:

~~~text
invoice_total = 20 000
payment #1 = 10 000
→ PARTIALLY_PAID
→ debt = 10 000

payment #2 = 10 000
→ PAID
→ debt = 0
~~~

Не допускать отрицательный долг без специально реализованной логики переплаты.
В сводке и карточке клиента долг включает все действующие счета, в том числе
черновики. CANCELLED и SUPERSEDED не учитываются. Ввод оплаты из черновика
после проверки суммы атомарно выставляет счёт и проводит оплату.

## 19. Финансовая целостность

Финансовые операции — наиболее критичная часть системы.

Обязательно:

- DB transactions;
- unique constraints;
- idempotency;
- защита от повторных callbacks;
- защита от duplicate Telegram updates;
- snapshot выставленного счёта;
- отсутствие hard delete финансовой истории.

Запрещено физически удалять выставленные счета и проведённые платежи без специально реализованного механизма корректировки.

Предпочитать CANCELLED, ARCHIVED или ADJUSTMENT вместо silent delete.

## 20. Concurrency

Учитывать:

- двойной tap inline button;
- повторный Telegram update;
- retry Telegram API;
- restart worker между callback и commit;
- два параллельных действия по одному счёту.

Критические state transitions должны быть защищены не только Python-проверкой, но и DB transaction/constraint, где это возможно.

## 21. FSM

FSM использовать только как UX-механизм коротких flows.

Примеры:

~~~text
CreateClient
CreateProject
CreateTask
EditTaskAmount
AddClientField
AddProjectField
AddRecurringCharge
AddPayment
CreateReminder
~~~

Критическое бизнес-состояние нельзя хранить только в FSM storage.

После рестарта процесса уже сохранённая сущность не должна исчезать или становиться неконсистентной.

## 22. Конфигурация

Все изменяемые параметры идут через configuration source.

Не хардкодить:

- Telegram token;
- owner ID;
- DB DSN;
- timezone;
- URL;
- размеры pool;
- timeout;
- provider settings;
- изменяемые бизнес-лимиты.

Использовать Pydantic Settings или существующий config layer.

Обновлять .env.example при добавлении новых переменных.

В .env.example — только безопасные placeholder values.

## 23. Timezone

В БД даты хранить в UTC.

Показывать владельцу в timezone из конфигурации.

Не хардкодить локальную timezone в бизнес-логике.

## 24. Миграции

Все изменения production schema только через Alembic.

Не использовать Base.metadata.create_all() как production migration mechanism.

Каждая schema change должна иметь migration.

Перед merge проверить upgrade на чистой БД.

Для небезопасных изменений предусматривать rollback/recovery plan.

## 25. Repository/service boundaries

Handlers:

- принимают Telegram update;
- валидируют вход;
- вызывают service;
- формируют ответ.

Services:

- содержат use-case/business logic;
- управляют транзакционными сценариями;
- не зависят от конкретной Telegram keyboard разметки.

Repositories:

- отвечают за persistence/query;
- не содержат UI logic.

Не создавать циклические зависимости.

Не обращаться к БД хаотично из callback handlers.

## 26. Логи и observability

Использовать структурированные логи.

Минимально полезные correlation fields:

~~~text
event
telegram_update_id
owner_id
client_id
project_id
task_id
billing_period_id
payment_id
exception_type
exception_message
~~~

Основные события:

~~~text
client_created
client_updated
project_created
task_created
task_status_changed
task_completed
billing_period_created
invoice_issued
payment_added
payment_status_changed
recurring_charge_created
recurring_charge_applied
telegram_update_failed
~~~

Не логировать токены, пароли и приватные ключи.

Лог должен помогать восстановить execution path:

~~~text
Telegram update
→ handler
→ service
→ repository/DB
→ response
~~~

## 27. Error handling

Пользователь не должен видеть traceback.

Ожидаемые ошибки должны превращаться в понятное сообщение.

Неожиданные ошибки:

1. логируются со stack trace;
2. содержат correlation IDs;
3. не оставляют частично записанные финансовые данные;
4. не раскрывают secrets.

Внешние API, если появятся, считать ненадёжной границей: timeout, retry, rate limits, malformed response, provider outage, duplicate/delayed callback.

## 28. Поиск

Минимальный поиск должен поддерживать:

- имя клиента;
- Telegram username;
- компанию;
- проект;
- title задачи;
- description задачи;
- ClientField.value;
- ProjectField.value.

Поиск case-insensitive.

Не добавлять отдельный search engine в MVP без доказанной необходимости.

## 29. Напоминания

Напоминания в MVP идут только владельцу.

Не отправлять сообщения заказчикам автоматически без отдельного требования.

## 30. MVP scope — что не делать

Без отдельного задания запрещено расширять MVP на:

- Mini App;
- web CRM;
- мультипользовательский SaaS;
- роли менеджеров;
- AI-анализ заказчиков;
- автоматическое чтение личных чатов владельца;
- генерацию юридических договоров;
- генерацию актов;
- 1С;
- налоговую;
- banking API;
- GitHub API automation;
- автоматическое управление серверами;
- secret storage;
- сложную BI;
- Kafka/Celery/Redis только «на будущее»;
- микросервисную архитектуру.

Если задача решается одной БД и одним bot service — не вводить распределённую систему.

## 31. Обязательные тесты

Минимальный unit/regression набор.

### Client

- создание клиента;
- lookup по telegram_user_id;
- создание клиента без Telegram ID;
- отсутствие duplicate client для одного Telegram ID.

### Project

- создание проекта;
- принадлежность правильному клиенту;
- archive без потери истории.

### Task

- создание задачи;
- переходы NEW/IN_PROGRESS/DONE/CANCELLED;
- DONE выставляет completed_at;
- повторный DONE идемпотентен;
- исходное сообщение сохраняется отдельно.

### Forward flow

~~~text
unknown sender
→ client not found
→ create client
→ create project
→ create task
~~~

~~~text
known sender
→ resolve existing client
→ no duplicate client
~~~

~~~text
hidden sender ID
→ manual resolution
→ no fake ID
~~~

### Duplicate source

~~~text
same forwarded message twice
→ warning
→ no automatic duplicate task
~~~

### Billing

- unique open draft per client/year/month;
- several independently payable issued invoices per month;
- no issuing an empty/zero-total invoice;
- no payment input when debt is zero;
- DONE task appears exactly once;
- recurring charge appears exactly once;
- repeat generation is idempotent;
- invoice total uses Decimal;
- issued snapshot does not change after task edit.

### Payments

~~~text
20k invoice
+ 10k payment
→ PARTIALLY_PAID
→ debt 10k
~~~

~~~text
+ 10k payment
→ PAID
→ debt 0
~~~

Duplicate callback/payment action must not double-book payment.

## 32. Integration tests

Критичные repository/service тесты запускать против настоящего PostgreSQL test instance/container.

Проверять:

- unique constraints;
- DB transactions;
- concurrent state transitions;
- Decimal/NUMERIC behaviour;
- billing generation;
- invoice snapshot;
- partial payments;
- idempotency.

SQLite не считать достаточной заменой PostgreSQL для финансовых integration tests.

## 33. Smoke test перед завершением MVP

Фактически пройти:

~~~text
1. Запустить bot + PostgreSQL.
2. Переслать сообщение неизвестного клиента.
3. Создать новую карточку.
4. Создать проект.
5. Добавить project field.
6. Создать задачу из forwarded message.
7. Цена 5 000 ₽.
8. NEW → IN_PROGRESS.
9. IN_PROGRESS → DONE.
10. Проверить 5 000 ₽ в текущем billing period.
11. Добавить recurring charge: сервер 5 000 ₽/мес.
12. Убедиться, что итог DRAFT = 10 000 ₽.
13. Повторить billing generation и убедиться, что итог всё ещё 10 000 ₽.
14. ISSUE invoice.
15. Добавить payment 4 000 ₽.
16. Проверить PARTIALLY_PAID и debt 6 000 ₽.
17. Добавить payment 6 000 ₽.
18. Проверить PAID и debt 0.
19. Изменить Task.amount.
20. Проверить, что issued invoice остался 10 000 ₽.
~~~

Не считать MVP готовым без этого сценария либо честно указать, какой шаг невозможно проверить и почему.

## 34. CI

Минимальный CI должен запускать:

~~~text
lint
tests
migration check
~~~

При появлении type checker добавить его как обязательную проверку.

PR нельзя считать готовым при красных обязательных checks.

## 35. Definition of Done

Задача считается завершённой, только если:

1. Root cause или требование понятны.
2. Изменение минимально и соответствует архитектуре.
3. Добавлены или обновлены тесты.
4. Все релевантные тесты реально запущены.
5. Миграции проверены, если БД менялась.
6. Исходный пользовательский сценарий проверен.
7. Логи и observability достаточны для диагностики.
8. Нет секретов в diff.
9. Документация обновлена, если поведение, config или schema изменились.
10. PR прошёл self-review.
11. Известные риски указаны явно.

## 36. Формат отчёта агента

После работы сообщать:

### Найдено
Что реально обнаружено.

### Причина
Root cause или техническое обоснование изменений.

### Исправлено
Какие файлы и поведение изменены.

### Проверено
Конкретные команды и реальные результаты.

### Smoke
Какой пользовательский сценарий фактически пройден.

### Риски
Что осталось непроверенным или потенциально проблемным.

Запрещено писать «всё работает», «готово» или «исправлено», если это не подтверждено фактической проверкой.

## 37. Главный продуктовый принцип

Бот должен уменьшать бюрократию владельца.

Правильный ежедневный сценарий:

~~~text
заказчик написал
→ владелец переслал сообщение
→ бот узнал клиента
→ нажать "Дозаказ"
→ выбрать проект
→ ввести цену
→ задача сохранена
~~~

После выполнения:

~~~text
открыть задачу
→ "Выполнено"
~~~

После этого финансовый учёт должен выполняться системой.

В конце месяца владелец должен видеть:

~~~text
кто
за что
сколько начислено
сколько выставлено
сколько оплачено
сколько должен
~~~

Любое усложнение продукта должно оцениваться относительно этого принципа.
