import gc
import weakref
from uuid import uuid4

import pytest

from app.services.locks import ConversationLocks


def test_same_conversation_returns_same_lock():
    locks = ConversationLocks()
    conversation_id = uuid4()

    first = locks.for_conversation(conversation_id)
    second = locks.for_conversation(conversation_id)

    assert first is second


def test_different_conversations_use_different_locks():
    locks = ConversationLocks()

    assert locks.for_conversation(uuid4()) is not locks.for_conversation(uuid4())


def test_lock_is_collected_after_last_reference_drops():
    locks = ConversationLocks()
    conversation_id = uuid4()
    lock = locks.for_conversation(conversation_id)
    reference = weakref.ref(lock)

    assert locks.for_conversation(conversation_id) is lock

    del lock
    gc.collect()

    assert reference() is None
    assert locks.for_conversation(conversation_id) is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))