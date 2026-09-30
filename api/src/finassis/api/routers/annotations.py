from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from ...domain import annotations as ann
from ..deps import Auth, Tenant, UserId, require
from ..schemas import AnnotationIn

router = APIRouter(prefix="/annotations", tags=["annotations"])


@router.get("")
async def list_annotations(conn: Tenant, uid: UserId, target_type: str | None = None, target_id: str | None = None) -> dict[str, Any]:
    return {"items": await ann.list_annotations(conn, uid, target_type, target_id)}


@router.post("", status_code=201, dependencies=[require("annotations:write")])
async def add(body: AnnotationIn, conn: Tenant, uid: UserId, p: Auth) -> dict[str, Any]:
    author_kind = "agent" if p.key_id and body.author_name and body.author_name.startswith("agent:") else "user"
    return await ann.add_annotation(
        conn, uid, target_type=body.target_type, target_id=body.target_id, body=body.body,
        author_kind=author_kind, author_name=body.author_name, tags=body.tags, valid_until=body.valid_until,
    )


@router.delete("/{annotation_id}", status_code=204, dependencies=[require("annotations:write")])
async def delete(annotation_id: uuid.UUID, conn: Tenant, uid: UserId) -> None:
    await ann.delete_annotation(conn, uid, annotation_id)
