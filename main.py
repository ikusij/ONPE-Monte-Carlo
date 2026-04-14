from monte_carlo import *
from fetch import format_data
import json

def run(n_simulations, prior, confidence_level, data):
    
    results = [
        monte_carlo_simulation(d, MonteCarloConfig(
            n_simulations=n_simulations,
            prior=prior,
            confidence_level=confidence_level,
            random_seed=i,
        ))
        for i, d in enumerate(data)
    ]

    result = aggregate_province(results)

    print_results(result, top_n=10)


if __name__ == "__main__":

    zone = "TUMBES"

    with open("nombre_ubigeo.json", "r", encoding="utf-8") as f:
        nombre_ubigeo = json.load(f)

    ids = nombre_ubigeo.get(zone, [])
    data = [format_data(ubigeo) for ubigeo in ids]

    print("Collected data")

    run(10_000, "flat", 0.95, data)