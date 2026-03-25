"""
Async helpers for Celery worker tasks.

Celery workers are synchronous, but many tasks need to call async SQLAlchemy /
asyncpg functions.  Using bare asyncio.run() causes:

    RuntimeError: Event loop is closed

because asyncpg schedules cleanup callbacks on the loop *after* the coroutine
returns, but asyncio.run() closes the loop immediately.  run_async() fixes this
by draining all pending tasks and async generators before closing the loop.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run an async coroutine from a synchronous Celery task without leaking
    asyncpg connections or generating 'Event loop is closed' errors.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            # Drain any pending callbacks (e.g. asyncpg connection cleanup)
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)
