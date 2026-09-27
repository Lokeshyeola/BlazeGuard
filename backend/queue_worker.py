from sqlalchemy.orm import Session

from backend.admission_service import get_current_admission
from database.models import Request
from queue_management.queue_manager import process_next_request


async def process_next_request_if_allowed(db: Session) -> Request | None:
    """Claim one FIFO request only while the existing policy returns ALLOW."""
    admission = await get_current_admission()
    if admission["decision"] != "ALLOW":
        return None
    return process_next_request(db)
