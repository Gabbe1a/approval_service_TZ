# approval-service

[English](README.md) | **Русский**

Backend-сервис для согласования контента внутри рабочих пространств. Сервис создает,
показывает и обрабатывает заявки, а затем фиксирует одно итоговое решение: согласование,
отклонение или отмену.

Проект использует FastAPI, SQLAlchemy 2 и Alembic. Для локального запуска по умолчанию
подключается SQLite, а Docker Compose запускает PostgreSQL.

## Быстрый запуск через Docker

Установите Docker с плагином Compose и выполните:

```bash
docker compose up --build
```

Контейнер дождется готовности PostgreSQL, применит `alembic upgrade head` и запустит API по
адресу `http://localhost:8000`.

- Swagger UI: `http://localhost:8000/docs`
- проверка процесса: `GET http://localhost:8000/health`
- проверка готовности базы данных: `GET http://localhost:8000/ready`

Команда `docker compose down` остановит сервисы. Флаг `-v` дополнительно удалит локальный
том PostgreSQL.

## Локальный запуск с SQLite

Потребуется Python 3.11 или новее.

```bash
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:DATABASE_URL = "sqlite:///./approval.db"
alembic upgrade head
uvicorn app.main:app --reload
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export DATABASE_URL=sqlite:///./approval.db
alembic upgrade head
uvicorn app.main:app --reload
```

Приложение не создает таблицы при старте. Перед запуском примените миграции.

## Заглушка авторизации

Каждый запрос к `/api/v1/...` должен содержать три заголовка:

| Заголовок | Пример | Назначение |
|---|---|---|
| `X-Workspace-Id` | `ws_1` | Идентификатор workspace; должен совпадать с workspace в URL |
| `X-User-Id` | `usr_1` | Внешний идентификатор пользователя |
| `X-Actions` | `approval:read,approval:decide` | Разрешенные действия через запятую |

Доступные действия:

| Действие | Для каких операций требуется |
|---|---|
| `approval:read` | список заявок и просмотр одной заявки |
| `approval:create` | создание заявки |
| `approval:decide` | согласование и отклонение |
| `approval:cancel` | отмена заявки |

Заглушка предназначена для локального запуска и не принимает bearer-токены или учетные
данные. В рабочем окружении доверенный gateway или middleware должен проверить сервисный
токен и передать приложению workspace, пользователя и список действий.

Согласовать или отклонить заявку может назначенный reviewer с действием
`approval:decide`. Отменить заявку может пользователь с действием `approval:cancel` в том
же workspace.

## Идемпотентность

Каждый изменяющий `POST`-запрос должен содержать заголовок `Idempotency-Key`. Сервис
привязывает ключ к workspace и пользователю. Допустимая длина ключа: от 1 до 128 символов.
Можно использовать буквы, цифры, `.`, `_`, `:` и `-`.

- Повтор с тем же ключом, операцией и JSON возвращает исходный HTTP-статус и тело ответа без
  нового изменения. Заголовок `Idempotency-Replayed` получит значение `true`.
- Повторное использование ключа с другим телом или endpoint вернет
  `409 idempotency_key_reused`.
- Новый ключ не позволяет изменить уже принятое итоговое решение.

Сервис записывает бизнес-изменение, аудит, outbox-событие и результат идемпотентности в
одной транзакции базы данных.

## API

| Метод | Путь | Операция |
|---|---|---|
| `GET` | `/health` | проверка процесса |
| `GET` | `/ready` | проверка подключения к базе данных |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests` | создать заявку |
| `GET` | `/api/v1/workspaces/{workspace_id}/approval-requests` | получить список |
| `GET` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}` | получить заявку с историей |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/approve` | согласовать |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/reject` | отклонить |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/cancel` | отменить |

Endpoint списка принимает параметры `status`, `limit` и `offset`. Значение `limit` может
быть от 1 до 100, по умолчанию сервис возвращает 50 записей. Публичный JSON использует
`camelCase`.

Пример создания заявки:

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests \
  -H "Content-Type: application/json" \
  -H "X-Workspace-Id: ws_1" \
  -H "X-User-Id: usr_creator" \
  -H "X-Actions: approval:create" \
  -H "Idempotency-Key: create-pub-123-v1" \
  -d '{
    "sourceType": "publication",
    "sourceId": "pub_123",
    "title": "Instagram reel draft",
    "description": "Needs final approval",
    "reviewerUserIds": ["usr_1", "usr_2"]
  }'
```

Пример согласования:

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/REQUEST_ID/approve \
  -H "Content-Type: application/json" \
  -H "X-Workspace-Id: ws_1" \
  -H "X-User-Id: usr_1" \
  -H "X-Actions: approval:decide" \
  -H "Idempotency-Key: approve-request-v1" \
  -d '{"comment":"Approved"}'
```

Отклонение и отмена принимают тело `{"reason":"..."}`. Возможные состояния заявки:
`pending`, `approved`, `rejected` и `cancelled`.

## Защита чувствительных данных

Схемы запросов принимают только описанные поля. Внешние идентификаторы не могут содержать
URL или email. Валидатор отклоняет email, web-адреса, storage URL, bearer-токены, значения,
похожие на JWT, access keys и распространенные записи секретов. Ответ с ошибкой валидации
не повторяет введенное значение.

При неожиданной ошибке сервис пишет в лог сгенерированный request ID и класс исключения.
Аудит и outbox используют разрешенный набор полей. В них не попадают title, description,
comment, reason, provider payload и произвольные данные запроса.

## Тесты

```bash
python -m pytest
```

Тесты проверяют все endpoints, права доступа, изоляцию workspace, повторы запросов,
защиту итогового состояния, назначенных reviewers, аудит, outbox, чувствительный ввод и
Alembic-миграции в обе стороны.

## Структура проекта

```text
app/
  api.py          HTTP-маршруты
  auth.py         auth-заглушка и проверка Idempotency-Key
  models.py       модели SQLAlchemy
  schemas.py      проверка запросов, ответов и публичного текста
  services.py     транзакции и правила переходов
alembic/          миграции базы данных
tests/            API-тесты и тест миграции
Dockerfile
docker-compose.yml
DESIGN.md
README_RU.md
```

Файл [DESIGN.md](DESIGN.md) описывает модель данных, границы транзакций, outbox и известные
компромиссы.
