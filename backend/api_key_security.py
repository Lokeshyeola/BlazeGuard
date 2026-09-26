import hashlib
import hmac
import secrets
import uuid
from sqlalchemy.orm import Session
from database.api_key_repository import find_active_key

def generate_api_key():
    key_id = uuid.uuid4().hex
    return key_id, f"BG_{key_id}_{secrets.token_urlsafe(32)}"

def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

def verify_api_key(raw_key: str, db: Session):
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "BG" or len(parts[1]) != 32:
        return None
    record = find_active_key(db, parts[1])
    if record is None or not hmac.compare_digest(record.key_hash, hash_api_key(raw_key)):
        return None
    return record
