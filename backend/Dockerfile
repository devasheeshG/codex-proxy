# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Set work directory
WORKDIR /app

# Install curl for healthcheck and uv for dependency management
RUN apt-get update && apt-get install -y --no-install-recommends curl borgbackup && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy only requirements to cache them in docker layer
COPY pyproject.toml uv.lock /app/

# Project initialization
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Run as a non-root user (this image holds the Fernet key that decrypts pooled-account OAuth tokens).
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app /opt/venv
USER appuser
