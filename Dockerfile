FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/
COPY run_bot.py run_reply_bot.py reset_discovery.py ./

RUN mkdir -p data logs

CMD ["python", "-u", "run_bot.py"]
