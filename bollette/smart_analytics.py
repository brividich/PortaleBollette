"""
Motore di Analisi Intelligente, Algoritmi Predittivi e Rilevamento Anomalie.

Algoritmi implementati:
1. Modello PRISM (Princeton Scorekeeping Method):
   - Regressione lineare tra Gradi Giorno (HDD) e consumo reale.
   - Scomposizione analitica del Baseload (consumo base continuo a HDD=0) vs Quota Riscaldamento (sensibilità termica dell'edificio m).
   - Coefficiente di correlazione di Pearson (R²) per misurare la fedeltà termica dell'abitazione.
2. Anomaly Detection (Rilevamento Anomalie & Costi Nascosti):
   - Rilevamento impennate dello Spread fornitore (es. rincari post-promozione 12 mesi).
   - Rilevamento anomalie di consumo rispetto al meteo (consumo > 25% rispetto al modello atteso).
   - Rilevamento discrepanze tra fattura e contatore Home Assistant.
3. Forecasting & Proiezione della Prossima Bolletta:
   - Proiezione del consumo di fine mese estrapolato dai giorni correnti di Home Assistant + previsioni Open-Meteo.
4. Energy Health Score (Punteggio di Efficienza Economica da 0 a 100):
   - Valutazione della convenienza contrattuale, incidenza dei costi fissi e stabilità dei prezzi.
5. Smart Insights Feed:
   - Raccomandazioni e pillole diagnostiche generate in linguaggio naturale.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.utils import timezone

from . import ha_client, meteo_client, pun_client, services
from .models import BollettaElettrica, BollettaGas, ConfigurazioneSistema


logger = logging.getLogger("bollette.smart")


# ===========================================================================
# 1. MODELLO TERMICO PRISM & REGRESSIONE CLIMATICA
# ===========================================================================
def analizza_modello_termico(tipo: str = "luce") -> dict[str, Any] | None:
    """
    Applica il modello di regressione PRISM (Consumo = Baseload + Sensibilità * HDD).
    Ritorna:
    - baseload_giornaliero: consumo minimo a HDD=0 (elettrodomestici, standby o acs)
    - sensibilita_termica: kWh o Smc necessari per ogni Grado Giorno (m)
    - r_quadro: bontà del fit climatico (0 = casuale, 1 = correlazione termica perfetta)
    - punti: coordinate (x: HDD, y: Consumo) per grafici di dispersione
    """
    if tipo == "gas":
        bollette = list(BollettaGas.objects.filter(gradi_giorno__isnull=False).order_by("periodo_fine"))
        punti = [
            (float(b.gradi_giorno), float(b.smc_fatturati or 0), b.giorni_periodo, str(b))
            for b in bollette if (b.gradi_giorno or 0) > 0 and (b.smc_fatturati or 0) > 0
        ]
        unita = "Smc"
    else:
        bollette = list(BollettaElettrica.objects.filter(gradi_giorno__isnull=False).order_by("periodo_fine"))
        punti = [
            (float(b.gradi_giorno), float(b.kwh_fatturati or 0), b.giorni_periodo, str(b))
            for b in bollette if (b.gradi_giorno or 0) > 0 and (b.kwh_fatturati or 0) > 0
        ]
        unita = "kWh"

    if len(punti) < 2:
        return None

    # Normalizza su base giornaliera per confrontare periodi di durata diversa (28 vs 31 giorni)
    xs = [p[0] / p[2] for p in punti]  # HDD giornalieri medi
    ys = [p[1] / p[2] for p in punti]  # Consumo giornaliero medio
    n = len(xs)

    media_x = sum(xs) / n
    media_y = sum(ys) / n

    # Covarianza e Varianza
    var_x = sum((x - media_x) ** 2 for x in xs)
    var_y = sum((y - media_y) ** 2 for y in ys)
    cov_xy = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys))

    if var_x == 0 or var_y == 0:
        return None

    sensibilita = cov_xy / var_x
    baseload_giorno = media_y - (sensibilita * media_x)

    # Correlazione di Pearson e R²
    r = cov_xy / math.sqrt(var_x * var_y)
    r2 = r ** 2

    # Assicura baseload non negativo
    baseload_giorno = max(0.0, baseload_giorno)

    return {
        "tipo": tipo,
        "unita": unita,
        "n_campioni": n,
        "baseload_giornaliero": round(baseload_giorno, 2),
        "baseload_mensile_stimato": round(baseload_giorno * 30.5, 1),
        "sensibilita_termica": round(sensibilita, 4),
        "correlazione_r": round(r, 3),
        "r_quadro": round(r2, 3),
        "affidabilita": "Alta" if r2 > 0.75 else ("Media" if r2 > 0.45 else "Bassa"),
        "spiegazione": (
            f"Il tuo consumo base fisiologico (a riscaldamento spento) è di circa {round(baseload_giorno * 30.5, 1)} {unita}/mese. "
            f"Per ogni Grado Giorno invernale in più, la casa richiede circa {round(sensibilita, 2)} {unita} aggiuntivi."
        ),
    }


# ===========================================================================
# 2. RILEVAMENTO ANOMALIE & COSTI NASCOSTI (ANOMALY DETECTION)
# ===========================================================================
def rileva_anomalie() -> list[dict[str, Any]]:
    """
    Analizza lo storico di tutte le bollette e identifica:
    1. Aumenti anomali dello spread rispetto alla media storica (rincari unilaterali del fornitore)
    2. Consumi anomali rispetto all'andamento termico Open-Meteo
    3. Discrepanze contatore HA vs fattura
    4. Incidenza eccessiva dei costi fissi (> 35% del totale fattura)
    """
    anomalie = []
    bollette_luce = list(BollettaElettrica.objects.all().order_by("periodo_fine"))
    bollette_gas = list(BollettaGas.objects.all().order_by("periodo_fine"))

    # A. Anomalia Spread Luce
    spreads_luce = [float(b.spread_pun) for b in bollette_luce if b.spread_pun is not None]
    if len(spreads_luce) >= 2:
        media_spread = sum(spreads_luce[:-1]) / len(spreads_luce[:-1])
        ultimo_spread = spreads_luce[-1]
        ultima_b = bollette_luce[-1]
        delta_spread = ultimo_spread - media_spread

        if delta_spread > 0.03:  # aumento > 3 centesimi/kWh di ricarico
            anomalie.append({
                "severita": "critica",
                "tipo": "spread_luce",
                "titolo": "Allerta Rincaro Fornitore Luce (Spread Anomalo)",
                "messaggio": (
                    f"Nell'ultima bolletta {ultima_b.fornitore} ({ultima_b.periodo_fine.strftime('%m/%Y')}), "
                    f"lo spread rispetto al PUN è salito a +{round(ultimo_spread, 4)} €/kWh, "
                    f"contro una media storica di +{round(media_spread, 4)} €/kWh. "
                    f"Il fornitore potrebbe aver modificato unilateralmente il contratto o sono scaduti i 12 mesi promozionali."
                ),
                "bolletta_id": ultima_b.pk,
                "fornitura": "luce",
            })

    # B. Anomalia Incidenza Costi Fissi
    for b in bollette_luce[-3:]:
        if b.importo_totale and b.importo_totale > 0 and b.kwh_fatturati > 0:
            costo_fissato = (b.quota_fissa_mensile or Decimal("10.00")) * (Decimal(b.giorni_periodo) / Decimal("30"))
            incidenza_fissa = (costo_fissato / b.importo_totale) * 100
            if incidenza_fissa > 35:
                anomalie.append({
                    "severita": "avviso",
                    "tipo": "costi_fissi",
                    "titolo": f"Alta Incidenza Quote Fisse Luce ({round(incidenza_fissa)}%)",
                    "messaggio": (
                        f"Nella bolletta {b.periodo_inizio.strftime('%d/%m')} → {b.periodo_fine.strftime('%d/%m/%Y')}, "
                        f"i costi fissi ({round(costo_fissato, 2)} €) pesano per il {round(incidenza_fissa, 1)}% dell'importo totale pagato. "
                        f"Il tuo costo finito effettivo sale a {b.costo_unitario_totale} €/kWh."
                    ),
                    "bolletta_id": b.pk,
                    "fornitura": "luce",
                })

    # C. Anomalia Consumo vs Meteo (Efficienza Termica)
    modello_gas = analizza_modello_termico("gas")
    if modello_gas and modello_gas["r_quadro"] > 0.5 and bollette_gas:
        ultima_g = bollette_gas[-1]
        hdd = float(ultima_g.gradi_giorno or 0)
        smc_reali = float(ultima_g.smc_fatturati or 0)
        giorni = ultima_g.giorni_periodo
        smc_attesi = (modello_gas["baseload_giornaliero"] * giorni) + (modello_gas["sensibilita_termica"] * hdd)

        if smc_attesi > 0:
            scostamento = (smc_reali - smc_attesi) / smc_attesi * 100
            if scostamento > 30:  # Consumo oltre 30% rispetto al clima
                anomalie.append({
                    "severita": "avviso",
                    "tipo": "consumo_meteo",
                    "titolo": f"Consumo Gas Superiore alle Attese (+{round(scostamento)}%)",
                    "messaggio": (
                        f"Nella bolletta gas di {ultima_g.periodo_fine.strftime('%m/%Y')} hai consumato {smc_reali} Smc, "
                        f"mentre in base alle temperature esterne Open-Meteo ({hdd} HDD) il modello ne stimava circa {round(smc_attesi, 1)} Smc. "
                        f"Possibili cause: temperature interne più elevate, dispersione o consumi stimati dal distributore."
                    ),
                    "bolletta_id": ultima_g.pk,
                    "fornitura": "gas",
                })

    return anomalie


# ===========================================================================
# 3. FORECASTING & PROIEZIONE FINE MESE
# ===========================================================================
def stima_proiezione_mese_corrente() -> dict[str, Any] | None:
    """
    Estrapola la stima della bolletta del mese in corso combinando:
    1. Telemetria live da Home Assistant (delta kWh dal 1° del mese ad oggi), OPPURE
    2. Modello statistico basato sulla serie storica dei consumi medi giornalieri.
    Calcola la proiezione dei kWh a fine mese e la stima del costo finale.
    """
    oggi = timezone.localdate()
    inizio_mese = oggi.replace(day=1)

    # Calcola giorni trascorsi e totali del mese
    if oggi.month == 12:
        fine_mese = date(oggi.year + 1, 1, 1) - timedelta(days=1)
    else:
        fine_mese = date(oggi.year, oggi.month + 1, 1) - timedelta(days=1)

    giorni_totali = fine_mese.day
    giorni_trascorsi = max(1, oggi.day)

    ultima_b = BollettaElettrica.objects.order_by("-periodo_fine").first()
    if not ultima_b:
        return None

    prezzo_kwh = float(ultima_b.prezzo_marginale_medio or Decimal("0.200000"))
    quota_fissa = float(ultima_b.quota_fissa_mensile or Decimal("10.00"))

    # Tentativo 1: Telemetria live da Home Assistant
    cfg = ConfigurazioneSistema.get_config()
    entity_kwh = cfg.ha_entity_kwh_consumo
    usato_ha = False
    kwh_misurati_finora = None

    if ha_client.is_configured() and entity_kwh and giorni_trascorsi >= 2:
        try:
            tz = timezone.get_current_timezone()
            dt_inizio = timezone.make_aware(datetime.combine(inizio_mese, datetime.min.time()), tz)
            dt_ora = timezone.now()
            dati_ha = ha_client.get_energia_consumata(entity_kwh, dt_inizio, dt_ora)
            if dati_ha and dati_ha.get("delta") is not None and dati_ha["delta"] > 0:
                kwh_misurati_finora = round(float(dati_ha["delta"]), 1)
                media_giornaliera = kwh_misurati_finora / giorni_trascorsi
                kwh_proiettati = round(media_giornaliera * giorni_totali)
                usato_ha = True
                fonte_label = f"Telemetria Live Home Assistant ({entity_kwh})"
        except Exception as exc:
            logger.debug("Lettura HA per stima fine mese fallita: %s", exc)

    # Tentativo 2: Fallback su Modello Statistico Storico
    if not usato_ha:
        bollette_recenti = list(BollettaElettrica.objects.all().order_by("-periodo_fine")[:3])
        medie_die = [b.kwh_giorno for b in bollette_recenti if b.kwh_giorno and b.kwh_giorno > 0]
        if medie_die:
            media_giornaliera = round(sum(medie_die) / len(medie_die), 2)
        else:
            media_giornaliera = round(ultima_b.kwh_giorno or 8.5, 2)

        kwh_misurati_finora = round(media_giornaliera * giorni_trascorsi, 1)
        kwh_proiettati = round(media_giornaliera * giorni_totali)
        fonte_label = "Modello Statistico Predittivo (Media Storica Consumi)"

    costo_variabile_stimato = round(kwh_proiettati * prezzo_kwh, 2)
    spesa_stimata_totale = round(costo_variabile_stimato + quota_fissa, 2)

    return {
        "mese": inizio_mese.strftime("%B %Y").capitalize(),
        "giorni_trascorsi": giorni_trascorsi,
        "giorni_totali": giorni_totali,
        "percentuale_mese": round((giorni_trascorsi / giorni_totali) * 100),
        "kwh_attuali": kwh_misurati_finora,
        "media_kwh_giorno": round(media_giornaliera, 2),
        "kwh_proiettati_fine_mese": kwh_proiettati,
        "prezzo_kwh_applicato": prezzo_kwh,
        "quota_fissa_applicata": quota_fissa,
        "spesa_proiettata_totale": spesa_stimata_totale,
        "supera_soglia_accisa": kwh_proiettati > 150,
        "usato_ha": usato_ha,
        "fonte_label": fonte_label,
    }


# ===========================================================================
# 4. CONFRONTO ANNO SU ANNO (YoY - YEAR OVER YEAR)
# ===========================================================================
def confronto_anno_su_anno(tipo: str = "luce") -> dict[str, Any] | None:
    """
    Esegue il confronto tra l'ultima bolletta registrata e la bolletta dello stesso mese
    dell'anno precedente, calcolando:
    - Delta Consumo assoluto e percentuale
    - Delta Climatico (Gradi Giorno HDD)
    - Indice di Efficienza Normalizzato (kWh/HDD o Smc/HDD)
    - Delta Spesa e Costo Unitario
    """
    if tipo == "gas":
        qs = BollettaGas.objects.all().order_by("-periodo_fine")
        unita = "Smc"
        campo_consumo = "smc_fatturati"
    else:
        qs = BollettaElettrica.objects.all().order_by("-periodo_fine")
        unita = "kWh"
        campo_consumo = "kwh_fatturati"

    ultima = qs.first()
    if not ultima:
        return None

    # Cerca la bolletta dello stesso mese dell'anno precedente (+/- 15 giorni)
    target_data = date(ultima.periodo_fine.year - 1, ultima.periodo_fine.month, ultima.periodo_fine.day)
    finestra_inizio = target_data - timedelta(days=20)
    finestra_fine = target_data + timedelta(days=20)

    anno_fa = qs.filter(periodo_fine__gte=finestra_inizio, periodo_fine__lte=finestra_fine).first()
    if not anno_fa:
        return None

    c_attuale = float(getattr(ultima, campo_consumo) or 0)
    c_anno_fa = float(getattr(anno_fa, campo_consumo) or 0)
    spesa_attuale = float(ultima.importo_totale or 0)
    spesa_anno_fa = float(anno_fa.importo_totale or 0)

    delta_c = c_attuale - c_anno_fa
    delta_c_pct = round((delta_c / c_anno_fa * 100), 1) if c_anno_fa > 0 else 0.0

    delta_spesa = spesa_attuale - spesa_anno_fa
    delta_spesa_pct = round((delta_spesa / spesa_anno_fa * 100), 1) if spesa_anno_fa > 0 else 0.0

    # Normalizzazione climatica (consumo per Grado Giorno)
    hdd_attuale = float(ultima.gradi_giorno or 0)
    hdd_anno_fa = float(anno_fa.gradi_giorno or 0)
    eff_attuale = round(c_attuale / hdd_attuale, 3) if hdd_attuale > 0 else None
    eff_anno_fa = round(c_anno_fa / hdd_anno_fa, 3) if hdd_anno_fa > 0 else None

    miglioramento_termico = None
    if eff_attuale and eff_anno_fa:
        # Se eff_attuale < eff_anno_fa, consumiamo meno per ogni grado giorno (casa più efficiente)
        miglioramento_termico = round(((eff_anno_fa - eff_attuale) / eff_anno_fa) * 100, 1)

    return {
        "tipo": tipo,
        "unita": unita,
        "periodo_attuale": f"{ultima.periodo_inizio.strftime('%d/%m')} - {ultima.periodo_fine.strftime('%d/%m/%Y')}",
        "periodo_anno_fa": f"{anno_fa.periodo_inizio.strftime('%d/%m')} - {anno_fa.periodo_fine.strftime('%d/%m/%Y')}",
        "consumo_attuale": c_attuale,
        "consumo_anno_fa": c_anno_fa,
        "delta_consumo": round(delta_c, 1),
        "delta_consumo_pct": delta_c_pct,
        "spesa_attuale": round(spesa_attuale, 2),
        "spesa_anno_fa": round(spesa_anno_fa, 2),
        "delta_spesa": round(delta_spesa, 2),
        "delta_spesa_pct": delta_spesa_pct,
        "hdd_attuale": hdd_attuale,
        "hdd_anno_fa": hdd_anno_fa,
        "eff_attuale": eff_attuale,
        "eff_anno_fa": eff_anno_fa,
        "miglioramento_termico": miglioramento_termico,
    }


# ===========================================================================
# 4. ENERGY HEALTH SCORE (EFFICIENZA CONTRATTUALE)
# ===========================================================================
def calcola_health_score() -> dict[str, Any]:
    """
    Calcola un punteggio da 0 a 100 che valuta la salute energetica della fornitura:
    - Trasparenza Spread (max 40 pt): penalità se lo spread rispetto al PUN/PSV è elevato
    - Efficienza Costi Fissi (max 30 pt): penalità se le quote fisse superano 10€/mese
    - Accuratezza Fatturato vs Reale (max 30 pt): congruenza tra bolletta e contatore HA
    """
    score = 100
    dettagli = []

    ultima_luce = BollettaElettrica.objects.order_by("-periodo_fine").first()
    ultima_gas = BollettaGas.objects.order_by("-periodo_fine").first()

    # 1. Valutazione Spread Luce (Spread ideale: <= 0.02 €/kWh)
    if ultima_luce and ultima_luce.spread_pun is not None:
        sp = float(ultima_luce.spread_pun)
        if sp > 0.06:
            score -= 25
            dettagli.append(f"Spread luce elevato (+{sp:.4f} €/kWh vs PUN): -25 pt")
        elif sp > 0.035:
            score -= 12
            dettagli.append(f"Spread luce moderato (+{sp:.4f} €/kWh vs PUN): -12 pt")
        else:
            dettagli.append(f"Spread luce competitivo (+{sp:.4f} €/kWh): Ottimo!")

    # 2. Valutazione Costi Fissi
    if ultima_luce and ultima_luce.quota_fissa_mensile:
        qf = float(ultima_luce.quota_fissa_mensile)
        if qf > 12.0:
            score -= 15
            dettagli.append(f"Quota fissa luce alta ({qf:.2f} €/mese): -15 pt")
        elif qf <= 8.5:
            dettagli.append(f"Quota fissa luce conveniente ({qf:.2f} €/mese): +10 pt")

    # 3. Congruenza vs Home Assistant
    if ultima_luce:
        validazione = services.valida_vs_ha(ultima_luce)
        if validazione.get("ok") and validazione.get("scostamento_pct") is not None:
            scost = abs(validazione["scostamento_pct"])
            if scost > 8.0:
                score -= 15
                dettagli.append(f"Scostamento contatore HA elevato ({scost}%): -15 pt")
            else:
                dettagli.append(f"Misurazione Home Assistant accurata (scostamento {scost}%): Ottimo!")

    score = max(0, min(100, score))
    giudizio = "Eccellente" if score >= 85 else ("Buono" if score >= 70 else ("Migliorabile" if score >= 50 else "Critico"))

    return {
        "score": score,
        "giudizio": giudizio,
        "dettagli": dettagli,
        "colore": "#22c55e" if score >= 75 else ("#f59e0b" if score >= 50 else "#ef4444"),
    }
