import ipaddress
import requests
import json
import logging

with open('config.json') as config_file:
    config_data = json.load(config_file)

ZONES = config_data["ZONES"]
CLOUDFLARE_DNS_API_TOKEN = config_data["CLOUDFLARE_DNS_API_TOKEN"]
HEADERS = {'Authorization': f'Bearer {CLOUDFLARE_DNS_API_TOKEN}'}
EXCLUDED_DNS_RECORD_NAMES = set(config_data["EXCLUDED_DNS_RECORD_NAMES"])

logging.basicConfig(filename='error.log', level=logging.ERROR)

def get_current_ip():
    ip_request = requests.get(config_data["IP_ADDRESS_PROVIDER_ENDPOINT"])
    return ip_request.text

def should_update_dns_record(dns_record, current_ip):
    return (
        dns_record["name"] not in EXCLUDED_DNS_RECORD_NAMES and
        dns_record["content"] != current_ip and
        not ipaddress.ip_address(dns_record["content"]).is_private
    )

def update_dns_records_for_zone(zone):
    try:
        dns_records_json = requests.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records?type=A", headers=HEADERS).text
        dns_records = json.loads(dns_records_json)
        for dns_record in dns_records["result"]:
            if should_update_dns_record(dns_record, current_ip):
                requests.patch(f"https://api.cloudflare.com/client/v4/zones/{zone['zone_id']}/dns_records/{dns_record['id']}", headers=HEADERS, json={
                    "type": "A", "name": dns_record["name"], "content": current_ip, "ttl": 120})
    except Exception as e:
        logging.error(f"Failed to update DNS records for zone {zone['name']}: {e}")

current_ip = get_current_ip()

for zone in ZONES:
    update_dns_records_for_zone(zone)
