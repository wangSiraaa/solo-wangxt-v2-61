FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 容器默认使用 PostgreSQL，由 DATABASE_URL 覆盖
ENV DATABASE_URL=postgresql+psycopg://pilot:pilot@db:5432/pilot_schedule

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
