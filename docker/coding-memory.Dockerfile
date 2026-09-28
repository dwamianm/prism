FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra mcp --extra api --no-editable
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    FASTEMBED_CACHE_PATH=/cache/fastembed
CMD ["python", "-m", "prme.integrations.coding_server"]

FROM runtime AS sandbox
# No model, credentials, memory pack, Docker socket, or host checkout is mounted.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
# Packing checks run without network access, including their tokenizer assets.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work/src
CMD ["python", "/case/check.py"]
