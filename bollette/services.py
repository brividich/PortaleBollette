"""
Logica di dominio del Portale Bollette:
- Calcolo prezzi marginali Luce & Gas
- Normalizzazione climatica via Open-Meteo (Gradi Giorno HDD/CDD)
- Benchmark indici energetici (PUN / PSV)
- Bridge fail-safe verso Home Assistant (scrittura prezzi e validazione consumi)
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

from . import ha_client, meteo_client, pun_client
from .models import BollettaElettrica, BollettaGas, HaSyncLog

logger = logging.getLogger("bollette.services")

SEI_DECIMALI = Decimal("0.000001")
DUE_DECIMALI = Decimal("0.01")
QUATTRO_DECIMALI = Decimal("0.0001")


def _q6(valore: Decimal) -> Decimal:
    """Arrotonda a 6 decimali (half-up)."""
    return Decimal(valore).quantize(SEI_DECIMALI, rounding=ROUND_HALF_UP)


def _get_cfg():
    try:
        from .models import ConfigurazioneSistema
        return ConfigurazioneSistema.get_config()
    except Exception:
        return None


def _get_mode() -> str:
    cfg = _get_cfg()
    return (cfg.prezzo_ha_mode if cfg else None) or getattr(settings, "PREZZO_HA_MODE", "media_mobile_12m")


def _get_sync_auto() -> bool:
    cfg = _get_cfg()
    return cfg.ha_sync_auto if cfg else getattr(settings, "HA_SYNC_AUTO", False)


def _get_entity_luce() -> str:
    cfg = _get_cfg()
    return (cfg.ha_entity_kwh_consumo if cfg else "") or getattr(settings, "HA_ENTITY_KWH_CONSUMO", "")


def _get_entity_gas_consumo() -> str:
    cfg = _get_cfg()
    return (cfg.ha_entity_smc_consumo if cfg else "") or getattr(settings, "HA_ENTITY_SMC_CONSUMO", "")


def _get_entity_gas_prezzo() -> str:
    cfg = _get_cfg()
    return (cfg.ha_entity_prezzo_gas if cfg else "") or getattr(ha_client, "ENTITY_PREZZO_GAS", "input_number.prezzo_gas_smc")



# ===========================================================================
# LUCE: Calcolo Prezzo Marginale & Arricchimento
# ===========================================================================
def calcola_prezzo_marginale(bolletta: BollettaElettrica) -> Decimal:
    """
    Ritorna il €/kWh marginale MEDIO del mese applicando lo scalino accisa:
        costo = base * min(kwh, soglia) + (base + accisa) * max(0, kwh - soglia)
        prezzo_medio = costo / kwh
    """
    kwh = int(bolletta.kwh_fatturati or 0)
    base = Decimal(bolletta.prezzo_marginale_base or 0)
    accisa = Decimal(bolletta.accisa_marginale or 0)
    soglia = int(bolletta.soglia_accisa_kwh or 0)

    if kwh <= 0:
        return _q6(base)

    sotto = min(kwh, soglia)
    sopra = max(0, kwh - soglia)
    costo = base * sotto + (base + accisa) * sopra
    return _q6(costo / Decimal(kwh))


def aggiorna_campi_calcolati(bolletta: BollettaElettrica, *, salva: bool = True) -> BollettaElettrica:
    """
    Ricalcola prezzo marginale, recupera dati meteo da Open-Meteo e benchmark PUN.
    """
    soglia = int(bolletta.soglia_accisa_kwh or 0)
    bolletta.sopra_soglia_accisa = int(bolletta.kwh_fatturati or 0) > soglia
    bolletta.prezzo_marginale_medio = calcola_prezzo_marginale(bolletta)

    # 1. Integrazione Open-Meteo: Gradi Giorno e Temperatura media
    if bolletta.periodo_inizio and bolletta.periodo_fine:
        meteo = meteo_client.calcola_gradi_giorno(bolletta.periodo_inizio, bolletta.periodo_fine)
        if meteo:
            bolletta.gradi_giorno = meteo["hdd"]
            bolletta.temperatura_media = meteo["t_media"]
            if meteo["hdd"] > 0 and int(bolletta.kwh_fatturati or 0) > 0:
                kwh_norm = Decimal(bolletta.kwh_fatturati) / Decimal(str(meteo["hdd"]))
                bolletta.kwh_per_gradi_giorno = kwh_norm.quantize(QUATTRO_DECIMALI, rounding=ROUND_HALF_UP)

    # 2. Benchmark PUN e calcolo Spread
    if bolletta.periodo_inizio and bolletta.periodo_fine:
        pun_medio = pun_client.get_pun_periodo(bolletta.periodo_inizio, bolletta.periodo_fine)
        if pun_medio:
            bolletta.pun_medio_periodo = pun_medio
            bolletta.spread_pun = pun_client.calcola_spread(bolletta.prezzo_marginale_medio, pun_medio)

    if salva:
        bolletta.save()
    return bolletta


def _componenti_da_bolletta(b: BollettaElettrica) -> dict:
    """Estrae i 3 componenti + singolo valore da una singola bolletta elettrica."""
    return {
        "prezzo_kwh_base": _q6(Decimal(b.prezzo_marginale_base or 0)),
        "accisa_marginale_kwh": _q6(Decimal(b.accisa_marginale or 0)),
        "soglia_accisa_kwh": int(b.soglia_accisa_kwh or 150),
        "prezzo_energia_kwh": calcola_prezzo_marginale(b),
        "n_bollette": 1,
        "kwh_totali": int(b.kwh_fatturati or 0),
    }


def calcola_prezzo_ha(mode: str | None = None) -> dict | None:
    """Ritorna il dict dei valori luce da pubblicare in HA secondo PREZZO_HA_MODE."""
    mode = mode or _get_mode()


    ultima = BollettaElettrica.objects.order_by("-periodo_fine").first()
    if ultima is None:
        logger.warning("Nessuna bolletta elettrica in archivio.")
        return None

    if mode == "ultima_bolletta":
        dati = _componenti_da_bolletta(ultima)
        dati["mode"] = "ultima_bolletta"
        return dati

    # media_mobile_12m (default pesata sui kWh)
    limite = timezone.localdate() - timedelta(days=365)
    qs = BollettaElettrica.objects.filter(periodo_fine__gte=limite)
    bollette = [b for b in qs if int(b.kwh_fatturati or 0) > 0]

    if not bollette:
        dati = _componenti_da_bolletta(ultima)
        dati["mode"] = "media_mobile_12m (fallback: ultima_bolletta)"
        return dati

    kwh_tot = sum(int(b.kwh_fatturati) for b in bollette)
    base_pesata = sum(Decimal(b.prezzo_marginale_base or 0) * int(b.kwh_fatturati) for b in bollette) / kwh_tot
    accisa_pesata = sum(Decimal(b.accisa_marginale or 0) * int(b.kwh_fatturati) for b in bollette) / kwh_tot
    medio_pesato = sum(calcola_prezzo_marginale(b) * int(b.kwh_fatturati) for b in bollette) / kwh_tot

    return {
        "mode": "media_mobile_12m",
        "prezzo_kwh_base": _q6(base_pesata),
        "accisa_marginale_kwh": _q6(accisa_pesata),
        "soglia_accisa_kwh": int(ultima.soglia_accisa_kwh or 150),
        "prezzo_energia_kwh": _q6(medio_pesato),
        "n_bollette": len(bollette),
        "kwh_totali": kwh_tot,
    }


def _pubblica_componenti(componenti: dict, modalita: str, bolletta=None) -> dict:
    cfg = _get_cfg()
    entity_prezzo_singolo = (cfg.ha_entity_prezzo_singolo if cfg else "") or ha_client.ENTITY_PREZZO_SINGOLO

    # Calcolo quota fissa giornaliera (€/giorno)
    quota_mensile = Decimal("10.00")
    if bolletta and bolletta.quota_fissa_mensile:
        quota_mensile = bolletta.quota_fissa_mensile
    else:
        ultima = BollettaElettrica.objects.order_by("-periodo_fine").first()
        if ultima and ultima.quota_fissa_mensile:
            quota_mensile = ultima.quota_fissa_mensile
    quota_die = round(float(quota_mensile) / 30.0, 3)

    scritture = [
        ha_client.set_input_number(
            ha_client.ENTITY_PREZZO_BASE, componenti["prezzo_kwh_base"],
            modalita=modalita, bolletta=bolletta),
        ha_client.set_input_number(
            ha_client.ENTITY_ACCISA_MARGINALE, componenti["accisa_marginale_kwh"],
            modalita=modalita, bolletta=bolletta),
        ha_client.set_input_number(
            ha_client.ENTITY_SOGLIA_ACCISA, componenti["soglia_accisa_kwh"],
            modalita=modalita, bolletta=bolletta),
        ha_client.set_input_number(
            entity_prezzo_singolo, componenti["prezzo_energia_kwh"],
            modalita=modalita, bolletta=bolletta),
        ha_client.set_input_number(
            ha_client.ENTITY_QUOTA_FISSA, quota_die,
            modalita=modalita, bolletta=bolletta),
    ]
    ok = all(s.ok for s in scritture)

    # Aggiorna anche data tariffa e stato del portale in Home Assistant
    ha_client.set_input_datetime(ha_client.ENTITY_DATA_TARIFFA, timezone.localdate())
    ha_client.set_portal_state(
        ha_client.ENTITY_PORTALE_STATO,
        "Sincronizzato" if ok else "Errore Sync",
        {
            "friendly_name": "Portale Bollette",
            "icon": "mdi:file-document-check" if ok else "mdi:alert-circle",
            "prezzo_energia_kwh": float(componenti["prezzo_energia_kwh"]),
            "prezzo_kwh_base": float(componenti["prezzo_kwh_base"]),
            "accisa_marginale_kwh": float(componenti["accisa_marginale_kwh"]),
            "quota_fissa_giornaliera": quota_die,
            "modalita_calcolo": modalita,
            "ultima_sincronizzazione": timezone.now().strftime("%d/%m/%Y %H:%M"),
            "bolletta_riferimento": str(bolletta) if bolletta else "Media archivio",
        }
    )

    if bolletta is not None:
        bolletta.prezzo_pubblicato_ha = Decimal(str(componenti["prezzo_energia_kwh"]))
        bolletta.ha_sync_at = timezone.now()
        bolletta.ha_sync_ok = ok
        bolletta.save(update_fields=["prezzo_pubblicato_ha", "ha_sync_at", "ha_sync_ok"])

    return {
        "ok": ok,
        "modalita": modalita,
        "componenti": componenti,
        "quota_fissa_giornaliera": quota_die,
        "scritture": scritture,
    }


def pubblica_prezzo_su_ha(*, bolletta: BollettaElettrica | None = None, mode: str | None = None) -> dict:
    """Pubblica il prezzo elettrico in HA. Fail-safe."""
    try:
        if bolletta is not None:
            componenti = _componenti_da_bolletta(bolletta)
            return _pubblica_componenti(componenti, HaSyncLog.Modalita.MANUALE, bolletta)

        componenti = calcola_prezzo_ha(mode)
        if componenti is None:
            return {"ok": False, "errore": "Nessuna bolletta elettrica in archivio."}
        modalita = (mode or settings.PREZZO_HA_MODE)
        if modalita not in HaSyncLog.Modalita.values:
            modalita = HaSyncLog.Modalita.MEDIA_MOBILE_12M
        return _pubblica_componenti(componenti, modalita, None)
    except Exception as exc:
        logger.exception("Errore nella pubblicazione luce su HA: %s", exc)
        return {"ok": False, "errore": f"{type(exc).__name__}: {exc}"}


def sincronizza_dopo_ingest(bolletta: BollettaElettrica) -> dict | None:
    if not _get_sync_auto():
        return None
    return pubblica_prezzo_su_ha(bolletta=bolletta)


def valida_vs_ha(bolletta: BollettaElettrica) -> dict:
    """Valida kWh fatturati vs consumi reali registrati in HA."""
    entity = _get_entity_luce()

    esito = {
        "ok": False,
        "entity": entity,
        "kwh_misurati": None,
        "kwh_fatturati": int(bolletta.kwh_fatturati or 0),
        "scostamento_pct": None,
        "messaggio": "",
    }

    if not entity:
        esito["messaggio"] = "Sensore consumi luce non configurato. Vai in Configurazioni e seleziona il tuo sensore di consumo elettrico (kWh)."
        return esito

    try:
        tz = timezone.get_current_timezone()
        inizio = timezone.make_aware(datetime.combine(bolletta.periodo_inizio, time.min), tz)
        fine = timezone.make_aware(datetime.combine(bolletta.periodo_fine, time.max), tz)
        dati = ha_client.get_energia_consumata(entity, inizio, fine)
    except Exception as exc:
        logger.exception("Errore validazione vs HA: %s", exc)
        esito["messaggio"] = f"Errore lettura HA: {type(exc).__name__}: {exc}"
        return esito

    if dati is None:
        esito["messaggio"] = f"Nessun dato storico trovato in Home Assistant per il sensore '{entity}' nel periodo {bolletta.periodo_inizio} -> {bolletta.periodo_fine}."
        return esito

    kwh_mis = round(dati["delta"], 2)
    esito["kwh_misurati"] = kwh_mis
    esito["ok"] = True
    fatt = esito["kwh_fatturati"]
    if fatt > 0:
        esito["scostamento_pct"] = round((kwh_mis - fatt) / fatt * 100, 2)
    nota_reset = " (possibile reset contatore nel periodo)" if dati.get("reset") else ""
    esito["messaggio"] = f"Misurati {kwh_mis} kWh vs {fatt} fatturati (scostamento {esito['scostamento_pct']}%).{nota_reset}"
    return esito


# ===========================================================================
# GAS: Calcolo Prezzo Marginale & Arricchimento
# ===========================================================================
def calcola_prezzo_marginale_gas(bolletta: BollettaGas) -> Decimal:
    """
    Ritorna il costo variabile marginale €/Smc:
    Prezzo = (Materia Prima Gas + Accisa + Addizionale Regionale)
    """
    materia = Decimal(bolletta.quota_materia_prima_smc or 0)
    accisa = Decimal(bolletta.accisa_smc or 0)
    return _q6(materia + accisa)


def aggiorna_campi_calcolati_gas(bolletta: BollettaGas, *, salva: bool = True) -> BollettaGas:
    """Ricalcola il prezzo marginale del gas e arricchisce con meteo Open-Meteo e benchmark PSV."""
    bolletta.prezzo_marginale_medio_smc = calcola_prezzo_marginale_gas(bolletta)

    if bolletta.periodo_inizio and bolletta.periodo_fine:
        # Metriche meteo Open-Meteo
        meteo = meteo_client.calcola_gradi_giorno(bolletta.periodo_inizio, bolletta.periodo_fine)
        if meteo:
            bolletta.gradi_giorno = meteo["hdd"]
            bolletta.temperatura_media = meteo["t_media"]
            smc = Decimal(str(bolletta.smc_fatturati or 0))
            if meteo["hdd"] > 0 and smc > 0:
                bolletta.smc_per_gradi_giorno = (smc / Decimal(str(meteo["hdd"]))).quantize(QUATTRO_DECIMALI, rounding=ROUND_HALF_UP)

        # Benchmark PSV
        psv_medio = pun_client.get_psv_periodo(bolletta.periodo_inizio, bolletta.periodo_fine)
        if psv_medio:
            bolletta.psv_medio_periodo = psv_medio
            bolletta.spread_psv = pun_client.calcola_spread(bolletta.prezzo_marginale_medio_smc, psv_medio)

    if salva:
        bolletta.save()
        try:
            from . import netatmo_service
            netatmo_service.sincronizza_bollette_gas_con_netatmo(bolletta.pk)
            bolletta.refresh_from_db()
        except Exception:
            pass
    return bolletta


def calcola_prezzo_gas_ha(mode: str | None = None) -> dict | None:
    """Calcola il prezzo €/Smc da pubblicare in HA (ultima bolletta o media pesata)."""
    mode = mode or _get_mode()
    ultima = BollettaGas.objects.order_by("-periodo_fine").first()
    if ultima is None:
        return None

    if mode == "ultima_bolletta":
        return {
            "mode": "ultima_bolletta",
            "prezzo_gas_smc": calcola_prezzo_marginale_gas(ultima),
            "smc_totali": float(ultima.smc_fatturati or 0),
            "n_bollette": 1,
        }

    # Media pesata 12m
    limite = timezone.localdate() - timedelta(days=365)
    qs = BollettaGas.objects.filter(periodo_fine__gte=limite)
    bollette = [b for b in qs if Decimal(str(b.smc_fatturati or 0)) > 0]

    if not bollette:
        return {
            "mode": "media_mobile_12m (fallback)",
            "prezzo_gas_smc": calcola_prezzo_marginale_gas(ultima),
            "smc_totali": float(ultima.smc_fatturati or 0),
            "n_bollette": 1,
        }

    smc_tot = sum(Decimal(str(b.smc_fatturati)) for b in bollette)
    medio_pesato = sum(calcola_prezzo_marginale_gas(b) * Decimal(str(b.smc_fatturati)) for b in bollette) / smc_tot

    return {
        "mode": "media_mobile_12m",
        "prezzo_gas_smc": _q6(medio_pesato),
        "smc_totali": float(smc_tot),
        "n_bollette": len(bollette),
    }


def pubblica_prezzo_gas_su_ha(*, bolletta: BollettaGas | None = None, mode: str | None = None) -> dict:
    """Pubblica il prezzo del gas in HA su input_number.prezzo_gas_smc."""
    try:
        valore = None
        modalita = HaSyncLog.Modalita.MANUALE
        if bolletta is not None:
            valore = calcola_prezzo_marginale_gas(bolletta)
        else:
            dati = calcola_prezzo_gas_ha(mode)
            if dati is None:
                return {"ok": False, "errore": "Nessuna bolletta gas in archivio."}
            valore = dati["prezzo_gas_smc"]
            modalita = HaSyncLog.Modalita.MEDIA_MOBILE_12M

        entity = _get_entity_gas_prezzo()
        esito = ha_client.set_input_number(entity, valore, modalita=modalita, bolletta_gas=bolletta)


        if bolletta is not None:
            bolletta.prezzo_pubblicato_ha = Decimal(str(valore))
            bolletta.ha_sync_at = timezone.now()
            bolletta.ha_sync_ok = esito.ok
            bolletta.save(update_fields=["prezzo_pubblicato_ha", "ha_sync_at", "ha_sync_ok"])

        return {
            "ok": esito.ok,
            "entity": entity,
            "valore": valore,
            "dettaglio": esito.dettaglio,
        }
    except Exception as exc:
        logger.exception("Errore nella pubblicazione gas su HA: %s", exc)
        return {"ok": False, "errore": f"{type(exc).__name__}: {exc}"}


def esegui_sync_bidirezionale() -> dict:
    """
    Esegue una sincronizzazione bidirezionale completa tra Portale Bollette e Home Assistant:
    1. Direzione Portale -> HA:
       - Calcola e pubblica i prezzi marginali correnti (Luce & Gas).
       - Invia la quota fissa giornaliera (€/giorno) e aggiorna la data della tariffa.
       - Pubblica lo stato del portale e indicatori chiave su sensor.portale_bollette_stato.
    2. Direzione HA -> Portale:
       - Esegue polling e audit di congruenza su tutte le bollette archiviate via statistiche long-term HA.
       - Raccoglie la telemetria live istantanea (potenza W, consumi oggi/settimana/mese, contatore).
       - Estrae la ripartizione dei consumi per singolo carico/elettrodomestico del mese.
    """
    from . import ha_comparison

    # 1. Portale -> HA (scrittura tariffe & stato)
    esito_luce = pubblica_prezzo_su_ha()
    esito_gas = pubblica_prezzo_gas_su_ha()

    # 2. HA -> Portale (lettura storico & audit)
    esito_polling = ha_comparison.esegui_polling_confronto(tipo="tutti")

    # 3. Telemetria live & ripartizione consumi carichi
    telemetria = ha_client.get_live_telemetry()
    breakdown = ha_client.get_device_breakdown()

    ok = bool(esito_luce.get("ok"))
    return {
        "ok": ok,
        "esito_luce": esito_luce,
        "esito_gas": esito_gas,
        "polling": esito_polling,
        "telemetria": telemetria,
        "breakdown": breakdown,
        "timestamp": timezone.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
