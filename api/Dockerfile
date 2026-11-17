FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Mirrors can be injected at build time for networks without direct PyPI access:
#   docker build --build-arg PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple .
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY pyproject.toml ./
COPY app ./app
RUN pip install --index-url "$PIP_INDEX_URL" .

COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]