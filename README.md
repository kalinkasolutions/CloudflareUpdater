# CloudflareUpdater

A simple python script to keep cloudflare DNS entries up to date.

Every run it looks up the public IP and points every A record at it, except records listed in
`excluded_records` and records that point at a private (LAN) address.

## Setup

Needs only Python 3, no extra packages.

Copy `config.example.json` to `config.json` and fill in your zones and API tokens.

crontab entry (`flock -n` skips a run while the previous one is still busy):
`* * * * * flock -n /tmp/cloudflare_updater.lock /usr/bin/python3 /path/to/update_cloudflare.py`

The config is read from `config.json` next to the script; use `--config /other/path.json` to pick another one.

## Optional settings

| Key | Default | Meaning |
| --- | --- | --- |
| `request_timeout_seconds` | `10` | Give up on a request to ipify or Cloudflare after this long |
| `dns_record_ttl_seconds` | `120` | TTL set on records the script updates |
| `log_file` | `cloudflare_updater.log` | Where errors and updates are logged |
| `metrics_file` | not set | Where to write Prometheus metrics; no metrics without it |

Relative paths are relative to the script's folder.

## Monitoring

Set `metrics_file` to a file in node_exporter's textfile collector folder, e.g.
`/var/lib/prometheus/node-exporter/cloudflare_updater.prom`. The user running the cron job needs
write access to that folder:
`sudo setfacl -m u:$USER:rwx /var/lib/prometheus/node-exporter`

| Metric | Meaning |
| --- | --- |
| `cloudflare_updater_last_run_timestamp_seconds` | When the last run finished |
| `cloudflare_updater_success` | 1 if the last run had no errors |
| `cloudflare_updater_ip_lookup_success` | 1 if the public IP could be looked up |
| `cloudflare_updater_zone_success{zone="..."}` | 1 if that zone's records are up to date |

Useful alerts:
- Failing: `cloudflare_updater_success == 0` for 10 minutes
- Not running or crashing: `time() - cloudflare_updater_last_run_timestamp_seconds > 600`

Error details are in the log file.
