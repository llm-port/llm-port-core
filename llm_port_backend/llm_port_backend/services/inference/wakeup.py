"""Waking the inference reconciler when there is something for it to do.

The reconciler sleeps 30 s between passes. Scaling a deployment from the
console went unnoticed for that long -- plus whatever pass was already under
way, 93 s in the walkthrough -- while the page still said "1 / 1", as if the
request had been lost. Pressing "check now" only re-queued the row, which was
already queued, and changed nothing.

So a change to what is wanted wakes the loop. The wake-up fires on *commit*,
never before: woken early, a pass would look for the change before it is
visible, find nothing, and go back to sleep for the full interval.

In-process only. Every uvicorn worker runs the loop and only the holder of
the advisory lock acts, so the worker that took the request may not be the
one that runs the pass; the others still pick the change up on their next
tick, which is no worse than before.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

#: One event per event loop: an ``asyncio.Event`` belongs to the loop it is
#: first awaited on, and tests run each case on a new one.
_events: dict[int, asyncio.Event] = {}


def _event() -> asyncio.Event:
    key = id(asyncio.get_running_loop())
    event = _events.get(key)
    if event is None:
        event = asyncio.Event()
        _events[key] = event
    return event


def wake_reconciler() -> None:
    """Start the next reconcile pass now rather than at the next tick."""
    try:
        _event().set()
    except RuntimeError:  # no running loop (sync caller): the tick covers it
        pass


def wake_reconciler_after_commit(session: AsyncSession) -> None:
    """Wake the reconciler once *session* commits (never if it rolls back)."""
    try:
        sync_session = session.sync_session
    except Exception:  # noqa: BLE001 - not every session exposes one
        wake_reconciler()
        return

    def _on_commit(_sess: Any) -> None:
        wake_reconciler()

    sa_event.listen(sync_session, "after_commit", _on_commit, once=True)


async def wait_for_work(timeout_sec: float) -> bool:
    """Sleep until woken or *timeout_sec* passes. True when woken."""
    event = _event()
    woken = False
    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=timeout_sec)
        woken = True
    event.clear()
    return woken
