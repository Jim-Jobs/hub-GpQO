import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import Settings
from app.domain.errors import AppError, input_too_long_error
from app.domain.ports import PipelineAdapter
from app.engines.shared.llm import translate_llm_error
from app.infra.db.models import Conversation, Message
from app.infra.db.session import SessionFactory
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.locks import ConversationLocks

logger = logging.getLogger("app.chat")


class ChatService:
    def __init__(
        self,
        session_factory: SessionFactory,
        pipeline: PipelineAdapter,
        settings: Settings,
        locks: ConversationLocks,
    ) -> None:
        self._session_factory = session_factory
        self._pipeline = pipeline
        self._settings = settings
        self._locks = locks

    async def handle(self, request: ChatRequest) -> ChatResponse:
        if len(request.message) > self._settings.max_input_length:
            raise input_too_long_error(self._settings.max_input_length)

        conversation_id = request.conversation_id
        lock = self._locks.for_conversation(conversation_id)
        async with lock:
            started = time.perf_counter()
            started_at = datetime.now(timezone.utc)
            try:
                result = await self._pipeline.run(conversation_id, request.message)
            except Exception as exc:
                error = translate_llm_error(exc)
                if error is not None:
                    self._log(
                        "chat_failed",
                        logging.WARNING,
                        conversation_id,
                        status=error.status_code,
                        error_code=error.code,
                        latency_ms=self._elapsed_ms(started),
                        input_length=len(request.message),
                        input_sha256=input_digest(request.message),
                    )
                    raise error from exc
                raise

            await self._persist(
                conversation_id, request.message, result.sentiment.value, result.reply,
                started_at,
            )
            self._log(
                "chat_completed",
                logging.INFO,
                conversation_id,
                status=200,
                sentiment=result.sentiment.value,
                latency_ms=self._elapsed_ms(started),
                input_length=len(request.message),
                input_sha256=input_digest(request.message),
            )
            return ChatResponse(
                input=request.message,
                sentiment=result.sentiment,
                reply=result.reply,
                conversation_id=conversation_id,
            )

    async def _persist(
        self,
        conversation_id: UUID,
        message: str,
        sentiment: str,
        reply: str,
        started_at: datetime,
    ) -> None:
        assistant_at = datetime.now(timezone.utc)
        if assistant_at <= started_at:
            assistant_at = started_at + timedelta(microseconds=1)

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(Conversation)
                    .values(id=conversation_id, created_at=started_at)
                    .on_conflict_do_nothing(index_elements=[Conversation.id])
                )
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        role="user",
                        content=message,
                        sentiment=None,
                        created_at=started_at,
                    )
                )
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=reply,
                        sentiment=sentiment,
                        created_at=assistant_at,
                    )
                )

    def _log(
        self,
        event: str,
        level: int,
        conversation_id: UUID,
        *,
        status: int,
        latency_ms: float,
        input_length: int,
        input_sha256: str,
        sentiment: str | None = None,
        error_code: str | None = None,
    ) -> None:
        extra = {
            "conversation_id": str(conversation_id),
            "engine": self._pipeline.name,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "input_length": input_length,
            "input_sha256": input_sha256,
        }
        if sentiment is not None:
            extra["sentiment"] = sentiment
        if error_code is not None:
            extra["error_code"] = error_code
        logger.log(level, event, extra=extra)

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000


def input_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


if __name__ == "__main__":
    print("input digest sample:", input_digest("今天面试没过"))
    print("AppError sample:", isinstance(input_too_long_error(4000), AppError))
