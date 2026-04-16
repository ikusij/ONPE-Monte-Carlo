import json
import os
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from monte_carlo import (
    MonteCarloConfig,
    CandidateResult,
    SimulationResult,
    monte_carlo_simulation,
    aggregate_province,
    make_synthetic_result,
)

@st.cache_data(show_spinner=False, ttl=1800)
def _load_bundle() -> dict:
    with open("bundle.json", "r", encoding="utf-8") as f:
        return json.load(f)

bundle: dict = _load_bundle()


def run_simulation(
    ids: tuple,
    n_simulations: int,
    confidence_level: float,
    prior: str,
    votes_per_acta: int,
    compute_breakdown: bool = False,
    geo_grouping: str = "none",   # "province" | "department" | "none"
):
    unique_ids = list(dict.fromkeys(ids))
    data = [bundle[uid] for uid in unique_ids if uid in bundle]
    fetch_failures = [
        {"Ubigeo": uid, "Provincia": uid, "Error": "No encontrado en el bundle"}
        for uid in unique_ids if uid not in bundle
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
            "Provincia":          ubigeo_names.get(ubigeo_str, "—"),
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

    final_agg = aggregate_province(all_results)

    # Compute geographic breakdown before freeing raw arrays
    breakdown = None
    if compute_breakdown and geo_grouping != "none":
        if geo_grouping == "province":
            geo_label = "Provincia"
            key_fn = lambda uid: ubigeo_names.get(uid, uid)
        else:
            geo_label = "Departamento"
            key_fn = lambda uid: ubigeo_to_dept.get(uid, uid)

        top_candidates = [c.name for c in final_agg.candidates[:5]]
        district_pairs = []
        synthetic_iter = iter(synthetic_results)
        for d, r in zip(data, results):
            ubigeo_str = str(d["ubigeo_distrito"])
            if r is not None:
                district_pairs.append((ubigeo_str, r))
            else:
                syn = next(synthetic_iter, None)
                if syn is not None:
                    district_pairs.append((ubigeo_str, syn))

        groups: dict[str, list] = {}
        for ubigeo, r in district_pairs:
            groups.setdefault(key_fn(ubigeo), []).append(r)

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

        breakdown = (geo_label, breakdown_rows)

    # Free raw simulation arrays — large numpy matrices not needed after aggregation
    for r in all_results:
        r.raw_finals = None
    final_agg.raw_finals = None

    return final_agg, estimated, truly_skipped, fetch_failures, breakdown


_run_simulation_cached = st.cache_data(show_spinner=False, max_entries=10, ttl=1800)(run_simulation)



st.set_page_config(page_title="ONPE Probabilidad de Victoria", layout="wide")
st.title("ONPE — Probabilidad de Victoria Electoral")

@st.cache_data(show_spinner=False, ttl=1800)
def _load_null_votes_data() -> pd.DataFrame:
    """Pre-compute null vote stats for every district, joined with geo names."""
    with open("hierarchy.json", "r", encoding="utf-8") as f:
        _output = json.load(f)

    ubigeo_to_geo: dict[str, dict] = {}
    for dept in _output:
        for prov in dept.get("provincias", []):
            ubigeo_to_geo[str(prov["ubigeo"])] = {
                "Departamento": dept["nombre"],
                "Provincia":    prov["nombre"],
            }

    excluded = {"VOTOS NULOS", "VOTOS EN BLANCO"}
    rows = []
    for ubigeo, province in bundle.items():
        emitidos = province.get("votosEmitidos", 0)
        if emitidos == 0:
            continue
        candidatos = province.get("candidatos", {})
        nulos = candidatos.get("VOTOS NULOS", 0)
        pct = nulos / emitidos * 100
        valid = {k: v for k, v in candidatos.items() if k not in excluded}
        leader = max(valid, key=lambda k: valid[k]) if valid else "—"
        geo = ubigeo_to_geo.get(ubigeo, {"Departamento": "—", "Provincia": ubigeo})
        rows.append({
            "Departamento":   geo["Departamento"],
            "Provincia":      geo["Provincia"],
            "Ubigeo":         ubigeo,
            "Votos emitidos": emitidos,
            "Votos nulos":    nulos,
            "% nulos":        pct,
            "Líder":          leader,
        })

    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, ttl=1800)
def _load_geo_data() -> tuple[dict, dict, dict, dict]:
    with open("hierarchy.json", "r", encoding="utf-8") as f:
        _output = json.load(f)

    _ubigeo_names: dict[str, str] = {}
    _ubigeo_to_dept: dict[str, str] = {}
    _ubigeo_to_prov: dict[str, str] = {}
    _hierarchy: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for dept in _output:
        dept_name = dept["nombre"]
        _hierarchy[dept_name] = {}
        for prov in dept.get("provincias", []):
            prov_name = prov["nombre"]
            prov_uid  = str(prov["ubigeo"])
            if prov_uid in bundle:
                # Each district entry maps to the province ubigeo
                pairs = sorted(
                    [(d["nombre"], prov_uid) for d in prov.get("distritos", [])],
                    key=lambda x: x[0],
                )
                _hierarchy[dept_name][prov_name] = pairs
            _ubigeo_names[prov_uid]   = prov_name
            _ubigeo_to_dept[prov_uid] = dept_name
            _ubigeo_to_prov[prov_uid] = prov_name
    return _ubigeo_names, _ubigeo_to_dept, _ubigeo_to_prov, _hierarchy

ubigeo_names, ubigeo_to_dept, ubigeo_to_prov, hierarchy = _load_geo_data()
null_votes_df = _load_null_votes_data()

TODOS = "— Todos —"

active_tab = st.sidebar.radio("Vista", ["Simulación Monte Carlo", "Votos Nulos", "Serie de Tiempo"], label_visibility="collapsed")
st.sidebar.markdown("---")

if active_tab == "Simulación Monte Carlo":
    n_simulations    = st.sidebar.number_input("Simulaciones", min_value=500, max_value=2000, value=500, step=100)
    confidence_level = st.sidebar.slider("Nivel de confianza", 0.80, 0.99, 0.95, step=0.01)
    prior_option     = st.sidebar.selectbox("Prior", ["flat", "jeffreys"])
    votes_per_acta   = st.sidebar.number_input(
        "Votos por acta",
        min_value=150, max_value=300, value=220, step=1,
        help="Número estimado de votos por acta electoral. Se usa para calcular los votos pendientes en distritos sin datos (votos estimados = actas pendientes × este valor).",
    )
elif active_tab == "Votos Nulos":
    nulos_threshold = st.sidebar.slider(
        "Umbral % votos nulos",
        min_value=0.0, max_value=20.0, value=5.0, step=0.5,
        help="Mostrar distritos cuyo porcentaje de votos nulos supera este umbral.",
    )

if active_tab == "Simulación Monte Carlo":
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

    if st.button("Ejecutar simulación", key="run_sim"):
        # Collect ubigeo IDs based on selection level — all from hierarchy.json directly
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

        if dist_sel == TODOS:
            if prov_sel != TODOS:
                geo_grouping = "none"
            elif dept_sel != TODOS:
                geo_grouping = "province"
            else:
                geo_grouping = "department"
        else:
            geo_grouping = "none"

        _sim_fn = _run_simulation_cached if dist_sel == TODOS else run_simulation
        with st.spinner("Ejecutando simulación Monte Carlo…"):
            result, estimated, truly_skipped, fetch_failures, breakdown = _sim_fn(
                ids=tuple(ids),
                n_simulations=int(n_simulations),
                confidence_level=confidence_level,
                prior=prior_option,
                votes_per_acta=int(votes_per_acta),
                compute_breakdown=dist_sel == TODOS and int(n_simulations) <= 1000,
                geo_grouping=geo_grouping,
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
        if breakdown:
            geo_label, breakdown_rows = breakdown
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


# ── Vista: Votos Nulos ───────────────────────────────────────────────────────
if active_tab == "Votos Nulos":
    global_mean_pct = null_votes_df["% nulos"].mean()

    filtered = null_votes_df[null_votes_df["% nulos"] > nulos_threshold].copy()
    filtered["Votos para llegar a la media"] = (
        filtered["Votos nulos"] - (global_mean_pct / 100) * filtered["Votos emitidos"]
    ).clip(lower=0).round().astype(int)

    # ── Aggregate table ───────────────────────────────────────────────────────
    st.subheader(f"Resumen — provincias con votos nulos > {nulos_threshold:.1f}%")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Provincias", f"{len(filtered):,}")
    m2.metric("% del total", f"{len(filtered)/len(null_votes_df)*100:.1f}%")
    m3.metric("Media nacional % nulos", f"{global_mean_pct:.2f}%")
    m4.metric("Media filtrada % nulos", f"{filtered['% nulos'].mean():.2f}%" if len(filtered) else "—")

    agg = (
        filtered.groupby("Líder")
        .agg(
            Provincias=("Provincia", "count"),
            Votos_nulos_total=("Votos nulos", "sum"),
            Votos_emitidos_total=("Votos emitidos", "sum"),
        )
        .assign(**{"% nulos promedio": lambda d: d["Votos_nulos_total"] / d["Votos_emitidos_total"] * 100})
        .sort_values("Provincias", ascending=False)
        .rename(columns={"Votos_nulos_total": "Votos nulos (total)", "Votos_emitidos_total": "Votos emitidos (total)"})
    )
    agg["% provincias"] = agg["Provincias"] / agg["Provincias"].sum() * 100
    agg["Votos para llegar a la media"] = (
        agg["Votos nulos (total)"] - (global_mean_pct / 100) * agg["Votos emitidos (total)"]
    ).clip(lower=0).round().astype(int)

    st.dataframe(
        agg.style
            .format({"% nulos promedio": "{:.2f}%", "% provincias": "{:.1f}%",
                     "Votos nulos (total)": "{:,}", "Votos emitidos (total)": "{:,}",
                     "Votos para llegar a la media": "{:,}"}),
        use_container_width=True,
    )

    # ── Province-level table ──────────────────────────────────────────────────
    st.subheader(f"Detalle por provincia ({len(filtered):,} provincias)")
    st.caption(
        f"**Votos para llegar a la media**: cuántos votos nulos habría que reclasificar "
        f"para que la provincia iguale la media nacional de {global_mean_pct:.2f}%."
    )

    detail_cols = [
        "Departamento", "Provincia",
        "Votos emitidos", "Votos nulos", "% nulos",
        "Votos para llegar a la media", "Líder",
    ]
    detail_df = filtered[detail_cols].sort_values("% nulos", ascending=False)

    st.dataframe(
        detail_df.style
            .format({"% nulos": "{:.2f}%", "Votos emitidos": "{:,}",
                     "Votos nulos": "{:,}", "Votos para llegar a la media": "{:,}"}),
        use_container_width=True,
        hide_index=True,
    )


# ── Vista: Serie de Tiempo ───────────────────────────────────────────────────
if active_tab == "Serie de Tiempo":
    TIMESERIES_FILE = "timeseries.csv"

    if not os.path.exists(TIMESERIES_FILE):
        st.info("No hay datos de serie de tiempo todavía. Ejecuta `run_simulation.py --date '...'` para generar snapshots.")
        st.stop()

    ts_df = pd.read_csv(TIMESERIES_FILE, parse_dates=["timestamp"])

    all_candidates = sorted(ts_df["candidate"].unique())
    selected = st.multiselect(
        "Candidatos",
        options=all_candidates,
        default=[c for c in all_candidates if any(k in c for k in ("FUJIMORI", "SANCHEZ", "LÓPEZ ALIAGA", "LOPEZ ALIAGA"))],
    )

    filtered_ts = ts_df[ts_df["candidate"].isin(selected)] if selected else ts_df

    def ts_line_chart(data, y_field, y_title):
        brush = alt.selection_interval(encodings=["x"])

        base = alt.Chart(data).encode(
            color=alt.Color("candidate:N", title="Candidato"),
        )

        detail = (
            base.transform_filter(brush)
            .mark_line(point=True)
            .encode(
                x=alt.X("timestamp:T", title="Fecha/Hora", axis=alt.Axis(format="%d/%m %H:%M")),
                y=alt.Y(f"{y_field}:Q", title=y_title, scale=alt.Scale(zero=False)),
                tooltip=[
                    alt.Tooltip("timestamp:T", title="Fecha/Hora", format="%Y-%m-%d %H:%M"),
                    alt.Tooltip("candidate:N", title="Candidato"),
                    alt.Tooltip(f"{y_field}:Q", title=y_title, format=","),
                ],
            )
            .properties(height=300)
        )

        overview = (
            base.mark_line()
            .encode(
                x=alt.X("timestamp:T", title="", axis=alt.Axis(format="%d/%m %H:%M")),
                y=alt.Y(f"{y_field}:Q", title="", axis=None),
            )
            .properties(height=60)
            .add_params(brush)
        )

        return detail & overview

    st.subheader("Votos proyectados a lo largo del tiempo")
    st.altair_chart(ts_line_chart(filtered_ts, "projected_votes", "Votos proyectados"), use_container_width=True)

    st.subheader("Votos contabilizados a lo largo del tiempo")
    st.altair_chart(ts_line_chart(filtered_ts, "votes_counted", "Votos contabilizados"), use_container_width=True)

    st.subheader("% de votos evaluados a lo largo del tiempo")
    pct_df = ts_df.drop_duplicates("timestamp")[["timestamp", "pct_counted"]].copy()
    pct_df["pct_counted"] = pct_df["pct_counted"] * 100
    pct_brush = alt.selection_interval(encodings=["x"])
    pct_detail = (
        alt.Chart(pct_df).transform_filter(pct_brush).mark_line(point=True)
        .encode(
            x=alt.X("timestamp:T", title="Fecha/Hora", axis=alt.Axis(format="%d/%m %H:%M")),
            y=alt.Y("pct_counted:Q", title="% contabilizado", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Fecha/Hora", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("pct_counted:Q", title="% contabilizado", format=".2f"),
            ],
        )
        .properties(height=300)
    )
    pct_overview = (
        alt.Chart(pct_df).mark_line()
        .encode(
            x=alt.X("timestamp:T", title="", axis=alt.Axis(format="%d/%m %H:%M")),
            y=alt.Y("pct_counted:Q", title="", axis=None),
        )
        .properties(height=60)
        .add_params(pct_brush)
    )
    st.altair_chart(pct_detail & pct_overview, use_container_width=True)

    with st.expander("Datos crudos"):
        st.dataframe(
            ts_df.sort_values(["timestamp", "candidate"]),
            use_container_width=True,
            hide_index=True,
        )
