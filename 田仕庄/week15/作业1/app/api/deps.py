from fastapi import Request

from app.config import Settings
from app.infra.db.session import SessionFactory
from app.services.chat_service import ChatService
from app.services.conversation_service import ConversationService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request) -> SessionFactory:
    return request.app.state.session_factory


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


if __name__ == "__main__":
    print([item.__name__ for item in (get_chat_service, get_conversation_service)])
