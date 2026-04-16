import json
import time
from fetch import format_data, reset_session

reset_session()

with open("hierarchy.json", "r", encoding="utf-8") as f:
    output = json.load(f)

all_provinces = list(dict.fromkeys(
    str(prov["ubigeo"])
    for dept in output
    for prov in dept.get("provincias", [])
))

print(f"Fetching {len(all_provinces)} provinces...")

bundle = {}
failed = all_provinces

MAX_RETRIES = 5
attempt = 0

while failed and attempt < MAX_RETRIES:
    attempt += 1
    if attempt > 1:
        print(f"\nRetry {attempt}/{MAX_RETRIES} — {len(failed)} provinces remaining...")
        time.sleep(2)

    next_failed = []
    for i, prov_ubigeo in enumerate(failed, 1):
        try:
            bundle[prov_ubigeo] = format_data(prov_ubigeo)
            print(f"[{i}/{len(failed)}] {prov_ubigeo} OK")
        except Exception as e:
            next_failed.append(prov_ubigeo)
            print(f"[{i}/{len(failed)}] {prov_ubigeo} FAILED: {e}")
        time.sleep(0.05)

    failed = next_failed

with open("bundle.json", "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False)

print(f"\nDone. {len(bundle)} fetched, {len(failed)} still failed after {attempt} attempt(s).")
if failed:
    print("Still failed:", failed)
