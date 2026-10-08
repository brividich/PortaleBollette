"""
Client REST per le API Cloud ufficiali Netatmo Connect (dev.netatmo.com).

Fornisce:
1. Gestione del ciclo di vita del token OAuth2:
   - Rinnovo automatico e trasparente dell'access token tramite refresh token
   - Salvataggio persistente in ConfigurazioneSistema (database locale)
2. Auto-discovery dell'infrastruttura termica domestica:
   - Identificazione automatica di home_id, device_id (relè caldaia NAPlug) e module_id (termostato NATherm1)
3. Telemetria live:
   - Temperatura ambiente attuale, temperatura di setpoint e stato fiamma caldaia (ON/OFF)
4. Download e sincronizzazione automatica delle misure storiche:
   - Download orario via /api/getmeasure con durata fiamma bruciatore (sum_boiler_on in secondi)
   - Aggregazione e salvataggio nei record giornalieri NetatmoRecordGiornaliero
   - Ricalcolo immediato delle metriche sulle bollette del gas archiviate
5. Test diagnostico live della connessione
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import requests
from django.utils import timezone

from .models import ConfigurazioneSistema, NetatmoRecordGiornaliero

logger = logging.getLogger("bollette.netatmo_api")

NETATMO_OAUTH_URL = "https://api.netatmo.com/oauth2/token"
NETATMO_HOMESDATA_URL = "https://api.netatmo.com/api/homesdata"
NETATMO_HOMESTATUS_URL = "https://api.netatmo.com/api/homestatus"
NETATMO_GETMEASURE_URL = "https://api.netatmo.com/api/getmeasure"


def is_configured() -> bool:
    """Verifica se le credenziali API Netatmo (client_id, secret, refresh_token) sono configurate."""
    cfg = ConfigurazioneSistema.get_config()
    return bool(cfg.netatmo_client_id and cfg.netatmo_client_secret and cfg.netatmo_refresh_token)


def ottieni_access_token(forza_rinnovo: bool = False) -> str | None:
    """
    Restituisce un access token valido.
    Se il token attuale è scaduto (o prossimo alla scadenza), richiede un rinnovo a Netatmo.
    """
    cfg = ConfigurazioneSistema.get_config()
    if not (cfg.netatmo_client_id and cfg.netatmo_client_secret and cfg.netatmo_refresh_token):
        return None

    # Se abbiamo un token valido e non è richiesta la forzatura, lo riusiamo
    if not forza_rinnovo and cfg.netatmo_access_token and cfg.netatmo_token_expires_at:
        # Buffer di 5 minuti prima della scadenza
        if cfg.netatmo_token_expires_at > timezone.now() + timedelta(minutes=5):
            return cfg.netatmo_access_token

    # Rinnovo token via OAuth2 Refresh Token
    logger.info("Rinnovo access token Netatmo via OAuth2 refresh token...")
    payload = {
        "grant_type": "refresh_token",
        "client_id": cfg.netatmo_client_id.strip(),
        "client_secret": cfg.netatmo_client_secret.strip(),
        "refresh_token": cfg.netatmo_refresh_token.strip(),
    }

    try:
        r = requests.post(NETATMO_OAUTH_URL, data=payload, timeout=10)
        if r.status_code == 200:
            data = r.json()
            access_token = data.get("access_token")
            new_refresh = data.get("refresh_token") or cfg.netatmo_refresh_token
            expires_in = int(data.get("expires_in", 10800))  # solitamente 3 ore

            cfg.netatmo_access_token = access_token
            cfg.netatmo_refresh_token = new_refresh
            cfg.netatmo_token_expires_at = timezone.now() + timedelta(seconds=expires_in)
            cfg.save(update_fields=["netatmo_access_token", "netatmo_refresh_token", "netatmo_token_expires_at"])
            logger.info("Access token Netatmo rinnovato con successo (scadenza tra %s secondi).", expires_in)
            return access_token
        else:
            logger.error("Errore rinnovo token Netatmo (HTTP %s): %s", r.status_code, r.text)
            return None
    except Exception as exc:
        logger.exception("Eccezione durante il rinnovo token Netatmo: %s", exc)
        return None


def scopri_dispositivi_netatmo() -> dict[str, Any]:
    """
    Interroga /api/homesdata per scoprire l'abitazione e gli ID del termostato (relè e sonda).
    Salva automaticamente gli identificativi in ConfigurazioneSistema.
    """
    token = ottieni_access_token()
    if not token:
        return {"ok": False, "errore": "Impossibile ottenere l'Access Token Netatmo. Verifica le credenziali."}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(NETATMO_HOMESDATA_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "errore": f"Netatmo homesdata ha risposto con codice HTTP {r.status_code}: {r.text[:120]}"}

        data = r.json()
        homes = data.get("body", {}).get("homes", [])
        if not homes:
            return {"ok": False, "errore": "Nessuna abitazione trovata nell'account Netatmo."}

        home = homes[0]
        home_id = home.get("id")
        home_name = home.get("name", "Casa")

        # Cerca relè e termostato
        # In Netatmo Energy: il relè è solitamente tipo 'NAPlug' e il modulo termostato è 'NATherm1' o 'NRV'
        modules = home.get("modules", [])
        device_id = None  # MAC Relè
        module_id = None  # MAC Termostato
        termostato_name = "Termostato Netatmo"

        # Cerca il modulo termostato o testa i moduli presenti
        for m in modules:
            m_type = m.get("type", "")
            if m_type == "NATherm1" or "therm" in m_type.lower():
                module_id = m.get("id")
                device_id = m.get("bridge")
                termostato_name = m.get("name", termostato_name)
                break

        # Fallback se non trovato bridge esplicito
        if not device_id:
            for m in modules:
                if m.get("type") in ("NAPlug", "relay", "bridge"):
                    device_id = m.get("id")
                    break

        if not module_id and modules:
            module_id = modules[0].get("id")
            termostato_name = modules[0].get("name", "Modulo 1")

        cfg = ConfigurazioneSistema.get_config()
        cfg.netatmo_home_id = home_id or ""
        if device_id:
            cfg.netatmo_device_id = device_id
        if module_id:
            cfg.netatmo_module_id = module_id
        cfg.save(update_fields=["netatmo_home_id", "netatmo_device_id", "netatmo_module_id"])

        return {
            "ok": True,
            "home_id": home_id,
            "home_name": home_name,
            "device_id": cfg.netatmo_device_id,
            "module_id": cfg.netatmo_module_id,
            "termostato_nome": termostato_name,
            "tot_moduli": len(modules),
        }
    except Exception as exc:
        logger.exception("Errore scoperta dispositivi Netatmo: %s", exc)
        return {"ok": False, "errore": str(exc)}


def get_live_status() -> dict[str, Any]:
    """
    Interroga /api/homestatus per estrarre lo stato in tempo reale del riscaldamento:
    temperatura attuale, setpoint e se la caldaia è in questo momento accesa (heating/boiler ON).
    """
    if not is_configured():
        return {"online": False, "motivo": "Non configurato"}

    cfg = ConfigurazioneSistema.get_config()
    if not cfg.netatmo_home_id:
        scopri = scopri_dispositivi_netatmo()
        if not scopri.get("ok"):
            return {"online": False, "errore": scopri.get("errore")}
        cfg = ConfigurazioneSistema.get_config()

    token = ottieni_access_token()
    if not token:
        return {"online": False, "motivo": "Token non valido o scaduto"}

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{NETATMO_HOMESTATUS_URL}?home_id={cfg.netatmo_home_id}"

    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return {"online": False, "errore": f"HTTP {r.status_code}"}

        data = r.json()
        home = data.get("body", {}).get("home", {})
        rooms = home.get("rooms", [])
        modules = home.get("modules", [])

        room_temp = None
        setpoint = None
        heating_active = False

        if rooms:
            main_room = rooms[0]
            room_temp = main_room.get("therm_measured_temperature")
            setpoint = main_room.get("therm_setpoint_temperature")
            # In Netatmo, heating_power_request indica la richiesta di riscaldamento (0..100)
            power_req = main_room.get("heating_power_request", 0)
            heating_active = bool(power_req and power_req > 0)

        # Controlla anche lo stato relè nei moduli
        battery_state = None
        rf_strength = None
        for m in modules:
            if m.get("boiler_status") is not None:
                heating_active = bool(m.get("boiler_status"))
            if m.get("battery_percent") is not None:
                battery_state = m.get("battery_percent")
            if m.get("rf_strength") is not None:
                rf_strength = m.get("rf_strength")

        return {
            "online": True,
            "room_temp": round(float(room_temp), 1) if room_temp is not None else None,
            "setpoint": round(float(setpoint), 1) if setpoint is not None else None,
            "boiler_active": heating_active,
            "battery_percent": battery_state,
            "rf_strength": rf_strength,
            "aggiornato_il": timezone.now().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        logger.debug("Lettura homestatus Netatmo fallita: %s", exc)
        return {"online": False, "errore": str(exc)}


def scarica_e_sincronizza_misure(
    giorni: int = 90,
    dt_inizio: date | datetime | None = None,
    dt_fine: date | datetime | None = None,
) -> dict[str, Any]:
    """
    Scarica le misure storiche dal Cloud Netatmo (/api/getmeasure) e sincronizza i record giornalieri.
    Aggiorna automaticamente le bollette del gas archiviate.
    """
    token = ottieni_access_token()
    if not token:
        return {"ok": False, "errore": "Access Token Netatmo non disponibile. Verifica le credenziali."}

    cfg = ConfigurazioneSistema.get_config()
    if not cfg.netatmo_device_id or not cfg.netatmo_module_id:
        scopri = scopri_dispositivi_netatmo()
        if not scopri.get("ok"):
            return scopri
        cfg = ConfigurazioneSistema.get_config()

    # Determinazione dell'intervallo temporale
    if dt_fine is None:
        dt_fine = timezone.localdate()
    if dt_inizio is None:
        dt_inizio = dt_fine - timedelta(days=giorni)

    ts_inizio = int(datetime.combine(dt_inizio, datetime.min.time()).timestamp())
    ts_fine = int(datetime.combine(dt_fine, datetime.max.time()).timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "device_id": cfg.netatmo_device_id,
        "module_id": cfg.netatmo_module_id,
        "scale": "1hour",
        "type": "temperature,sp_temperature,sum_boiler_on",
        "date_begin": ts_inizio,
        "date_end": ts_fine,
        "optimize": "false",
    }

    try:
        r = requests.get(NETATMO_GETMEASURE_URL, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            # Se 1hour non è disponibile per il modulo, prova con la scala a 30min
            if "scale" in r.text.lower():
                params["scale"] = "30min"
                r = requests.get(NETATMO_GETMEASURE_URL, headers=headers, params=params, timeout=20)

        if r.status_code != 200:
            return {"ok": False, "errore": f"Errore Netatmo getmeasure (HTTP {r.status_code}): {r.text[:150]}"}

        data = r.json()
        body = data.get("body", {})

        # Parsing della risposta Netatmo getmeasure
        # Se optimize=false, body è un dizionario con chiavi timestamp: {"1705312800": [temp, sp, boiler_s], ...}
        # oppure una lista di intervalli: [{"beg_time": ..., "step_time": 3600, "value": [...]}]
        campioni_per_giorno: dict[date, dict[str, Any]] = {}

        if isinstance(body, dict):
            for ts_str, val in body.items():
                try:
                    ts = int(ts_str)
                    giorno = datetime.fromtimestamp(ts).date()
                    if giorno not in campioni_per_giorno:
                        campioni_per_giorno[giorno] = {"secondi": 0.0, "temps": [], "setpoints": [], "count": 0}

                    if isinstance(val, list) and len(val) >= 1:
                        # [temperature, sp_temperature, sum_boiler_on]
                        if len(val) > 0 and val[0] is not None:
                            campioni_per_giorno[giorno]["temps"].append(float(val[0]))
                        if len(val) > 1 and val[1] is not None:
                            campioni_per_giorno[giorno]["setpoints"].append(float(val[1]))
                        if len(val) > 2 and val[2] is not None:
                            campioni_per_giorno[giorno]["secondi"] += float(val[2])
                        campioni_per_giorno[giorno]["count"] += 1
                except (ValueError, TypeError):
                    continue

        elif isinstance(body, list):
            for serie in body:
                beg_time = serie.get("beg_time")
                step_time = serie.get("step_time", 3600)
                values = serie.get("value", [])
                for i, val in enumerate(values):
                    ts = beg_time + (i * step_time)
                    giorno = datetime.fromtimestamp(ts).date()
                    if giorno not in campioni_per_giorno:
                        campioni_per_giorno[giorno] = {"secondi": 0.0, "temps": [], "setpoints": [], "count": 0}

                    if isinstance(val, list):
                        if len(val) > 0 and val[0] is not None:
                            campioni_per_giorno[giorno]["temps"].append(float(val[0]))
                        if len(val) > 1 and val[1] is not None:
                            campioni_per_giorno[giorno]["setpoints"].append(float(val[1]))
                        if len(val) > 2 and val[2] is not None:
                            campioni_per_giorno[giorno]["secondi"] += float(val[2])
                        campioni_per_giorno[giorno]["count"] += 1

        if not campioni_per_giorno:
            return {"ok": False, "errore": "Nessuna misura restituita da Netatmo per il periodo richiesto."}

        creati = 0
        aggiornati = 0

        for giorno, d in campioni_per_giorno.items():
            sec_tot = min(86400, int(round(d["secondi"])))
            t_media = (
                Decimal(str(round(sum(d["temps"]) / len(d["temps"]), 2)))
                if d["temps"] else None
            )
            sp_media = (
                Decimal(str(round(sum(d["setpoints"]) / len(d["setpoints"]), 2)))
                if d["setpoints"] else None
            )

            _, is_new = NetatmoRecordGiornaliero.objects.update_or_create(
                data=giorno,
                defaults={
                    "secondi_caldaia": sec_tot,
                    "temp_interna_media": t_media,
                    "temp_setpoint_media": sp_media,
                    "n_campionamenti": d["count"],
                    "fonte_file": "Netatmo Cloud REST API",
                },
            )
            if is_new:
                creati += 1
            else:
                aggiornati += 1

        # Sincronizza le bollette del gas
        from . import netatmo_service
        bollette_aggiornate = netatmo_service.sincronizza_bollette_gas_con_netatmo()

        return {
            "ok": True,
            "giorni_ricevuti": len(campioni_per_giorno),
            "creati": creati,
            "aggiornati": aggiornati,
            "bollette_gas_aggiornate": bollette_aggiornate,
            "inizio": min(campioni_per_giorno.keys()).strftime("%d/%m/%Y"),
            "fine": max(campioni_per_giorno.keys()).strftime("%d/%m/%Y"),
        }
    except Exception as exc:
        logger.exception("Errore chiamata getmeasure Netatmo: %s", exc)
        return {"ok": False, "errore": str(exc)}


def test_connessione() -> dict[str, Any]:
    """Test diagnostico per verificare se le credenziali API Netatmo sono corrette ed operative."""
    t0 = time.time()
    token = ottieni_access_token(forza_rinnovo=True)
    if not token:
        return {
            "ok": False,
            "messaggio": "Autenticazione fallita: Client ID, Client Secret o Refresh Token non validi.",
        }

    dispositivi = scopri_dispositivi_netatmo()
    latency_ms = int((time.time() - t0) * 1000)

    if dispositivi.get("ok"):
        return {
            "ok": True,
            "messaggio": (
                f"Connessione a Netatmo Cloud riuscita ({latency_ms} ms)! "
                f"Abitazione: '{dispositivi['home_name']}' · Termostato: '{dispositivi['termostato_nome']}' "
                f"(Relè MAC: {dispositivi['device_id'] or 'rilevato'})."
            ),
            "info": dispositivi,
            "latency_ms": latency_ms,
        }
    return {
        "ok": False,
        "messaggio": f"Token valido ma errore nella lettura dei dispositivi: {dispositivi.get('errore')}",
    }
