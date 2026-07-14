from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, IdempotencyRecord, OutboxEvent
from tests.conftest import auth_headers


BASE_URL = "/api/v1/workspaces/{workspace}/approval-requests"


def create_request(
    client: TestClient,
    payload: dict,
    *,
    workspace: str = "ws_1",
    key: str = "create-1",
) -> dict:
    response = client.post(
        BASE_URL.format(workspace=workspace),
        json=payload,
        headers=auth_headers(workspace=workspace, idempotency_key=key),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_create_request_writes_audit_outbox_and_idempotency(
    client: TestClient,
    session_factory: sessionmaker[Session],
    create_payload: dict,
) -> None:
    response = client.post(
        BASE_URL.format(workspace="ws_1"),
        json=create_payload,
        headers=auth_headers(idempotency_key="create-audit"),
    )

    assert response.status_code == 201
    assert response.headers["Idempotency-Replayed"] == "false"
    body = response.json()
    assert body["workspaceId"] == "ws_1"
    assert body["status"] == "pending"
    assert body["reviewerUserIds"] == ["usr_1", "usr_2"]
    assert body["auditTrail"][0]["action"] == "created"
    assert body["auditTrail"][0]["actorUserId"] == "usr_creator"

    with session_factory() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == 1
        assert session.scalar(select(func.count(OutboxEvent.id))) == 1
        assert session.scalar(select(func.count(IdempotencyRecord.id))) == 1
        outbox = session.scalar(select(OutboxEvent))
        assert outbox is not None
        encoded = json.dumps(outbox.payload)
        assert create_payload["title"] not in encoded
        assert create_payload["description"] not in encoded


def test_create_is_idempotent_and_key_cannot_be_reused(
    client: TestClient, create_payload: dict
) -> None:
    url = BASE_URL.format(workspace="ws_1")
    headers = auth_headers(idempotency_key="same-client-request")

    first = client.post(url, json=create_payload, headers=headers)
    second = client.post(url, json=create_payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Idempotency-Replayed"] == "true"

    changed_payload = {**create_payload, "title": "A different draft"}
    conflict = client.post(url, json=changed_payload, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    another_actor = client.post(
        url,
        json=create_payload,
        headers=auth_headers(user="usr_other", idempotency_key="same-client-request"),
    )
    assert another_actor.status_code == 409
    assert another_actor.json()["error"]["code"] == "idempotency_key_reused"


def test_workspace_isolation_for_list_and_detail(
    client: TestClient, create_payload: dict
) -> None:
    created = create_request(client, create_payload)

    own_detail = client.get(
        f'{BASE_URL.format(workspace="ws_1")}/{created["id"]}',
        headers=auth_headers(actions="approval:read"),
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["id"] == created["id"]
    assert own_detail.json()["auditTrail"][0]["action"] == "created"

    list_response = client.get(
        BASE_URL.format(workspace="ws_2"),
        headers=auth_headers(workspace="ws_2", actions="approval:read"),
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0

    detail_response = client.get(
        f'{BASE_URL.format(workspace="ws_2")}/{created["id"]}',
        headers=auth_headers(workspace="ws_2", actions="approval:read"),
    )
    assert detail_response.status_code == 404

    mismatch = client.get(
        BASE_URL.format(workspace="ws_1"),
        headers=auth_headers(workspace="ws_2", actions="approval:read"),
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "workspace_mismatch"


def test_auth_action_and_idempotency_headers_are_required(
    client: TestClient, create_payload: dict
) -> None:
    url = BASE_URL.format(workspace="ws_1")

    unauthenticated = client.get(url)
    assert unauthenticated.status_code == 401

    forbidden = client.post(
        url,
        json=create_payload,
        headers=auth_headers(actions="approval:read", idempotency_key="no-create-action"),
    )
    assert forbidden.status_code == 403

    missing_key = client.post(url, json=create_payload, headers=auth_headers())
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"


def test_assigned_reviewer_can_approve_and_retry_is_replayed(
    client: TestClient, create_payload: dict
) -> None:
    created = create_request(client, create_payload)
    decision_url = f'{BASE_URL.format(workspace="ws_1")}/{created["id"]}/approve'
    headers = auth_headers(
        user="usr_1", actions="approval:decide", idempotency_key="approve-once"
    )

    approved = client.post(decision_url, json={"comment": "Approved"}, headers=headers)
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    assert body["version"] == 2
    assert body["decision"] == {
        "type": "approved",
        "actorUserId": "usr_1",
        "comment": "Approved",
        "reason": None,
        "decidedAt": body["decision"]["decidedAt"],
    }
    assert [entry["action"] for entry in body["auditTrail"]] == ["created", "approved"]

    replay = client.post(decision_url, json={"comment": "Approved"}, headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == body

    reject_after_approve = client.post(
        f'{BASE_URL.format(workspace="ws_1")}/{created["id"]}/reject',
        json={"reason": "Changed our mind"},
        headers=auth_headers(
            user="usr_2", actions="approval:decide", idempotency_key="reject-too-late"
        ),
    )
    assert reject_after_approve.status_code == 409
    assert reject_after_approve.json()["error"]["code"] == "invalid_state_transition"


def test_non_reviewer_cannot_decide(client: TestClient, create_payload: dict) -> None:
    created = create_request(client, create_payload)
    response = client.post(
        f'{BASE_URL.format(workspace="ws_1")}/{created["id"]}/reject',
        json={"reason": "Not my call"},
        headers=auth_headers(
            user="usr_outsider", actions="approval:decide", idempotency_key="outsider-reject"
        ),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reviewer_required"


@pytest.mark.parametrize(
    ("action", "user", "actions", "payload", "expected_status"),
    [
        ("reject", "usr_2", "approval:decide", {"reason": "Brand tone is wrong"}, "rejected"),
        ("cancel", "usr_creator", "approval:cancel", {"reason": "Draft was removed"}, "cancelled"),
    ],
)
def test_reject_and_cancel(
    client: TestClient,
    create_payload: dict,
    action: str,
    user: str,
    actions: str,
    payload: dict,
    expected_status: str,
) -> None:
    created = create_request(client, create_payload, key=f"create-for-{action}")
    response = client.post(
        f'{BASE_URL.format(workspace="ws_1")}/{created["id"]}/{action}',
        json=payload,
        headers=auth_headers(
            user=user, actions=actions, idempotency_key=f"{action}-decision"
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected_status
    assert body["decision"]["reason"] == payload["reason"]
    assert body["auditTrail"][-1]["actorUserId"] == user


def test_list_filter_and_pagination(client: TestClient, create_payload: dict) -> None:
    first = create_request(client, create_payload, key="create-list-1")
    second_payload = {**create_payload, "sourceId": "pub_456", "title": "Second draft"}
    create_request(client, second_payload, key="create-list-2")

    client.post(
        f'{BASE_URL.format(workspace="ws_1")}/{first["id"]}/approve',
        json={"comment": "Approved"},
        headers=auth_headers(
            user="usr_1", actions="approval:decide", idempotency_key="approve-list-1"
        ),
    )
    response = client.get(
        f'{BASE_URL.format(workspace="ws_1")}?status=pending&limit=1&offset=0',
        headers=auth_headers(actions="approval:read"),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["auditTrail"] == []


def test_validation_response_does_not_echo_sensitive_input(
    client: TestClient, create_payload: dict
) -> None:
    secret = "very-secret-provider-token"
    payload = {**create_payload, "providerPayload": {"token": secret}}
    response = client.post(
        BASE_URL.format(workspace="ws_1"),
        json=payload,
        headers=auth_headers(idempotency_key="unsafe-input"),
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert "providerPayload" in response.text

    email = "private.person@example.com"
    payload_with_email = {**create_payload, "description": f"Contact {email}"}
    email_response = client.post(
        BASE_URL.format(workspace="ws_1"),
        json=payload_with_email,
        headers=auth_headers(idempotency_key="email-input"),
    )
    assert email_response.status_code == 422
    assert email not in email_response.text
