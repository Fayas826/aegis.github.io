FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.lock ./backend/requirements.lock
RUN python -m pip install --no-cache-dir --requirement backend/requirements.lock

# API + shared core must be present at runtime
COPY backend ./backend
COPY core ./core
COPY frontend ./frontend
COPY project_memory ./project_memory

ENV AEGIS_PORT=8000
EXPOSE 8000

WORKDIR /app
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
