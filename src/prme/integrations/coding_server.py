"""Single-owner MCP service for a repository's Docker memory volume."""

import os

from prme.client import config_from_directory
from prme.config import EmbeddingConfig, MCPConfig


def main() -> None:
    import uvicorn

    from prme.mcp.server import create_http_app

    token = os.environ["PRME_CODING_TOKEN"]
    if len(token) < 32:
        raise ValueError("PRME_CODING_TOKEN must contain at least 32 characters")
    config = config_from_directory("/memory")
    config.database_url = None
    config.embedding = EmbeddingConfig(provider="fastembed")
    config.duckdb_threads = 2
    config.organizer.opportunistic_enabled = False
    config.mcp = MCPConfig(user_keys={"coding-agent": token})
    # The generated Compose file publishes this container port on host loopback.
    uvicorn.run(create_http_app(config), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
