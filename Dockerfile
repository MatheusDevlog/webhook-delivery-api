FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt

RUN useradd --create-home --uid 1000 app \
    && chown app:app /app
COPY --chown=app:app . .

USER app

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
