"""
Modulo puro per il calcolo della fiscalità elettrica e delle componenti di costo (ADR-006).

Regole di calcolo:
- Nessun I/O e nessun accesso ORM: funzioni deterministiche e pure.
- Tutti i calcoli su importi monetari e consumi utilizzano esclusivamente `Decimal`.
- Arrotondamento standard ROUND_HALF_UP a due cifre decimali per imposte, recupero, IVA e totali,
  e a tre o sei cifre decimali per quote giornaliere e tariffe unitarie marginali.

Modello Fiscale (fonte: bollette reali Acea, ricalcolate al centesimo):
- F = franchigia accisa = 150 kWh/mese
- T = soglia recupero = 220 kWh/mese
- A = aliquota imposta erariale domestici = 0,0227 €/kWh (IVA esclusa)
- IVA = 10%
Per ogni mese di consumo k (kWh):
- riga_accisa   = round2( max(0, k − F) × A )
- riga_recupero = round2( min(F, max(0, k − T)) × A )   ← "Recupero imposta erariale"
- kWh_tassabili(k) = max(0, k − F) + min(F, max(0, k − T))
La franchigia si riduce progressivamente oltre T e si azzera a k = T + F = 370.
Il cap `min(F, …)` NON è osservato nelle bollette storiche (max 335 kWh/mese):
è implementato come da formula ministeriale/ARERA, segnalato come da verificare oltre 370 kWh.
Pendenza marginale: 0 se k ≤ F; 1 se F < k ≤ T; 2 se T < k < T+F; 1 se k ≥ T+F.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence

logger = logging.getLogger("bollette.fiscalita")

# Parametri di default del modello fiscale
DEFAULT_FRANCHIGIA_KWH = Decimal("150")
DEFAULT_SOGLIA_RECUPERO_KWH = Decimal("220")
DEFAULT_ALIQUOTA_ACCISA = Decimal("0.0227")
DEFAULT_ALIQUOTA_IVA = Decimal("0.10")

DUE_DECIMALI = Decimal("0.01")
TRE_DECIMALI = Decimal("0.001")
SEI_DECIMALI = Decimal("0.000001")


def _to_decimal(val: int | float | str | Decimal) -> Decimal:
    """Converte in modo sicuro il valore in Decimal."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def _round2(valore: Decimal) -> Decimal:
    """Arrotonda un importo monetario a 2 decimali con ROUND_HALF_UP."""
    return valore.quantize(DUE_DECIMALI, rounding=ROUND_HALF_UP)


def kwh_tassabili(
    kwh_mese: int | float | str | Decimal,
    *,
    franchigia: int | float | str | Decimal = DEFAULT_FRANCHIGIA_KWH,
    soglia_recupero: int | float | str | Decimal = DEFAULT_SOGLIA_RECUPERO_KWH,
) -> Decimal:
    """
    Calcola i kWh tassabili complessivi per un mese di consumo k:
        kWh_tassabili(k) = max(0, k - F) + min(F, max(0, k - T))
    """
    k = _to_decimal(kwh_mese)
    f = _to_decimal(franchigia)
    t = _to_decimal(soglia_recupero)

    quota_eccedente_franchigia = max(Decimal("0"), k - f)
    quota_recupero = min(f, max(Decimal("0"), k - t))
    return quota_eccedente_franchigia + quota_recupero


def imposte_mese(
    kwh_mese: int | float | str | Decimal,
    *,
    franchigia: int | float | str | Decimal = DEFAULT_FRANCHIGIA_KWH,
    soglia_recupero: int | float | str | Decimal = DEFAULT_SOGLIA_RECUPERO_KWH,
    aliquota: Decimal = DEFAULT_ALIQUOTA_ACCISA,
) -> dict[str, Decimal]:
    """
    Calcola le righe fiscali per un mese solare di consumo:
    - riga_accisa   = round2( max(0, k − F) × A )
    - riga_recupero = round2( min(F, max(0, k − T)) × A )
    - kwh_tassabili = quota_eccedente + quota_recupero

    Ritorna un dizionario con chiavi 'accisa', 'recupero', 'kwh_tassabili'.
    Ciascuna riga monetaria è arrotondata separatamente a 2 decimali (HALF_UP).
    """
    k = _to_decimal(kwh_mese)
    f = _to_decimal(franchigia)
    t = _to_decimal(soglia_recupero)
    a = _to_decimal(aliquota)

    quota_eccedente = max(Decimal("0"), k - f)
    quota_recupero = min(f, max(Decimal("0"), k - t))

    riga_accisa = _round2(quota_eccedente * a)
    riga_recupero = _round2(quota_recupero * a)
    tassabili = quota_eccedente + quota_recupero

    return {
        "accisa": riga_accisa,
        "recupero": riga_recupero,
        "kwh_tassabili": tassabili,
    }


def pendenza_accisa(
    kwh_mese: int | float | str | Decimal,
    *,
    franchigia: int | float | str | Decimal = DEFAULT_FRANCHIGIA_KWH,
    soglia_recupero: int | float | str | Decimal = DEFAULT_SOGLIA_RECUPERO_KWH,
) -> int:
    """
    Ritorna la pendenza marginale dei kWh tassabili per ogni kWh consumato in più:
    - 0 se k ≤ F
    - 1 se F < k ≤ T
    - 2 se T < k < T + F
    - 1 se k ≥ T + F
    """
    k = _to_decimal(kwh_mese)
    f = _to_decimal(franchigia)
    t = _to_decimal(soglia_recupero)
    t_piu_f = t + f

    if k <= f:
        return 0
    if k <= t:
        return 1
    if k < t_piu_f:
        return 2
    return 1


def prezzo_marginale_al_consumo(
    base_iva_incl: Decimal | float | str,
    accisa_iva_incl: Decimal | float | str,
    kwh_mese_cumulati: int | float | str | Decimal,
    *,
    franchigia: int | float | str | Decimal = DEFAULT_FRANCHIGIA_KWH,
    soglia_recupero: int | float | str | Decimal = DEFAULT_SOGLIA_RECUPERO_KWH,
) -> Decimal:
    """
    Calcola il prezzo al consumo del prossimo kWh (€/kWh IVA inclusa):
        prezzo_marginale = base + accisa × pendenza
    """
    base = _to_decimal(base_iva_incl)
    accisa = _to_decimal(accisa_iva_incl)
    pendenza = pendenza_accisa(
        kwh_mese_cumulati, franchigia=franchigia, soglia_recupero=soglia_recupero
    )
    valore = base + accisa * Decimal(pendenza)
    return valore.quantize(SEI_DECIMALI, rounding=ROUND_HALF_UP)


def totale_bolletta_luce(
    vendita: Decimal | float | str,
    rete: Decimal | float | str,
    oneri: Decimal | float | str,
    kwh_per_mese: Sequence[int | float | str | Decimal],
    *,
    franchigia: int | float | str | Decimal = DEFAULT_FRANCHIGIA_KWH,
    soglia_recupero: int | float | str | Decimal = DEFAULT_SOGLIA_RECUPERO_KWH,
    aliquota: Decimal = DEFAULT_ALIQUOTA_ACCISA,
    iva: Decimal = DEFAULT_ALIQUOTA_IVA,
) -> dict[str, Decimal]:
    """
    Calcola il totale fattura di una bolletta elettrica aggregando le componenti nette,
    calcolando le righe fiscali mese per mese e applicando l'IVA all'imponibile totale.

    Ritorna un dict con:
    - 'accisa': totale imposta erariale
    - 'recupero': totale recupero imposta erariale
    - 'imponibile': vendita + rete + oneri + accisa + recupero
    - 'iva': imponibile × aliquota IVA (round2)
    - 'totale': imponibile + iva
    """
    v = _to_decimal(vendita)
    r = _to_decimal(rete)
    o = _to_decimal(oneri)
    aliquota_iva = _to_decimal(iva)

    tot_accisa = Decimal("0.00")
    tot_recupero = Decimal("0.00")

    for kwh in kwh_per_mese:
        d_imp = imposte_mese(
            kwh,
            franchigia=franchigia,
            soglia_recupero=soglia_recupero,
            aliquota=aliquota,
        )
        tot_accisa += d_imp["accisa"]
        tot_recupero += d_imp["recupero"]

    imponibile = _round2(v + r + o + tot_accisa + tot_recupero)
    iva_calc = _round2(imponibile * aliquota_iva)
    totale = imponibile + iva_calc

    return {
        "accisa": tot_accisa,
        "recupero": tot_recupero,
        "imponibile": imponibile,
        "iva": iva_calc,
        "totale": totale,
    }


def quota_fissa_giornaliera(
    quota_fissa_netta_periodo: Decimal | float | str,
    giorni: int,
    iva: Decimal = DEFAULT_ALIQUOTA_IVA,
) -> Decimal:
    """
    Calcola la quota fissa giornaliera comprensiva di IVA:
        quota_fissa_giornaliera = quota_fissa_netta_periodo × (1 + IVA) / giorni
    Quantizzato a 3 decimali (ROUND_HALF_UP).
    """
    q_netta = _to_decimal(quota_fissa_netta_periodo)
    if giorni <= 0:
        giorni = 1
    aliquota_iva = _to_decimal(iva)
    quota_lorda = q_netta * (Decimal("1") + aliquota_iva)
    return (quota_lorda / Decimal(giorni)).quantize(TRE_DECIMALI, rounding=ROUND_HALF_UP)


def _to_date_pure(val: Any) -> date | None:
    """Converte un valore in oggetto date (formati ISO YYYY-MM-DD o IT DD/MM/YYYY)."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        val = val.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                pass
    return None


def _spezza_periodo_in_mesi_solari(d_ini: date, d_fin: date) -> list[tuple[date, date]]:
    """Suddivide un intervallo temporale arbitrario in segmenti di mese solare."""
    mesi: list[tuple[date, date]] = []
    curr = d_ini
    while curr <= d_fin:
        anno = curr.year
        mese = curr.month
        _, ultimo_giorno = calendar.monthrange(anno, mese)
        fine_mese = date(anno, mese, ultimo_giorno)
        fine_intervallo = min(fine_mese, d_fin)
        mesi.append((curr, fine_intervallo))
        curr = fine_intervallo + timedelta(days=1)
    return mesi


def ripartisci_consumi_per_mese(
    periodo_inizio: date,
    periodo_fine: date,
    kwh_totali: int | float | str | Decimal,
    consumi_mensili: Sequence[dict[str, Any]] | None = None,
) -> list[tuple[date, date, Decimal, bool]]:
    """
    Ripartisce i consumi totali di una bolletta sui singoli mesi solari compresi nel periodo.

    Ritorna una lista di tuple:
        [(data_inizio_mese, data_fine_mese, kwh_mese, stimato)]

    - Se `consumi_mensili` è presente e valorizzato (es. estratti dal parser letture e consumi),
      utilizza direttamente tali misurazioni. Il flag `stimato` è True se il tipo è 'Stimato',
      False se 'Effettivo' o 'Rilevato'.
    - Altrimenti: esegue un riparto pro-rata sui giorni solari di ciascun mese intersecato.
      L'ultimo mese riceve il residuo per garantire che la somma coincida esattamente con kwh_totali.
      Imposta `stimato=True` per tutti i mesi e registra un `logger.warning`.
    """
    tot_kwh = _to_decimal(kwh_totali)

    # 1. Se disponiamo dei consumi mensili espliciti estratti dal documento
    if consumi_mensili:
        risultato: list[tuple[date, date, Decimal, bool]] = []
        for blocco in consumi_mensili:
            d_ini = _to_date_pure(blocco.get("inizio")) or periodo_inizio
            d_fin = _to_date_pure(blocco.get("fine")) or periodo_fine
            k = _to_decimal(blocco.get("kwh", 0))
            tipo_str = str(blocco.get("tipo", "Effettivo")).strip().lower()
            stimato = tipo_str == "stimato"
            risultato.append((d_ini, d_fin, k, stimato))
        return risultato

    # 2. Ripiego degradato: pro-rata sui giorni per mese solare
    logger.warning(
        "Ripartizione pro-rata consumi per il periodo %s -> %s (kWh totali=%s): "
        "dati disaggregati mensili assenti, calcolo degradato su base giornaliera.",
        periodo_inizio, periodo_fine, tot_kwh
    )

    intervalli = _spezza_periodo_in_mesi_solari(periodo_inizio, periodo_fine)
    if not intervalli:
        return [(periodo_inizio, periodo_fine, tot_kwh, True)]

    tot_giorni = sum((f - i).days + 1 for i, f in intervalli)
    if tot_giorni <= 0:
        tot_giorni = 1

    risultato_prorata: list[tuple[date, date, Decimal, bool]] = []
    somma_cumulata = Decimal("0")

    for idx, (d_ini, d_fin) in enumerate(intervalli):
        giorni_int = Decimal((d_fin - d_ini).days + 1)
        if idx == len(intervalli) - 1:
            # Ultimo mese: resto per far quadrare la somma esattamente con kwh_totali
            k_mese = tot_kwh - somma_cumulata
        else:
            k_mese = (tot_kwh * giorni_int / Decimal(tot_giorni)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            somma_cumulata += k_mese
        risultato_prorata.append((d_ini, d_fin, k_mese, True))

    return risultato_prorata

