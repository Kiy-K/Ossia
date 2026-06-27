# =============================================================================
# Stage 1 — Build dependencies
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency metadata first for better layer caching
# pyproject.toml references README.md; hatchling validates it at install time
COPY pyproject.toml README.md langgraph.json ./

# Install all dependencies (dev included for type stubs, but we strip them later)
# Using regular (non-editable) install so site-packages copy cleanly to runtime
RUN uv pip install --system ".[dev]"

# =============================================================================
# Stage 2 — Runtime image
# =============================================================================
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create a non-root user for security
RUN groupadd -r ossia && useradd -r -g ossia -d /app -s /sbin/nologin ossia

# Copy installed packages from builder (non-editable = clean copy)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source so 'core' module is importable
# PYTHONPATH ensures `from core.X import Y` resolves from /app/src
COPY src/ src/

# Copy other runtime files
COPY langgraph.json ./
COPY specs/ specs/

# Create runtime data directory
RUN mkdir -p /tmp/ossia && chown -R ossia:ossia /app /tmp/ossia

# Add src/ to Python path so 'core' is importable
ENV PYTHONPATH=/app/src

USER ossia

EXPOSE 8000

# Health check — uses the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5).raise_for_status()"

# Start the FastAPI server
CMD ["uvicorn", "core.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
