import asyncio

from sqlalchemy.orm import Session

from backend.admission_service import get_current_admission
from backend.request_forwarder import ProtectedServiceConfig, forward_request
from database.models import Request
from database.request_repository import update_status
from queue_management.queue_manager import process_next_request


async def process_next_request_if_allowed(
    db: Session,
    forwarding_config: ProtectedServiceConfig | None = None,
) -> Request | None:
    """Claim and forward one FIFO request only while policy returns ALLOW."""
    admission = await get_current_admission()
    if admission["decision"] != "ALLOW":
        return None

    request = process_next_request(db)
    if request is None:
        return None

    try:
        result = await asyncio.to_thread(forward_request, request, forwarding_config)
        final_status = "COMPLETED" if result.succeeded else "FAILED"
    except Exception:
        final_status = "FAILED"
    return update_status(db, request.id, final_status)
