import requests
from pprint import pprint
import json

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://resultadoelectoral.onpe.gob.pe/",
}

def get_initial_list():
    DEPARTAMENTOS = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend/ubigeos/departamentos?idEleccion=10&idAmbitoGeografico={}"
    PROVINCIAS = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend/ubigeos/provincias?idEleccion=10&idAmbitoGeografico={}&idUbigeoDepartamento={}"
    DISTRITO = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend/ubigeos/distritos?idEleccion=10&idAmbitoGeografico={}&idUbigeoProvincia={}"

    def load_departamentos():
        data = []

        # Correct looping over ambito geografico 1 and 2 (inclusive)
        for ambito_geografico in [1, 2]:
            try:
                rsp = requests.get(DEPARTAMENTOS.format(ambito_geografico), headers=headers, timeout=10)
                rsp.raise_for_status()
                departamentos = rsp.json().get('data', [])
            except Exception as e:
                print(f"Failed to load departamentos for ambito {ambito_geografico}: {e}")
                continue

            for idx, departamento in enumerate(departamentos):
                # Departamento names may be duplicated across ambitos, print ambito for clarity
                print(f"Ambito {ambito_geografico}: {idx + 1}/{len(departamentos)} {departamento['nombre']}")

                departamento_info = {
                    "nombre": departamento["nombre"],
                    "ubigeo": departamento["ubigeo"],
                    "provincias": load_provincias(ambito_geografico, departamento["ubigeo"])
                }
                data.append(departamento_info)
        return data

    def load_provincias(ambito_geografico, ubigeo_departamento):
        try:
            rsp = requests.get(PROVINCIAS.format(ambito_geografico, ubigeo_departamento), headers=headers, timeout=10)
            rsp.raise_for_status()
            provincias = rsp.json().get('data', [])
        except Exception as e:
            print(f"Failed to load provincias {ubigeo_departamento} for ambito {ambito_geografico}: {e}")
            return []

        provincias_data = []
        for provincia in provincias:
            provincia_info = {
                "nombre": provincia["nombre"],
                "ubigeo": provincia["ubigeo"],
                "distritos": load_distritos(ambito_geografico, provincia["ubigeo"])
            }
            provincias_data.append(provincia_info)
        return provincias_data

    def load_distritos(ambito_geografico, ubigeo_provincia):
        try:
            rsp = requests.get(DISTRITO.format(ambito_geografico, ubigeo_provincia), headers=headers, timeout=10)
            rsp.raise_for_status()
            distritos = rsp.json().get('data', [])
        except Exception as e:
            print(f"Failed to load distritos {ubigeo_provincia} for ambito {ambito_geografico}: {e}")
            return []

        distritos_data = []
        for distrito in distritos:
            distrito_info = {
                "nombre": distrito["nombre"],
                "ubigeo": distrito["ubigeo"]
            }
            distritos_data.append(distrito_info)
        return distritos_data

    with open("output.json", "w", encoding="utf-8") as f:
        json.dump(load_departamentos(), f, ensure_ascii=False, indent=2)

def write_zone_dict():
    with open("output.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    zone_dict = {}
    all_district_ubigeos = []

    for departamento in data:
        dept_districts = []
        for provincia in departamento.get("provincias", []):
            prov_districts = []
            for distrito in provincia.get("distritos", []):
                # District maps to just its own code as a single-element array
                zone_dict[distrito["nombre"]] = [distrito["ubigeo"]]
                prov_districts.append(distrito["ubigeo"])
                dept_districts.append(distrito["ubigeo"])
                all_district_ubigeos.append(distrito["ubigeo"])
            # Province mapped to array of its districts' ids
            zone_dict[provincia["nombre"]] = prov_districts
        # Department mapped to array of all districts' ids (from all its provinces)
        zone_dict[departamento["nombre"]] = dept_districts

    # Add ALL key with all district codes
    zone_dict["ALL"] = all_district_ubigeos

    with open("nombre_ubigeo.json", "w", encoding="utf-8") as f:
        json.dump(zone_dict, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    get_initial_list()
    write_zone_dict()
