# syntax=docker/dockerfile:1.7
FROM --platform=linux/amd64 python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml ./backend/
RUN pip install --upgrade pip && pip install ./backend
COPY backend/ ./backend/
ENV PYTHONPATH=/app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
