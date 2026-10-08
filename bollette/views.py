"""
Viste del Portale Bollette:
- Dashboard principale con grafici e metriche
- Archivio documentale completo (filtri, ricerca, download PDF, dettaglio bolletta)
- Statistiche avanzate, confronti mese su mese e simulatore tariffe
- Gestione configurazioni dinamiche (Bridge HA, Open-Meteo, Ollama) con diagnostica live
- Esportazione dati in CSV/Excel
"""
import csv
import json
import time
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import requests

from . import (
    ha_client,
    ha_comparison,
    meteo_client,
    netatmo_api_client,
    netatmo_service,
    parser_service,
    pun_client,
    services,
    smart_analytics,
)

from .forms import (
    BollettaElettricaForm,
    BollettaGasForm,
    CaricamentoMassivoForm,
    ConfigurazioneSistemaForm,
    UploadBollettaForm,
    UploadNetatmoForm,
)
from .models import BollettaElettrica, BollettaGas, ConfigurazioneSistema, HaSyncLog, NetatmoRecordGiornaliero


def home(request):
    """Dashboard principale con schede Luce, Gas, Bridge HA e grafici interattivi."""
    cfg = ConfigurazioneSistema.get_config()
    bollette_luce = list(BollettaElettrica.objects.all().order_by("-periodo_fine"))
    bollette_gas = list(BollettaGas.objects.all().order_by("-periodo_fine"))

    prezzo_ha_luce = services.calcola_prezzo_ha()
    prezzo_ha_gas = services.calcola_prezzo_gas_ha()

    # Dati cronologici per i grafici Chart.js
    luce_cronologica = sorted(bollette_luce, key=lambda b: b.periodo_fine)
    gas_cronologica = sorted(bollette_gas, key=lambda b: b.periodo_fine)

    chart_luce = {
        "labels": [b.periodo_fine.strftime("%m/%y") for b in luce_cronologica],
        "prezzi": [float(b.prezzo_marginale_medio or 0) for b in luce_cronologica],
        "costo_tot": [float(b.costo_unitario_totale or 0) for b in luce_cronologica],
        "pun": [float(b.pun_medio_periodo or 0) for b in luce_cronologica],
        "kwh": [int(b.kwh_fatturati or 0) for b in luce_cronologica],
        "hdd": [float(b.gradi_giorno or 0) for b in luce_cronologica],
        "temp": [float(b.temperatura_media or 0) for b in luce_cronologica],
    }

    chart_gas = {
        "labels": [b.periodo_fine.strftime("%m/%y") for b in gas_cronologica],
        "prezzi": [float(b.prezzo_marginale_medio_smc or 0) for b in gas_cronologica],
        "costo_tot": [float(b.costo_unitario_totale or 0) for b in gas_cronologica],
        "psv": [float(b.psv_medio_periodo or 0) for b in gas_cronologica],
        "smc": [float(b.smc_fatturati or 0) for b in gas_cronologica],
        "hdd": [float(b.gradi_giorno or 0) for b in gas_cronologica],
        "temp": [float(b.temperatura_media or 0) for b in gas_cronologica],
    }

    # Statistiche rapide per i box in cima
    tot_spesa_luce = sum((b.importo_totale or Decimal("0")) for b in bollette_luce)
    tot_spesa_gas = sum((b.importo_totale or Decimal("0")) for b in bollette_gas)
    tot_kwh = sum((b.kwh_fatturati or 0) for b in bollette_luce)
    tot_smc = sum((b.smc_fatturati or Decimal("0")) for b in bollette_gas)

    context = {
        "cfg": cfg,
        "bollette_luce": bollette_luce[:5],  # ultime 5
        "bollette_gas": bollette_gas[:5],
        "conteggio_luce": len(bollette_luce),
        "conteggio_gas": len(bollette_gas),
        "tot_spesa_luce": tot_spesa_luce,
        "tot_spesa_gas": tot_spesa_gas,
        "tot_spesa_complessiva": tot_spesa_luce + tot_spesa_gas,
        "tot_kwh": tot_kwh,
        "tot_smc": tot_smc,
        "prezzo_ha_luce": prezzo_ha_luce,
        "prezzo_ha_gas": prezzo_ha_gas,
        "ha_configurato": ha_client.is_configured(),
        "ha_base_url": cfg.ha_base_url or settings.HA_BASE_URL,
        "mode": cfg.prezzo_ha_mode,
        "entity_consumo_luce": cfg.ha_entity_kwh_consumo,
        "entity_consumo_gas": cfg.ha_entity_smc_consumo,
        "upload_form": UploadBollettaForm(),
        "chart_luce_json": json.dumps(chart_luce),
        "chart_gas_json": json.dumps(chart_gas),
        "anomalie": smart_analytics.rileva_anomalie(),
        "health_score": smart_analytics.calcola_health_score(),
        "proiezione_mese": smart_analytics.stima_proiezione_mese_corrente(),
        "telemetria_ha": ha_client.get_live_telemetry() if ha_client.is_configured() else {"online": False},
        "device_breakdown": ha_client.get_device_breakdown() if ha_client.is_configured() else {"carichi": []},
        "stat_netatmo": netatmo_service.ottieni_statistiche_netatmo(),
    }
    return render(request, "bollette/home.html", context)



# ===========================================================================
# ARCHIVIO BOLLETTE
# ===========================================================================
def archivio(request):
    """Archivio documentale completo con filtri per fornitura, anno, fornitore e ricerca."""
    tipo_filtro = request.GET.get("tipo", "tutti")
    anno_filtro = request.GET.get("anno", "")
    fornitore_filtro = request.GET.get("fornitore", "")
    query = request.GET.get("q", "").strip().lower()

    qs_luce = BollettaElettrica.objects.all().order_by("-periodo_fine")
    qs_gas = BollettaGas.objects.all().order_by("-periodo_fine")

    if anno_filtro:
        try:
            anno = int(anno_filtro)
            qs_luce = qs_luce.filter(periodo_fine__year=anno)
            qs_gas = qs_gas.filter(periodo_fine__year=anno)
        except ValueError:
            pass

    if fornitore_filtro:
        qs_luce = qs_luce.filter(fornitore__iexact=fornitore_filtro)
        qs_gas = qs_gas.filter(fornitore__iexact=fornitore_filtro)

    if query:
        qs_luce = qs_luce.filter(pod__icontains=query) | qs_luce.filter(numero_fattura__icontains=query) | qs_luce.filter(note__icontains=query)
        qs_gas = qs_gas.filter(pdr__icontains=query) | qs_gas.filter(numero_fattura__icontains=query) | qs_gas.filter(note__icontains=query)

    # Elenco fornitori e anni distinti per i filtri
    fornitori = set(BollettaElettrica.objects.values_list("fornitore", flat=True)).union(
        set(BollettaGas.objects.values_list("fornitore", flat=True))
    )
    anni_luce = set(BollettaElettrica.objects.dates("periodo_fine", "year"))
    anni_gas = set(BollettaGas.objects.dates("periodo_fine", "year"))
    anni = sorted({d.year for d in (anni_luce | anni_gas)}, reverse=True)

    bollette_luce = list(qs_luce) if tipo_filtro in ("tutti", "luce") else []
    bollette_gas = list(qs_gas) if tipo_filtro in ("tutti", "gas") else []

    context = {
        "bollette_luce": bollette_luce,
        "bollette_gas": bollette_gas,
        "totale_risultati": len(bollette_luce) + len(bollette_gas),
        "tipo_filtro": tipo_filtro,
        "anno_filtro": anno_filtro,
        "fornitore_filtro": fornitore_filtro,
        "query": query,
        "fornitori": sorted(filter(None, fornitori)),
        "anni": anni,
    }
    return render(request, "bollette/archivio.html", context)


def dettaglio_bolletta_luce(request, pk):
    """Scheda dettagliata di una bolletta elettrica con scomposizione costi e metriche."""
    bolletta = get_object_or_404(BollettaElettrica, pk=pk)
    return render(request, "bollette/dettaglio_luce.html", {"b": bolletta})


def dettaglio_bolletta_gas(request, pk):
    """Scheda dettagliata di una bolletta gas metano."""
    bolletta = get_object_or_404(BollettaGas, pk=pk)
    return render(request, "bollette/dettaglio_gas.html", {"b": bolletta})


@require_POST
def elimina_bolletta_luce(request, pk):
    """Eliminazione sicura di una bolletta elettrica."""
    bolletta = get_object_or_404(BollettaElettrica, pk=pk)
    nome = str(bolletta)
    bolletta.delete()
    messages.success(request, f"{nome} eliminata dall'archivio.")
    return redirect("archivio")


@require_POST
def elimina_bolletta_gas(request, pk):
    """Eliminazione sicura di una bolletta gas."""
    bolletta = get_object_or_404(BollettaGas, pk=pk)
    nome = str(bolletta)
    bolletta.delete()
    messages.success(request, f"{nome} eliminata dall'archivio.")
    return redirect("archivio")


# ===========================================================================
# STATISTICHE & CONFRONTI
# ===========================================================================
def statistiche(request):
    """Sezione avanzata di statistiche, confronti mese su mese e simulatore tariffe."""
    bollette_luce = list(BollettaElettrica.objects.all().order_by("periodo_fine"))
    bollette_gas = list(BollettaGas.objects.all().order_by("periodo_fine"))

    # Raggruppamento per Anno
    stat_anni = {}
    for b in bollette_luce:
        anno = b.periodo_fine.year
        if anno not in stat_anni:
            stat_anni[anno] = {
                "spesa_luce": Decimal("0"),
                "spesa_gas": Decimal("0"),
                "kwh": 0,
                "smc": Decimal("0"),
                "hdd": Decimal("0"),
                "_temps": [],
            }
        stat_anni[anno]["spesa_luce"] += (b.importo_totale or Decimal("0"))
        stat_anni[anno]["kwh"] += (b.kwh_fatturati or 0)
        stat_anni[anno]["hdd"] += (b.gradi_giorno or Decimal("0"))
        if b.temperatura_media is not None:
            stat_anni[anno]["_temps"].append(float(b.temperatura_media))

    for g in bollette_gas:
        anno = g.periodo_fine.year
        if anno not in stat_anni:
            stat_anni[anno] = {
                "spesa_luce": Decimal("0"),
                "spesa_gas": Decimal("0"),
                "kwh": 0,
                "smc": Decimal("0"),
                "hdd": Decimal("0"),
                "_temps": [],
            }
        stat_anni[anno]["spesa_gas"] += (g.importo_totale or Decimal("0"))
        stat_anni[anno]["smc"] += (g.smc_fatturati or Decimal("0"))
        if g.temperatura_media is not None:
            stat_anni[anno]["_temps"].append(float(g.temperatura_media))

    # Calcola totali complessivi e media temperature
    for anno, dati in stat_anni.items():
        dati["spesa_totale"] = dati["spesa_luce"] + dati["spesa_gas"]
        temps = dati.pop("_temps", [])
        dati["temp_media"] = round(sum(temps) / len(temps), 1) if temps else None

    # Medie generali
    tot_kwh = sum(b.kwh_fatturati or 0 for b in bollette_luce)
    tot_spesa_luce = sum(b.importo_totale or Decimal("0") for b in bollette_luce)
    costo_medio_kwh_finito = (tot_spesa_luce / tot_kwh).quantize(Decimal("0.0001")) if tot_kwh > 0 else None

    tot_smc = sum(b.smc_fatturati or Decimal("0") for b in bollette_gas)
    tot_spesa_gas = sum(b.importo_totale or Decimal("0") for b in bollette_gas)
    costo_medio_smc_finito = (tot_spesa_gas / tot_smc).quantize(Decimal("0.0001")) if tot_smc > 0 else None

    # Simulatore di spesa con tariffa fissa (es. 0.11 €/kWh luce e 0.40 €/Smc gas)
    prezzo_fisso_luce = Decimal("0.110000")
    prezzo_fisso_gas = Decimal("0.400000")
    spesa_simulata_luce = sum(Decimal(b.kwh_fatturati or 0) * prezzo_fisso_luce + Decimal(b.quota_fissa_mensile or 10) for b in bollette_luce)
    differenza_luce = (tot_spesa_luce - spesa_simulata_luce).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Statistiche andamento consumi e picchi stagionali
    medie_kwh_die = [b.kwh_giorno for b in bollette_luce if b.kwh_giorno > 0]
    media_kwh_giorno = round(sum(medie_kwh_die) / len(medie_kwh_die), 2) if medie_kwh_die else 0
    max_bolletta_luce = max(bollette_luce, key=lambda b: b.kwh_fatturati or 0) if bollette_luce else None
    min_bolletta_luce = min(bollette_luce, key=lambda b: b.kwh_fatturati or 0) if bollette_luce else None

    medie_smc_die = [b.smc_giorno for b in bollette_gas if b.smc_giorno > 0]
    media_smc_giorno = round(sum(medie_smc_die) / len(medie_smc_die), 2) if medie_smc_die else 0
    max_bolletta_gas = max(bollette_gas, key=lambda b: b.smc_fatturati or 0) if bollette_gas else None
    min_bolletta_gas = min(bollette_gas, key=lambda b: b.smc_fatturati or 0) if bollette_gas else None

    context = {
        "stat_anni": dict(sorted(stat_anni.items(), reverse=True)),
        "costo_medio_kwh_finito": costo_medio_kwh_finito,
        "costo_medio_smc_finito": costo_medio_smc_finito,
        "tot_kwh": tot_kwh,
        "tot_smc": tot_smc,
        "tot_spesa_luce": tot_spesa_luce,
        "tot_spesa_gas": tot_spesa_gas,
        "tot_spesa_complessiva": tot_spesa_luce + tot_spesa_gas,
        "differenza_luce": differenza_luce,
        "prezzo_fisso_luce": prezzo_fisso_luce,
        "media_kwh_giorno": media_kwh_giorno,
        "media_smc_giorno": media_smc_giorno,
        "max_bolletta_luce": max_bolletta_luce,
        "min_bolletta_luce": min_bolletta_luce,
        "max_bolletta_gas": max_bolletta_gas,
        "min_bolletta_gas": min_bolletta_gas,
        "cronologia_luce": bollette_luce[::-1],
        "cronologia_gas": bollette_gas[::-1],
        "modello_termico_luce": smart_analytics.analizza_modello_termico("luce"),
        "modello_termico_gas": smart_analytics.analizza_modello_termico("gas"),
        "health_score": smart_analytics.calcola_health_score(),
        "anomalie": smart_analytics.rileva_anomalie(),
        "proiezione_mese": smart_analytics.stima_proiezione_mese_corrente(),
        "confronto_yoy_luce": smart_analytics.confronto_anno_su_anno("luce"),
        "confronto_yoy_gas": smart_analytics.confronto_anno_su_anno("gas"),
    }
    return render(request, "bollette/statistiche.html", context)


# ===========================================================================
# CONFIGURAZIONI & DIAGNOSTICA
# ===========================================================================
def configurazioni_view(request):
    """Gestione delle configurazioni del portale con form reattivo e diagnostica."""
    cfg = ConfigurazioneSistema.get_config()

    if request.method == "POST":
        form = ConfigurazioneSistemaForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            messages.success(request, "Configurazioni di sistema aggiornate con successo.")
            return redirect("configurazioni")
    else:
        form = ConfigurazioneSistemaForm(instance=cfg)

    env_ha_token = getattr(settings, "HA_TOKEN", "").strip()
    env_ha_url = getattr(settings, "HA_BASE_URL", "").strip().rstrip("/")
    ha_configured = bool((env_ha_url or cfg.ha_base_url) and (env_ha_token or cfg.ha_token))
    netatmo_configured = bool(cfg.netatmo_client_id and cfg.netatmo_client_secret and cfg.netatmo_refresh_token)
    meteo_configured = bool(cfg.meteo_latitude and cfg.meteo_longitude)
    ha_logs = list(HaSyncLog.objects.all().order_by("-creato_il")[:10])
    tot_sync = HaSyncLog.objects.count()
    webhook_url = request.build_absolute_uri("/api/v1/ha/push/")

    context = {
        "form": form,
        "cfg": cfg,
        "ha_configured": ha_configured,
        "ha_from_env": bool(env_ha_token),
        "netatmo_configured": netatmo_configured,
        "meteo_configured": meteo_configured,
        "webhook_url": webhook_url,
        "ha_logs": ha_logs,
        "tot_sync": tot_sync,
    }
    return render(request, "bollette/configurazioni.html", context)


def test_servizio_api(request):
    """Endpoint diagnostico per testare Home Assistant, Open-Meteo o Ollama in tempo reale."""
    servizio = request.GET.get("servizio") or request.POST.get("servizio", "ha")
    cfg = ConfigurazioneSistema.get_config()

    if servizio == "ha":
        env_base_url = (getattr(settings, "HA_BASE_URL", "") or "").rstrip("/")
        env_token = getattr(settings, "HA_TOKEN", "") or ""
        base_url = (request.GET.get("url") or env_base_url or cfg.ha_base_url or "").rstrip("/")
        token = request.GET.get("token") or env_token or cfg.ha_token or ""
        tls_param = request.GET.get("tls_verify")
        if tls_param is not None:
            verify = tls_param.lower() in ("1", "true", "yes", "on")
        else:
            verify = cfg.ha_ca_bundle or cfg.ha_tls_verify

        if not base_url or not token:
            return JsonResponse({
                "ok": False,
                "messaggio": "Indirizzo URL o Token di Home Assistant non configurati. Inseriscili nei campi sottostanti per testare."
            })
        try:
            url = f"{base_url}/api/"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            t0 = time.time()
            r = requests.get(url, headers=headers, timeout=5, verify=verify)
            latency_ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                data = r.json()
                msg = data.get("message", "API attiva e pronta")
                return JsonResponse({
                    "ok": True,
                    "messaggio": f"Connessione a Home Assistant riuscita ({latency_ms} ms)! Risposta server: '{msg}'",
                    "latency_ms": latency_ms,
                })
            elif r.status_code == 401:
                return JsonResponse({
                    "ok": False,
                    "messaggio": "Errore 401 (Non autorizzato): Il Token di Accesso non è valido o è scaduto. Verifica di aver copiato l'intero token.",
                })
            return JsonResponse({
                "ok": False,
                "messaggio": f"Home Assistant ha risposto con codice HTTP {r.status_code}: {r.text[:120]}"
            })
        except requests.exceptions.SSLError as ssl_err:
            return JsonResponse({
                "ok": False,
                "messaggio": f"Errore SSL/TLS: {ssl_err}. Suggerimento: se usi un IP locale con certificato autofirmato, disabilita 'Verifica Certificato TLS'."
            })
        except requests.exceptions.ConnectionError:
            return JsonResponse({
                "ok": False,
                "messaggio": f"Impossibile raggiungere Home Assistant all'indirizzo '{base_url}'. Controlla che l'indirizzo IP/porta (8123) sia corretto e che il server sia acceso."
            })
        except Exception as exc:
            return JsonResponse({"ok": False, "messaggio": f"Errore durante il test di connessione: {exc}"})

    elif servizio == "meteo":
        lat = request.GET.get("lat")
        lon = request.GET.get("lon")
        citta = request.GET.get("citta") or cfg.meteo_citta
        try:
            latitude = float(lat) if lat else float(cfg.meteo_latitude)
            longitude = float(lon) if lon else float(cfg.meteo_longitude)
        except (ValueError, TypeError):
            latitude = float(cfg.meteo_latitude)
            longitude = float(cfg.meteo_longitude)

        meteo = meteo_client.calcola_gradi_giorno(
            date.today().replace(day=1), date.today(),
            latitudine=latitude,
            longitudine=longitude,
        )
        if meteo:
            return JsonResponse({
                "ok": True,
                "messaggio": f"Open-Meteo OK! Località: {citta} (Lat: {latitude:.4f}, Lon: {longitude:.4f}). Temp. media mese in corso: {meteo['t_media']} °C.",
                "t_media": meteo["t_media"],
                "hdd": meteo["hdd"],
            })
        return JsonResponse({"ok": False, "messaggio": "Nessuna risposta valida da Open-Meteo per le coordinate fornite."})

    elif servizio == "netatmo":
        c_id = request.GET.get("client_id")
        c_sec = request.GET.get("client_secret")
        ref_tok = request.GET.get("refresh_token")
        if c_id and c_sec and ref_tok:
            cfg.netatmo_client_id = c_id
            cfg.netatmo_client_secret = c_sec
            cfg.netatmo_refresh_token = ref_tok
            cfg.save(update_fields=["netatmo_client_id", "netatmo_client_secret", "netatmo_refresh_token"])
        return JsonResponse(netatmo_api_client.test_connessione())

    return JsonResponse({"ok": False, "messaggio": "Servizio sconosciuto."})


def esporta_archivio_csv(request):
    """Esporta tutte le bollette dell'archivio in un file CSV formattato per Excel."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="archivio_bollette_luce_gas.csv"'
    response.write("\ufeff".encode("utf-8"))  # BOM per apertura corretta in Microsoft Excel

    writer = csv.writer(response, delimiter=";")
    writer.writerow([
        "Tipo", "Fornitore", "Codice (POD/PDR)", "Fattura N.", "Data Emissione",
        "Periodo Inizio", "Periodo Fine", "Giorni", "Importo Totale (€)",
        "Consumo (kWh o Smc)", "Prezzo Marginale Unitario", "Prezzo Tutto Compreso",
        "Gradi Giorno (HDD)", "Temp. Media (°C)", "Consumo/HDD", "Benchmark Borsa (PUN/PSV)",
        "Spread vs Borsa", "Sync HA", "Note",
    ])

    for b in BollettaElettrica.objects.all().order_by("periodo_fine"):
        writer.writerow([
            "Luce", b.fornitore, b.pod, b.numero_fattura, b.data_emissione,
            b.periodo_inizio, b.periodo_fine, b.giorni_periodo, b.importo_totale,
            b.kwh_fatturati, b.prezzo_marginale_medio, b.costo_unitario_totale,
            b.gradi_giorno, b.temperatura_media, b.kwh_per_gradi_giorno,
            b.pun_medio_periodo, b.spread_pun, "OK" if b.ha_sync_ok else "NO", b.note,
        ])

    for g in BollettaGas.objects.all().order_by("periodo_fine"):
        writer.writerow([
            "Gas", g.fornitore, g.pdr, g.numero_fattura, g.data_emissione,
            g.periodo_inizio, g.periodo_fine, g.giorni_periodo, g.importo_totale,
            g.smc_fatturati, g.prezzo_marginale_medio_smc, g.costo_unitario_totale,
            g.gradi_giorno, g.temperatura_media, g.smc_per_gradi_giorno,
            g.psv_medio_periodo, g.spread_psv, "OK" if g.ha_sync_ok else "NO", g.note,
        ])

    return response


# ===========================================================================
# INGESTION & INSERIMENTO BOLLETTE
# ===========================================================================
def nuova_bolletta(request):
    """Inserimento manuale o assistito di una bolletta elettrica con autocompilazione da PDF."""
    if request.method == "POST":
        post_data = request.POST.copy()
        file_pdf = request.FILES.get("file_bolletta")
        if file_pdf:
            campi_critici = ["fornitore", "periodo_inizio", "periodo_fine", "importo_totale", "kwh_fatturati"]
            if any(not post_data.get(k) for k in campi_critici):
                dati = parser_service.analizza_documento(file_pdf)
                if dati.get("ok"):
                    if not post_data.get("fornitore") and dati.get("fornitore"):
                        post_data["fornitore"] = dati["fornitore"]
                    if not post_data.get("pod") and dati.get("pod"):
                        post_data["pod"] = dati["pod"]
                    if not post_data.get("numero_fattura") and dati.get("numero_fattura"):
                        post_data["numero_fattura"] = dati["numero_fattura"]
                    if not post_data.get("data_emissione") and dati.get("data_emissione_iso"):
                        post_data["data_emissione"] = dati["data_emissione_iso"]
                    if not post_data.get("periodo_inizio") and dati.get("periodo_inizio_iso"):
                        post_data["periodo_inizio"] = dati["periodo_inizio_iso"]
                    if not post_data.get("periodo_fine") and dati.get("periodo_fine_iso"):
                        post_data["periodo_fine"] = dati["periodo_fine_iso"]
                    if not post_data.get("importo_totale") and dati.get("importo_totale") is not None:
                        post_data["importo_totale"] = str(dati["importo_totale"])
                    if not post_data.get("kwh_fatturati") and dati.get("consumo") is not None:
                        try:
                            post_data["kwh_fatturati"] = int(float(dati["consumo"]))
                        except (ValueError, TypeError):
                            pass
                    if not post_data.get("note") and dati.get("fasce"):
                        f = dati["fasce"]
                        post_data["note"] = f"Ripartizione fasce da PDF: F1={f.get('f1', 0)} kWh, F2={f.get('f2', 0)} kWh, F3={f.get('f3', 0)} kWh."

        form = BollettaElettricaForm(post_data, request.FILES)
        if form.is_valid():
            bolletta = form.save()
            services.aggiorna_campi_calcolati(bolletta)
            messages.success(
                request,
                f"Bolletta elettrica archiviata con successo. Prezzo marginale medio: {bolletta.prezzo_marginale_medio} €/kWh."
                + (f" Temp. media rilevata: {bolletta.temperatura_media} °C." if bolletta.temperatura_media else "")
            )
            esito = services.sincronizza_dopo_ingest(bolletta)
            if esito and esito.get("ok"):
                messages.success(request, "Prezzo pubblicato automaticamente in Home Assistant.")
            return redirect("dettaglio_bolletta_luce", pk=bolletta.pk)
    else:
        initial_data = request.session.pop("prefill_bolletta", None)
        form = BollettaElettricaForm(initial=initial_data)

    return render(request, "bollette/nuova_bolletta.html", {"form": form, "tipo": "luce"})


def nuova_bolletta_gas(request):
    """Inserimento manuale o assistito di una bolletta gas metano con autocompilazione da PDF."""
    if request.method == "POST":
        post_data = request.POST.copy()
        file_pdf = request.FILES.get("file_bolletta")
        if file_pdf:
            campi_critici = ["fornitore", "periodo_inizio", "periodo_fine", "importo_totale", "smc_fatturati"]
            if any(not post_data.get(k) for k in campi_critici):
                dati = parser_service.analizza_documento(file_pdf)
                if dati.get("ok"):
                    if not post_data.get("fornitore") and dati.get("fornitore"):
                        post_data["fornitore"] = dati["fornitore"]
                    if not post_data.get("pdr") and dati.get("pdr"):
                        post_data["pdr"] = dati["pdr"]
                    if not post_data.get("numero_fattura") and dati.get("numero_fattura"):
                        post_data["numero_fattura"] = dati["numero_fattura"]
                    if not post_data.get("data_emissione") and dati.get("data_emissione_iso"):
                        post_data["data_emissione"] = dati["data_emissione_iso"]
                    if not post_data.get("periodo_inizio") and dati.get("periodo_inizio_iso"):
                        post_data["periodo_inizio"] = dati["periodo_inizio_iso"]
                    if not post_data.get("periodo_fine") and dati.get("periodo_fine_iso"):
                        post_data["periodo_fine"] = dati["periodo_fine_iso"]
                    if not post_data.get("importo_totale") and dati.get("importo_totale") is not None:
                        post_data["importo_totale"] = str(dati["importo_totale"])
                    if not post_data.get("smc_fatturati") and dati.get("consumo") is not None:
                        post_data["smc_fatturati"] = str(dati["consumo"])
                    if not post_data.get("coeff_c") and dati.get("coeff_c") is not None:
                        post_data["coeff_c"] = str(dati["coeff_c"])
                    if not post_data.get("potere_calorifico_pcs") and dati.get("potere_calorifico_pcs") is not None:
                        post_data["potere_calorifico_pcs"] = str(dati["potere_calorifico_pcs"])

        form = BollettaGasForm(post_data, request.FILES)
        if form.is_valid():
            bolletta = form.save()
            services.aggiorna_campi_calcolati_gas(bolletta)
            messages.success(
                request,
                f"Bolletta gas archiviata con successo. Prezzo marginale: {bolletta.prezzo_marginale_medio_smc} €/Smc."
                + (f" Temp. media rilevata: {bolletta.temperatura_media} °C." if bolletta.temperatura_media else "")
            )
            return redirect("dettaglio_bolletta_gas", pk=bolletta.pk)
    else:
        initial_data = request.session.pop("prefill_bolletta_gas", None)
        form = BollettaGasForm(initial=initial_data)

    return render(request, "bollette/nuova_bolletta_gas.html", {"form": form, "tipo": "gas"})


@csrf_exempt
def analizza_pdf_api(request):
    """Endpoint AJAX per estrarre dati da un PDF caricato in tempo reale nel browser."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "errore": "Metodo non consentito."}, status=405)
    file_obj = request.FILES.get("file_bolletta") or request.FILES.get("file")
    if not file_obj:
        return JsonResponse({"ok": False, "errore": "Nessun file PDF inviato."}, status=400)
    try:
        res = parser_service.analizza_documento(file_obj)
        return JsonResponse(res)
    except Exception as exc:
        return JsonResponse({"ok": False, "errore": str(exc)}, status=500)


def upload_pdf_bolletta(request):
    """Analizza il file PDF caricato e reindirizza al form appropriato precompilato."""
    if request.method == "POST":
        form = UploadBollettaForm(request.POST, request.FILES)
        if form.is_valid():
            file_pdf = request.FILES["file_bolletta"]
            dati = parser_service.analizza_documento(file_pdf)

            if not dati.get("ok"):
                messages.error(request, f"Errore analisi PDF: {dati.get('errore')}")
                return redirect("home")

            tipo = dati.get("tipo", "luce")
            initial = {
                "fornitore": dati.get("fornitore") or "Acea",
                "periodo_inizio": dati.get("periodo_inizio"),
                "periodo_fine": dati.get("periodo_fine"),
                "importo_totale": dati.get("importo_totale"),
            }

            if tipo == "gas":
                initial["pdr"] = dati.get("pdr") or ""
                initial["smc_fatturati"] = dati.get("consumo") or Decimal("0")
                request.session["prefill_bolletta_gas"] = initial
                messages.info(request, f"PDF Gas analizzato con successo (fonte: {dati['fonte_parsing']}). Controlla e conferma i dati prima del salvataggio.")
                return redirect("nuova_bolletta_gas")
            else:
                initial["pod"] = dati.get("pod") or ""
                initial["kwh_fatturati"] = int(dati.get("consumo") or 0)
                request.session["prefill_bolletta"] = initial
                messages.info(request, f"PDF Luce analizzato con successo (fonte: {dati['fonte_parsing']}). Controlla e conferma i dati prima del salvataggio.")
                return redirect("nuova_bolletta")

    return redirect("home")


def caricamento_massivo_view(request):
    """Gestione del caricamento multiplo (batch) di più file PDF di bollette in contemporanea."""
    report = None
    if request.method == "POST":
        form = CaricamentoMassivoForm(request.POST, request.FILES)
        if form.is_valid():
            files = request.FILES.getlist("file_bollette")
            sovrascrivi = form.cleaned_data.get("sovrascrivi_esistenti", False)
            if not files:
                messages.warning(request, "Nessun file PDF valido selezionato.")
            else:
                report = parser_service.elabora_caricamento_massivo(files, sovrascrivi=sovrascrivi)
                messages.success(
                    request,
                    f"Elaborazione completata: {report['tot_creati']} create, "
                    f"{report['tot_aggiornati']} aggiornate, {report['tot_saltati']} già presenti, "
                    f"{report['tot_errori']} errori."
                )
    else:
        form = CaricamentoMassivoForm()

    return render(request, "bollette/caricamento_massivo.html", {"form": form, "report": report})


def confronto_ha_view(request):
    """
    Sezione di confronto e validazione tra consumi fatturati in bolletta
    e misure reali di Home Assistant (sia via polling REST che via importazione CSV).
    """
    if request.method == "POST":
        azione = request.POST.get("azione", "polling")

        if azione == "polling":
            tipo = request.POST.get("tipo", "tutti")
            bolletta_id = request.POST.get("bolletta_id")
            if bolletta_id:
                try:
                    bolletta_id = int(bolletta_id)
                except ValueError:
                    bolletta_id = None
            esito = ha_comparison.esegui_polling_confronto(tipo=tipo, bolletta_id=bolletta_id)
            if esito.get("ok"):
                messages.success(request, esito.get("messaggio", "Polling HA completato con successo."))
            else:
                messages.warning(request, esito.get("errore", "Errore durante il polling di Home Assistant."))
            return redirect("confronto_ha")

        elif azione == "upload_csv":
            file_csv = request.FILES.get("file_csv")
            tipo_pref = request.POST.get("tipo_predefinito", "luce")
            if not file_csv:
                messages.warning(request, "Nessun file CSV selezionato.")
            else:
                esito = ha_comparison.elabora_csv_home_assistant(file_csv, tipo_predefinito=tipo_pref)
                if esito.get("ok"):
                    messages.success(request, esito.get("messaggio", "File CSV analizzato con successo."))
                else:
                    messages.error(request, esito.get("errore", "Errore durante l'elaborazione del CSV."))
            return redirect("confronto_ha")

    riepilogo = ha_comparison.ottieni_riepilogo_confronto()
    cfg = ConfigurazioneSistema.get_config()

    context = {
        "riepilogo": riepilogo,
        "cfg": cfg,
        "telemetria_ha": ha_client.get_live_telemetry() if ha_client.is_configured() else {"online": False},
        "device_breakdown": ha_client.get_device_breakdown() if ha_client.is_configured() else {"carichi": []},
    }
    return render(request, "bollette/confronto_ha.html", context)


def ha_discovery_api(request):
    """Endpoint JSON per auto-scoperta sensori energia in Home Assistant."""
    url_param = (request.GET.get("url") or "").rstrip("/")
    token_param = request.GET.get("token") or ""
    if url_param and token_param:
        try:
            headers = {"Authorization": f"Bearer {token_param}", "Content-Type": "application/json"}
            verify = request.GET.get("tls_verify", "true").lower() in ("1", "true", "yes", "on")
            r = requests.get(f"{url_param}/api/states", headers=headers, timeout=6, verify=verify)
            if r.status_code == 200:
                stati = r.json()
                sensori = []
                unita_valide = {"kwh", "wh", "mwh", "smc", "m³", "m3", "w", "kw", "€/kwh", "€/smc"}
                for item in stati:
                    entity_id = item.get("entity_id", "")
                    attrs = item.get("attributes", {})
                    unit = (attrs.get("unit_of_measurement") or "").lower()
                    dev_class = attrs.get("device_class")
                    if (
                        dev_class in ("energy", "power", "gas", "monetary")
                        or unit in unita_valide
                        or any(k in entity_id.lower() for k in ("energy", "consumo", "pun", "shelly", "gas", "contatore"))
                    ):
                        sensori.append({
                            "entity_id": entity_id,
                            "name": attrs.get("friendly_name", entity_id),
                            "state": item.get("state"),
                            "unit": attrs.get("unit_of_measurement", ""),
                            "device_class": dev_class,
                            "state_class": attrs.get("state_class"),
                        })
                sensori.sort(key=lambda s: s["entity_id"])
                return JsonResponse({"ok": True, "sensori": sensori})
        except Exception as exc:
            return JsonResponse({"ok": False, "errore": str(exc), "sensori": []})

    sensori = ha_client.scopri_entita_energia()
    return JsonResponse({"ok": True, "sensori": sensori})


# ===========================================================================
# AZIONI HOME ASSISTANT
# ===========================================================================
@require_POST
def pubblica_media(request):
    cfg = ConfigurazioneSistema.get_config()
    esito = services.pubblica_prezzo_su_ha(mode=cfg.prezzo_ha_mode)
    if esito.get("ok"):
        c = esito["componenti"]
        messages.success(request, f"Prezzo luce pubblicato in HA: {c['prezzo_energia_kwh']} €/kWh (su {c['n_bollette']} bollette).")
    else:
        messages.warning(request, f"Pubblicazione HA luce non riuscita ({esito.get('errore')}).")
    return redirect("home")


@require_POST
def pubblica_bolletta(request, pk):
    bolletta = get_object_or_404(BollettaElettrica, pk=pk)
    esito = services.pubblica_prezzo_su_ha(bolletta=bolletta)
    if esito.get("ok"):
        messages.success(request, f"{bolletta}: prezzo pubblicato in HA ({esito['componenti']['prezzo_energia_kwh']} €/kWh).")
    else:
        messages.warning(request, f"{bolletta}: pubblicazione non riuscita.")
    return redirect(request.META.get("HTTP_REFERER", "home"))


@require_POST
def valida_bolletta(request, pk):
    bolletta = get_object_or_404(BollettaElettrica, pk=pk)
    esito = services.valida_vs_ha(bolletta)
    if esito["ok"]:
        messages.success(request, f"{bolletta}: {esito['messaggio']}")
    else:
        messages.warning(request, f"{bolletta}: {esito['messaggio']}")
    return redirect(request.META.get("HTTP_REFERER", "home"))


@require_POST
def pubblica_media_gas(request):
    cfg = ConfigurazioneSistema.get_config()
    esito = services.pubblica_prezzo_gas_su_ha(mode=cfg.prezzo_ha_mode)
    if esito.get("ok"):
        messages.success(request, f"Prezzo gas pubblicato in HA: {esito['valore']} €/Smc.")
    else:
        messages.warning(request, f"Pubblicazione HA gas fallita ({esito.get('errore')}).")
    return redirect("home")


@require_POST
def pubblica_bolletta_gas(request, pk):
    bolletta = get_object_or_404(BollettaGas, pk=pk)
    esito = services.pubblica_prezzo_gas_su_ha(bolletta=bolletta)
    if esito.get("ok"):
        messages.success(request, f"{bolletta}: prezzo inviato a HA ({esito['valore']} €/Smc).")
    else:
        messages.warning(request, f"{bolletta}: pubblicazione non riuscita.")
    return redirect(request.META.get("HTTP_REFERER", "home"))


def sincronizzazione_bidirezionale_view(request):
    """
    Esegue una sincronizzazione bidirezionale completa tra Portale Bollette e Home Assistant:
    - Scrive le tariffe aggiornate, la quota fissa e lo stato su HA.
    - Esegue il polling per verificare tutte le bollette archiviate con le statistiche reali di HA.
    - Raccoglie la telemetria live e la ripartizione dei consumi per carichi.
    """
    if request.method not in ("POST", "GET"):
        return redirect("home")

    esito = services.esegui_sync_bidirezionale()
    if esito.get("ok"):
        aggiornati = esito.get("polling", {}).get("aggiornati", 0)
        messages.success(
            request,
            f"⚡ Sincronizzazione bidirezionale completata con successo! "
            f"Prezzo e quota fissa aggiornati in HA · {aggiornati} periodi bolletta verificati con statistiche reali."
        )
    else:
        err = esito.get("esito_luce", {}).get("errore") or "Verifica configurazione HA"
        messages.warning(request, f"⚠️ Sincronizzazione parziale o con avvisi: {err}")

    return redirect(request.META.get("HTTP_REFERER", "home"))


def ha_telemetria_api(request):
    """API JSON per ottenere la telemetria istantanea e stato contatore da Home Assistant."""
    dati = ha_client.get_live_telemetry()
    return JsonResponse(dati)


def ha_device_breakdown_api(request):
    """API JSON per ottenere la ripartizione consumi dei singoli elettrodomestici/carichi."""
    dati = ha_client.get_device_breakdown()
    return JsonResponse(dati)


@csrf_exempt
@require_POST
def ha_push_api(request):
    """
    Endpoint PUSH cloud-ready per Home Assistant.
    Permette a qualsiasi istanza remota di Home Assistant (dietro firewall/NAT o senza IP pubblico)
    di inviare periodicamente la propria telemetria al Portale online e ricevere in risposta
    le tariffe marginali aggiornate (Luce & Gas) per aggiornare automaticamente gli aiutanti locali.
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "errore": "Payload JSON non valido."}, status=400)

    from django.utils import timezone
    oggi = timezone.localdate()
    t_curr = data.get("temperatura_interna")
    t_set = data.get("temp_setpoint")
    is_heating = data.get("caldaia_accesa", False)

    # Aggiorna record Netatmo / Termostato se presenti nel payload
    if t_curr is not None or t_set is not None:
        rec, _ = NetatmoRecordGiornaliero.objects.get_or_create(
            data=oggi,
            defaults={
                "secondi_caldaia": 1800 if is_heating else 0,
                "temp_interna_media": Decimal(str(round(float(t_curr), 2))) if t_curr is not None else None,
                "temp_setpoint_media": Decimal(str(round(float(t_set), 2))) if t_set is not None else None,
                "n_campionamenti": 1,
                "fonte_file": "HA Push Cloud Webhook",
            }
        )
        if not _:
            if t_curr is not None:
                rec.temp_interna_media = Decimal(str(round(float(t_curr), 2)))
            if t_set is not None:
                rec.temp_setpoint_media = Decimal(str(round(float(t_set), 2)))
            rec.n_campionamenti += 1
            rec.fonte_file = "HA Push Cloud Webhook"
            rec.save()

    # Prepara tariffe calcolate più recenti da inviare in risposta a HA
    ult_luce = BollettaElettrica.objects.order_by("-periodo_fine").first()
    ult_gas = BollettaGas.objects.order_by("-periodo_fine").first()

    tariffe_risposta = {
        "prezzo_energia_kwh": float(ult_luce.prezzo_marginale_medio) if (ult_luce and ult_luce.prezzo_marginale_medio) else 0.19261,
        "prezzo_gas_smc": float(ult_gas.prezzo_marginale_medio_smc) if (ult_gas and ult_gas.prezzo_marginale_medio_smc) else 0.665,
        "quota_fissa_giornaliera": 0.45,
        "aggiornato_il": oggi.isoformat(),
    }

    return JsonResponse({
        "ok": True,
        "messaggio": "Telemetria registrata con successo nel Portale Bollette.",
        "carichi_ricevuti": len(data.get("carichi", [])),
        "tariffe": tariffe_risposta,
    })


def dispositivi_ha_view(request):
    """
    Cruscotto dedicato al monitoraggio avanzato di Home Assistant e dei suoi dispositivi:
    - Ripartizione consumi singoli carichi (PC+NAS, TV, Forno, Cucina, Clima, Luci, ecc.)
    - Potenza istantanea assorbita in tempo reale (Watt)
    - Stima spesa in € per ogni apparecchio applicando la tariffa marginale
    - Grafici interattivi Chart.js (Donut per categoria, Bar chart carichi)
    - Controllo stato prese smart ed entità climate (Termostato Netatmo & Condizionatore)
    - Analisi baseload e consumi standby di fondo della casa
    """
    if not ha_client.is_configured():
        messages.warning(request, "Home Assistant non è configurato. Inserisci URL e Token nella sezione Configurazioni.")

    breakdown = ha_client.get_device_breakdown() if ha_client.is_configured() else {
        "carichi": [], "totale_kwh": 0.0, "totale_costo_eur": 0.0, "categorie": [], "baseload": {}
    }
    telemetria = ha_client.get_live_telemetry() if ha_client.is_configured() else {"online": False}
    climate_devices = ha_client.get_climate_devices() if ha_client.is_configured() else []
    cfg = ConfigurazioneSistema.get_config()

    # Dati preparati per Chart.js
    chart_cat_labels = [c["nome"] for c in breakdown.get("categorie", [])]
    chart_cat_kwh = [c["kwh"] for c in breakdown.get("categorie", [])]
    chart_cat_colors = [c["colore"] for c in breakdown.get("categorie", [])]

    top_carichi = [c for c in breakdown.get("carichi", []) if c["kwh"] > 0][:8]
    chart_dev_labels = [c["nome"] for c in top_carichi]
    chart_dev_kwh = [c["kwh"] for c in top_carichi]
    chart_dev_costo = [c["costo_eur"] for c in top_carichi]
    chart_dev_colors = [c["colore"] for c in top_carichi]

    context = {
        "cfg": cfg,
        "breakdown": breakdown,
        "telemetria": telemetria,
        "climate_devices": climate_devices,
        "chart_cat_labels_json": json.dumps(chart_cat_labels),
        "chart_cat_kwh_json": json.dumps(chart_cat_kwh),
        "chart_cat_colors_json": json.dumps(chart_cat_colors),
        "chart_dev_labels_json": json.dumps(chart_dev_labels),
        "chart_dev_kwh_json": json.dumps(chart_dev_kwh),
        "chart_dev_costo_json": json.dumps(chart_dev_costo),
        "chart_dev_colors_json": json.dumps(chart_dev_colors),
    }
    return render(request, "bollette/dispositivi_ha.html", context)


@require_POST
def ha_toggle_device_api(request):
    """API AJAX per commutare lo stato di una presa smart o switch in Home Assistant."""
    entity_id = request.POST.get("entity_id")
    if not entity_id:
        try:
            body = json.loads(request.body)
            entity_id = body.get("entity_id")
        except Exception as exc:
            logger.warning("Decodifica JSON payload switch toggle fallita: %s", exc, exc_info=True)

    if not entity_id:
        return JsonResponse({"ok": False, "errore": "entity_id non specificato."})

    ok = ha_client.toggle_switch(entity_id)
    new_state = None
    st = ha_client.get_state(entity_id)
    if st:
        new_state = st.get("state")

    return JsonResponse({"ok": ok, "entity_id": entity_id, "new_state": new_state})


@require_POST
def ha_climate_control_api(request):
    """API AJAX per impostare la temperatura target su un'entità climate (es. Netatmo)."""
    entity_id = request.POST.get("entity_id")
    temp_val = request.POST.get("temperature")
    if not entity_id or temp_val is None:
        try:
            body = json.loads(request.body)
            entity_id = body.get("entity_id")
            temp_val = body.get("temperature")
        except Exception as exc:
            logger.warning("Decodifica JSON payload climate control fallita: %s", exc, exc_info=True)

    if not entity_id or temp_val is None:
        return JsonResponse({"ok": False, "errore": "Parametri mancanti."})

    try:
        temp_float = float(temp_val)
    except ValueError:
        return JsonResponse({"ok": False, "errore": "Temperatura non valida."})

    ok = ha_client.set_climate_temperature(entity_id, temp_float)
    return JsonResponse({"ok": ok, "entity_id": entity_id, "temperature": temp_float})


@require_POST
def sincronizza_netatmo_da_ha_view(request):
    """Sincronizza lo stato attuale del termostato Netatmo da Home Assistant."""
    esito = ha_client.sincronizza_termostato_netatmo_da_ha()
    if esito.get("ok"):
        messages.success(
            request,
            f"🔥 Sincronizzazione da Home Assistant completata: "
            f"Temp. interna {esito.get('temp_interna')} °C · Setpoint {esito.get('temp_setpoint')} °C "
            f"(Stato caldaia: {esito.get('hvac_action')})."
        )
    else:
        messages.error(request, f"Errore sincronizzazione termostato da HA: {esito.get('errore')}")

    return redirect(request.META.get("HTTP_REFERER", "dispositivi_ha"))


# ===========================================================================
# NETATMO & RISCALDAMENTO SMART
# ===========================================================================
def netatmo_dashboard_view(request):
    """
    Dashboard di analisi termica Netatmo:
    - Upload di file CSV esportati da Netatmo Energy WebApp
    - Visualizzazione serie temporale ore caldaia vs temperature
    - Correlazione automatica con le bollette del gas archiviate
    """
    if request.method == "POST":
        form = UploadNetatmoForm(request.POST, request.FILES)
        if form.is_valid():
            file_netatmo = request.FILES["file_netatmo"]
            esito = netatmo_service.elabora_csv_netatmo(file_netatmo, nome_file=file_netatmo.name)
            if esito.get("ok"):
                messages.success(
                    request,
                    f"🔥 File Netatmo elaborato con successo! {esito['giorni_elaborati']} giorni acquisiti "
                    f"({esito['creati']} nuovi, {esito['aggiornati']} aggiornati). "
                    f"{esito['bollette_gas_aggiornate']} bollette gas collegate e ricalcolate."
                )
            else:
                messages.error(request, f"Errore durante l'elaborazione del file Netatmo: {esito.get('errore')}")
            return redirect("netatmo_dashboard")
    else:
        form = UploadNetatmoForm()

    statistiche = netatmo_service.ottieni_statistiche_netatmo()
    bollette_gas = list(BollettaGas.objects.all().order_by("-periodo_fine"))
    api_configurata = netatmo_api_client.is_configured()
    live_status = netatmo_api_client.get_live_status() if api_configurata else {"online": False}

    context = {
        "form": form,
        "stat": statistiche,
        "bollette_gas": bollette_gas,
        "api_configurata": api_configurata,
        "live_status": live_status,
    }
    return render(request, "bollette/netatmo.html", context)


@require_POST
def sincronizza_netatmo_api_view(request):
    """Scarica le misure storiche direttamente dal Cloud Netatmo via API REST."""
    if not netatmo_api_client.is_configured():
        messages.warning(request, "Credenziali API Netatmo non configurate. Configurale prima in 'Configurazioni' o nel pannello qui sotto.")
        return redirect("netatmo_dashboard")

    giorni = 90
    try:
        giorni = int(request.POST.get("giorni", 90))
    except ValueError:
        giorni = 90

    esito = netatmo_api_client.scarica_e_sincronizza_misure(giorni=giorni)
    if esito.get("ok"):
        messages.success(
            request,
            f"⚡ Sincronizzazione Netatmo Cloud API completata con successo! "
            f"{esito['giorni_ricevuti']} giorni acquisiti dal {esito['inizio']} al {esito['fine']} "
            f"({esito['creati']} nuovi, {esito['aggiornati']} aggiornati). "
            f"{esito['bollette_gas_aggiornate']} bollette gas ricalcolate."
        )
    else:
        messages.error(request, f"Errore durante la sincronizzazione Netatmo Cloud: {esito.get('errore')}")

    return redirect("netatmo_dashboard")


@require_POST
def sincronizza_netatmo_gas_view(request):
    """Ricalcola la correlazione tra tutti i record Netatmo e tutte le bollette gas archiviate."""
    aggiornate = netatmo_service.sincronizza_bollette_gas_con_netatmo()
    messages.success(request, f"Sincronizzazione completata: {aggiornate} bollette gas aggiornate con i dati del termostato.")
    return redirect("netatmo_dashboard")


@require_POST
def svuota_netatmo_view(request):
    """Elimina tutti i record giornalieri Netatmo e resetta le metriche sulle bollette gas."""
    conteggio = NetatmoRecordGiornaliero.objects.count()
    NetatmoRecordGiornaliero.objects.all().delete()
    BollettaGas.objects.update(
        ore_caldaia=None,
        temp_interna_media=None,
        smc_ora_caldaia=None,
        costo_ora_caldaia=None,
        smc_riscaldamento_stimati=None,
        smc_acs_cucina_stimati=None,
    )
    messages.info(request, f"Archivio Netatmo svuotato ({conteggio} giorni rimossi). Metriche gas reimpostate.")
    return redirect("netatmo_dashboard")


