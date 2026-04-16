import time
import requests

def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "Accept":           "application/json, text/plain, */*",
        "Accept-Encoding":  "gzip, deflate, br",
        "Accept-Language":  "en-US,en;q=0.9",
        "Sec-Ch-Ua":        '"Not.A.Brand";v="24", "Chromium";v="146"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Fetch-Dest":   "empty",
        "Sec-Fetch-Mode":   "cors",
        "Sec-Fetch-Site":   "same-origin",
        "Referer":          "https://resultadoelectoral.onpe.gob.pe/",
    })
    return s

SESSION = _new_session()


def reset_session() -> None:
    global SESSION
    SESSION = _new_session()

BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"
PARTICIPANTES_PROV_URL = BASE + "/eleccion-presidencial/participantes-ubicacion-geografica-nombre?tipoFiltro=ubigeo_nivel_02&idAmbitoGeografico={ambito}&ubigeoNivel1={dep}&ubigeoNivel2={prov}&idEleccion=10"
TOTALES_PROV_URL       = BASE + "/resumen-general/totales?idAmbitoGeografico={ambito}&idEleccion=10&tipoFiltro=ubigeo_nivel_02&idUbigeoDepartamento={dep}&idUbigeoProvincia={prov}"

TOTALES_DROP_KEYS = {
    "idUbigeoDepartamento", "idUbigeoProvincia", "idUbigeoDistrito",
    "idUbigeoDistritoElectoral", "porcentajeVotosEmitidos", "porcentajeVotosValidos",
}


def _province_ubigeos(ubigeo_provincia: str) -> dict[str, str]:
    p = int(ubigeo_provincia)
    return {
        "dep":    str(p // 10000 * 10000),
        "prov":   ubigeo_provincia,
        "ambito": "2" if p >= 260000 else "1",
    }


def _get(url: str, **ubigeo_kwargs) -> dict:
    final_url = url.format(**ubigeo_kwargs) + f"&_t={int(time.time())}"
    rsp = SESSION.get(final_url, timeout=10, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    rsp.raise_for_status()
    return rsp.json()


def format_data(ubigeo_provincia: str) -> dict:
    ub = _province_ubigeos(ubigeo_provincia)

    totales_raw      = _get(TOTALES_PROV_URL, **ub).get("data", {})
    totales          = {k: v for k, v in totales_raw.items() if k not in TOTALES_DROP_KEYS}
    participantes    = _get(PARTICIPANTES_PROV_URL, **ub).get("data", [])

    candidatos = {
        (p["nombreCandidato"] or "VOTOS NULOS"): p.get("totalVotosValidos", 0)
        for p in participantes
    }

    votos_emitidos       = totales.get("totalVotosEmitidos", 0)
    actas_contabilizadas = totales.get("actasContabilizadas", 0)

    votos_restantes = (
        int(votos_emitidos * (100 / actas_contabilizadas - 1))
        if actas_contabilizadas else 0
    )

    candidatos["VOTOS EN BLANCO"] = max(0, votos_emitidos - sum(candidatos.values()))

    return {
        "ubigeo_distrito": ubigeo_provincia,
        "pendientesJee":   totales.get("pendientesJee", 0),
        "votosEmitidos":   votos_emitidos,
        "votosRestantes":  votos_restantes,
        "candidatos":      candidatos,
    }
