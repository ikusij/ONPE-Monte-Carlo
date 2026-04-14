import json
import numpy as np
import pandas as pd
import streamlit as st

from monte_carlo import (
    MonteCarloConfig,
    CandidateResult,
    SimulationResult,
    monte_carlo_simulation,
    aggregate_province,
)

with open("bundle.json", "r", encoding="utf-8") as f:
    bundle: dict = json.load(f)


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


st.set_page_config(page_title="ONPE Probabilidad de Victoria", layout="wide")
st.title("ONPE — Probabilidad de Victoria Electoral")

with open("nombre_ubigeo.json", "r", encoding="utf-8") as f:
    nombre_ubigeo: dict[str, list] = json.load(f)

with open("output.json", "r", encoding="utf-8") as f:
    _output = json.load(f)

ubigeo_names: dict[str, str] = {}
# Hierarchy: department → province → [(district_name, ubigeo_code)]
# Uses ubigeo codes from output.json directly — avoids name collisions in nombre_ubigeo.json
hierarchy: dict[str, dict[str, list[tuple[str, str]]]] = {}
for dept in _output:
    dept_name = dept["nombre"]
    hierarchy[dept_name] = {}
    for prov in dept.get("provincias", []):
        prov_name = prov["nombre"]
        pairs = [
            (d["nombre"], str(d["ubigeo"]))
            for d in prov.get("distritos", [])
            if str(d["ubigeo"]) in bundle
        ]
        if pairs:
            hierarchy[dept_name][prov_name] = sorted(pairs, key=lambda x: x[0])
        for dist in prov.get("distritos", []):
            ubigeo_names[str(dist["ubigeo"])] = dist["nombre"]

TODOS = "— Todos —"

col_dep, col_prov, col_dist = st.columns(3)

with col_dep:
    dept_sel = st.selectbox("Departamento", [TODOS] + sorted(hierarchy.keys()))

with col_prov:
    if dept_sel == TODOS:
        st.selectbox("Provincia", [TODOS], disabled=True)
        prov_sel = TODOS
    else:
        provs = [TODOS] + sorted(hierarchy.get(dept_sel, {}).keys())
        prov_sel = st.selectbox("Provincia", provs)

with col_dist:
    if prov_sel == TODOS:
        st.selectbox("Distrito", [TODOS], disabled=True)
        dist_sel = TODOS
        dist_ubigeo = None
    else:
        pairs = hierarchy.get(dept_sel, {}).get(prov_sel, [])
        dist_options = [TODOS] + [name for name, _ in pairs]
        dist_sel = st.selectbox("Distrito", dist_options)
        dist_ubigeo = next((uid for name, uid in pairs if name == dist_sel), None)

n_simulations    = st.sidebar.number_input("Simulaciones", min_value=500, max_value=3000, value=3000, step=100)
confidence_level = st.sidebar.slider("Nivel de confianza", 0.80, 0.99, 0.95, step=0.01)
prior_option     = st.sidebar.selectbox("Prior", ["flat", "jeffreys"])
votes_per_acta   = st.sidebar.number_input("Votos por acta", min_value=150, max_value=300, value=160, step=1)

if st.button("Ejecutar simulación"):
    # Collect ubigeo IDs based on selection level — all from output.json directly
    if dist_sel != TODOS and dist_ubigeo:
        ids = [dist_ubigeo]
    elif prov_sel != TODOS:
        ids = [uid for _, uid in hierarchy.get(dept_sel, {}).get(prov_sel, [])]
    elif dept_sel != TODOS:
        ids = [uid for pairs in hierarchy.get(dept_sel, {}).values() for _, uid in pairs]
    else:
        ids = [uid for dept_provs in hierarchy.values() for pairs in dept_provs.values() for _, uid in pairs]

    if not ids:
        st.error("No se encontraron ubigeos para esta selección.")
        st.stop()

    zone_label = (
        dist_sel if dist_sel != TODOS else
        prov_sel if prov_sel != TODOS else
        dept_sel if dept_sel != TODOS else
        "Nacional"
    )

    data = []
    fetch_failures = []
    for ubigeo in ids:
        row = bundle.get(str(ubigeo))
        if row is not None:
            data.append(row)
        else:
            fetch_failures.append({
                "Ubigeo":   str(ubigeo),
                "Distrito": ubigeo_names.get(str(ubigeo), "—"),
                "Error":    "No encontrado en el bundle",
            })

    if not data:
        st.error("No se encontraron datos en el bundle para esta zona.")
        st.stop()

    # ── Paso 1: simular distritos válidos ────────────────────────────────────
    with st.spinner("Ejecutando simulación Monte Carlo…"):
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

    # ── Paso 2: agregados provinciales de distritos válidos ──────────────────
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

    # ── Paso 3: sintetizar distritos omitidos con distribución provincial ────
    estimated, truly_skipped = [], []
    synthetic_results = []

    def _skip_reason(d):
        if d["votosEmitidos"] == 0:
            return "Sin votos contabilizados"
        cand_sum = sum(v for k, v in d["candidatos"].items() if k != "VOTOS EN BLANCO")
        if abs(cand_sum - d["votosEmitidos"]) > d["votosEmitidos"] * 0.05:
            return f"Datos inconsistentes (candidatos: {cand_sum:,} vs emitidos: {d['votosEmitidos']:,})"
        return "Desconocido"

    for d, r in zip(data, results):
        if r is not None:
            continue
        ubigeo_str = str(d["ubigeo_distrito"])
        pc = province_code(ubigeo_str)
        dc = department_code(ubigeo_str)
        prov_agg = province_aggregates.get(pc)
        dept_agg = department_aggregates.get(dc)
        fallback_agg = prov_agg or dept_agg
        fallback_label = "provincia" if prov_agg else ("departamento" if dept_agg else None)
        total_votes = d.get("pendientesJee", 0) * int(votes_per_acta)
        synthetic = make_synthetic_result(fallback_agg, total_votes)

        row = {
            "Ubigeo":              ubigeo_str,
            "Distrito":            ubigeo_names.get(ubigeo_str, "—"),
            "Motivo":              _skip_reason(d),
            "Distribución usada":  fallback_label or "—",
            "Votos est. (actas)":  total_votes,
        }

        if synthetic is not None:
            synthetic_results.append(synthetic)
            estimated.append(row)
        else:
            truly_skipped.append(row)

    # ── Paso 4: agregación final ─────────────────────────────────────────────
    all_results = [r for r in results if r is not None] + synthetic_results

    if not all_results:
        st.error("Sin datos utilizables para esta zona — todos los distritos fueron omitidos y no se pudo inferir una distribución provincial.")
        st.stop()

    result = aggregate_province(all_results)

    ci_pct = int(result.confidence_level * 100)

    st.subheader(f"Resultados — {zone_label}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Votos contabilizados", f"{result.votes_counted:,}")
    col2.metric("Votos restantes",      f"{result.votes_remaining:,}")
    col3.metric("Total de votos",       f"{result.total_votes:,}")
    col4.metric("% contabilizado",      f"{result.pct_counted:.1%}")

    rows = []
    for c in result.candidates:
        proj_votes = int(c.projected_share * result.total_votes)
        additional = proj_votes - c.votes_counted
        rows.append({
            "Candidato":                  c.name,
            "Votos contabilizados":       c.votes_counted,
            "Porcentaje actual":          c.current_share,
            "Porcentaje proyectado":      c.projected_share,
            f"IC inferior ({ci_pct}%)":   c.ci_low,
            f"IC superior ({ci_pct}%)":   c.ci_high,
            "Prob. de victoria":          c.win_probability,
            "Votos proyectados":          proj_votes,
            "Votos adicionales":          additional,
        })

    df = pd.DataFrame(rows)
    pct_cols = ["Porcentaje actual", "Porcentaje proyectado", f"IC inferior ({ci_pct}%)", f"IC superior ({ci_pct}%)", "Prob. de victoria"]
    int_cols = ["Votos contabilizados", "Votos proyectados", "Votos adicionales"]

    # Custom styling for 'Porcentaje proyectado' and 'Prob. de victoria' to show red text, no background
    def style_red_text(val):
        return "color: red;"  # Only red text, no background

    styled_df = (
        df.style
            .format({col: "{:.2%}" for col in pct_cols})
            .format({col: "{:,}"   for col in int_cols})
    )

    # Remove highlight_max for "Porcentaje proyectado" and "Prob. de victoria", keep for others if needed
    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
    )

    winner = result.projected_winner
    st.success(
        f"**Ganador proyectado:** {winner.name} — "
        f"probabilidad de victoria {winner.win_probability:.1%}, "
        f"porcentaje proyectado {winner.projected_share:.2%}"
    )

    if estimated:
        with st.expander(f"Distritos estimados con distribución provincial ({len(estimated)})"):
            st.write(
                "Estos distritos no tenían votos contabilizados. "
                "Sus totales de votos fueron estimados con `votasRestantesEstimadoConActas` "
                "y su distribución fue inferida de los distritos válidos de la misma provincia o departamento."
            )
            st.dataframe(pd.DataFrame(estimated), use_container_width=True, hide_index=True)

    if truly_skipped:
        with st.expander(f"Distritos excluidos completamente ({len(truly_skipped)} — sin datos provinciales disponibles)"):
            st.write(
                "Estos distritos no tenían datos válidos ni agregado provincial del cual inferir, "
                "por lo que fueron excluidos de la simulación."
            )
            st.dataframe(pd.DataFrame(truly_skipped), use_container_width=True, hide_index=True)

    if fetch_failures:
        with st.expander(f"Distritos no encontrados en el bundle ({len(fetch_failures)})"):
            st.write("Estos distritos no fueron encontrados en el bundle de datos y fueron excluidos.")
            st.dataframe(pd.DataFrame(fetch_failures), use_container_width=True, hide_index=True)
    