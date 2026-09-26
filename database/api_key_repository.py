from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import ApiKey

def create_key(db: Session, key_id: str, key_hash: str):
    record = ApiKey(id=key_id, key_hash=key_hash, status="ACTIVE")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record

def list_keys(db: Session):
    return list(db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all())

def find_active_key(db: Session, key_id: str):
    return db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.status == "ACTIVE"))

def revoke_key(db: Session, key_id: str):
    record = db.get(ApiKey, key_id)
    if record and record.status != "REVOKED":
        record.status = "REVOKED"
        record.revoked_at = datetime.utcnow()
        db.commit()
        db.refresh(record)
    return record
