import ipaddress
import requests
import json
import logging
import sys

def get_current_ip():
    try:
        ip_request = requests.get(config_data["ip_address_provider_url"])
        ip_request.raise_for_status()
        return ip_request.text
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get current IP: {e}")
        sys.exit(1)

def should_update_dns_record(dns_record, current_ip):
    return (
        dns_record["name"] not in excluded_dns_records and
        dns_record["content"] != current_ip and
        not ipaddress.ip_address(dns_record["content"]).is_private
    )

def update_dns_records_for_zone(zone, authorization_headers, current_ip):
    try:
        dns_records_json = requests.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records?type=A", headers=authorization_headers).text
        dns_records = json.loads(dns_records_json)
        for dns_record in dns_records["result"]:
            if should_update_dns_record(dns_record, current_ip):
                logging.info(f"updating record: {dns_record['name']} with ip: {current_ip}")
                requests.patch(f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records/{dns_record['id']}", headers=authorization_headers, json={
                    "type": "A", "name": dns_record["name"], "content": current_ip, "ttl": 120})
    except Exception as e:
        logging.error(f"Failed to update DNS records for zone {zone['name']}: {e}")


logging.basicConfig(filename='log.log', level=logging.INFO)

with open('config.json') as config_file:
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

for config in config_data["cloudflare_configs"]:
    zones = config["zones"]
    cloudflare_api_token = config["cloudflare_api_token"]
    authorization_headers = {'Authorization': f'Bearer {cloudflare_api_token}'}
    excluded_dns_records = set(config["excluded_dns_records"])

    for zone in zones:
        update_dns_records_for_zone(zone, authorization_headers, current_ip)

with open(ip_file_path, "w") as f:
        f.write(current_ip)