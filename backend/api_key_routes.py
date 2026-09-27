import hmac
import os
import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.api_key_security import generate_api_key, hash_api_key, verify_api_key
from database.api_key_repository import create_key, list_keys, revoke_key
from database.database import SessionLocal
from database.models import ApiKey


router = APIRouter(prefix="/api/v1")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(authorization: str | None = Header(default=None)):
    token = os.getenv("BLAZEGUARD_ADMIN_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="API key administration is not configured.")
    provided = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(
            status_code=401,
            detail="Administrator authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_api_key(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ApiKey:
    raw_key = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    record = verify_api_key(raw_key, db) if raw_key else None
    if record is None:
        raise HTTPException(
            status_code=401,
            detail="A valid BlazeGuard API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record


def metadata(record: ApiKey):
    return {
        "key_id": record.id,
        "status": record.status.lower(),
        "created_at": record.created_at.isoformat() + "Z",
        "revoked_at": record.revoked_at.isoformat() + "Z" if record.revoked_at else None,
    }


@router.post("/api-keys", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
def create_api_key(db: Session = Depends(get_db)):
    key_id, raw_key = generate_api_key()
    record = create_key(db, key_id, hash_api_key(raw_key))
    return {**metadata(record), "api_key": raw_key}


@router.get("/api-keys", dependencies=[Depends(require_admin)])
def get_api_keys(db: Session = Depends(get_db)):
    return {"keys": [metadata(record) for record in list_keys(db)]}


@router.delete("/api-keys/{key_id}", dependencies=[Depends(require_admin)])
def delete_api_key(key_id: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[a-f0-9]{32}", key_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")
    record = revoke_key(db, key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")
    return metadata(record)


@router.get("/connection")
def check_connection(record: ApiKey = Depends(require_api_key)):
    return {"authenticated": True, "connected": True, "key_id": record.id}
