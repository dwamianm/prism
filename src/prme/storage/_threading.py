"""Completion and cancellation handling for storage work."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def run_to_completion(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Keep the caller's locks held until a worker thread actually finishes.

    Cancelling ``asyncio.to_thread`` does not stop its native operation. Delay
    cancellation propagation until the worker finishes, including repeated
    cancellation during cleanup. A cancelled write may therefore have committed;
    this helper provides serialization, not rollback or a hard timeout.
    """
    return await run_async_to_completion(asyncio.to_thread(function, *args, **kwargs))


async def run_async_to_completion(coroutine: Coroutine[Any, Any, T]) -> T:
    """Finish an already-requested operation before propagating cancellation.

    Used for bounded storage writes and cleanup whose outcome must be recorded
    before the caller unwinds. This is not a rollback or a provider timeout.
    """
    worker = asyncio.create_task(coroutine)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Retrieve any worker error; preserve caller cancellation.
        raise
