from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Header

from app.errors import APIError
from app.schemas import validate_external_id


@dataclass(frozen=True, slots=True)
class AuthContext:
    workspace_id: str
    user_id: str
    actions: frozenset[str]


def require_action(action: str) -> Callable[..., AuthContext]:
    def dependency(
        workspace_id: str,
        x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
        x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
        x_actions: Annotated[str | None, Header(alias="X-Actions")] = None,
    ) -> AuthContext:
        if not x_workspace_id or not x_user_id or not x_actions:
            raise APIError(401, "auth_required", "Authentication headers are required")

        try:
            auth_workspace_id = validate_external_id(x_workspace_id)
            user_id = validate_external_id(x_user_id)
        except ValueError as exc:
            raise APIError(401, "invalid_auth", "Authentication headers are invalid") from exc

        if auth_workspace_id != workspace_id:
            raise APIError(
                403,
                "workspace_mismatch",
                "The authenticated workspace does not match the URL workspace",
            )

        actions = frozenset(item.strip() for item in x_actions.split(",") if item.strip())
        if action not in actions:
            raise APIError(403, "action_forbidden", f"Action '{action}' is required")

        return AuthContext(workspace_id=auth_workspace_id, user_id=user_id, actions=actions)

    return dependency


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    from app.schemas import IDEMPOTENCY_KEY_PATTERN

    if not idempotency_key:
        raise APIError(
            400,
            "idempotency_key_required",
            "Idempotency-Key header is required for mutating requests",
        )
    normalized = idempotency_key.strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized):
        raise APIError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-128 characters using letters, digits, '.', '_', ':' or '-'",
        )
    return normalized

