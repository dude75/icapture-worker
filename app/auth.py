"""Bearer auth for API and metrics endpoints."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.schemas import ErrorCode, error_payload

_bearer = HTTPBearer(auto_error=False)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def token_is_valid(authorization: str | None, expected: str) -> bool:
    token = _bearer_token(authorization)
    if token is None or not expected:
        return False
    return hmac.compare_digest(token, expected)


def api_token_is_valid(authorization: str | None) -> bool:
    return token_is_valid(authorization, get_settings().API_TOKEN)


def metrics_token_is_valid(authorization: str | None) -> bool:
    expected = get_settings().METRICS_TOKEN
    if not expected:
        return False
    return token_is_valid(authorization, expected)


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    header = f"{creds.scheme} {creds.credentials}" if creds is not None else None
    if not api_token_is_valid(header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_payload(ErrorCode.unauthorized),
        )
    return creds.credentials if creds is not None else ""


def require_metrics_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    header = f"{creds.scheme} {creds.credentials}" if creds is not None else None
    if not metrics_token_is_valid(header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_payload(ErrorCode.unauthorized),
        )
    return creds.credentials if creds is not None else ""
