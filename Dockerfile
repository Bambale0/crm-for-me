FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN pip install --no-deps . && useradd --create-home --uid 10001 appuser
USER appuser
CMD ["sh", "-c", "alembic upgrade head && exec crm-bot"]
