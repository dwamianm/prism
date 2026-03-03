"""Terminal chat app with PRME memory integration.

A CLI chat interface that connects to GPT-4o (OpenAI) for responses, with
every message stored in PRME and relevant memories retrieved and injected
into each call. Full session logging to JSON-lines files.

Run:
    OPENAI_API_KEY=sk-... python examples/chat.py

Persistent memory across sessions:
    PRME_CHAT_DATA_DIR=./my_memories OPENAI_API_KEY=sk-... python examples/chat.py
"""

import asyncio
import json
import os
import sys
import signal
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# Suppress noisy warnings from dependencies
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")

from openai import AsyncOpenAI

from prme import MemoryEngine, PRMEConfig, Scope

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = os.environ.get("PRME_CHAT_MODEL", "gpt-4o")
USER_ID = os.environ.get("PRME_CHAT_USER", "chat_user")
SLIDING_WINDOW = 20  # max exchanges kept in conversation history
MAX_MEMORIES = 10    # top-K memories injected into system prompt

SYSTEM_BASE = (
    "You are a helpful assistant. You have access to a persistent memory system "
    "that remembers things from previous conversations. When relevant memories "
    "are provided, use them naturally in your responses — refer to past context, "
    "preferences, and decisions the user has shared before. If something contradicts "
    "what you remember, mention it."
)

COMMANDS = {
    "/debug":   "Toggle memory retrieval debug panel",
    "/history": "Show last N conversation exchanges (e.g. /history 5)",
    "/stats":   "Session statistics",
    "/clear":   "Clear sliding window (PRME memories persist)",
    "/quit":    "Exit",
    "/help":    "Show this help",
}


# ---------------------------------------------------------------------------
# Session logger (JSONL)
# ---------------------------------------------------------------------------

class SessionLogger:
    def __init__(self, session_id: str, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._path = log_dir / f"chat_{session_id}_{ts}.jsonl"
        self._fh = open(self._path, "a")

    def log(self, event_type: str, **data):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()

    @property
    def path(self) -> Path:
        return self._path

    def close(self):
        self._fh.close()


# ---------------------------------------------------------------------------
# Memory formatting
# ---------------------------------------------------------------------------

def format_memories(results) -> str:
    """Format retrieval results into a system prompt section."""
    if not results:
        return ""

    lines = ["## Relevant memories from past conversations\n"]
    for i, r in enumerate(results[:MAX_MEMORIES], 1):
        content = r.node.content.strip()
        score = r.composite_score
        node_type = r.node.node_type.value
        lifecycle = r.node.lifecycle_state.value
        lines.append(f"{i}. [{node_type}/{lifecycle}] (score: {score:.3f}) {content}")
    return "\n".join(lines)


def format_debug(response, retrieval_ms: float) -> str:
    """Format retrieval debug info for display."""
    meta = response.metadata
    lines = [
        "",
        "\033[90m┌─ Memory Debug ──────────────────────────────────────┐\033[0m",
        f"\033[90m│ Retrieval: {retrieval_ms:.0f}ms | "
        f"Candidates: {meta.candidates_included}/{sum(meta.candidates_generated.values())} | "
        f"Tokens: {response.bundle.tokens_used}/{response.bundle.token_budget}\033[0m",
    ]
    for i, r in enumerate(response.results[:5], 1):
        preview = r.node.content[:60].replace("\n", " ")
        trace = ""
        if r.score_trace:
            t = r.score_trace
            trace = (
                f" sem={t.semantic_similarity:.2f} lex={t.lexical_relevance:.2f}"
                f" rec={t.recency_factor:.2f} conf={t.confidence:.2f}"
            )
        lines.append(f"\033[90m│ {i}. [{r.composite_score:.3f}]{trace}\033[0m")
        lines.append(f"\033[90m│    {preview}\033[0m")
    if not response.results:
        lines.append("\033[90m│ (no memories found)\033[0m")
    lines.append("\033[90m└─────────────────────────────────────────────────────┘\033[0m")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

async def main():
    # --- Setup ---
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_API_KEY")
    if not api_key:
        print("Error: Set OPENAI_API_KEY (or OPEN_AI_API_KEY) in .env or environment.")
        sys.exit(1)

    client = AsyncOpenAI(api_key=api_key)
    session_id = uuid4().hex[:12]

    # Data directory: env var or temp
    data_dir_env = os.environ.get("PRME_CHAT_DATA_DIR")
    if data_dir_env:
        data_dir = Path(data_dir_env).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        persistent = True
    else:
        data_dir = Path(tempfile.mkdtemp(prefix="prme_chat_"))
        persistent = False

    lexical_dir = data_dir / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)

    config = PRMEConfig(
        db_path=str(data_dir / "memory.duckdb"),
        vector_path=str(data_dir / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )
    engine = await MemoryEngine.create(config)

    log_dir = Path(__file__).parent / "logs"
    logger = SessionLogger(session_id, log_dir)

    # Session state
    history: list[dict] = []  # sliding window of {"role", "content"}
    debug_mode = False
    msg_count = 0
    total_retrieval_ms = 0.0
    total_llm_ms = 0.0
    session_start = time.monotonic()

    logger.log(
        "session_start",
        session_id=session_id,
        user_id=USER_ID,
        data_dir=str(data_dir),
        model=MODEL,
        persistent=persistent,
    )

    # --- Banner ---
    print()
    print("\033[1mPRME Chat\033[0m — Terminal chat with persistent memory")
    print(f"  Session:  {session_id}")
    print(f"  Model:    {MODEL}")
    print(f"  Data dir: {data_dir}" + (" (persistent)" if persistent else " (temp)"))
    print(f"  Type /help for commands, /quit to exit")
    print()

    # --- Loop ---
    try:
        while True:
            # Read input
            try:
                user_input = input("\033[1myou>\033[0m ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            # --- Command handling ---
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                logger.log("command", command=cmd, args=arg)

                if cmd == "/quit":
                    break

                elif cmd == "/help":
                    print()
                    for c, desc in COMMANDS.items():
                        print(f"  {c:12s} {desc}")
                    print()

                elif cmd == "/debug":
                    debug_mode = not debug_mode
                    state = "ON" if debug_mode else "OFF"
                    print(f"\n  Debug mode: {state}\n")

                elif cmd == "/history":
                    n = int(arg) if arg.isdigit() else 10
                    print()
                    shown = history[-n * 2:] if history else []
                    if not shown:
                        print("  (no history)")
                    for msg in shown:
                        role = msg["role"]
                        content = msg["content"][:120].replace("\n", " ")
                        label = "\033[1myou\033[0m" if role == "user" else "\033[36mbot\033[0m"
                        print(f"  {label}: {content}")
                    print()

                elif cmd == "/stats":
                    elapsed = time.monotonic() - session_start
                    avg_retrieval = total_retrieval_ms / msg_count if msg_count else 0
                    avg_llm = total_llm_ms / msg_count if msg_count else 0
                    print()
                    print(f"  Messages:       {msg_count}")
                    print(f"  Session time:   {elapsed:.0f}s")
                    print(f"  Avg retrieval:  {avg_retrieval:.0f}ms")
                    print(f"  Avg LLM:        {avg_llm:.0f}ms")
                    print(f"  History window: {len(history) // 2} exchanges")
                    print(f"  Debug mode:     {'ON' if debug_mode else 'OFF'}")
                    print(f"  Log file:       {logger.path}")
                    print()

                elif cmd == "/clear":
                    history.clear()
                    print("\n  Sliding window cleared. PRME memories persist.\n")

                else:
                    print(f"\n  Unknown command: {cmd}. Type /help for options.\n")

                continue

            # --- Store user message ---
            t0 = time.monotonic()
            event_id = await engine.ingest(
                user_input,
                user_id=USER_ID,
                session_id=session_id,
                role="user",
                scope=Scope.PERSONAL,
            )
            store_ms = (time.monotonic() - t0) * 1000

            logger.log(
                "user_message",
                content=user_input,
                event_id=event_id,
                store_ms=round(store_ms, 1),
            )

            # --- Retrieve memories ---
            t0 = time.monotonic()
            retrieval_response = await engine.retrieve(
                user_input,
                user_id=USER_ID,
            )
            retrieval_ms = (time.monotonic() - t0) * 1000
            total_retrieval_ms += retrieval_ms

            logger.log(
                "retrieval",
                query=user_input,
                result_count=len(retrieval_response.results),
                retrieval_ms=round(retrieval_ms, 1),
                candidates_generated=retrieval_response.metadata.candidates_generated,
                tokens_used=retrieval_response.bundle.tokens_used,
                top_scores=[
                    {
                        "score": round(r.composite_score, 4),
                        "preview": r.node.content[:80],
                    }
                    for r in retrieval_response.results[:5]
                ],
            )

            if debug_mode:
                print(format_debug(retrieval_response, retrieval_ms))

            # --- Build system prompt with memories ---
            memory_section = format_memories(retrieval_response.results)
            system_prompt = SYSTEM_BASE
            if memory_section:
                system_prompt += "\n\n" + memory_section

            # --- Build messages for OpenAI ---
            messages = [{"role": "system", "content": system_prompt}]

            # Add sliding window history
            window = history[-(SLIDING_WINDOW * 2):]
            messages.extend(window)

            # Add current user message
            messages.append({"role": "user", "content": user_input})

            # --- Call OpenAI with streaming ---
            t0 = time.monotonic()
            print("\033[36mbot>\033[0m ", end="", flush=True)

            full_response = []
            try:
                stream = await client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        text = delta.content
                        full_response.append(text)
                        print(text, end="", flush=True)
            except Exception as e:
                error_msg = f"\n  [Error: {type(e).__name__}: {e}]"
                print(error_msg)
                logger.log("error", type=type(e).__name__, message=str(e))
                continue
            finally:
                print()  # newline after streaming

            llm_ms = (time.monotonic() - t0) * 1000
            total_llm_ms += llm_ms
            msg_count += 1

            assistant_content = "".join(full_response)

            # --- Store assistant response ---
            await engine.ingest(
                assistant_content,
                user_id=USER_ID,
                session_id=session_id,
                role="assistant",
                scope=Scope.PERSONAL,
            )

            logger.log(
                "assistant_message",
                content=assistant_content,
                llm_ms=round(llm_ms, 1),
                model=MODEL,
            )

            # --- Update sliding window ---
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": assistant_content})

    except KeyboardInterrupt:
        print("\n")

    # --- Cleanup ---
    elapsed = time.monotonic() - session_start
    logger.log(
        "session_end",
        messages=msg_count,
        duration_s=round(elapsed, 1),
        total_retrieval_ms=round(total_retrieval_ms, 1),
        total_llm_ms=round(total_llm_ms, 1),
    )
    logger.close()

    await engine.close()

    print(f"Session ended. {msg_count} messages in {elapsed:.0f}s.")
    print(f"Log: {logger.path}")
    if not persistent:
        print(f"Temp data at: {data_dir} (set PRME_CHAT_DATA_DIR to persist)")


if __name__ == "__main__":
    asyncio.run(main())
