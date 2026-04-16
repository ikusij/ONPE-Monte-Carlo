import argparse
import csv
import json
import os
from datetime import datetime

from monte_carlo import MonteCarloConfig, monte_carlo_simulation, aggregate_province, print_results, make_synthetic_result

TIMESERIES_FILE = "timeseries.csv"
TIMESERIES_COLUMNS = [
    "timestamp",
    "pct_counted",
    "candidate",
    "projected_votes",
    "votes_counted",
]

parser = argparse.ArgumentParser(description="Run Monte Carlo simulation over all districts in bundle.json")
parser.add_argument("--date", metavar="DATETIME", help="Snapshot timestamp, e.g. '2026-04-15 19:54'. Required to save results to timeseries.")
parser.add_argument("--simulations", "-n", type=int, default=1_000, help="Number of simulations (default: 1,000)")
parser.add_argument("--prior", default="flat", help="Prior: 'flat', 'jeffreys', or a float (default: flat)")
parser.add_argument("--confidence", type=float, default=0.95, help="Confidence level (default: 0.95)")
parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
parser.add_argument("--top", type=int, default=10, help="Number of top candidates to display (default: 10)")
parser.add_argument("--bundle", default="bundle.json", help="Path to bundle.json (default: bundle.json)")
parser.add_argument("--votes-per-acta", type=int, default=220, help="Estimated votes per acta for districts with no counted votes (default: 220)")
args = parser.parse_args()

if args.date:
    try:
        timestamp = datetime.strptime(args.date, "%Y-%m-%d %H:%M")
    except ValueError:
        raise SystemExit(f"ERROR: --date must be in format 'YYYY-MM-DD HH:MM', got: {args.date!r}")
else:
    timestamp = None

prior = args.prior
try:
    prior = float(prior)
except ValueError:
    pass  # keep as string ("flat" or "jeffreys")

config = MonteCarloConfig(
    n_simulations=args.simulations,
    prior=prior,
    confidence_level=args.confidence,
    random_seed=args.seed,
)

print(f"Loading {args.bundle}...")
with open(args.bundle, encoding="utf-8") as f:
    bundle = json.load(f)

print(f"Running simulation over {len(bundle)} districts ({args.simulations:,} simulations each)...")

district_data = list(bundle.values())
results = []
for i, data in enumerate(district_data, 1):
    results.append(monte_carlo_simulation(data, config))
    if i % 200 == 0 or i == len(district_data):
        skipped = sum(1 for r in results if r is None)
        print(f"  {i}/{len(district_data)} districts processed ({skipped} skipped so far)...")

# Build province/department aggregates to back-fill skipped districts
province_valid: dict[str, list] = {}
department_valid: dict[str, list] = {}
for data, result in zip(district_data, results):
    if result is not None:
        pc = str(data["ubigeo_distrito"])[:4]
        dc = str(data["ubigeo_distrito"])[:2]
        province_valid.setdefault(pc, []).append(result)
        department_valid.setdefault(dc, []).append(result)

province_aggregates = {pc: aggregate_province(rs) for pc, rs in province_valid.items()}
department_aggregates = {dc: aggregate_province(rs) for dc, rs in department_valid.items()}

# Synthesise skipped districts using provincial/departmental distribution
synthetic_results = []
for data, result in zip(district_data, results):
    if result is not None:
        continue
    ubigeo_str = str(data["ubigeo_distrito"])
    pc, dc = ubigeo_str[:4], ubigeo_str[:2]
    fallback = province_aggregates.get(pc) or department_aggregates.get(dc)
    total_votes = data.get("pendientesJee", 0) * args.votes_per_acta
    synthetic = make_synthetic_result(fallback, total_votes)
    if synthetic is not None:
        synthetic_results.append(synthetic)

all_results = [r for r in results if r is not None] + synthetic_results
print(f"\nAggregating {len(all_results)} results ({len(synthetic_results)} synthetic from {sum(1 for r in results if r is None)} skipped districts)...")
national = aggregate_province(all_results)

print_results(national, top_n=args.top)

if timestamp:
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M")
    write_header = not os.path.exists(TIMESERIES_FILE)
    with open(TIMESERIES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TIMESERIES_COLUMNS)
        if write_header:
            writer.writeheader()
        for c in national.candidates:
            writer.writerow({
                "timestamp":       ts_str,
                "pct_counted":     round(national.pct_counted, 6),
                "candidate":       c.name,
                "projected_votes": int(c.projected_share * national.total_votes),
                "votes_counted":   c.votes_counted,
            })
    print(f"\nSaved snapshot '{ts_str}' → {TIMESERIES_FILE}")
else:
    print("\n(No --date given — results not saved to timeseries.)")
