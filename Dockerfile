FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93 AS build

COPY --from=ghcr.io/astral-sh/uv:0.8.17@sha256:e4644cb5bd56fdc2c5ea3ee0525d9d21eed1603bccd6a21f887a938be7e85be1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY uv.lock ./
COPY examples/handoffs ./examples/handoffs
COPY examples/northstar-continuity-review.pdf ./examples/northstar-continuity-review.pdf
COPY src ./src
ARG HANDOFF_FORGE_INSTALL_PROVIDERS=false
RUN if [ "$HANDOFF_FORGE_INSTALL_PROVIDERS" = "true" ]; then \
        uv sync --frozen --no-dev --no-editable --extra providers; \
    else \
        uv sync --frozen --no-dev --no-editable; \
    fi

FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    HANDOFF_FORGE_DATA_DIR=/data \
    HANDOFF_FORGE_OFFLINE=true \
    HANDOFF_FORGE_ALLOW_NETWORK=false \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app /app

RUN groupadd --gid 10001 handoff \
    && useradd --uid 10001 --gid 10001 --create-home handoff \
    && mkdir --parents /data \
    && chown --recursive handoff:handoff /data /app

USER 10001:10001
EXPOSE 8517
ENTRYPOINT ["handoff-forge"]
CMD ["ui", "--host", "0.0.0.0", "--port", "8517"]
