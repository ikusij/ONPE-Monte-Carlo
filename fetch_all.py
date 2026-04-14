import json
import time
from fetch import format_data

with open("nombre_ubigeo.json", "r", encoding="utf-8") as f:
    nombre_ubigeo = json.load(f)

all_ubigeos = list(dict.fromkeys(
    ubigeo for ids in nombre_ubigeo.values() for ubigeo in ids
))

print(f"Fetching {len(all_ubigeos)} districts...")

bundle = {}
failed = []

for i, ubigeo in enumerate(all_ubigeos, 1):
    try:
        bundle[str(ubigeo)] = format_data(ubigeo)
        print(f"[{i}/{len(all_ubigeos)}] {ubigeo} OK")
    except Exception as e:
        failed.append(str(ubigeo))
        print(f"[{i}/{len(all_ubigeos)}] {ubigeo} FAILED: {e}")
    time.sleep(0.05)

with open("bundle.json", "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False)

print(f"\nDone. {len(bundle)} fetched, {len(failed)} failed.")
if failed:
    print("Failed ubigeos:", failed)
