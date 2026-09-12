"""Guard PRME's entries into dateparser's process-wide mutable parser state."""

from threading import RLock

# Older supported dateparser versions share settings and lazy locale caches.
# Ingestion and retrieval can run on different MemoryClient/event-loop threads.
# Keep every PRME parse/search call in the same critical section; an independent
# lock per pipeline does not protect either shared settings or locale caches.
DATEPARSER_LOCK = RLock()
