# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

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

EXPOSE 8088

CMD ["python", "-m", "charter_agent"]
