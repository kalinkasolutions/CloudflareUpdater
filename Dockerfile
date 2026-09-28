FROM python:3.14-alpine

WORKDIR /app
COPY update_cloudflare.py .

# Like the cron entry: a fresh process per run, so a crash only costs that one run.
CMD ["sh", "-c", "while true; do python3 update_cloudflare.py --config /config/config.json --log-file /logs/cloudflare_updater.log; sleep \"${INTERVAL_SECONDS:-60}\"; done"]
