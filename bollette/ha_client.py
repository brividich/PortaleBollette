"""
Client REST verso Home Assistant (ADR-002, punti a/e/f).

- Trasporto: REST API nativa HA + Long-Lived Access Token (NIENTE ha-mcp).
- Configurazione SOLO da env (settings): HA_BASE_URL, HA_TOKEN, HA_TLS_VERIFY,
  HA_CA_BUNDLE.
- Fail-safe: nessuna funzione qui solleva eccezioni verso il chiamante; in caso
  di errore restituisce esito negativo e registra su HaSyncLog (per le scritture).
- Sicurezza: il token non viene MAI loggato.

Dipendenze: solo `requests` (niente librerie pesanti).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

from .models import HaSyncLog

logger = logging.getLogger("bollette.ha")

# Timeout (connect, read) in secondi e backoff esponenziale dei retry.
_TIMEOUT = (3, 5)
_BACKOFF = (2, 4, 8)  # 3 tentativi: attese tra un tentativo e il successivo.

# Entity scritte in HA. Sovrascrivibili da settings; default come da ADR-002 (b).
ENTITY_PREZZO_BASE = getattr(
    settings, "HA_ENTITY_PREZZO_BASE", "input_number.prezzo_kwh_base")
ENTITY_ACCISA_MARGINALE = getattr(
    settings, "HA_ENTITY_ACCISA_MARGINALE", "input_number.accisa_marginale_kwh")
ENTITY_SOGLIA_ACCISA = getattr(
    settings, "HA_ENTITY_SOGLIA_ACCISA", "input_number.soglia_accisa_kwh")
ENTITY_SOGLIA_RECUPERO = getattr(
    settings, "HA_ENTITY_SOGLIA_RECUPERO", "input_number.soglia_recupero_kwh")
ENTITY_PREZZO_SINGOLO = getattr(
    settings, "HA_ENTITY_PREZZO_SINGOLO", "input_number.prezzo_energia_kwh")
ENTITY_PREZZO_GAS = getattr(
    settings, "HA_ENTITY_PREZZO_GAS", "input_number.prezzo_gas_smc")
ENTITY_QUOTA_FISSA = getattr(
    settings, "HA_ENTITY_QUOTA_FISSA", "input_number.quota_fissa_giornaliera")
ENTITY_DATA_TARIFFA = getattr(
    settings, "HA_ENTITY_DATA_TARIFFA", "input_datetime.tariffa_aggiornata_il")
ENTITY_PORTALE_STATO = getattr(
    settings, "HA_ENTITY_PORTALE_STATO", "sensor.portale_bollette_stato")



@dataclass
class RisultatoScrittura:
    """Esito di una scrittura verso HA."""
    ok: bool
    entity_id: str
    valore: float | None
    dettaglio: str = ""


def _get_ha_config():
    """Recupera la configurazione HA con priorità alle variabili d'ambiente (settings), con fallback a ConfigurazioneSistema (DB)."""
    env_base_url = (getattr(settings, "HA_BASE_URL", "") or "").rstrip("/")
    env_token = getattr(settings, "HA_TOKEN", "") or ""
    env_verify = getattr(settings, "HA_TLS_VERIFY", True)
    env_ca_bundle = getattr(settings, "HA_CA_BUNDLE", None)

    try:
        from .models import ConfigurazioneSistema
        cfg = ConfigurazioneSistema.get_config()
        # L'env ha priorità sul database per URL e Token (sicurezza ADR-002)
        base_url = env_base_url or (cfg.ha_base_url or "").rstrip("/")
        token = env_token or cfg.ha_token
        verify = env_verify if env_base_url else (cfg.ha_tls_verify if cfg.ha_base_url else env_verify)
        ca_bundle = env_ca_bundle or cfg.ha_ca_bundle
        return base_url, token, verify, ca_bundle
    except Exception:
        logger.warning("Impossibile caricare ConfigurazioneSistema dal database, uso settings.", exc_info=True)
        return (
            env_base_url,
            env_token,
            env_verify,
            env_ca_bundle,
        )


def is_configured() -> bool:
    """True se HA_BASE_URL e HA_TOKEN sono valorizzati."""
    base_url, token, _, _ = _get_ha_config()
    return bool(base_url) and bool(token)


def _verify():
    """Parametro `verify` per requests: bundle custom, True o False (con warning)."""
    _, _, verify, ca_bundle = _get_ha_config()
    if not verify:
        return False
    if ca_bundle:
        return ca_bundle
    return True


def _headers():
    """Header di autenticazione. NON loggare mai questo dizionario."""
    _, token, _, _ = _get_ha_config()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, *, json=None, params=None, timeout=None):
    """
    Esegue una richiesta REST verso HA con timeout e retry esponenziale.

    Ritorna l'oggetto Response in caso di successo HTTP, oppure None se dopo
    tutti i tentativi non si ottiene risposta valida. NON solleva eccezioni.
    """
    base_url, _, _, _ = _get_ha_config()
    url = f"{base_url}{path}"
    req_timeout = timeout or _TIMEOUT

    ultimo_errore = ""
    for tentativo, attesa in enumerate((*_BACKOFF, None), start=1):
        try:
            resp = requests.request(
                method, url, headers=_headers(), json=json, params=params,
                timeout=req_timeout, verify=_verify(),
            )
            if resp.status_code < 400:
                return resp
            ultimo_errore = f"HTTP {resp.status_code}"
            # 4xx (es. entity inesistente) non si risolve ritentando: esci subito.
            if 400 <= resp.status_code < 500:
                logger.warning("HA %s %s -> %s", method, path, ultimo_errore)
                return None
        except requests.RequestException as exc:
            ultimo_errore = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "HA %s %s tentativo %d fallito (%s)", method, path, tentativo, ultimo_errore)
        if attesa is not None:
            time.sleep(attesa)
    logger.error("HA %s %s: tutti i tentativi falliti (%s)", method, path, ultimo_errore)
    return None


def get_state(entity_id: str) -> dict | None:
    """
    Legge lo stato corrente di una entity HA.

    Ritorna il dict JSON dello stato (con 'state' e 'attributes') o None.
    """
    if not is_configured():
        logger.warning("HA non configurato: get_state(%s) saltato.", entity_id)
        return None
    resp = _request("GET", f"/api/states/{entity_id}")
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def set_input_number(
    entity_id: str,
    value,
    *,
    modalita: str = HaSyncLog.Modalita.MANUALE,
    bolletta=None,
    bolletta_gas=None,
) -> RisultatoScrittura:
    """
    Imposta un helper input_number in HA via POST /api/services/input_number/set_value.

    Registra SEMPRE l'esito su HaSyncLog (tracciabilità delle scritture).
    Non solleva eccezioni: in caso di errore ritorna ok=False.
    """
    valore_float = float(value)
    dettaglio = ""
    ok = False

    if not is_configured():
        dettaglio = "HA non configurato (HA_BASE_URL/HA_TOKEN mancanti)."
        logger.warning(dettaglio)
    else:
        resp = _request(
            "POST", "/api/services/input_number/set_value",
            json={"entity_id": entity_id, "value": valore_float},
        )
        if resp is not None:
            try:
                modificati = resp.json()
                if isinstance(modificati, list) and len(modificati) == 0:
                    stato = get_state(entity_id)
                    if stato is None:
                        ok = False
                        dettaglio = f"L'entità '{entity_id}' non esiste in Home Assistant. Creala come Aiutante (input_number) in HA."
                    else:
                        ok = True
                else:
                    ok = True
            except Exception as exc:
                logger.warning("Verifica esistenza entità '%s' non riuscita: %s", entity_id, exc, exc_info=True)
                ok = True
        else:
            dettaglio = f"Scrittura '{entity_id}' su HA fallita (servizio non ha risposto)."

    HaSyncLog.objects.create(
        bolletta=bolletta if (bolletta and bolletta.pk) else None,
        bolletta_gas=bolletta_gas if (bolletta_gas and bolletta_gas.pk) else None,
        modalita=modalita,
        entity_id=entity_id,
        valore=Decimal(str(value)),
        esito=ok,
        dettaglio_errore=dettaglio,
    )
    return RisultatoScrittura(ok=ok, entity_id=entity_id, valore=valore_float,
                              dettaglio=dettaglio)



def set_input_datetime(entity_id: str, date_val) -> bool:
    """
    Imposta un aiutante input_datetime in HA via POST /api/services/input_datetime/set_datetime.
    date_val può essere una data/datetime o una stringa 'YYYY-MM-DD'.
    """
    if not is_configured() or not entity_id:
        return False
    
    if hasattr(date_val, "strftime"):
        str_val = date_val.strftime("%Y-%m-%d")
    else:
        str_val = str(date_val)

    resp = _request(
        "POST", "/api/services/input_datetime/set_datetime",
        json={"entity_id": entity_id, "date": str_val}
    )
    return resp is not None


def set_portal_state(sensor_id: str, state_val: str, attributes: dict | None = None) -> bool:
    """
    Crea o aggiorna un sensore virtuale in Home Assistant via REST API (POST /api/states/<entity_id>).
    Permette al Portale Bollette di pubblicare lo stato di sincronizzazione e metriche chiave in HA.
    """
    if not is_configured() or not sensor_id:
        return False

    payload = {
        "state": str(state_val),
        "attributes": attributes or {}
    }
    resp = _request("POST", f"/api/states/{sensor_id}", json=payload)
    return resp is not None


def _ws_url() -> str:
    base_url, _, _, _ = _get_ha_config()
    return base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"


def get_energia_statistiche_ws(entity_id: str, inizio, fine, period: str = "day") -> dict | None:
    """
    Interroga le Long-Term Statistics di Home Assistant tramite WebSocket API
    (recorder/statistics_during_period).
    A differenza di /api/history/period (che viene svuotato ogni 10 giorni dal recorder),
    le statistiche a lungo termine conservano lo storico per mesi/anni.
    """
    if not is_configured() or not entity_id:
        return None

    try:
        import websockets
    except ImportError:
        logger.debug("Modulo websockets non disponibile.")
        return None

    _, token, _, _ = _get_ha_config()
    ws_url = _ws_url()
    start_iso = timezone.localtime(inizio).isoformat()
    end_iso = timezone.localtime(fine).isoformat()

    async def _query():
        try:
            async with websockets.connect(ws_url, ping_timeout=6, close_timeout=3) as ws:
                await ws.recv()
                await ws.send(json.dumps({"type": "auth", "access_token": token}))
                auth = json.loads(await ws.recv())
                if auth.get("type") != "auth_ok":
                    return None

                await ws.send(json.dumps({
                    "id": 1,
                    "type": "recorder/statistics_during_period",
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "statistic_ids": [entity_id],
                    "period": period,
                }))
                res = json.loads(await ws.recv())
                records = res.get("result", {}).get(entity_id, [])
                if not records:
                    return None

                tot_change = sum(r.get("change", 0) for r in records if r.get("change") is not None)
                first_state = records[0].get("state") or records[0].get("sum", 0)
                last_state = records[-1].get("state") or records[-1].get("sum", 0)
                return {
                    "delta": tot_change,
                    "inizio": first_state,
                    "fine": last_state,
                    "records_count": len(records),
                }
        except Exception as exc:
            logger.debug("WS stats query error per %s: %s", entity_id, exc)
            return None

    try:
        return asyncio.run(_query())
    except Exception as exc:
        logger.warning("Errore esecuzione async query statistiche HA: %s", exc)
        return None


def get_energia_consumata(entity_id: str, inizio, fine) -> dict | None:
    """
    Calcola i kWh consumati tra `inizio` e `fine` per un sensore cumulativo.
    Strategia ibrida ad alta precisione:
    1. Tenta prima le Long-Term Statistics via WebSocket (funziona anche per mesi fa!).
    2. In caso di esito nullo, fallback sull'endpoint REST /api/history/period.
    """
    if not is_configured() or not entity_id:
        logger.warning("HA non configurato o entity vuota: lettura consumi saltata.")
        return None

    # Tentativo 1: Long-term statistics (supporta periodi storici senza limiti di retention)
    stats_ws = get_energia_statistiche_ws(entity_id, inizio, fine, period="day")
    if stats_ws and stats_ws.get("delta") is not None and stats_ws["delta"] > 0:
        return {
            "inizio": stats_ws.get("inizio", 0.0),
            "fine": stats_ws.get("fine", 0.0),
            "delta": stats_ws["delta"],
            "reset": False,
            "fonte": "Statistiche Long-Term HA",
        }

    # Tentativo 2: REST API /api/history/period (telemetria live ultimi 10 giorni)
    start_iso = timezone.localtime(inizio).isoformat()
    end_iso = timezone.localtime(fine).isoformat()
    resp = _request(
        "GET", f"/api/history/period/{start_iso}",
        params={
            "end_time": end_iso,
            "filter_entity_id": entity_id,
            "minimal_response": "true",
            "no_attributes": "true",
        },
        timeout=(4, 25),
    )
    if resp is None:
        return None
    try:
        dati = resp.json()
    except ValueError:
        return None
    if not dati or not dati[0]:
        return None

    def _num(stato):
        try:
            return float(stato.get("state"))
        except (TypeError, ValueError):
            return None

    serie = [v for v in (_num(s) for s in dati[0]) if v is not None]
    if len(serie) < 2:
        return None

    primo, ultimo = serie[0], serie[-1]
    reset = ultimo < primo  # possibile reset del contatore nel periodo
    delta = ultimo - primo if not reset else ultimo  # stima grezza in caso di reset
    return {
        "inizio": primo,
        "fine": ultimo,
        "delta": delta,
        "reset": reset,
        "fonte": "REST API History",
    }


# Metadati avanzati dei carichi specifici presenti nell'impianto Home Assistant dell'utente
DEVICE_DEFINITIONS = [
    {
        "id": "pc_nas",
        "nome": "Presa PC + NAS",
        "energia_entity": "sensor.shellyplus1pm_cc7b5c851450_energia",
        "potenza_entity": "sensor.shellyplus1pm_cc7b5c851450_potenza",
        "potenza_fallback": "sensor.presa_pc_potenza",
        "switch_entity": "switch.presa_pc",
        "icon": "🖥️",
        "categoria": "Ufficio & Tech",
        "colore": "#818cf8",
        "descrizione": "Postazione PC di lavoro, triplo monitor e server NAS",
    },
    {
        "id": "tv_media",
        "nome": "Presa TV & Media",
        "energia_entity": "sensor.presa_tv_energia",
        "potenza_entity": "sensor.presa_tv_potenza_2",
        "potenza_fallback": "sensor.presa_tv_potenza",
        "switch_entity": "switch.presa_tv_2",
        "icon": "📺",
        "categoria": "Svago & Living",
        "colore": "#c084fc",
        "descrizione": "Smart TV OLED/QLED, soundbar, console intrattenimento",
    },
    {
        "id": "lavastoviglie",
        "nome": "Lavastoviglie",
        "energia_entity": "sensor.lavastoviglie_energia",
        "potenza_entity": "sensor.lavastoviglie_potenza",
        "switch_entity": None,
        "icon": "🍽️",
        "categoria": "Cucina",
        "colore": "#38bdf8",
        "descrizione": "Lavastoviglie a incasso con monitoraggio cicli",
    },
    {
        "id": "piano_cottura",
        "nome": "Piano Cottura",
        "energia_entity": "sensor.piano_cottura_energia",
        "potenza_entity": "sensor.piano_cottura_potenza",
        "switch_entity": None,
        "icon": "🍳",
        "categoria": "Cucina",
        "colore": "#f97316",
        "descrizione": "Piano cottura a induzione 4 zone",
    },
    {
        "id": "forno_microonde",
        "nome": "Forno & Microonde",
        "energia_entity": "sensor.forno_microonde_energia",
        "potenza_entity": "sensor.forno_microonde_potenza",
        "switch_entity": None,
        "icon": "♨️",
        "categoria": "Cucina",
        "colore": "#fb923c",
        "descrizione": "Forno elettrico multifunzione e microonde combinato",
    },
    {
        "id": "prese_cucina",
        "nome": "Prese Piano Lavoro",
        "energia_entity": "sensor.shellyplus1pm_345f452041b0_energia",
        "potenza_entity": "sensor.shellyplus1pm_345f452041b0_potenza",
        "potenza_fallback": "sensor.prese_cucina_potenza",
        "switch_entity": None,
        "icon": "🔌",
        "categoria": "Cucina",
        "colore": "#34d399",
        "descrizione": "Prese bancone cucina per piccoli elettrodomestici",
    },
    {
        "id": "condizionatore",
        "nome": "Condizionatore Clima",
        "energia_entity": "sensor.condizionatore_energia",
        "potenza_entity": "sensor.condizionatore_potenza",
        "switch_entity": None,
        "climate_entity": "climate.condizionatore_salotto",
        "icon": "❄️",
        "categoria": "Climatizzazione",
        "colore": "#06b6d4",
        "descrizione": "Pompa di calore e climatizzatore salotto (Samsung)",
    },
    {
        "id": "luci_totali",
        "nome": "Luci Totali (Stima)",
        "energia_entity": "sensor.luci_energia_stimata",
        "potenza_entity": "sensor.luci_potenza_stimata",
        "switch_entity": None,
        "icon": "💡",
        "categoria": "Illuminazione",
        "colore": "#facc15",
        "descrizione": "Stima aggregata punti luce interni ed esterni",
    },
    {
        "id": "terrazzo_grande",
        "nome": "Terrazzo Grande",
        "energia_entity": "sensor.terrazzo_grande_energia_stimata",
        "potenza_entity": "sensor.terrazzo_grande_potenza_stimata",
        "switch_entity": None,
        "icon": "🌿",
        "categoria": "Esterni",
        "colore": "#a3e635",
        "descrizione": "Illuminazione e utenze terrazzo principale",
    },
    {
        "id": "scale",
        "nome": "Luci Scale",
        "energia_entity": "sensor.scale_energia_stimata",
        "potenza_entity": "sensor.scale_potenza_stimata",
        "switch_entity": None,
        "icon": "🪜",
        "categoria": "Illuminazione",
        "colore": "#eab308",
        "descrizione": "Illuminazione vano scale",
    },
    {
        "id": "terrazzino",
        "nome": "Terrazzino",
        "energia_entity": "sensor.terrazzino_energia_stimata",
        "potenza_entity": "sensor.terrazzino_potenza_stimata",
        "switch_entity": None,
        "icon": "🌙",
        "categoria": "Esterni",
        "colore": "#4ade80",
        "descrizione": "Illuminazione balcone di servizio",
    },
    {
        "id": "tenda",
        "nome": "Tenda Finestra",
        "energia_entity": "sensor.tenda_finestra_energy",
        "potenza_entity": "sensor.tenda_finestra_power",
        "switch_entity": None,
        "icon": "🪟",
        "categoria": "Comfort",
        "colore": "#2dd4bf",
        "descrizione": "Motore tenda elettrica oscurante",
    },
    {
        "id": "luci_albero",
        "nome": "Presa Ausiliaria / Luci Albero",
        "energia_entity": "sensor.luci_albero_sommatoria_consegnata",
        "potenza_entity": "sensor.luci_albero_potenza",
        "switch_entity": "switch.luci_albero",
        "icon": "🎄",
        "categoria": "Svago & Living",
        "colore": "#10b981",
        "descrizione": "Presa comandata con monitoraggio consumi",
    },
]

# Compatibilità per vecchie importazioni
DEVICE_ENTITIES = [
    (d["energia_entity"], d["nome"], d["icon"], d["categoria"].lower(), d["colore"])
    for d in DEVICE_DEFINITIONS
]


def call_service(domain: str, service: str, service_data: dict | None = None) -> bool:
    """
    Invoca un servizio nativo di Home Assistant via POST /api/services/<domain>/<service>.
    Fail-safe: non solleva eccezioni, ritorna True se HTTP < 400.
    """
    if not is_configured() or not domain or not service:
        return False
    resp = _request("POST", f"/api/services/{domain}/{service}", json=service_data or {})
    return resp is not None


def toggle_switch(entity_id: str) -> bool:
    """Commuta lo stato (on/off) di una presa smart o switch in Home Assistant."""
    return call_service("switch", "toggle", {"entity_id": entity_id})


def set_switch_state(entity_id: str, turn_on: bool) -> bool:
    """Accende o spegne una presa smart o switch in Home Assistant."""
    svc = "turn_on" if turn_on else "turn_off"
    return call_service("switch", svc, {"entity_id": entity_id})


def set_climate_temperature(entity_id: str, temperature: float) -> bool:
    """Imposta la temperatura di setpoint target su un'entità climate."""
    return call_service("climate", "set_temperature", {
        "entity_id": entity_id,
        "temperature": float(temperature),
    })


def set_climate_hvac_mode(entity_id: str, hvac_mode: str) -> bool:
    """Imposta la modalità HVAC (heat, cool, off) su un'entità climate."""
    return call_service("climate", "set_hvac_mode", {
        "entity_id": entity_id,
        "hvac_mode": str(hvac_mode),
    })


def get_climate_devices() -> list[dict]:
    """
    Recupera lo stato attuale di tutte le entità clima (es. termostato Netatmo, climatizzatore).
    """
    if not is_configured():
        return []
    resp = _request("GET", "/api/states")
    if resp is None:
        return []
    try:
        stati = {s.get("entity_id"): s for s in resp.json() if "entity_id" in s}
    except Exception:
        return []

    climate_targets = [
        ("climate.netatmo_smart_thermostat", "Termostato Netatmo", "🔥", "Riscaldamento Metano"),
        ("climate.condizionatore_salotto", "Condizionatore Salotto", "❄️", "Pompa di Calore"),
    ]

    dispositivi = []
    for eid, nome, icona, sub in climate_targets:
        st = stati.get(eid)
        if not st:
            continue
        attrs = st.get("attributes", {})
        curr_t = attrs.get("current_temperature")
        targ_t = attrs.get("temperature")
        action = attrs.get("hvac_action") or st.get("state")
        modes = attrs.get("hvac_modes") or []
        state_val = st.get("state", "off")

        dispositivi.append({
            "entity_id": eid,
            "nome": nome,
            "icona": icona,
            "sottotitolo": sub,
            "state": state_val,
            "current_temperature": round(float(curr_t), 1) if curr_t is not None else None,
            "target_temperature": round(float(targ_t), 1) if targ_t is not None else None,
            "hvac_action": action,
            "hvac_modes": modes,
            "is_heating": action in ("heating", "heat") or (state_val == "heat" and action != "idle"),
            "is_cooling": action in ("cooling", "cool") or (state_val == "cool" and action != "idle"),
            "is_idle": action == "idle",
        })
    return dispositivi


def get_device_breakdown(inizio=None, fine=None) -> dict:
    """
    Recupera la ripartizione dettagliata dei consumi per singoli carichi/dispositivi da Home Assistant:
    - Storico consumi (kWh) nel periodo
    - Potenza istantanea assorbita (W) in tempo reale
    - Stima spesa in € applicando il prezzo marginale della luce
    - Ripartizione aggregata per macro-categoria
    - Analisi baseload e quota standby non monitorata della casa
    """
    if not is_configured():
        return {
            "carichi": [],
            "totale_kwh": 0.0,
            "totale_costo_eur": 0.0,
            "periodo_inizio": "",
            "periodo_fine": "",
            "categorie": [],
            "baseload": {},
        }

    from datetime import datetime, time
    tz = timezone.get_current_timezone()
    if inizio is None:
        inizio = timezone.localtime(timezone.now()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif not timezone.is_aware(inizio):
        inizio = timezone.make_aware(datetime.combine(inizio, time(0, 0)), tz)

    if fine is None:
        fine = timezone.now()
    elif not timezone.is_aware(fine):
        fine = timezone.make_aware(datetime.combine(fine, time(23, 59, 59)), tz)

    # 1. Recupero stati live di tutte le entità per catturare potenza (W) e switch
    stati = {}
    resp_states = _request("GET", "/api/states")
    if resp_states:
        try:
            stati = {s.get("entity_id"): s for s in resp_states.json() if "entity_id" in s}
        except Exception as exc:
            logger.warning("Decodifica stati HA fallita: %s", exc, exc_info=True)

    # Tariffa marginale di riferimento per stimare il costo (€/kWh)
    tariffa_kwh = 0.20
    try:
        from .models import BollettaElettrica
        ult_bolletta = BollettaElettrica.objects.order_by("-periodo_fine").first()
        if ult_bolletta and ult_bolletta.prezzo_marginale_medio:
            tariffa_kwh = float(ult_bolletta.prezzo_marginale_medio)
    except Exception as exc:
        logger.warning("Recupero bolletta di riferimento per tariffa fallito: %s", exc, exc_info=True)

    # 2. Interrogazione statistiche long-term via WebSocket
    _, token, _, _ = _get_ha_config()
    ws_url = _ws_url()
    start_iso = timezone.localtime(inizio).isoformat()
    end_iso = timezone.localtime(fine).isoformat()
    stat_ids = [d["energia_entity"] for d in DEVICE_DEFINITIONS]

    raw_energy = {}
    try:
        import websockets

        async def _query():
            try:
                async with websockets.connect(ws_url, ping_timeout=6, close_timeout=3) as ws:
                    await ws.recv()
                    await ws.send(json.dumps({"type": "auth", "access_token": token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        return {}
                    await ws.send(json.dumps({
                        "id": 1,
                        "type": "recorder/statistics_during_period",
                        "start_time": start_iso,
                        "end_time": end_iso,
                        "statistic_ids": stat_ids,
                        "period": "day",
                    }))
                    res = json.loads(await ws.recv())
                    return res.get("result", {})
            except Exception as exc:
                logger.debug("Errore query WS breakdown dispositivi: %s", exc)
                return {}

        raw_energy = asyncio.run(_query())
    except Exception as exc:
        logger.warning("Modulo websockets non utilizzabile: %s", exc)

    def _get_num_state(eid):
        if not eid:
            return 0.0
        val = stati.get(eid, {}).get("state")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    carichi = []
    somma_potenza_monitorata_w = 0.0

    for d in DEVICE_DEFINITIONS:
        eid = d["energia_entity"]
        recs = raw_energy.get(eid, [])
        kwh = sum(r.get("change", 0) for r in recs if r.get("change") is not None)

        # Potenza istantanea (W)
        p_val = _get_num_state(d.get("potenza_entity"))
        if p_val <= 0 and d.get("potenza_fallback"):
            p_val = _get_num_state(d.get("potenza_fallback"))
        p_val = round(max(0.0, p_val), 1)
        somma_potenza_monitorata_w += p_val

        # Switch associato
        switch_id = d.get("switch_entity")
        switch_state = None
        if switch_id and switch_id in stati:
            switch_state = stati[switch_id].get("state")

        costo = round(kwh * tariffa_kwh, 2)
        carichi.append({
            "id": d["id"],
            "entity_id": eid,
            "potenza_entity": d.get("potenza_entity"),
            "switch_entity": switch_id,
            "switch_state": switch_state,
            "climate_entity": d.get("climate_entity"),
            "nome": d["nome"],
            "icon": d["icon"],
            "categoria": d["categoria"],
            "colore": d["colore"],
            "descrizione": d.get("descrizione", ""),
            "kwh": round(kwh, 2),
            "potenza_w": p_val,
            "costo_eur": costo,
            "is_active": p_val > 5.0,
        })

    carichi.sort(key=lambda x: (x["kwh"], x["potenza_w"]), reverse=True)
    totale_kwh = sum(c["kwh"] for c in carichi)
    totale_costo_eur = sum(c["costo_eur"] for c in carichi)

    for c in carichi:
        c["pct"] = round((c["kwh"] / totale_kwh * 100), 1) if totale_kwh > 0 else 0.0

    # Ripartizione per Categorie
    cat_map = {}
    for c in carichi:
        cat = c["categoria"]
        if cat not in cat_map:
            cat_map[cat] = {"nome": cat, "kwh": 0.0, "costo_eur": 0.0, "potenza_w": 0.0, "colore": c["colore"], "count": 0}
        cat_map[cat]["kwh"] += c["kwh"]
        cat_map[cat]["costo_eur"] += c["costo_eur"]
        cat_map[cat]["potenza_w"] += c["potenza_w"]
        cat_map[cat]["count"] += 1

    categorie = sorted(cat_map.values(), key=lambda x: x["kwh"], reverse=True)
    for cat in categorie:
        cat["kwh"] = round(cat["kwh"], 2)
        cat["costo_eur"] = round(cat["costo_eur"], 2)
        cat["potenza_w"] = round(cat["potenza_w"], 1)
        cat["pct"] = round((cat["kwh"] / totale_kwh * 100), 1) if totale_kwh > 0 else 0.0

    # Analisi Potenza Totale e Standby / Baseload
    potenza_totale_casa_w = _get_num_state("sensor.potenza_totale")
    if potenza_totale_casa_w <= 0:
        potenza_totale_casa_w = somma_potenza_monitorata_w

    potenza_standby_w = max(0.0, round(potenza_totale_casa_w - somma_potenza_monitorata_w, 1))
    pct_copertura = round((somma_potenza_monitorata_w / potenza_totale_casa_w * 100), 1) if potenza_totale_casa_w > 0 else 100.0

    baseload = {
        "potenza_totale_casa_w": round(potenza_totale_casa_w, 1),
        "potenza_monitorata_w": round(somma_potenza_monitorata_w, 1),
        "potenza_standby_w": potenza_standby_w,
        "pct_copertura": min(100.0, pct_copertura),
        "tariffa_kwh_applicata": tariffa_kwh,
    }

    return {
        "carichi": carichi,
        "totale_kwh": round(totale_kwh, 2),
        "totale_costo_eur": round(totale_costo_eur, 2),
        "periodo_inizio": inizio.strftime("%d/%m/%Y"),
        "periodo_fine": fine.strftime("%d/%m/%Y"),
        "categorie": categorie,
        "baseload": baseload,
    }


def sincronizza_termostato_netatmo_da_ha() -> dict:
    """
    Legge lo stato attuale del termostato Netatmo da Home Assistant
    (climate.netatmo_smart_thermostat e sensore temperatura associato)
    e sincronizza/aggiorna il record Netatmo della giornata odierna.
    """
    from .models import NetatmoRecordGiornaliero
    from django.utils import timezone
    oggi = timezone.localdate()

    if not is_configured():
        return {"ok": False, "errore": "Home Assistant non configurato."}

    clim = get_state("climate.netatmo_smart_thermostat")
    temp_st = get_state("sensor.netatmo_smart_thermostat_current_temperature")

    if not clim and not temp_st:
        return {"ok": False, "errore": "Entità termostato Netatmo non trovata in Home Assistant."}

    attrs = (clim or {}).get("attributes", {})
    t_curr = None
    if temp_st and temp_st.get("state") not in ("unavailable", "unknown", None):
        try:
            t_curr = float(temp_st["state"])
        except (ValueError, TypeError):
            pass
    if t_curr is None and "current_temperature" in attrs:
        try:
            t_curr = float(attrs["current_temperature"])
        except (ValueError, TypeError):
            pass

    t_set = None
    if "temperature" in attrs:
        try:
            t_set = float(attrs["temperature"])
        except (ValueError, TypeError):
            pass

    action = attrs.get("hvac_action") or (clim or {}).get("state", "idle")
    is_heating = action in ("heating", "heat")

    record, created = NetatmoRecordGiornaliero.objects.get_or_create(
        data=oggi,
        defaults={
            "secondi_caldaia": 1800 if is_heating else 0,
            "temp_interna_media": Decimal(str(round(t_curr, 2))) if t_curr is not None else None,
            "temp_setpoint_media": Decimal(str(round(t_set, 2))) if t_set is not None else None,
            "n_campionamenti": 1,
            "fonte_file": "Home Assistant Live API",
        }
    )

    if not created:
        if t_curr is not None:
            record.temp_interna_media = Decimal(str(round(t_curr, 2)))
        if t_set is not None:
            record.temp_setpoint_media = Decimal(str(round(t_set, 2)))
        if is_heating and record.secondi_caldaia < 1800:
            record.secondi_caldaia += 1800
        record.n_campionamenti = max(1, record.n_campionamenti + 1)
        record.fonte_file = "Home Assistant Live API"
        record.save()

    # Ricalcola bollette gas collegate
    try:
        from . import netatmo_service
        netatmo_service.sincronizza_bollette_gas_con_netatmo()
    except Exception as exc:
        logger.warning("Sincronizzazione gas Netatmo non riuscita: %s", exc, exc_info=True)

    return {
        "ok": True,
        "data": oggi.isoformat(),
        "created": created,
        "temp_interna": t_curr,
        "temp_setpoint": t_set,
        "hvac_action": action,
    }



def get_live_telemetry() -> dict:
    """
    Ritorna un istantanea telemetrica completa da Home Assistant:
    - Potenza totale assorbita istantanea (W)
    - Consumi totali oggi / settimana / mese (kWh)
    - Costo energia oggi e mese (€)
    - Prezzo marginale applicato (€/kWh)
    - Lettura e data contatore manuale archiviata
    - Proiezione bolletta stimata contatore
    """
    default_offline = {
        "online": False,
        "potenza_totale_w": 0.0,
        "consumi_oggi_kwh": 0.0,
        "consumi_settimana_kwh": 0.0,
        "consumi_mese_kwh": 0.0,
        "energia_mese_utility_kwh": 0.0,
        "costo_oggi_eur": 0.0,
        "costo_mese_eur": 0.0,
        "prezzo_kwh_ha": 0.0,
        "prezzo_marginale_kwh": 0.0,
        "quota_fissa_giornaliera": 0.0,
        "contatore_lettura": 0.0,
        "contatore_lettura_prec": 0.0,
        "contatore_data": "",
        "tariffa_aggiornata_il": "",
        "consumo_periodo_contatore": 0.0,
        "bolletta_stimata_contatore": 0.0,
    }
    if not is_configured():
        return default_offline

    resp = _request("GET", "/api/states")
    if resp is None:
        return default_offline

    try:
        stati_raw = resp.json()
        stati = {s.get("entity_id"): s for s in stati_raw if "entity_id" in s}
    except Exception:
        return default_offline

    def _val(eid, default=None, is_float=True):
        st = stati.get(eid, {}).get("state")
        if st is None or st in ("unavailable", "unknown"):
            return default
        if is_float:
            try:
                return float(st)
            except (ValueError, TypeError):
                return default
        return st

    return {
        "online": True,
        "potenza_totale_w": _val("sensor.potenza_totale", 0.0),
        "consumi_oggi_kwh": _val("sensor.consumi_totali_oggi", 0.0),
        "consumi_settimana_kwh": _val("sensor.consumi_totali_settimana", 0.0),
        "consumi_mese_kwh": _val("sensor.consumi_totali_mese", 0.0),
        "energia_mese_utility_kwh": _val("sensor.energia_mese", 0.0),
        "costo_oggi_eur": _val("sensor.costo_energia_oggi", 0.0),
        "costo_mese_eur": _val("sensor.costo_energia_mese", 0.0),
        "prezzo_kwh_ha": _val("input_number.prezzo_energia_kwh", 0.0),
        "prezzo_marginale_kwh": _val("sensor.prezzo_energia_marginale_kwh", 0.0),
        "quota_fissa_giornaliera": _val("input_number.quota_fissa_giornaliera", 0.0),
        "contatore_lettura": _val("input_number.contatore_lettura", 0.0),
        "contatore_lettura_prec": _val("input_number.contatore_lettura_precedente", 0.0),
        "contatore_data": _val("input_datetime.contatore_data", "", is_float=False),
        "tariffa_aggiornata_il": _val("input_datetime.tariffa_aggiornata_il", "", is_float=False),
        "consumo_periodo_contatore": _val("sensor.consumo_periodo_contatore", 0.0),
        "bolletta_stimata_contatore": _val("sensor.bolletta_stimata_contatore", 0.0),
    }


def scopri_entita_energia() -> list[dict]:
    """
    Interroga HA per scoprire tutti i sensori di energia/consumi disponibili.
    Utile per scoprire e selezionare le entità senza dover analizzare manualmente i log.
    """
    if not is_configured():
        return []
    resp = _request("GET", "/api/states")
    if resp is None:
        return []
    try:
        stati = resp.json()
    except Exception:
        return []

    sensori = []
    unita_valide = {"kwh", "wh", "mwh", "smc", "m³", "m3", "w", "kw", "€/kwh", "€/smc"}
    for item in stati:
        entity_id = item.get("entity_id", "")
        attrs = item.get("attributes", {})
        unit = (attrs.get("unit_of_measurement") or "").lower()
        dev_class = attrs.get("device_class")
        state_class = attrs.get("state_class")

        if (
            dev_class in ("energy", "power", "gas", "monetary")
            or unit in unita_valide
            or "energy" in entity_id
            or "consumo" in entity_id
            or "pun" in entity_id
            or "shelly" in entity_id
        ):
            sensori.append({
                "entity_id": entity_id,
                "name": attrs.get("friendly_name", entity_id),
                "state": item.get("state"),
                "unit": attrs.get("unit_of_measurement", ""),
                "device_class": dev_class,
                "state_class": state_class,
            })
    sensori.sort(key=lambda s: s["entity_id"])
    return sensori

