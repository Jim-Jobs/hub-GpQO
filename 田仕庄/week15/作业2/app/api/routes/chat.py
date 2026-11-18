from fastapi import APIRouter, Depends

from app.api.deps import get_chat_service
from app.api.security import require_api_token
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"], dependencies=[Depends(require_api_token)])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return await service.handle(payload)


if __name__ == "__main__":
    print(router.routes[0].path, sorted(router.routes[0].methods))
