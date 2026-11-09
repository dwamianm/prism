"""Whole-source entity mention excerpts with explicit provenance and token limits."""
import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache, partial
import os
import re
import threading
from typing import ParamSpec, TypeVar

from prme.models import MemoryNode
from prme.retrieval.owner_profile import PROFILE_HEADER
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType

P = ParamSpec("P")
T = TypeVar("T")

# Profile builds in one process share these worker threads. Concurrent slow
# builds then wait their turn here instead of filling the event loop's default
# executor, which storage and retrieval calls also use, and a cancelled build
# drops its queued steps rather than adding running ones.
_PROFILE_WORKERS = 2
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _profile_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(_PROFILE_WORKERS, thread_name_prefix="prme-profile")
        return _executor


def _reset_after_fork() -> None:
    # A forked child inherits the pool without its threads, and the lock in
    # whatever state another thread left it; give the child its own of each.
    global _executor, _executor_lock
    _executor, _executor_lock = None, threading.Lock()


os.register_at_fork(after_in_child=_reset_after_fork)


async def run_profile_work(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run CPU-bound profile work off the caller's event loop.

    Token counting releases the GIL; ``mentions_entity`` bounds each regex call.
    The step runs in a copy of the caller's context, like ``asyncio.to_thread``,
    so protected-diagnostics redaction and journal deadlines follow it.
    """
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _profile_executor(), partial(context.run, function, *args, **kwargs))


# Characters one regex call may compare, about 2 ms. The regex engine holds the
# GIL for a whole call, so a long name matched against long text written to
# match it slowly would stall every other thread, an event loop included.
_MATCH_WORK = 1 << 22


@lru_cache(maxsize=256)
def _mention_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)


def mentions_entity(text: str, name: str) -> bool:
    """Match a complete literal name, including possessives and Unicode boundaries.

    Long text is searched in windows so that one regex call compares about
    ``_MATCH_WORK`` characters at most, or ``len(name)`` for a longer name. A
    case-insensitive match of a literal name is exactly ``len(name)`` characters
    long, the lookbehind reads the text before ``pos``, and each window reaches
    one character past its last start for the lookahead, so the result is the
    same as one search of the whole text.
    """
    pattern = _mention_pattern(name)
    step = max(1, _MATCH_WORK // max(1, len(name)))
    for start in range(0, max(len(text) - len(name), 0) + 1, step):
        found = pattern.search(text, start, min(len(text), start + step + len(name)))
        if found is not None and found.start() < start + step:
            return True
    return False


def _date(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return (value.astimezone(timezone.utc) if value.tzinfo is not None else value).isoformat()


# A turn is the record of what a person said at one event time; a claim
# extracted from it is a narrower part of the same moment.
_TURN_TYPES = frozenset({NodeType.NOTE, NodeType.EVENT})

_WORD = re.compile(r"\w+")


def _distinct_terms(content: str) -> int:
    """How many different words a source states, a proxy for the facts it covers."""
    return len({word.casefold() for word in _WORD.findall(content)})


def _profile_order(node: MemoryNode) -> tuple:
    """Oldest first; at one event time the broader source, never a random id.

    The event time orders a profile, and its tie-break used to be the node
    id, a random UUID: a turn and a claim extracted from it share one event
    time, so which of them fit the token budget changed with the ids
    (issue #497). A turn now comes before claims derived from it, a source
    that states more distinct terms comes first, then its text, and only
    records whose text is also the same fall back to their id.
    """
    return (
        _date(node.event_time or node.created_at),
        0 if node.node_type in _TURN_TYPES else 1,
        -_distinct_terms(node.content),
        node.content,
        str(node.id),
    )


def build_profile(
    name: str, sources: list[MemoryNode], *, token_budget: int, tokenizer: str,
) -> tuple[str, list[MemoryNode], int] | None:
    """Select complete source records; distinct episodes never deduplicate by text."""
    text = PROFILE_HEADER.format(name=name) + "\nSource mentions; entity association is inferred."
    selected = []
    ordered = sorted(sources, key=_profile_order)
    for node in ordered:
        source = (
            f"[source={node.id}; event_time={_date(node.event_time)}; "
            f"recorded_at={_date(node.created_at)}; valid_from={_date(node.valid_from)}; "
            f"valid_to={_date(node.valid_to)}; epistemic={node.epistemic_type.value}; "
            f"source_type={node.source_type.value}]\n{node.content}"
        )
        candidate = text + "\n\n" + source
        if count_tokens(candidate, tokenizer) > token_budget:
            # A long source must not prevent later, smaller complete sources.
            continue
        text = candidate
        selected.append(node)
    if not selected:
        return None
    return text, selected, count_tokens(text, tokenizer)
