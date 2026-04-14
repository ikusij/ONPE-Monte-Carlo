import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://resultadoelectoral.onpe.gob.pe/",
})

BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"
PARTICIPANTES_URL = BASE + "/eleccion-presidencial/participantes-ubicacion-geografica-nombre?tipoFiltro=ubigeo_nivel_03&idAmbitoGeografico=1&ubigeoNivel1={dep}&ubigeoNivel2={prov}&ubigeoNivel3={dist}&idEleccion=10"
TOTALES_URL       = BASE + "/resumen-general/totales?idAmbitoGeografico=1&idEleccion=10&tipoFiltro=ubigeo_nivel_03&idUbigeoDepartamento={dep}&idUbigeoProvincia={prov}&idUbigeoDistrito={dist}"

TOTALES_DROP_KEYS = {
    "idUbigeoDepartamento", "idUbigeoProvincia", "idUbigeoDistrito",
    "idUbigeoDistritoElectoral", "porcentajeVotosEmitidos", "porcentajeVotosValidos",
}


def _ubigeos(ubigeo_distrito: int | str) -> dict[str, str]:
    distrito = int(ubigeo_distrito)
    return {
        "dep":  str(distrito // 10000 * 10000),
        "prov": str(distrito // 100 * 100),
        "dist": str(distrito),
    }


def _get(url: str, **ubigeo_kwargs) -> dict:
    rsp = SESSION.get(url.format(**ubigeo_kwargs), timeout=10)
    rsp.raise_for_status()
    return rsp.json()


def load_participantes(ubigeo_distrito: int | str) -> dict[str, int]:
    ub = _ubigeos(ubigeo_distrito)
    participantes = _get(PARTICIPANTES_URL, **ub).get("data", [])

    candidatos = {
        (p["nombreCandidato"] or "VOTOS NULOS"): p.get("totalVotosValidos", 0)
        for p in participantes
    }
    return candidatos


def load_totales(ubigeo_distrito: int | str) -> dict:
    ub = _ubigeos(ubigeo_distrito)
    data = _get(TOTALES_URL, **ub).get("data", {})
    return {k: v for k, v in data.items() if k not in TOTALES_DROP_KEYS}


def format_data(ubigeo_distrito: int | str) -> dict:
    totales     = load_totales(ubigeo_distrito)
    candidatos  = load_participantes(ubigeo_distrito)

    votos_emitidos        = totales.get("totalVotosEmitidos", 0)
    actas_contabilizadas  = totales.get("actasContabilizadas", 0)

    votos_restantes = (
        int(votos_emitidos * (100 / actas_contabilizadas - 1))
        if actas_contabilizadas else 0
    )

    suma_votos_validos        = sum(candidatos.values())
    candidatos["VOTOS EN BLANCO"] = max(0, votos_emitidos - suma_votos_validos)

    return {
        "ubigeo_distrito": ubigeo_distrito,
        "pendientesJee":  totales.get("pendientesJee", 0),
        "votosEmitidos":  votos_emitidos,
        "votosRestantes": votos_restantes,
        "candidatos":     candidatos,
    }
