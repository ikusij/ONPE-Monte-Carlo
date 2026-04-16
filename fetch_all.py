import argparse
import json
import time
from fetch import format_data, set_proxy

parser = argparse.ArgumentParser()
parser.add_argument("--proxy", metavar="IP", help="Proxy IP address, e.g. 152.228.134.212")
args = parser.parse_args()

if args.proxy:
    set_proxy(args.proxy)
    print(f"Using proxy: {args.proxy}")

with open("hierarchy.json", "r", encoding="utf-8") as f:
    output = json.load(f)

all_ubigeos = list(dict.fromkeys(
    str(dist["ubigeo"])
    for dept in output
    for prov in dept.get("provincias", [])
    for dist in prov.get("distritos", [])
))

print(f"Fetching {len(all_ubigeos)} districts...")

bundle = {}
failed = all_ubigeos

MAX_RETRIES = 5
attempt = 0

while failed and attempt < MAX_RETRIES:
    attempt += 1
    if attempt > 1:
        print(f"\nRetry {attempt}/{MAX_RETRIES} — {len(failed)} districts remaining...")
        time.sleep(2)

    next_failed = []
    for i, ubigeo in enumerate(failed, 1):
        try:
            bundle[ubigeo] = format_data(ubigeo)
            print(f"[{i}/{len(failed)}] {ubigeo} OK")
        except Exception as e:
            next_failed.append(ubigeo)
            print(f"[{i}/{len(failed)}] {ubigeo} FAILED: {e}")
        time.sleep(0.05)

    failed = next_failed

with open("bundle.json", "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False)

print(f"\nDone. {len(bundle)} fetched, {len(failed)} still failed after {attempt} attempt(s).")
if failed:
    print("Still failed:", failed)
