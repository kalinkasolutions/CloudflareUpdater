import ipaddress
import requests
import json
import logging
import sys


def get_current_ip():
    try:
        ip_request = requests.get(config_data["IP_ADDRESS_PROVIDER_ENDPOINT"])
        ip_request.raise_for_status()
        return ip_request.text
    except requests.exceptions.RequestException as e:
        logging.error(
            f"Failed to get current IP from provider {config_data["IP_ADDRESS_PROVIDER_ENDPOINT"]}: {e}"
        )
        sys.exit(1)


def should_update_dns_record(dns_record, current_ip):
    return (
        dns_record["name"] not in excluded_dns_records
        and dns_record["content"] != current_ip
        and not ipaddress.ip_address(dns_record["content"]).is_private
    )


def try_get_dns_records(zone, authorization_headers):
    try:
        response = requests.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records?type=A",
            headers=authorization_headers,
        )
        response.raise_for_status()
        return True, json.loads(response.text)
    except Exception as e:
        logging.error(
            f"Failed to fetch dns records for zone id: {zone['zone_id']}: {e}"
        )
        return False, None


def update_dns_records_for_zone(zone, authorization_headers, current_ip):
    update_results = []

    success, dns_records = try_get_dns_records(zone, authorization_headers)
    if not success:
        return False

    for dns_record in dns_records["result"]:
        if should_update_dns_record(dns_record, current_ip):
            logging.info(
                f"Trying to update record: {dns_record['name']} with IP: {current_ip}"
            )
            try:
                requests.patch(
                    f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records/{dns_record['id']}",
                    headers=authorization_headers,
                    json={
                        "type": "A",
                        "name": dns_record["name"],
                        "content": current_ip,
                        "ttl": 120,
                    },
                )
                logging.info(
                    f"Updated record: {dns_record['name']} with IP: {current_ip}"
                )
                update_results.append(True)
            except Exception as e:
                logging.error(
                    f"Failed to update DNS records {dns_record['name']} with IP: {current_ip}: {e}"
                )
                update_results.append(False)

    return all(update_results)


logging.basicConfig(
    filename="log.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

with open("config.json") as config_file:
    config_data = json.load(config_file)

ip_file_path = "./previous_ip"

try:
    with open(ip_file_path, "r") as f:
        previous_ip = f.read().strip()
except FileNotFoundError:
    previous_ip = ""

current_ip = get_current_ip()

if current_ip == previous_ip:
    sys.exit(0)

update_success = []
for config in config_data["CLOUDFLARE_CONFIGS"]:
    zones = config["ZONES"]
    cloudflare_api_token = config["CLOUDFLARE_DNS_API_TOKEN"]
    authorization_headers = {"Authorization": f"Bearer {cloudflare_api_token}"}
    excluded_dns_records = set(config["EXCLUDED_DNS_RECORD_NAMES"])

    for zone in zones:
        update_success.append(
            update_dns_records_for_zone(zone, authorization_headers, current_ip)
        )

    if all(update_success):
        with open(ip_file_path, "w") as f:
            f.write(current_ip)
