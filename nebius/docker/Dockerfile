FROM python:3.11.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src ./src

# Install the package
RUN pip install --no-cache-dir -e "."

# Run as a non-root user
RUN useradd --create-home --uid 1001 app
USER app

# Default entrypoint runs the agent in API mode
EXPOSE 8000
CMD ["uvicorn", "ossia.api:app", "--host", "0.0.0.0", "--port", "8000"]
