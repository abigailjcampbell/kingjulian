# Development image: the app plus its runtime dependencies.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Run as an ordinary user. /data/media is where uploaded files live (a named volume in docker-compose.yml).
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data/media \
 && chown -R app:app /app /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
