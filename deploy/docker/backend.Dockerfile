FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip -i "$PIP_INDEX_URL" \
    && python -m pip install -r requirements.txt -i "$PIP_INDEX_URL"

COPY backend/ /app/backend/
COPY deploy/ /app/deploy/
COPY shared/ /app/shared/

RUN mkdir -p /app/backend/data/local_assets

EXPOSE 8000

CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2"]
