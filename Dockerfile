FROM python:3.12-alpine AS builder

RUN apk add --no-cache gcc musl-dev libffi-dev

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-alpine AS runner

ENV TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

COPY --from=builder /root/.local /home/appuser/.local
COPY run.py bridge.py bot.py database.py ./

RUN chown -R appuser:appgroup /app

USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH

CMD ["python", "run.py"]
