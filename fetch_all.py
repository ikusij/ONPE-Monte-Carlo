import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from fetch import format_data, set_proxy

parser = argparse.ArgumentParser()
parser.add_argument("--proxy", metavar="IP", help="Proxy IP address, e.g. 152.228.134.212")
parser.add_argument("--workers", type=int, default=10, help="Number of concurrent threads (default: 10)")
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
bundle_lock = Lock()
failed = all_ubigeos

MAX_RETRIES = 5
attempt = 0

def fetch_one(ubigeo):
    data = format_data(ubigeo)
    return ubigeo, data

while failed and attempt < MAX_RETRIES:
    attempt += 1
    if attempt > 1:
        print(f"\nRetry {attempt}/{MAX_RETRIES} — {len(failed)} districts remaining...")
        time.sleep(2)

    next_failed = []
    total = len(failed)
    done = 0
    lock = Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch_one, ubigeo): ubigeo for ubigeo in failed}
        for future in as_completed(futures):
            ubigeo = futures[future]
            with lock:
                done += 1
                pos = done
            try:
                _, data = future.result()
                with bundle_lock:
                    bundle[ubigeo] = data
                print(f"[{pos}/{total}] {ubigeo} OK")
            except Exception as e:
                next_failed.append(ubigeo)
                print(f"[{pos}/{total}] {ubigeo} FAILED: {e}")

    failed = next_failed

with open("bundle.json", "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False)

print(f"\nDone. {len(bundle)} fetched, {len(failed)} still failed after {attempt} attempt(s).")
if failed:
    print("Still failed:", failed)
