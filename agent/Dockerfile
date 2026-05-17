# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY src/ ./src/
COPY skills/ ./skills/

ENV PYTHONPATH=/app/src \
    CHARTER_AGENT_SKILLS_DIR=/app/skills

CMD ["python", "-m", "charter_agent"]
