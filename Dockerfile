FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
COPY requirements-models.txt /app/requirements-models.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-models.txt

COPY backend /app/backend
COPY frontend /app/frontend
COPY images /app/images
COPY skills-builtin /app/skills-builtin

EXPOSE 8000 8080

CMD ["sh", "-c", "python -m http.server 8080 --directory /app & uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
