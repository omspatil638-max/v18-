import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_contract, resolve_version
from app.db.database import get_db
from app.models.models import ChatMessage, ContractChunk, ContractStatus, User
from app.schemas.schemas import ChatMessageCreate, ChatMessageResponse
from app.services.rag_service import rag_service

router = APIRouter(prefix="/contracts/{contract_id}", tags=["Chat & RAG"])


@router.get("/chat", response_model=List[ChatMessageResponse])
async def get_chat_history(
    contract_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await get_owned_contract(contract_id, user, db)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.contract_id == contract_id, ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.asc())
    )
    return (await db.execute(stmt)).scalars().all()


@router.post("/chat", response_model=ChatMessageResponse)
async def ask_question(
    contract_id: uuid.UUID,
    payload: ChatMessageCreate,
    version_id: Optional[uuid.UUID] = Query(None, description="Defaults to the current version"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contract = await get_owned_contract(contract_id, user, db)
    version = await resolve_version(db, contract, version_id)
    if version.status != ContractStatus.READY:
        raise HTTPException(status_code=409, detail="This contract version is not ready for questions yet.")
    return await rag_service.ask_question(contract, version, payload.content.strip(), user.id, db)


@router.get("/chunks/{chunk_id}")
async def get_chunk_detail(
    contract_id: uuid.UUID,
    chunk_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_contract(contract_id, user, db)
    chunk = (await db.execute(
        select(ContractChunk).where(ContractChunk.id == chunk_id, ContractChunk.contract_id == contract_id)
    )).scalar_one_or_none()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {
        "id": chunk.id, "contract_id": chunk.contract_id, "contract_version_id": chunk.contract_version_id,
        "chunk_index": chunk.chunk_index, "content": chunk.content,
        "source_page": chunk.source_page, "source_section": chunk.source_section,
    }
