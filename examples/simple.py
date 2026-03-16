"""Minimal PRME example — store and retrieve memories in 10 lines.

No API key required. Uses local embeddings (fastembed).
First run downloads the embedding model (~130 MB, takes 1-2 minutes).
"""

import tempfile

from prme import MemoryClient

with MemoryClient(tempfile.mkdtemp()) as client:
    client.store("Alice prefers dark mode in all editors", user_id="alice")
    client.store("The team chose PostgreSQL for the backend", user_id="alice")

    response = client.retrieve("What does Alice prefer?", user_id="alice")
    for result in response.results[:3]:
        print(f"  [{result.composite_score:.2f}] {result.node.content}")
