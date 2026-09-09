FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
WORKDIR /app

RUN sed -i -e 's@https\?://deb.debian.org/debian@https://mirrors.aliyun.com/debian@g' \
    -e 's@https\?://security.debian.org/debian-security@https://mirrors.aliyun.com/debian-security@g' \
    /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip --index-url "$PIP_INDEX_URL" \
    && pip install -r requirements.txt --index-url "$PIP_INDEX_URL"
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
