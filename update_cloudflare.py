#!/usr/bin/env python3
import argparse
import ipaddress
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"

DEFAULT_CONFIG_FILE = SCRIPT_DIR / "config.json"
DEFAULT_LOG_FILE = "cloudflare_updater.log"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_DNS_RECORD_TTL_SECONDS = 120

LOKI_PUSH_PATH = "/loki/api/v1/push"
LOKI_PUSH_TIMEOUT_SECONDS = 5
LOKI_LABELS = {"service_name": "cloudflareupdater"}

# Network failures and HTTP error replies are OSErrors; a garbled reply is a ValueError.
REQUEST_ERRORS = (OSError, ValueError)

logger = logging.getLogger(__name__)


def send_request(url, timeout, method="GET", headers=None, json_body=None):
    """Return the response body as text. Raises on network errors and HTTP error replies."""
    request = urllib.request.Request(url, method=method, headers=headers or {})
    if json_body is not None:
        request.data = json.dumps(json_body).encode()
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode()


def get_public_ip(provider_url, timeout):
    reply = send_request(provider_url, timeout)
    # Raises if the provider sent back anything that isn't an IPv4 address, e.g. an error page.
    return str(ipaddress.IPv4Address(reply.strip()))


def try_get_public_ip(provider_url, timeout):
    try:
        return get_public_ip(provider_url, timeout)
    except REQUEST_ERRORS as error:
        logger.error(f"Failed to look up public IP: {error}")
        return None


def get_a_records(zone_id, headers, timeout):
    reply = send_request(
        f"{CLOUDFLARE_API}/zones/{zone_id}/dns_records?type=A", timeout, headers=headers
    )
    return json.loads(reply)["result"]


def set_record_ip(zone_id, record, ip, headers, timeout, ttl):
    send_request(
        f"{CLOUDFLARE_API}/zones/{zone_id}/dns_records/{record['id']}",
        timeout,
        method="PATCH",
        headers=headers,
        json_body={"content": ip, "ttl": ttl},
    )


def needs_update(record, public_ip, excluded_names):
    if record["name"] in excluded_names:
        return False
    # Records pointing into the LAN are managed by hand.
    if ipaddress.ip_address(record["content"]).is_private:
        return False
    return record["content"] != public_ip


def update_zone(zone, account, public_ip, timeout, ttl):
    headers = {"Authorization": f"Bearer {account['api_token']}"}
    excluded_names = set(account["excluded_records"])

    for record in get_a_records(zone["zone_id"], headers, timeout):
        if needs_update(record, public_ip, excluded_names):
            set_record_ip(zone["zone_id"], record, public_ip, headers, timeout, ttl)
            logger.info(
                f"Updated {record['name']} from {record['content']} to {public_ip}"
            )


def try_update_zone(zone, account, public_ip, timeout, ttl):
    try:
        update_zone(zone, account, public_ip, timeout, ttl)
        return True
    except REQUEST_ERRORS as error:
        logger.error(f"Failed to update zone {zone['name']}: {error}")
        return False


def format_metrics(ip_lookup_succeeded, zone_succeeded, finished_at):
    succeeded = ip_lookup_succeeded and all(zone_succeeded.values())
    lines = [
        "# HELP cloudflare_updater_last_run_timestamp_seconds When the updater last finished a run.",
        "# TYPE cloudflare_updater_last_run_timestamp_seconds gauge",
        f"cloudflare_updater_last_run_timestamp_seconds {finished_at:.0f}",
        "# HELP cloudflare_updater_success 1 if the last run had no errors at all.",
        "# TYPE cloudflare_updater_success gauge",
        f"cloudflare_updater_success {int(succeeded)}",
        "# HELP cloudflare_updater_ip_lookup_success 1 if the public IP could be looked up.",
        "# TYPE cloudflare_updater_ip_lookup_success gauge",
        f"cloudflare_updater_ip_lookup_success {int(ip_lookup_succeeded)}",
        "# HELP cloudflare_updater_zone_success 1 if the zone's records are up to date.",
        "# TYPE cloudflare_updater_zone_success gauge",
    ]
    for zone_name, zone_ok in zone_succeeded.items():
        lines.append(
            f'cloudflare_updater_zone_success{{zone="{zone_name}"}} {int(zone_ok)}'
        )
    return "\n".join(lines) + "\n"


def write_metrics(metrics_file, metrics_text):
    # Write then rename, so node_exporter never reads a half-written file. It only reads *.prom.
    temp_file = metrics_file.with_suffix(".tmp")
    temp_file.write_text(metrics_text)
    temp_file.replace(metrics_file)


def loki_push_url(loki_url):
    # A bare Loki address like http://loki:3100 gets the push path added.
    if urllib.parse.urlparse(loki_url).path in ("", "/"):
        return loki_url.rstrip("/") + LOKI_PUSH_PATH
    return loki_url


def format_loki_push(lines):
    """Build a Loki push body from (timestamp_ns, level, message) tuples."""
    values = [
        [str(timestamp_ns), json.dumps({"level": level, "msg": message})]
        for timestamp_ns, level, message in lines
    ]
    return {"streams": [{"stream": LOKI_LABELS, "values": values}]}


class LokiHandler(logging.Handler):
    """Collects a run's log lines, so they reach Loki in one request at the end of the run."""

    def __init__(self, push_url):
        super().__init__()
        self.push_url = push_url
        self.lines = []

    def emit(self, record):
        timestamp_ns = int(record.created * 1_000_000_000)
        self.lines.append((timestamp_ns, record.levelname.lower(), self.format(record)))

    def push(self):
        # Detached first, so a failed push is logged to the file and not queued for Loki again.
        logging.getLogger().removeHandler(self)
        if not self.lines:
            return
        try:
            send_request(
                self.push_url,
                LOKI_PUSH_TIMEOUT_SECONDS,
                method="POST",
                json_body=format_loki_push(self.lines),
            )
        except REQUEST_ERRORS as error:
            logger.warning(f"Failed to push {len(self.lines)} log lines to Loki: {error}")


def resolve_path(path):
    # Paths in the config are relative to the script, so cron works from any directory.
    return SCRIPT_DIR / path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Point Cloudflare A records at the current public IP."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_FILE, help="path to config.json"
    )
    parser.add_argument(
        "--log-file", type=Path, help="where to log; overrides log_file in the config"
    )
    return parser.parse_args()


def main(config):
    timeout = config.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
    ttl = config.get("dns_record_ttl_seconds", DEFAULT_DNS_RECORD_TTL_SECONDS)

    public_ip = try_get_public_ip(config["ip_provider_url"], timeout)
    ip_lookup_succeeded = public_ip is not None

    zone_succeeded = {}
    if ip_lookup_succeeded:
        for account in config["cloudflare_accounts"]:
            for zone in account["zones"]:
                zone_succeeded[zone["name"]] = try_update_zone(
                    zone, account, public_ip, timeout, ttl
                )

    if "metrics_file" in config:
        metrics_text = format_metrics(ip_lookup_succeeded, zone_succeeded, time.time())
        write_metrics(resolve_path(config["metrics_file"]), metrics_text)


if __name__ == "__main__":
    args = parse_args()
    config = json.loads(args.config.read_text())
    logging.basicConfig(
        filename=args.log_file or resolve_path(config.get("log_file", DEFAULT_LOG_FILE)),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    loki_handler = None
    if config.get("loki_url"):
        loki_handler = LokiHandler(loki_push_url(config["loki_url"]))
        logging.getLogger().addHandler(loki_handler)
    try:
        main(config)
    except Exception:
        # No metrics get written, so Grafana sees last_run go stale.
        logger.exception("Updater crashed")
        raise
    finally:
        if loki_handler:
            loki_handler.push()
