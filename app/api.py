from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_action, require_idempotency_key
from app.database import get_session
from app.schemas import (
    ApprovalRequestCreate,
    ApprovalRequestPage,
    ApprovalRequestResponse,
    ApprovalStatus,
    ApproveRequest,
    ReasonRequest,
)
from app.services import (
    create_approval_request,
    decide_approval_request,
    get_approval_request,
    list_approval_requests,
)


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/approval-requests")


@router.post("", response_model=ApprovalRequestResponse, status_code=201)
def create_request(
    payload: ApprovalRequestCreate,
    auth: Annotated[AuthContext, Depends(require_action("approval:create"))],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    result = create_approval_request(session, auth, payload, idempotency_key)
    return JSONResponse(
        content=result.body,
        status_code=result.status_code,
        headers={"Idempotency-Replayed": str(result.replayed).lower()},
    )


@router.get("", response_model=ApprovalRequestPage)
def list_requests(
    auth: Annotated[AuthContext, Depends(require_action("approval:read"))],
    session: Annotated[Session, Depends(get_session)],
    status: Annotated[ApprovalStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return list_approval_requests(
        session,
        auth.workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{request_id}", response_model=ApprovalRequestResponse)
def get_request(
    request_id: str,
    auth: Annotated[AuthContext, Depends(require_action("approval:read"))],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    return get_approval_request(session, auth.workspace_id, request_id)


@router.post("/{request_id}/approve", response_model=ApprovalRequestResponse)
def approve_request(
    request_id: str,
    payload: ApproveRequest,
    auth: Annotated[AuthContext, Depends(require_action("approval:decide"))],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    result = decide_approval_request(
        session,
        auth,
        request_id,
        ApprovalStatus.approved,
        payload,
        idempotency_key,
    )
    return JSONResponse(
        content=result.body,
        status_code=result.status_code,
        headers={"Idempotency-Replayed": str(result.replayed).lower()},
    )


@router.post("/{request_id}/reject", response_model=ApprovalRequestResponse)
def reject_request(
    request_id: str,
    payload: ReasonRequest,
    auth: Annotated[AuthContext, Depends(require_action("approval:decide"))],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    result = decide_approval_request(
        session,
        auth,
        request_id,
        ApprovalStatus.rejected,
        payload,
        idempotency_key,
    )
    return JSONResponse(
        content=result.body,
        status_code=result.status_code,
        headers={"Idempotency-Replayed": str(result.replayed).lower()},
    )


@router.post("/{request_id}/cancel", response_model=ApprovalRequestResponse)
def cancel_request(
    request_id: str,
    payload: ReasonRequest,
    auth: Annotated[AuthContext, Depends(require_action("approval:cancel"))],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    result = decide_approval_request(
        session,
        auth,
        request_id,
        ApprovalStatus.cancelled,
        payload,
        idempotency_key,
    )
    return JSONResponse(
        content=result.body,
        status_code=result.status_code,
        headers={"Idempotency-Replayed": str(result.replayed).lower()},
    )

