import json
import numpy as np
import pandas as pd
import streamlit as st

from fetch import format_data as _format_data
from monte_carlo import (
    MonteCarloConfig,
    CandidateResult,
    SimulationResult,
    monte_carlo_simulation,
    aggregate_province,
)

@st.cache_data(ttl=1800, show_spinner=False)
def format_data(ubigeo, votes_per_acta: int = 250):
    return _format_data(ubigeo, votes_per_acta)


def province_code(ubigeo: str) -> str:
    return str(ubigeo)[:4]

def department_code(ubigeo: str) -> str:
    return str(ubigeo)[:2]


def make_synthetic_result(province_agg: SimulationResult, total_votes: int) -> SimulationResult | None:
    """Synthesise a district result from a province aggregate distribution."""
    if total_votes <= 0 or province_agg is None:
        return None

    raw = province_agg.raw_finals          # (n_sim, n_cands) — already shares
    names = province_agg.candidate_names
    n_sim = province_agg.n_simulations
    cl = province_agg.confidence_level

    lo, hi = (1 - cl) / 2, 1 - (1 - cl) / 2
    means  = raw.mean(axis=0)
    stds   = raw.std(axis=0)
    ci_lo  = np.quantile(raw, lo, axis=0)
    ci_hi  = np.quantile(raw, hi, axis=0)
    win_probs = np.bincount(np.argmax(raw, axis=1), minlength=len(names)) / n_sim

    candidates = sorted([
        CandidateResult(
            name=names[i],
            votes_counted=0,
            current_share=0.0,
            projected_share=float(means[i]),
            ci_low=float(ci_lo[i]),
            ci_high=float(ci_hi[i]),
            win_probability=float(win_probs[i]),
            std=float(stds[i]),
        )
        for i in range(len(names))
    ], key=lambda c: c.projected_share, reverse=True)

    return SimulationResult(
        candidates=candidates,
        projected_winner=candidates[0],
        votes_counted=0,
        votes_remaining=total_votes,
        total_votes=total_votes,
        pct_counted=0.0,
        n_simulations=n_sim,
        prior_used=province_agg.prior_used,
        confidence_level=cl,
        raw_finals=raw,
        candidate_names=names,
    )


st.set_page_config(page_title="ONPE Win Probability", layout="wide")
st.title("ONPE — Election Win Probability")

with open("nombre_ubigeo.json", "r", encoding="utf-8") as f:
    nombre_ubigeo: dict[str, list] = json.load(f)

with open("output.json", "r", encoding="utf-8") as f:
    _output = json.load(f)

ubigeo_names: dict[str, str] = {}
for dept in _output:
    for prov in dept.get("provincias", []):
        for dist in prov.get("distritos", []):
            ubigeo_names[str(dist["ubigeo"])] = dist["nombre"]

zone = st.selectbox("Select zone (nombre_ubigeo)", sorted(nombre_ubigeo.keys()))

n_simulations    = st.sidebar.number_input("Simulations", min_value=1_000, max_value=100_000, value=10_000, step=1_000)
confidence_level = st.sidebar.slider("Confidence level", 0.80, 0.99, 0.95, step=0.01)
prior_option     = st.sidebar.selectbox("Prior", ["flat", "jeffreys"])
votes_per_acta   = st.sidebar.number_input("Votes per acta", min_value=1, max_value=1_000, value=250, step=1)

if st.button("Run simulation"):
    ids = nombre_ubigeo.get(zone, [])
    if not ids:
        st.error("No ubigeo IDs found for this zone.")
        st.stop()

    progress = st.progress(0, text="Fetching data…")
    data = []
    fetch_failures = []
    for i, ubigeo in enumerate(ids):
        try:
            data.append(format_data(ubigeo, int(votes_per_acta)))
        except Exception as e:
            fetch_failures.append({
                "Ubigeo":   str(ubigeo),
                "District": ubigeo_names.get(str(ubigeo), "—"),
                "Error":    str(e),
            })
        progress.progress((i + 1) / len(ids), text=f"Fetching data… {i+1}/{len(ids)}")
    progress.empty()

    if not data:
        st.error("No data could be fetched.")
        st.stop()

    # ── Step 1: simulate valid districts ────────────────────────────────────
    with st.spinner("Running Monte Carlo simulation…"):
        results = [
            monte_carlo_simulation(
                d,
                MonteCarloConfig(
                    n_simulations=int(n_simulations),
                    prior=prior_option,
                    confidence_level=confidence_level,
                    random_seed=i,
                ),
            )
            for i, d in enumerate(data)
        ]

    # ── Step 2: province aggregates from valid districts ────────────────────
    province_valid: dict[str, list[SimulationResult]] = {}
    for d, r in zip(data, results):
        if r is not None:
            pc = province_code(str(d["ubigeo_distrito"]))
            province_valid.setdefault(pc, []).append(r)

    province_aggregates: dict[str, SimulationResult] = {
        pc: aggregate_province(rs)
        for pc, rs in province_valid.items()
    }

    department_valid: dict[str, list[SimulationResult]] = {}
    for d, r in zip(data, results):
        if r is not None:
            dc = department_code(str(d["ubigeo_distrito"]))
            department_valid.setdefault(dc, []).append(r)

    department_aggregates: dict[str, SimulationResult] = {
        dc: aggregate_province(rs)
        for dc, rs in department_valid.items()
    }

    # ── Step 3: synthesise skipped districts using province distribution ────
    estimated, truly_skipped = [], []
    synthetic_results = []

    def _skip_reason(d):
        if d["votosEmitidos"] == 0:
            return "No votes counted"
        cand_sum = sum(v for k, v in d["candidatos"].items() if k != "VOTOS EN BLANCO")
        if abs(cand_sum - d["votosEmitidos"]) > d["votosEmitidos"] * 0.05:
            return f"Inconsistent data (candidates: {cand_sum:,} vs emitidos: {d['votosEmitidos']:,})"
        return "Unknown"

    for d, r in zip(data, results):
        if r is not None:
            continue
        ubigeo_str = str(d["ubigeo_distrito"])
        pc = province_code(ubigeo_str)
        dc = department_code(ubigeo_str)
        prov_agg = province_aggregates.get(pc)
        dept_agg = department_aggregates.get(dc)
        fallback_agg = prov_agg or dept_agg
        fallback_label = "province" if prov_agg else ("department" if dept_agg else None)
        total_votes = d.get("votasRestantesEstimadoConActas", 0)
        synthetic = make_synthetic_result(fallback_agg, total_votes)

        row = {
            "Ubigeo":        ubigeo_str,
            "District":      ubigeo_names.get(ubigeo_str, "—"),
            "Reason":        _skip_reason(d),
            "Distribution":  fallback_label or "—",
            "Est. votes (actas)": total_votes,
        }

        if synthetic is not None:
            synthetic_results.append(synthetic)
            estimated.append(row)
        else:
            truly_skipped.append(row)

    # ── Step 4: final aggregation ────────────────────────────────────────────
    all_results = [r for r in results if r is not None] + synthetic_results

    if not all_results:
        st.error("No usable data for this zone — all districts were skipped and no province-level distribution could be inferred.")
        st.stop()

    result = aggregate_province(all_results)

    ci_pct = int(result.confidence_level * 100)

    st.subheader(f"Results — {zone}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Votes counted",   f"{result.votes_counted:,}")
    col2.metric("Votes remaining", f"{result.votes_remaining:,}")
    col3.metric("Total votes",     f"{result.total_votes:,}")
    col4.metric("% counted",       f"{result.pct_counted:.1%}")

    rows = []
    for c in result.candidates:
        proj_votes = int(c.projected_share * result.total_votes)
        additional = proj_votes - c.votes_counted
        rows.append({
            "Candidate":             c.name,
            "Votes counted":         c.votes_counted,
            "Current share":         c.current_share,
            "Projected share":       c.projected_share,
            f"CI low ({ci_pct}%)":   c.ci_low,
            f"CI high ({ci_pct}%)":  c.ci_high,
            "Win probability":       c.win_probability,
            "Projected votes":       proj_votes,
            "Additional votes":      additional,
        })

    df = pd.DataFrame(rows)
    pct_cols = ["Current share", "Projected share", f"CI low ({ci_pct}%)", f"CI high ({ci_pct}%)", "Win probability"]
    int_cols = ["Votes counted", "Projected votes", "Additional votes"]

    st.dataframe(
        df.style
            .format({col: "{:.2%}" for col in pct_cols})
            .format({col: "{:,}"   for col in int_cols})
            .highlight_max(subset=["Win probability"],  color="#d4edda")
            .highlight_max(subset=["Projected share"],  color="#cce5ff"),
        use_container_width=True,
        hide_index=True,
    )

    winner = result.projected_winner
    st.success(
        f"**Projected winner:** {winner.name} — "
        f"win probability {winner.win_probability:.1%}, "
        f"projected share {winner.projected_share:.2%}"
    )

    if estimated:
        with st.expander(f"Districts estimated via provincial distribution ({len(estimated)})"):
            st.write(
                "These districts had no votes counted. "
                "Their vote totals were estimated from `votasRestantesEstimadoConActas` "
                "and their distribution was inferred from the other valid districts in the same province."
            )
            st.dataframe(pd.DataFrame(estimated), use_container_width=True, hide_index=True)

    if truly_skipped:
        with st.expander(f"Districts excluded entirely ({len(truly_skipped)} — no province data available)"):
            st.write(
                "These districts had no valid data and no province-level aggregate to draw from, "
                "so they were excluded from the simulation entirely."
            )
            st.dataframe(pd.DataFrame(truly_skipped), use_container_width=True, hide_index=True)

    if fetch_failures:
        with st.expander(f"Districts that could not be fetched ({len(fetch_failures)})"):
            st.write("These districts returned an error from the API and were excluded entirely.")
            st.dataframe(pd.DataFrame(fetch_failures), use_container_width=True, hide_index=True)
