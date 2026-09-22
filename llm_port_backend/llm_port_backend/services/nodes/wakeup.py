"""Waking a node's stream the moment a command is queued for it.

Dispatch used to be pull-only: the stream loop drained the command queue after
each frame the *agent* happened to send, so a command sat in ``queued`` until
the next heartbeat or inventory frame arrived.  Measured on the DGX pair, the
round trip for a trivial command was 30-44s, of which 0.0-0.1s was the work
itself.  Everything else was waiting for the agent to say something.

So the backend pushes instead.  Two halves, because neither alone is enough:

* **Across processes.** The socket for a node is held by one uvicorn worker and
  the command can be issued by any of them, so an in-process event would wake
  the wrong worker.  Postgres ``LISTEN/NOTIFY`` carries the node id between
  them -- no new infrastructure, and the database is already the queue's source
  of truth.  It is also transactional: the notification is delivered when the
  inserting transaction commits, never before, so a woken reader can never look
  for a row that is not visible yet.

* **Within one process.** ``NOTIFY`` needs a live listener connection, and
  there are deployments without one -- a single worker on SQLite, a listener
  that has not reconnected yet.  An ``after_commit`` hook on the issuing
  session covers those, and fires at exactly the same moment, so the two paths
  cannot disagree about ordering.  Both set the same event, and setting a set
  event costs nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

#: Postgres channel names are identifiers, so this must stay lowercase and
#: unquoted-safe.
CHANNEL = "llmport_node_commands"

#: How long a failed listener waits before dialling again.  Long enough not to
#: hammer a database that is down, short enough that a restart heals quickly.
_RECONNECT_DELAY_SEC = 5.0


class CommandNotifier:
    """Per-process registry of "a command is waiting for this node" events.

    One instance per application.  The stream handler awaits
    :meth:`wait_for`; the command issuer calls :meth:`notify`.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._listener_task: asyncio.Task[None] | None = None
        self._listening = False

    # ── the waiting side ─────────────────────────────────────────────────

    def _event(self, node_id: uuid.UUID | str) -> asyncio.Event:
        key = str(node_id)
        existing = self._events.get(key)
        if existing is None:
            existing = asyncio.Event()
            self._events[key] = existing
        return existing

    async def wait_for(self, node_id: uuid.UUID | str, *, timeout: float | None = None) -> bool:
        """Block until a command is queued for *node_id*.

        Returns ``True`` if woken, ``False`` on timeout.  The event is cleared
        before returning so the next queued command wakes the stream again;
        clearing *after* the drain would race with a command queued during it.
        """
        evt = self._event(node_id)
        try:
            if timeout is None:
                await evt.wait()
            else:
                await asyncio.wait_for(evt.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return False
        evt.clear()
        return True

    def wake(self, node_id: uuid.UUID | str) -> None:
        """Set the event for *node_id*.  Safe to call when nobody is waiting."""
        self._event(node_id).set()

    def forget(self, node_id: uuid.UUID | str) -> None:
        """Deliberately does nothing.  Kept so callers need not know that.

        It used to drop the node's event when a stream closed, which looked
        tidy and was wrong.  A reconnect overlaps: the new stream calls
        :meth:`wait_for` and stores its event, and only then does the old
        stream's cleanup run and pop it.  The next command creates a *third*
        event and sets that one, so the live stream waits on an event nobody
        will ever set -- until the idle timeout fires and drains the queue on
        its own.

        The symptom was exact and easy to misread: on a node that had
        reconnected, every command dispatched 30.0s after it was issued,
        which is ``node_stream_idle_timeout_sec`` to the second, while a node
        that had not reconnected dispatched in well under a second.

        There is nothing to reclaim anyway.  The map is keyed by node, not by
        connection, so it holds at most one entry per machine in the fleet
        however many times each reconnects.
        """

    # ── the notifying side ───────────────────────────────────────────────

    async def notify(self, session: AsyncSession, node_id: uuid.UUID | str) -> None:
        """Announce a queued command, to fire when *session* commits.

        Deliberately does **not** wake anything now: the command row is not
        visible to other sessions until the commit, and a reader woken early
        would find an empty queue and go back to sleep for a full idle period.
        """
        key = str(node_id)
        self._arm_after_commit(session, key)

        bind = session.get_bind()
        dialect = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect != "postgresql":
            # SQLite in tests, or anything without LISTEN/NOTIFY.  The
            # after-commit hook above still covers the single-process case,
            # which is the only case those deployments have.
            return
        try:
            await session.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": CHANNEL, "payload": key},
            )
        except Exception:  # noqa: BLE001 - a wake-up must never fail the command
            log.warning("Could not queue a wake-up for node %s", key, exc_info=True)

    def _arm_after_commit(self, session: AsyncSession, key: str) -> None:
        """Wake this process's waiters when *session* commits, once."""
        try:
            sync_session = session.sync_session
        except Exception:  # noqa: BLE001 - not every session exposes one
            return

        def _on_commit(_sess: Any) -> None:
            self.wake(key)

        sa_event.listen(sync_session, "after_commit", _on_commit, once=True)

    # ── the listener ─────────────────────────────────────────────────────

    @property
    def listening(self) -> bool:
        """Whether a Postgres listener is currently connected."""
        return self._listening

    def start_listener(self, dsn: str) -> None:
        """Run the LISTEN loop for this process in the background."""
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(
            self._listen_forever(dsn), name="node_command_notify_listener"
        )

    async def stop_listener(self) -> None:
        task, self._listener_task = self._listener_task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _listen_forever(self, dsn: str) -> None:
        """Hold one connection open on ``LISTEN``, reconnecting as needed.

        A dedicated connection rather than a pooled one: ``LISTEN`` is a
        property of the connection, so a pooled connection handed back to
        somebody else would stop listening and take dispatch latency back to
        where it started -- silently.
        """
        try:
            import asyncpg  # noqa: PLC0415 - optional at import time
        except ImportError:
            log.info("asyncpg is not installed; node command wake-ups stay in-process")
            return

        while True:
            conn = None
            try:
                conn = await asyncpg.connect(dsn)
                await conn.add_listener(CHANNEL, self._on_notify)
                self._listening = True
                log.info("Listening on %s for queued node commands", CHANNEL)
                # asyncpg dispatches notifications on its own reader task, so
                # this one only has to keep the connection alive and notice
                # when it dies.
                while not conn.is_closed():
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect rather than give up
                log.warning(
                    "Node command listener lost its connection; retrying in %ss",
                    _RECONNECT_DELAY_SEC,
                    exc_info=True,
                )
            finally:
                self._listening = False
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()
            await asyncio.sleep(_RECONNECT_DELAY_SEC)

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        if payload:
            self.wake(payload)


def dsn_for_asyncpg(db_url: Any) -> str:
    """Strip SQLAlchemy's driver marker so asyncpg can dial the URL itself."""
    raw = str(db_url)
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


#: The application's notifier.  A module-level singleton because the issuing
#: path (an HTTP request) and the waiting path (a websocket) reach it from
#: opposite ends of the app and share nothing else.
_NOTIFIER: CommandNotifier | None = None


def get_command_notifier() -> CommandNotifier:
    global _NOTIFIER  # noqa: PLW0603 - one per process, by design
    if _NOTIFIER is None:
        _NOTIFIER = CommandNotifier()
    return _NOTIFIER
