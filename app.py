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


def run_simulation(
    ids: tuple,
    n_simulations: int,
    confidence_level: float,
    prior: str,
    votes_per_acta: int,
    compute_breakdown: bool = False,
):
    data = [bundle[uid] for uid in ids if uid in bundle]
    fetch_failures = [
        {"Ubigeo": uid, "Distrito": uid, "Error": "No encontrado en el bundle"}
        for uid in ids if uid not in bundle
    ]

    if not data:
        return None, [], [], fetch_failures, []

    # Paso 1: simular distritos válidos
    results = [
        monte_carlo_simulation(
            d,
            MonteCarloConfig(
                n_simulations=n_simulations,
                prior=prior,
                confidence_level=confidence_level,
                random_seed=i,
            ),
        )
        for i, d in enumerate(data)
    ]

    # Paso 2: agregados provinciales y departamentales
    province_valid: dict[str, list] = {}
    department_valid: dict[str, list] = {}
    for d, r in zip(data, results):
        if r is not None:
            pc = str(d["ubigeo_distrito"])[:4]
            dc = str(d["ubigeo_distrito"])[:2]
            province_valid.setdefault(pc, []).append(r)
            department_valid.setdefault(dc, []).append(r)

    province_aggregates = {pc: aggregate_province(rs) for pc, rs in province_valid.items()}
    department_aggregates = {dc: aggregate_province(rs) for dc, rs in department_valid.items()}

    # Paso 3: sintetizar distritos omitidos
    def _skip_reason(d):
        if d["votosEmitidos"] == 0:
            return "Sin votos contabilizados"
        cand_sum = sum(v for k, v in d["candidatos"].items() if k != "VOTOS EN BLANCO")
        if abs(cand_sum - d["votosEmitidos"]) > d["votosEmitidos"] * 0.05:
            return f"Datos inconsistentes (candidatos: {cand_sum:,} vs emitidos: {d['votosEmitidos']:,})"
        return "Desconocido"

    estimated, truly_skipped, synthetic_results = [], [], []
    for d, r in zip(data, results):
        if r is not None:
            continue
        ubigeo_str = str(d["ubigeo_distrito"])
        pc, dc = ubigeo_str[:4], ubigeo_str[:2]
        prov_agg = province_aggregates.get(pc)
        dept_agg = department_aggregates.get(dc)
        fallback_agg = prov_agg or dept_agg
        fallback_label = "provincia" if prov_agg else ("departamento" if dept_agg else None)
        total_votes = d.get("pendientesJee", 0) * votes_per_acta
        synthetic = make_synthetic_result(fallback_agg, total_votes)
        row = {
            "Ubigeo":             ubigeo_str,
            "Distrito":           ubigeo_names.get(ubigeo_str, "—"),
            "Motivo":             _skip_reason(d),
            "Distribución usada": fallback_label or "—",
            "Votos est. (actas)": total_votes,
        }
        if synthetic is not None:
            synthetic_results.append(synthetic)
            estimated.append(row)
        else:
            truly_skipped.append(row)

    # Paso 4: agregación final
    all_results = [r for r in results if r is not None] + synthetic_results
    if not all_results:
        return None, estimated, truly_skipped, fetch_failures, []

    district_results = []
    if compute_breakdown:
        synthetic_iter = iter(synthetic_results)
        for d, r in zip(data, results):
            ubigeo_str = str(d["ubigeo_distrito"])
            if r is not None:
                district_results.append((ubigeo_str, r))
            else:
                synthetic = next(synthetic_iter, None)
                if synthetic is not None:
                    district_results.append((ubigeo_str, synthetic))

    return aggregate_province(all_results), estimated, truly_skipped, fetch_failures, district_results


_run_simulation_cached = st.cache_data(show_spinner=False)(run_simulation)



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

ubigeo_names: dict[str, str] = {}     # ubigeo → district name
ubigeo_to_dept: dict[str, str] = {}   # ubigeo → department name
ubigeo_to_prov: dict[str, str] = {}   # ubigeo → province name
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
            uid = str(dist["ubigeo"])
            ubigeo_names[uid]    = dist["nombre"]
            ubigeo_to_dept[uid]  = dept_name
            ubigeo_to_prov[uid]  = prov_name

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

n_simulations    = st.sidebar.number_input("Simulaciones", min_value=500, max_value=3000, value=100, step=100)
confidence_level = st.sidebar.slider("Nivel de confianza", 0.80, 0.99, 0.95, step=0.01)
prior_option     = st.sidebar.selectbox("Prior", ["flat", "jeffreys"])
votes_per_acta   = st.sidebar.number_input(
    "Votos por acta",
    min_value=150, max_value=300, value=160, step=1,
    help="Número estimado de votos por acta electoral. Se usa para calcular los votos pendientes en distritos sin datos (votos estimados = actas pendientes × este valor).",
)

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

    _sim_fn = _run_simulation_cached if dist_sel == TODOS else run_simulation
    with st.spinner("Ejecutando simulación Monte Carlo…"):
        result, estimated, truly_skipped, fetch_failures, district_results = _sim_fn(
            ids=tuple(ids),
            n_simulations=int(n_simulations),
            confidence_level=confidence_level,
            prior=prior_option,
            votes_per_acta=int(votes_per_acta),
            compute_breakdown=dist_sel == TODOS and int(n_simulations) <= 1000,
        )

    if result is None:
        st.error("Sin datos utilizables para esta zona — todos los distritos fueron omitidos y no se pudo inferir una distribución provincial.")
        st.stop()

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
        df.set_index("Candidato").style
            .format({col: "{:.2%}" for col in pct_cols})
            .format({col: "{:,}"   for col in int_cols})
    )

    st.dataframe(styled_df, use_container_width=True)

    winner = result.projected_winner
    st.success(
        f"**Ganador proyectado:** {winner.name} — "
        f"probabilidad de victoria {winner.win_probability:.1%}, "
        f"porcentaje proyectado {winner.projected_share:.2%}"
    )

    # ── Desglose geográfico ──────────────────────────────────────────────────
    if dist_sel == TODOS and district_results and int(n_simulations) <= 1000:
        if prov_sel != TODOS:
            geo_label  = "Distrito"
            key_fn     = lambda uid: ubigeo_names.get(uid, uid)
        elif dept_sel != TODOS:
            geo_label  = "Provincia"
            key_fn     = lambda uid: ubigeo_to_prov.get(uid, uid)
        else:
            geo_label  = "Departamento"
            key_fn     = lambda uid: ubigeo_to_dept.get(uid, uid)

        groups: dict[str, list] = {}
        for ubigeo, r in district_results:
            groups.setdefault(key_fn(ubigeo), []).append(r)

        top_candidates = [c.name for c in result.candidates[:5]]

        breakdown_rows = []
        for geo_name, group_results in sorted(groups.items()):
            grp = aggregate_province(group_results)
            cand_map = {c.name: c for c in grp.candidates}
            row = {
                geo_label:              geo_name,
                "% contabilizado":      grp.pct_counted,
                "Votos contabilizados": grp.votes_counted,
                "Total votos":          grp.total_votes,
                "Ganador proyectado":   grp.projected_winner.name,
            }
            for name in top_candidates:
                c = cand_map.get(name)
                if c:
                    proj = int(c.projected_share * grp.total_votes)
                    row[f"{name} — proy."] = proj
                    row[f"{name} — adic."] = proj - c.votes_counted
            breakdown_rows.append(row)

        with st.expander(f"Desglose por {geo_label.lower()} ({len(breakdown_rows)})"):
            bdf = pd.DataFrame(breakdown_rows).set_index(geo_label)
            pct_b = ["% contabilizado"]
            int_b = ["Votos contabilizados", "Total votos"] + [c for c in bdf.columns if "proy." in c or "adic." in c]
            st.dataframe(
                bdf.style
                    .format({col: "{:.1%}" for col in pct_b})
                    .format({col: "{:,}"   for col in int_b}),
                use_container_width=True,
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
    