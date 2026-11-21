FROM python:3.13.0-slim-bookworm
WORKDIR /bot
COPY mechfight.py bot.py ./
ENTRYPOINT ["python3", "-B", "-u", "/bot/bot.py"]
