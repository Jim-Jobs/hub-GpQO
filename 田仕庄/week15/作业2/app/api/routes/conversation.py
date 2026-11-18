from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_conversation_service
from app.api.security import require_api_token
from app.schemas.conversation import MessageListResponse
from app.services.conversation_service import ConversationService

router = APIRouter(tags=["conversation"], dependencies=[Depends(require_api_token)])


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    conversation_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[UUID | None, Query()] = None,
    service: ConversationService = Depends(get_conversation_service),
) -> MessageListResponse:
    return await service.list_messages(conversation_id, limit, before)


if __name__ == "__main__":
    print(router.routes[0].path)
