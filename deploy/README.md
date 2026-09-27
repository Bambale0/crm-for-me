# Автодеплой CRM

Сервер самостоятельно проверяет GitHub раз в минуту. Входящий порт или webhook
не требуется. Git и `gh` используют существующие локальные учётные данные root;
токен бота и пароль базы остаются в `/opt/crm-for-me/.env`.

## Последовательность

1. Проверить чистую рабочую копию на main, получить origin/main.
2. Найти последний push-запуск `.github/workflows/ci.yml` для точного SHA main.
   При ожидании, ошибке или отсутствии CI ничего не останавливать.
3. Собрать отдельный Docker-образ из `git archive` выбранного SHA. Локальный `.env`
   и резервные копии не попадают в сборку.
4. Повторно проверить рабочую копию, остановить bot, сделать `pg_dump -Fc`.
5. Fast-forward main, сохранить прежний образ с тегом rollback, применить миграции.
6. Пересоздать только bot; убедиться в правильном image ID и запуске Telegram polling.
7. Сохранить успешный SHA, образ, backup и ссылку CI в state.json.

Блокировка файла исключает одновременные деплои. Проверка Git не перезаписывает
локальные изменения. Новые коммиты, пришедшие во время сборки, обрабатываются
следующим запуском таймера. PostgreSQL и его том не пересоздаются при автодеплое.

## Установка на сервере

Нужны Docker Compose, Git, Python 3.10+ и GitHub CLI. Root должен иметь доступ
к origin без интерактивного SSH-agent и к `gh run list`. Проверки выполняются
с локальными credentials; новые токены в workflow не добавляются.

Из чистого `/opt/crm-for-me` на main:

```bash
install -D -m 755 deploy/autodeploy.py /usr/local/lib/crm-for-me/autodeploy.py
install -m 600 deploy/deploy.conf.example /etc/crm-for-me-deploy.conf
install -m 644 deploy/crm-for-me-deploy.service /etc/systemd/system/
install -m 644 deploy/crm-for-me-deploy.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now crm-for-me-deploy.timer
```

Настройки путей, репозитория, проекта Compose, образа и БД — в
`/etc/crm-for-me-deploy.conf`. При изменении самого механизма автодеплоя нужно
повторно установить скрипт/юниты командами выше; прикладные релизы автоматические.

## Управление

```bash
systemctl list-timers crm-for-me-deploy.timer
systemctl start crm-for-me-deploy.service
journalctl -u crm-for-me-deploy.service -n 50 --no-pager
cat /var/lib/crm-for-me-deploy/state.json
```

Проверить готовность релиза без сборки, миграций и перезапуска (с обычными путями
и настройками репозитория):

```bash
/usr/bin/python3 /usr/local/lib/crm-for-me/autodeploy.py --check
```

Остановить автоматические проверки: `systemctl disable --now crm-for-me-deploy.timer`.
Это не прерывает уже начатый сервис: дайте ему завершиться и проверьте его журнал.

## Сбой и восстановление

До остановки бота ошибка сборки/конфига оставляет рабочий контейнер как есть.
После остановки, если Alembic revision не изменился, восстанавливаются предыдущие
чистый checkout и Docker-образ; проверяется polling. Неудачный SHA записывается
и больше не запускается автоматически — следующий коммит будет проверен обычно.
Таймаут миграции останавливает её отдельный контейнер до проверки возможности отката.

Если revision изменился, старый код и старый dump автоматически не возвращаются:
новые данные могли быть записаны. Нужны исправляющий коммит или осознанное
восстановление; dump находится в backups, детали ошибки — в защищённом
`/var/lib/crm-for-me-deploy/last-command-error.log`. Доступ к state-каталогу только root.

После устранения причины можно явно повторить текущий SHA:

```bash
/usr/bin/python3 /usr/local/lib/crm-for-me/autodeploy.py --retry
```

Резервные копии и образы автоматически не удаляются. Срок хранения и очистка
контролируются владельцем сервера.

## Проверки

`tests/test_autodeploy.py` проверяет привязку CI к SHA/main/push, ожидание CI,
защиту локальных правок, порядок backup/миграций/проверки запуска, откат при
неизменной схеме и отказ от отката изменившейся схемы. Реальный запуск проверяется
через systemd и последующее сравнение Git SHA с меткой Docker-образа.
