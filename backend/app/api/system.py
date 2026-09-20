from fastapi import APIRouter

from app.core.config import settings
from app.schemas.schemas import SystemStatus
from app.services import embedding_service
from app.services.llm_service import llm_service

router = APIRouter(prefix="/system", tags=["System"])

DISCLAIMER = (
    "AI-assisted contract review. Not legal advice. It reduces manual review effort and does not "
    "replace a qualified legal professional."
)


@router.get("/status", response_model=SystemStatus)
async def system_status():
    """Non-secret runtime status so the UI can be honest about what is (not) available."""
    st = llm_service.status()
    emb = embedding_service.status()
    return SystemStatus(
        llm_provider=st["provider"], llm_model=st["model"], llm_configured=st["configured"],
        llm_reason=st["reason"], embedding_provider=emb["provider"], embedding_model=emb["model"],
        embedding_available=bool(emb["available"]), privacy_redaction=settings.privacy_redact, auth_mode=settings.AUTH_MODE,
        max_upload_mb=settings.MAX_UPLOAD_MB, disclaimer=DISCLAIMER,
    )
