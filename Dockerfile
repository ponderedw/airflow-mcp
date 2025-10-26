FROM python:3.12-slim

# Set environment variables for Poetry
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VENV_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Install Poetry
RUN pip install --no-cache-dir poetry

# Set work directory
# WORKDIR /app

# Copy Poetry configuration files
COPY pyproject.toml poetry.lock README.md ./
COPY airflow_mcp_ponder ./airflow_mcp_ponder

# Install dependencies
RUN poetry install


EXPOSE 8000

# Run with Poetry
CMD ["poetry", "run", "python", "-m", "airflow_mcp_ponder.mcp_airflow"]