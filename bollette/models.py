"""
Modelli del tool bollette: Luce + Gas, tracciamento Home Assistant, correlazione climatica e configurazione dinamica.
"""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.db import models


def _to_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(val.strip(), fmt).date()
            except ValueError:
                pass
    return None


class ConfigurazioneSistema(models.Model):
    """
    Configurazione centralizzata del portale (Bridge HA, Open-Meteo, Ollama).
    Modificabile direttamente dalla UI web e dall'Admin senza dover modificare file .env o riavviare.
    """
    class ModalitaPrezzo(models.TextChoices):
        MEDIA_MOBILE_12M = "media_mobile_12m", "Media mobile 12 mesi pesata"
        ULTIMA_BOLLETTA = "ultima_bolletta", "Ultima bolletta parsata"

    # --- Home Assistant ---
    ha_base_url = models.CharField(
        "URL Base Home Assistant", max_length=255, default="",
        help_text="Es. https://homeassistant.local:8123 (senza slash finale)."
    )
    ha_token = models.TextField(
        "Long-Lived Access Token HA", blank=True, default="",
        help_text="Token di autenticazione generato nel profilo utente di Home Assistant."
    )
    ha_tls_verify = models.BooleanField(
        "Verifica certificato TLS", default=True,
        help_text="Disabilita se usi un certificato self-signed su IP locale senza CA custom."
    )
    ha_ca_bundle = models.CharField(
        "Path CA Bundle / Certificato", max_length=255, blank=True, default="",
        help_text="Percorso al file .crt per validare certificati interni (opzionale)."
    )
    prezzo_ha_mode = models.CharField(
        "Modalità calcolo prezzo HA", max_length=32,
        choices=ModalitaPrezzo.choices, default=ModalitaPrezzo.MEDIA_MOBILE_12M,
        help_text="Come calcolare il prezzo inviato a Home Assistant."
    )
    ha_entity_kwh_consumo = models.CharField(
        "Entity consumi Luce (kWh)", max_length=128, blank=True, default="",
        help_text="Sensore cumulativo dei kWh per validazione (es. sensor.grid_consumption)."
    )
    ha_entity_smc_consumo = models.CharField(
        "Entity consumi Gas (Smc)", max_length=128, blank=True, default="",
        help_text="Sensore dei consumi gas per validazione (es. sensor.gas_consumption)."
    )
    ha_entity_prezzo_singolo = models.CharField(
        "Entity helper prezzo Luce", max_length=128, default="input_number.prezzo_energia_kwh",
        help_text="Helper HA in cui scrivere il costo marginale luce €/kWh."
    )
    ha_entity_prezzo_gas = models.CharField(
        "Entity helper prezzo Gas", max_length=128, default="input_number.prezzo_gas_smc",
        help_text="Helper HA in cui scrivere il costo marginale gas €/Smc."
    )
    ha_entity_pun = models.CharField(
        "Entity sensore PUN in HA", max_length=128, blank=True, default="sensor.pun_mono",
        help_text="Sensore PUN in HA (opzionale, es. da integrazione pun_sensor)."
    )
    ha_sync_auto = models.BooleanField(
        "Sync automatica dopo ogni salvataggio", default=False,
        help_text="Se attivo, pubblica subito il prezzo in HA appena viene creata una bolletta."
    )

    # --- Open-Meteo (Normalizzazione Climatica) ---
    meteo_citta = models.CharField(
        "Nome Località", max_length=64, default="Roma",
        help_text="Città di riferimento per l'archivio meteo."
    )
    meteo_latitude = models.DecimalField(
        "Latitudine", max_digits=8, decimal_places=4, default=Decimal("41.9028"),
        help_text="Latitudine geografica per le temperature storiche."
    )
    meteo_longitude = models.DecimalField(
        "Longitudine", max_digits=8, decimal_places=4, default=Decimal("12.4964"),
        help_text="Longitudine geografica."
    )

    # --- Ollama LLM (Parsing Bollette con GPU) ---
    ollama_base_url = models.CharField(
        "URL Server Ollama", max_length=255, default="http://127.0.0.1:11434",
        help_text="Indirizzo del server Ollama (es. http://192.168.1.X:11434 sul PC con RTX 5070 Ti)."
    )
    ollama_model = models.CharField(
        "Modello LLM Ollama", max_length=64, default="qwen2.5:latest",
        help_text="Nome del modello scaricato in Ollama (es. qwen2.5:latest, llama3.2, ecc.)."
    )

    # --- Netatmo Connect API (Integrazione Diretta Cloud) ---
    netatmo_client_id = models.CharField(
        "Netatmo Client ID", max_length=128, blank=True, default="",
        help_text="Ottenuto registrando un'app gratuita su dev.netatmo.com/apps."
    )
    netatmo_client_secret = models.CharField(
        "Netatmo Client Secret", max_length=128, blank=True, default="",
        help_text="Segreto API dell'applicazione Netatmo."
    )
    netatmo_refresh_token = models.TextField(
        "Netatmo Refresh Token", blank=True, default="",
        help_text="Token OAuth2 ottenuto dal 'Token Generator' su dev.netatmo.com (scope: read_thermostat)."
    )
    netatmo_access_token = models.TextField(
        "Access Token Netatmo (Cache)", blank=True, default="",
        help_text="Token corrente rinnovato automaticamente via refresh token."
    )
    netatmo_token_expires_at = models.DateTimeField(
        "Scadenza Access Token", null=True, blank=True,
    )
    netatmo_home_id = models.CharField(
        "ID Casa Netatmo", max_length=64, blank=True, default="",
        help_text="Identificativo dell'abitazione (rilevato in automatico via API)."
    )
    netatmo_device_id = models.CharField(
        "MAC Relè Caldaia", max_length=64, blank=True, default="",
        help_text="Indirizzo MAC del relè caldaia (rilevato in automatico via API)."
    )
    netatmo_module_id = models.CharField(
        "ID Modulo Termostato", max_length=64, blank=True, default="",
        help_text="Indirizzo MAC del termostato ambiente (rilevato in automatico via API)."
    )

    aggiornato_il = models.DateTimeField("Ultimo aggiornamento", auto_now=True)

    class Meta:
        verbose_name = "Configurazione del Portale"
        verbose_name_plural = "Configurazione del Portale"

    def __str__(self):
        return f"Configurazione Portale ({self.ha_base_url or 'HA non impostato'})"

    @classmethod
    def get_config(cls) -> "ConfigurazioneSistema":
        """Ritorna l'istanza singleton, creandola con i default da settings/.env se non esiste."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(
                ha_base_url=getattr(settings, "HA_BASE_URL", ""),
                ha_token=getattr(settings, "HA_TOKEN", ""),
                ha_tls_verify=getattr(settings, "HA_TLS_VERIFY", True),
                ha_ca_bundle=getattr(settings, "HA_CA_BUNDLE", "") or "",
                prezzo_ha_mode=getattr(settings, "PREZZO_HA_MODE", "media_mobile_12m"),
                ha_entity_kwh_consumo=getattr(settings, "HA_ENTITY_KWH_CONSUMO", ""),
                ha_entity_smc_consumo=getattr(settings, "HA_ENTITY_SMC_CONSUMO", ""),
                ha_entity_prezzo_singolo=getattr(settings, "HA_ENTITY_PREZZO_SINGOLO", "input_number.prezzo_energia_kwh"),
                ha_entity_prezzo_gas=getattr(settings, "HA_ENTITY_PREZZO_GAS", "input_number.prezzo_gas_smc"),
                ha_entity_pun=getattr(settings, "HA_ENTITY_PUN", "sensor.pun_mono"),
                ha_sync_auto=getattr(settings, "HA_SYNC_AUTO", False),
                meteo_latitude=Decimal(str(getattr(settings, "METEO_LATITUDE", 41.9028))),
                meteo_longitude=Decimal(str(getattr(settings, "METEO_LONGITUDE", 12.4964))),
                ollama_base_url=getattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                ollama_model=getattr(settings, "OLLAMA_MODEL", "qwen2.5:latest"),
            )
        return obj


class BollettaElettrica(models.Model):
    """Una bolletta elettrica archiviata, con prezzi marginali, documento allegato e metriche climatiche."""

    # --- Campi base & Archivio Documentale ---
    fornitore = models.CharField("Fornitore", max_length=64, default="Acea")
    pod = models.CharField("POD", max_length=20, blank=True)
    numero_fattura = models.CharField("Numero fattura", max_length=64, blank=True)
    data_emissione = models.DateField("Data emissione fattura", null=True, blank=True)
    periodo_inizio = models.DateField("Inizio periodo")
    periodo_fine = models.DateField("Fine periodo")
    importo_totale = models.DecimalField(
        "Importo totale (€)", max_digits=10, decimal_places=2,
        null=True, blank=True,
    )
    file_bolletta = models.FileField(
        "File PDF bolletta", upload_to="bollette_pdf/luce/", null=True, blank=True,
        help_text="PDF originale della bolletta conservato nell'archivio."
    )
    note = models.TextField("Note e osservazioni", blank=True)

    # --- Campi calcolati del prezzo marginale (ADR-002) ---
    kwh_fatturati = models.IntegerField(
        "kWh fatturati", default=0,
        help_text="kWh di energia fatturati nel periodo della bolletta.",
    )
    consumi_mensili = models.JSONField(
        "Consumi mensili disaggregati",
        null=True, blank=True,
        help_text="Lista di consumi mensili [{'inizio': 'YYYY-MM-DD', 'fine': 'YYYY-MM-DD', 'kwh': 123, 'tipo': 'Effettivo'|'Stimato'}].",
    )
    quota_fissa_netta_periodo = models.DecimalField(
        "Quota fissa netta periodo (€)", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Somma netta di quota fissa vendita, rete e potenza del periodo (es. 39.90 €).",
    )
    totale_da_pagare = models.DecimalField(
        "Totale da pagare fattura (€)", max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Totale documento comprensivo di partite addizionali come Canone TV.",
    )
    canone_rai = models.DecimalField(
        "Canone abbonamento TV (€)", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Quota Canone RAI addebitata in bolletta.",
    )
    quota_fissa_mensile = models.DecimalField(
        "Quota fissa mensile (€/mese)", max_digits=8, decimal_places=2,
        default=Decimal("10.00"),
        help_text="Costi fissi mensili di commercializzazione e vendita (CCV/PCV).",
    )
    prezzo_marginale_base = models.DecimalField(
        "Prezzo marginale base (€/kWh)", max_digits=10, decimal_places=6,
        default=Decimal("0"),
        help_text="€/kWh sotto la soglia accisa (accisa = 0). Es. Acea: 0.193400",
    )
    accisa_marginale = models.DecimalField(
        "Accisa marginale (€/kWh)", max_digits=10, decimal_places=6,
        default=Decimal("0"),
        help_text="€/kWh aggiuntivi sopra la soglia accisa. Es. Acea: 0.025000",
    )
    soglia_accisa_kwh = models.IntegerField(
        "Soglia accisa (kWh/mese)", default=150,
        help_text="Soglia oltre cui scatta l'accisa marginale (3 kW residente: 150).",
    )
    sopra_soglia_accisa = models.BooleanField(
        "Sopra soglia accisa", default=False,
        help_text="True se kwh_fatturati supera la soglia accisa.",
    )
    prezzo_marginale_medio = models.DecimalField(
        "Prezzo marginale medio (€/kWh)", max_digits=10, decimal_places=6,
        default=Decimal("0"),
        help_text="Variabile A reale del mese: €/kWh marginale medio applicato.",
    )

    # --- Metriche Climatiche (Open-Meteo API) ---
    gradi_giorno = models.DecimalField(
        "Gradi Giorno (HDD)", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Gradi Giorno riscaldamento calcolati da Open-Meteo per il periodo.",
    )
    temperatura_media = models.DecimalField(
        "Temp. Media (°C)", max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text="Temperatura media esterna registrata da Open-Meteo.",
    )
    kwh_per_gradi_giorno = models.DecimalField(
        "kWh / Gradi Giorno", max_digits=8, decimal_places=4,
        null=True, blank=True,
        help_text="Indice di consumo normalizzato sul meteo.",
    )

    # --- Benchmark di Mercato ---
    pun_medio_periodo = models.DecimalField(
        "PUN medio (€/kWh)", max_digits=10, decimal_places=6,
        null=True, blank=True,
        help_text="Prezzo Unico Nazionale medio nel periodo.",
    )
    spread_pun = models.DecimalField(
        "Spread vs PUN (€/kWh)", max_digits=10, decimal_places=6,
        null=True, blank=True,
        help_text="Differenza tra prezzo marginale della bolletta e PUN.",
    )

    # --- Tracciamento sync verso Home Assistant ---
    prezzo_pubblicato_ha = models.DecimalField(
        "Prezzo pubblicato in HA (€/kWh)", max_digits=10, decimal_places=6,
        null=True, blank=True,
        help_text="Valore effettivamente inviato a Home Assistant.",
    )
    ha_sync_at = models.DateTimeField(
        "Ultima sync HA", null=True, blank=True,
        help_text="Istante dell'ultima sync verso HA (riuscita o tentata).",
    )
    ha_sync_ok = models.BooleanField(
        "Sync HA riuscita", default=False,
        help_text="True se l'ultima pubblicazione in HA e andata a buon fine.",
    )

    # --- Validazione e Confronto vs Contatore HA ---
    kwh_misurati_ha = models.DecimalField(
        "kWh misurati HA", max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Consumo registrato dal contatore / sensore Home Assistant nel periodo.",
    )
    scostamento_ha_pct = models.DecimalField(
        "Scostamento HA (%)", max_digits=6, decimal_places=2,
        null=True, blank=True,
        help_text="Differenza percentuale tra consumo fatturato e contatore HA.",
    )
    fonte_misura_ha = models.CharField(
        "Fonte misura HA", max_length=64, blank=True, default="",
        help_text="Es: 'Polling REST API' o 'Import CSV'",
    )

    class Meta:
        verbose_name = "Bolletta elettrica"
        verbose_name_plural = "Bollette elettriche"
        ordering = ["-periodo_fine"]

    def __str__(self):
        return f"Bolletta Elettrica {self.fornitore} {self.periodo_inizio} -> {self.periodo_fine}"

    def clean(self):
        super().clean()
        d_ini = _to_date(self.periodo_inizio)
        d_fin = _to_date(self.periodo_fine)
        if d_ini and d_fin:
            if d_fin < d_ini:
                self.periodo_inizio, self.periodo_fine = d_fin, d_ini
            else:
                self.periodo_inizio, self.periodo_fine = d_ini, d_fin

    @property
    def giorni_periodo(self) -> int:
        d_ini = _to_date(self.periodo_inizio)
        d_fin = _to_date(self.periodo_fine)
        if d_ini and d_fin:
            return max(1, (d_fin - d_ini).days + 1)
        return 30

    @property
    def costo_unitario_totale(self) -> Decimal | None:
        """Costo unitario reale tutto compreso (€/kWh effettivo = totale fattura / kWh)."""
        if self.importo_totale and self.kwh_fatturati and self.kwh_fatturati > 0:
            return (self.importo_totale / Decimal(self.kwh_fatturati)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return None

    @property
    def kwh_giorno(self) -> float:
        """Consumo medio giornaliero in kWh."""
        g = self.giorni_periodo
        return round((self.kwh_fatturati or 0) / g, 2) if g > 0 else 0.0

    @property
    def spesa_giorno(self) -> Decimal | None:
        """Spesa media giornaliera in €."""
        if self.importo_totale:
            return (self.importo_totale / Decimal(self.giorni_periodo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return None


class BollettaGas(models.Model):
    """Una bolletta del gas metano archiviata con costi marginali, coefficiente C, documento allegato e gradi giorno."""

    # --- Campi base & Archivio Documentale ---
    fornitore = models.CharField("Fornitore", max_length=64, default="Acea")
    pdr = models.CharField("PDR", max_length=20, blank=True)
    numero_fattura = models.CharField("Numero fattura", max_length=64, blank=True)
    data_emissione = models.DateField("Data emissione fattura", null=True, blank=True)
    periodo_inizio = models.DateField("Inizio periodo")
    periodo_fine = models.DateField("Fine periodo")
    importo_totale = models.DecimalField(
        "Importo totale (€)", max_digits=10, decimal_places=2,
        null=True, blank=True,
    )
    file_bolletta = models.FileField(
        "File PDF bolletta", upload_to="bollette_pdf/gas/", null=True, blank=True,
        help_text="PDF originale della bolletta gas archiviato."
    )
    note = models.TextField("Note e osservazioni", blank=True)

    # --- Parametri tecnici e consumo gas ---
    smc_fatturati = models.DecimalField(
        "Smc fatturati", max_digits=10, decimal_places=2,
        default=Decimal("0"),
        help_text="Metri cubi standard fatturati nel periodo.",
    )
    coeff_c = models.DecimalField(
        "Coefficiente C", max_digits=8, decimal_places=6,
        default=Decimal("1.000000"),
        help_text="Coefficiente di conversione volumetrico del contatore (es. 1.0272).",
    )
    potere_calorifico_pcs = models.DecimalField(
        "PCS (GJ/Smc)", max_digits=8, decimal_places=6,
        default=Decimal("0.038520"),
        help_text="Potere calorifico superiore convenzionale.",
    )

    # --- Componenti tariffarie gas ---
    quota_materia_prima_smc = models.DecimalField(
        "Materia prima gas (€/Smc)", max_digits=10, decimal_places=6,
        default=Decimal("0.450000"),
        help_text="Prezzo base componente energia gas (€/Smc).",
    )
    quota_fissa_annua = models.DecimalField(
        "Quota fissa annua (€/anno)", max_digits=8, decimal_places=2,
        default=Decimal("120.00"),
        help_text="Costi fissi commercializzazione e vendita (QVD).",
    )
    accisa_smc = models.DecimalField(
        "Accisa e addizionale (€/Smc)", max_digits=10, decimal_places=6,
        default=Decimal("0.180000"),
        help_text="Imposte al consumo per Smc.",
    )
    prezzo_marginale_medio_smc = models.DecimalField(
        "Prezzo marginale (€/Smc)", max_digits=10, decimal_places=6,
        default=Decimal("0"),
        help_text="Costo variabile marginale totale per Smc (materia + oneri var + accise).",
    )

    # --- Metriche Climatiche (Open-Meteo API) ---
    gradi_giorno = models.DecimalField(
        "Gradi Giorno (HDD)", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Gradi Giorno calcolati per il periodo da Open-Meteo.",
    )
    temperatura_media = models.DecimalField(
        "Temp. Media (°C)", max_digits=5, decimal_places=2,
        null=True, blank=True,
    )
    smc_per_gradi_giorno = models.DecimalField(
        "Smc / Gradi Giorno", max_digits=8, decimal_places=4,
        null=True, blank=True,
        help_text="Indice di efficienza riscaldamento: Smc consumati per Grado Giorno.",
    )

    # --- Benchmark di Mercato ---
    psv_medio_periodo = models.DecimalField(
        "PSV medio (€/Smc)", max_digits=10, decimal_places=6,
        null=True, blank=True,
        help_text="Punto di Scambio Virtuale medio del periodo.",
    )
    spread_psv = models.DecimalField(
        "Spread vs PSV (€/Smc)", max_digits=10, decimal_places=6,
        null=True, blank=True,
    )

    # --- Tracciamento sync verso Home Assistant ---
    prezzo_pubblicato_ha = models.DecimalField(
        "Prezzo pubblicato in HA (€/Smc)", max_digits=10, decimal_places=6,
        null=True, blank=True,
        help_text="Valore €/Smc inviato a Home Assistant.",
    )
    ha_sync_at = models.DateTimeField(
        "Ultima sync HA", null=True, blank=True,
    )
    ha_sync_ok = models.BooleanField(
        "Sync HA riuscita", default=False,
    )

    # --- Validazione e Confronto vs Contatore HA ---
    smc_misurati_ha = models.DecimalField(
        "Smc misurati HA", max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Consumo registrato dal sensore gas Home Assistant nel periodo.",
    )
    scostamento_ha_pct = models.DecimalField(
        "Scostamento HA (%)", max_digits=6, decimal_places=2,
        null=True, blank=True,
        help_text="Differenza percentuale tra consumo fatturato e sensore HA.",
    )
    fonte_misura_ha = models.CharField(
        "Fonte misura HA", max_length=64, blank=True, default="",
        help_text="Es: 'Polling REST API' o 'Import CSV'",
    )

    # --- Metriche Netatmo & Riscaldamento Caldaia ---
    ore_caldaia = models.DecimalField(
        "Ore caldaia Netatmo", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Ore totali di accensione caldaia registrate da Netatmo nel periodo.",
    )
    temp_interna_media = models.DecimalField(
        "Temp. interna Netatmo (°C)", max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text="Temperatura ambiente interna media registrata da Netatmo nel periodo.",
    )
    smc_ora_caldaia = models.DecimalField(
        "Smc / ora caldaia", max_digits=8, decimal_places=4,
        null=True, blank=True,
        help_text="Consumo orario stimato del bruciatore (Smc per ora di fiamma).",
    )
    costo_ora_caldaia = models.DecimalField(
        "Costo orario caldaia (€/h)", max_digits=8, decimal_places=2,
        null=True, blank=True,
        help_text="Costo medio per ogni ora di riscaldamento acceso (€/h).",
    )
    smc_riscaldamento_stimati = models.DecimalField(
        "Smc Riscaldamento stimati", max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Smc stimati per il riscaldamento dell'ambiente.",
    )
    smc_acs_cucina_stimati = models.DecimalField(
        "Smc ACS/Cucina stimati", max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="Smc stimati per acqua calda sanitaria e cucina (fabbisogno continuo).",
    )

    class Meta:
        verbose_name = "Bolletta gas"
        verbose_name_plural = "Bollette gas"
        ordering = ["-periodo_fine"]

    def __str__(self):
        return f"Bolletta Gas {self.fornitore} {self.periodo_inizio} -> {self.periodo_fine}"

    def clean(self):
        super().clean()
        d_ini = _to_date(self.periodo_inizio)
        d_fin = _to_date(self.periodo_fine)
        if d_ini and d_fin:
            if d_fin < d_ini:
                self.periodo_inizio, self.periodo_fine = d_fin, d_ini
            else:
                self.periodo_inizio, self.periodo_fine = d_ini, d_fin

    @property
    def giorni_periodo(self) -> int:
        d_ini = _to_date(self.periodo_inizio)
        d_fin = _to_date(self.periodo_fine)
        if d_ini and d_fin:
            return max(1, (d_fin - d_ini).days + 1)
        return 30

    @property
    def costo_unitario_totale(self) -> Decimal | None:
        """Costo unitario reale tutto compreso (€/Smc effettivo = totale fattura / Smc)."""
        if self.importo_totale and self.smc_fatturati and self.smc_fatturati > 0:
            return (self.importo_totale / Decimal(str(self.smc_fatturati))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return None

    @property
    def smc_giorno(self) -> float:
        """Consumo medio giornaliero in Smc."""
        g = self.giorni_periodo
        return round(float(self.smc_fatturati or 0) / g, 2) if g > 0 else 0.0

    @property
    def spesa_giorno(self) -> Decimal | None:
        """Spesa media giornaliera in €."""
        if self.importo_totale:
            return (self.importo_totale / Decimal(self.giorni_periodo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return None


class NetatmoRecordGiornaliero(models.Model):
    """
    Riepilogo giornaliero dell'attività termica da Netatmo Smart Thermostat.
    Memorizza ore/secondi di fiamma caldaia, temperatura media interna e setpoint medio.
    """
    data = models.DateField("Data", unique=True, db_index=True)
    secondi_caldaia = models.IntegerField(
        "Secondi accensione caldaia", default=0,
        help_text="Secondi totali di accensione caldaia nel corso delle 24 ore.",
    )
    temp_interna_media = models.DecimalField(
        "Temp. interna media (°C)", max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text="Temperatura ambiente media registrata dalla sonda Netatmo.",
    )
    temp_setpoint_media = models.DecimalField(
        "Temp. setpoint media (°C)", max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text="Temperatura impostata (target/setpoint) media.",
    )
    n_campionamenti = models.IntegerField(
        "Numero campionamenti", default=0,
        help_text="Numero di rilevazioni aggregate per la giornata.",
    )
    fonte_file = models.CharField(
        "Nome file origine", max_length=255, blank=True, default="",
    )
    creato_il = models.DateTimeField("Creato il", auto_now_add=True)
    aggiornato_il = models.DateTimeField("Aggiornato il", auto_now=True)

    class Meta:
        verbose_name = "Record Giornaliero Netatmo"
        verbose_name_plural = "Record Giornalieri Netatmo"
        ordering = ["-data"]

    def __str__(self):
        return f"Netatmo {self.data}: {self.ore_caldaia}h caldaia, {self.temp_interna_media or '—'}°C"

    @property
    def ore_caldaia(self) -> float:
        """Ore di accensione caldaia calcolate dai secondi."""
        return round(float(self.secondi_caldaia) / 3600.0, 2)


class HaSyncLog(models.Model):
    """Registro di ogni scrittura tentata verso Home Assistant (tracciabilità)."""

    class Modalita(models.TextChoices):
        ULTIMA_BOLLETTA = "ultima_bolletta", "Ultima bolletta"
        MEDIA_MOBILE_12M = "media_mobile_12m", "Media mobile 12 mesi"
        MANUALE = "manuale", "Manuale / on-demand"

    creato_il = models.DateTimeField("Timestamp", auto_now_add=True)
    bolletta = models.ForeignKey(
        BollettaElettrica, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="ha_sync_logs",
        verbose_name="Bolletta Elettrica",
    )
    bolletta_gas = models.ForeignKey(
        BollettaGas, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="ha_sync_logs",
        verbose_name="Bolletta Gas",
    )
    modalita = models.CharField(
        "Modalità", max_length=20, choices=Modalita.choices,
    )
    entity_id = models.CharField("Entity HA", max_length=128, blank=True)
    valore = models.DecimalField(
        "Valore inviato (€/kWh o €/Smc)", max_digits=10, decimal_places=6,
        null=True, blank=True,
    )
    esito = models.BooleanField("Esito OK", default=False)
    dettaglio_errore = models.TextField(
        "Dettaglio errore", blank=True,
        help_text="Messaggio diagnostico in caso di fallimento (senza segreti).",
    )

    class Meta:
        verbose_name = "Log sync HA"
        verbose_name_plural = "Log sync HA"
        ordering = ["-creato_il"]

    def __str__(self):
        stato = "OK" if self.esito else "ERRORE"
        return f"[{stato}] {self.modalita} {self.entity_id} = {self.valore}"
