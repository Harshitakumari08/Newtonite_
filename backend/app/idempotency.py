from __future__ import annotations
"""Idempotency keys and the commit point for every mutating request.

How a request with `Idempotency-Key` is handled, all inside one transaction:

1. INSERT (user, key, request_hash) ... ON CONFLICT DO NOTHING.
   - If a concurrent request with the same key is still running, Postgres makes this INSERT wait for
     it to commit or roll back, so two copies of a request can never both run.
2. If the row already existed: same request hash -> replay the stored response; different -> 422.
3. Otherwise run the operation, store its response on the key row, and commit everything together.

If the operation fails, the whole transaction (including the key row) rolls back, so a retry runs
again. Only successful responses are remembered.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.errors import AppError
from app.models import IdempotencyKey

REPLAY_HEADER = "Idempotent-Replayed"


def request_hash(method: str, path: str, body: BaseModel | None, if_match: int | None = None) -> str:
    raw = json.dumps(
        {"m": method, "p": path, "b": body.model_dump(mode="json") if body else None, "v": if_match},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def run_mutation(
    db: Session,
    user: CurrentUser,
    key: str | None,
    req_hash: str,
    operation: Callable[[], BaseModel],
    status_code: int = 200,
) -> JSONResponse:
    if key is not None:
        if not 1 <= len(key) <= 100:
            raise AppError(400, "invalid_idempotency_key", "Idempotency-Key must be 1-100 characters")
        inserted = db.execute(
            insert(IdempotencyKey)
            .values(user_id=user.id, key=key, request_hash=req_hash)
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        ).first()
        if inserted is None:
            stored = db.execute(
                select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key)
            ).scalar_one()
            db.rollback()
            if stored.request_hash != req_hash:
                raise AppError(
                    422, "idempotency_key_reused", "This Idempotency-Key was already used for a different request"
                )
            return JSONResponse(stored.response_body, status_code=stored.status_code, headers={REPLAY_HEADER: "true"})

    try:
        body: dict[str, Any] = jsonable_encoder(operation())
        if key is not None:
            db.execute(
                IdempotencyKey.__table__.update()
                .where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key)
                .values(status_code=status_code, response_body=body)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(body, status_code=status_code)
