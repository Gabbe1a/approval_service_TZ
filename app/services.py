from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import AuthContext
from app.errors import APIError
from app.models import (
    ApprovalRequest,
    ApprovalReviewer,
    AuditEvent,
    IdempotencyRecord,
    OutboxEvent,
    new_id,
    utc_now,
)
from app.schemas import (
    ApprovalRequestCreate,
    ApprovalRequestPage,
    ApprovalRequestResponse,
    ApprovalStatus,
    ApproveRequest,
    AuditEntryResponse,
    DecisionResponse,
    ReasonRequest,
)


@dataclass(frozen=True, slots=True)
class ServiceResult:
    body: dict[str, Any]
    status_code: int
    replayed: bool = False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize(request: ApprovalRequest, *, include_history: bool) -> dict[str, Any]:
    decision: DecisionResponse | None = None
    if request.decision_kind and request.finalized_by_user_id and request.finalized_at:
        decision = DecisionResponse(
            type=ApprovalStatus(request.decision_kind),
            actor_user_id=request.finalized_by_user_id,
            comment=request.decision_text if request.decision_kind == "approved" else None,
            reason=(
                request.decision_text
                if request.decision_kind in {"rejected", "cancelled"}
                else None
            ),
            decided_at=_utc(request.finalized_at),
        )

    audit_trail: list[AuditEntryResponse] = []
    if include_history:
        audit_trail = [
            AuditEntryResponse(
                id=event.id,
                action=event.action,
                actor_user_id=event.actor_user_id,
                previous_status=(
                    ApprovalStatus(event.previous_status) if event.previous_status else None
                ),
                new_status=ApprovalStatus(event.new_status),
                details=event.details,
                occurred_at=_utc(event.created_at),
            )
            for event in request.audit_events
        ]

    response = ApprovalRequestResponse(
        id=request.id,
        workspace_id=request.workspace_id,
        source_type=request.source_type,
        source_id=request.source_id,
        title=request.title,
        description=request.description,
        reviewer_user_ids=[reviewer.user_id for reviewer in request.reviewers],
        status=ApprovalStatus(request.status),
        created_by_user_id=request.created_by_user_id,
        decision=decision,
        version=request.version,
        created_at=_utc(request.created_at),
        updated_at=_utc(request.updated_at),
        audit_trail=audit_trail,
    )
    return response.model_dump(mode="json", by_alias=True)


def _load_request(session: Session, workspace_id: str, request_id: str) -> ApprovalRequest:
    request = session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.id == request_id,
            ApprovalRequest.workspace_id == workspace_id,
        )
        .options(
            selectinload(ApprovalRequest.reviewers),
            selectinload(ApprovalRequest.audit_events),
        )
    )
    if request is None:
        raise APIError(404, "approval_request_not_found", "Approval request was not found")
    return request


def _find_idempotency(
    session: Session, workspace_id: str, idempotency_key: str
) -> IdempotencyRecord | None:
    return session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.workspace_id == workspace_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )


def _replay_or_conflict(
    record: IdempotencyRecord | None,
    operation: str,
    request_hash: str,
    actor_user_id: str,
) -> ServiceResult | None:
    if record is None:
        return None
    if (
        record.actor_user_id != actor_user_id
        or record.operation != operation
        or record.request_hash != request_hash
    ):
        raise APIError(
            409,
            "idempotency_key_reused",
            "Idempotency-Key was already used for a different request",
        )
    return ServiceResult(record.response_body, record.response_status, replayed=True)


def _outbox_event(
    *,
    workspace_id: str,
    request_id: str,
    event_type: str,
    status: str,
    actor_user_id: str,
    occurred_at: datetime,
) -> OutboxEvent:
    # Deliberately allowlisted: no title, description, decision text, provider data or URLs.
    return OutboxEvent(
        id=new_id(),
        workspace_id=workspace_id,
        aggregate_type="approval_request",
        aggregate_id=request_id,
        event_type=event_type,
        schema_version=1,
        payload={
            "requestId": request_id,
            "workspaceId": workspace_id,
            "status": status,
            "actorUserId": actor_user_id,
            "occurredAt": occurred_at.isoformat(),
        },
        attempts=0,
        created_at=occurred_at,
    )


def create_approval_request(
    session: Session,
    auth: AuthContext,
    payload: ApprovalRequestCreate,
    idempotency_key: str,
) -> ServiceResult:
    operation = "approval_request.create"
    request_hash = _request_hash(payload.model_dump(mode="json"))
    replay = _replay_or_conflict(
        _find_idempotency(session, auth.workspace_id, idempotency_key),
        operation,
        request_hash,
        auth.user_id,
    )
    if replay:
        return replay

    now = utc_now()
    request_id = new_id()
    request = ApprovalRequest(
        id=request_id,
        workspace_id=auth.workspace_id,
        source_type=payload.source_type.value,
        source_id=payload.source_id,
        title=payload.title,
        description=payload.description,
        status=ApprovalStatus.pending.value,
        created_by_user_id=auth.user_id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    request.reviewers = [
        ApprovalReviewer(id=new_id(), user_id=user_id, position=position)
        for position, user_id in enumerate(payload.reviewer_user_ids)
    ]
    request.audit_events = [
        AuditEvent(
            id=new_id(),
            workspace_id=auth.workspace_id,
            request_id=request_id,
            actor_user_id=auth.user_id,
            action="created",
            previous_status=None,
            new_status=ApprovalStatus.pending.value,
            details={"reviewerCount": len(payload.reviewer_user_ids)},
            created_at=now,
        )
    ]
    session.add(request)
    session.add(
        _outbox_event(
            workspace_id=auth.workspace_id,
            request_id=request_id,
            event_type="approval_request.created.v1",
            status=ApprovalStatus.pending.value,
            actor_user_id=auth.user_id,
            occurred_at=now,
        )
    )
    session.flush()

    response_body = _serialize(request, include_history=True)
    session.add(
        IdempotencyRecord(
            id=new_id(),
            workspace_id=auth.workspace_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
            resource_id=request_id,
            created_at=now,
        )
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        replay = _replay_or_conflict(
            _find_idempotency(session, auth.workspace_id, idempotency_key),
            operation,
            request_hash,
            auth.user_id,
        )
        if replay:
            return replay
        raise APIError(409, "write_conflict", "The request conflicted with another write")

    return ServiceResult(response_body, 201)


def list_approval_requests(
    session: Session,
    workspace_id: str,
    *,
    status: ApprovalStatus | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    filters = [ApprovalRequest.workspace_id == workspace_id]
    if status:
        filters.append(ApprovalRequest.status == status.value)

    total = session.scalar(select(func.count(ApprovalRequest.id)).where(*filters)) or 0
    requests = session.scalars(
        select(ApprovalRequest)
        .where(*filters)
        .options(selectinload(ApprovalRequest.reviewers))
        .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    page = ApprovalRequestPage(
        items=[
            ApprovalRequestResponse.model_validate(_serialize(item, include_history=False))
            for item in requests
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
    return page.model_dump(mode="json", by_alias=True)


def get_approval_request(session: Session, workspace_id: str, request_id: str) -> dict[str, Any]:
    return _serialize(_load_request(session, workspace_id, request_id), include_history=True)


def decide_approval_request(
    session: Session,
    auth: AuthContext,
    request_id: str,
    decision: ApprovalStatus,
    payload: ApproveRequest | ReasonRequest,
    idempotency_key: str,
) -> ServiceResult:
    operation = f"approval_request.{decision.value}:{request_id}"
    request_hash = _request_hash(payload.model_dump(mode="json"))
    replay = _replay_or_conflict(
        _find_idempotency(session, auth.workspace_id, idempotency_key),
        operation,
        request_hash,
        auth.user_id,
    )
    if replay:
        return replay

    request = _load_request(session, auth.workspace_id, request_id)
    if decision in {ApprovalStatus.approved, ApprovalStatus.rejected}:
        reviewer_ids = {reviewer.user_id for reviewer in request.reviewers}
        if auth.user_id not in reviewer_ids:
            raise APIError(
                403,
                "reviewer_required",
                "Only an assigned reviewer may approve or reject this request",
            )

    if request.status != ApprovalStatus.pending.value:
        late_replay = _replay_or_conflict(
            _find_idempotency(session, auth.workspace_id, idempotency_key),
            operation,
            request_hash,
            auth.user_id,
        )
        if late_replay:
            return late_replay
        raise APIError(
            409,
            "invalid_state_transition",
            f"Approval request is already in final state '{request.status}'",
        )

    now = utc_now()
    decision_text = payload.comment if isinstance(payload, ApproveRequest) else payload.reason
    next_version = request.version + 1
    statement = (
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == request_id,
            ApprovalRequest.workspace_id == auth.workspace_id,
            ApprovalRequest.status == ApprovalStatus.pending.value,
        )
        .values(
            status=decision.value,
            decision_kind=decision.value,
            decision_text=decision_text,
            finalized_by_user_id=auth.user_id,
            finalized_at=now,
            updated_at=now,
            version=ApprovalRequest.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    update_result = session.execute(statement)
    if update_result.rowcount != 1:
        session.rollback()
        late_replay = _replay_or_conflict(
            _find_idempotency(session, auth.workspace_id, idempotency_key),
            operation,
            request_hash,
            auth.user_id,
        )
        if late_replay:
            return late_replay
        current = _load_request(session, auth.workspace_id, request_id)
        raise APIError(
            409,
            "invalid_state_transition",
            f"Approval request is already in final state '{current.status}'",
        )

    request.status = decision.value
    request.decision_kind = decision.value
    request.decision_text = decision_text
    request.finalized_by_user_id = auth.user_id
    request.finalized_at = now
    request.updated_at = now
    request.version = next_version

    action = {
        ApprovalStatus.approved: "approved",
        ApprovalStatus.rejected: "rejected",
        ApprovalStatus.cancelled: "cancelled",
    }[decision]
    audit = AuditEvent(
        id=new_id(),
        workspace_id=auth.workspace_id,
        request_id=request_id,
        actor_user_id=auth.user_id,
        action=action,
        previous_status=ApprovalStatus.pending.value,
        new_status=decision.value,
        details={
            "commentSupplied": (
                bool(decision_text) if decision == ApprovalStatus.approved else False
            ),
            "reasonSupplied": bool(decision_text) if decision != ApprovalStatus.approved else False,
        },
        created_at=now,
    )
    request.audit_events.append(audit)
    session.add(
        _outbox_event(
            workspace_id=auth.workspace_id,
            request_id=request_id,
            event_type=f"approval_request.{action}.v1",
            status=decision.value,
            actor_user_id=auth.user_id,
            occurred_at=now,
        )
    )
    session.flush()

    response_body = _serialize(request, include_history=True)
    session.add(
        IdempotencyRecord(
            id=new_id(),
            workspace_id=auth.workspace_id,
            actor_user_id=auth.user_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
            resource_id=request_id,
            created_at=now,
        )
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        replay = _replay_or_conflict(
            _find_idempotency(session, auth.workspace_id, idempotency_key),
            operation,
            request_hash,
            auth.user_id,
        )
        if replay:
            return replay
        raise APIError(409, "write_conflict", "The request conflicted with another write")

    return ServiceResult(response_body, 200)
