import asyncio
from uuid import UUID
from weakref import WeakValueDictionary


class ConversationLocks:
    """单进程内按 conversation_id 串行化，保证同一会话的记忆不交错。"""

    def __init__(self) -> None:
        # 调用方在 await 期间持有强引用；请求结束后锁可被回收。
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def for_conversation(self, conversation_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(str(conversation_id), asyncio.Lock())


if __name__ == "__main__":
    locks = ConversationLocks()
    first = locks.for_conversation(UUID("3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c"))
    second = locks.for_conversation(UUID("3f2b1c9e-7a4d-4e2b-9c1a-0d5e6f7a8b9c"))
    print("same lock:", first is second)
