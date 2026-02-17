FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot/ ./bot/
RUN useradd -r -s /usr/sbin/nologin bot
USER bot
CMD ["python", "-m", "bot.main"]
