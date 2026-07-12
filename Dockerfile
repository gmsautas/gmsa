FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Invoked via "sh start.sh" (exec form, no shell-string parsing involved) so it
# works identically whether run directly, via docker-compose, or on a host
# whose custom-command field doesn't reliably parse compound `a && b` strings
# (observed on Render's dockerCommand).
CMD ["sh", "start.sh"]
