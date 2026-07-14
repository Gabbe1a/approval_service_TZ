# approval-service

Backend service for workspace-scoped content approval requests. It supports creating,
listing and reading requests, then approving, rejecting or cancelling them with an
immutable final state.

The implementation uses FastAPI, SQLAlchemy 2, Alembic and SQLite by default. The Docker
Compose profile uses PostgreSQL.

## Quick start with Docker

Requirements: Docker with the Compose plugin.

```bash
docker compose up --build
```

The container waits for PostgreSQL, applies `alembic upgrade head`, and starts the API on
`http://localhost:8000`. Useful endpoints:

- Swagger UI: `http://localhost:8000/docs`
- liveness: `GET http://localhost:8000/health`
- readiness: `GET http://localhost:8000/ready`

Stop the stack with `docker compose down`. Add `-v` only when the local database volume
should also be deleted.

## Local start with SQLite

Python 3.11 or newer is required.

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

The application does not create tables implicitly. Always apply migrations before starting
it.

## Auth stub

Every `/api/v1/...` request must contain three headers:

| Header | Example | Meaning |
|---|---|---|
| `X-Workspace-Id` | `ws_1` | Authenticated workspace; must equal the workspace in the URL |
| `X-User-Id` | `usr_1` | Authenticated external user identifier |
| `X-Actions` | `approval:read,approval:decide` | Comma-separated granted actions |

Supported actions:

| Action | Required for |
|---|---|
| `approval:read` | list and detail endpoints |
| `approval:create` | create endpoint |
| `approval:decide` | approve and reject endpoints |
| `approval:cancel` | cancel endpoint |

This is intentionally a local auth stub. It accepts no bearer token or credentials. In a
real deployment, a trusted gateway or identity middleware would build the same auth context
from a verified service token.

Approve/reject additionally requires `X-User-Id` to be one of the assigned reviewers.
Cancellation is available to any user with `approval:cancel` in the workspace.

## Idempotency

Every mutating `POST` requires an `Idempotency-Key` header. The key is scoped to a workspace,
bound to its actor, and may contain 1-128 letters, digits, `.`, `_`, `:` or `-`.

- The same key, operation and JSON body replay the original status/body without another
  state change. The response header `Idempotency-Replayed` is `true`.
- Reusing the key for another body or endpoint returns `409 idempotency_key_reused`.
- A new key cannot move an already final request to another final state.

Idempotency records, the business change, audit row and outbox event are committed in the
same database transaction.

## API

| Method | Path | Action |
|---|---|---|
| `GET` | `/health` | liveness |
| `GET` | `/ready` | database readiness |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests` | create |
| `GET` | `/api/v1/workspaces/{workspace_id}/approval-requests` | list |
| `GET` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}` | detail with audit trail |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/approve` | approve |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/reject` | reject |
| `POST` | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/cancel` | cancel |

The list endpoint accepts `status`, `limit` (1-100, default 50) and `offset` query parameters.
Public JSON uses camelCase.

Create example:

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

Approve example:

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/REQUEST_ID/approve \
  -H "Content-Type: application/json" \
  -H "X-Workspace-Id: ws_1" \
  -H "X-User-Id: usr_1" \
  -H "X-Actions: approval:decide" \
  -H "Idempotency-Key: approve-request-v1" \
  -d '{"comment":"Approved"}'
```

Reject and cancel accept `{"reason":"..."}`. Request states are `pending`, `approved`,
`rejected` and `cancelled`.

## Sensitive-data policy

The request models accept only documented fields. Opaque identifiers cannot be URLs or email
addresses, and user-visible text is rejected when it contains email, web/storage URLs, bearer
tokens, JWT-like values, access keys or common secret assignments. Validation errors never
echo submitted values.

Logs contain only a generated request ID and exception class for unexpected failures. Audit
metadata and outbox payloads are built from strict allowlists and never include title,
description, comment, reason, provider payload or arbitrary request data.

## Tests

```bash
python -m pytest
```

The suite covers all endpoints, permissions, workspace isolation, idempotent replay and key
reuse, terminal-state protection, reviewer checks, audit/outbox writes, sensitive input and
Alembic upgrade/downgrade.

## Project layout

```text
app/
  api.py          HTTP routes
  auth.py         auth stub and idempotency header validation
  models.py       SQLAlchemy persistence model
  schemas.py      request/response validation and sensitive-text policy
  services.py     transactions and workflow rules
alembic/          database migrations
tests/            API and migration tests
Dockerfile
docker-compose.yml
DESIGN.md
```

See [DESIGN.md](DESIGN.md) for data model, transaction boundaries, integration strategy and
known trade-offs.
