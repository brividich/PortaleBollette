"""Registrazione e personalizzazione del pannello di amministrazione Django."""
from django.contrib import admin, messages
from django.utils.html import format_html

from . import services
from .forms import ConfigurazioneSistemaForm
from .models import BollettaElettrica, BollettaGas, ConfigurazioneSistema, HaSyncLog, NetatmoRecordGiornaliero

# Personalizzazione intestazioni Admin
admin.site.site_header = "⚡ Portale Bollette — Amministrazione"
admin.site.site_title = "Portale Bollette Admin"
admin.site.index_title = "Gestione Bollette, Bridge Home Assistant & Configurazioni"


@admin.register(ConfigurazioneSistema)
class ConfigurazioneSistemaAdmin(admin.ModelAdmin):
    form = ConfigurazioneSistemaForm
    list_display = ("__str__", "ha_base_url", "prezzo_ha_mode", "ha_sync_auto", "meteo_citta", "aggiornato_il")
    fieldsets = (
        ("Bridge Home Assistant (REST API & Token)", {
            "fields": (
                "ha_base_url", "ha_token", "ha_tls_verify", "ha_ca_bundle",
                "prezzo_ha_mode", "ha_sync_auto",
            ),
            "description": "Parametri di connessione REST verso Home Assistant. I segreti sono salvati nel database locale.",
        }),
        ("Entità Sensori e Helper HA", {
            "fields": (
                "ha_entity_kwh_consumo", "ha_entity_smc_consumo",
                "ha_entity_prezzo_singolo", "ha_entity_prezzo_gas",
                "ha_entity_pun",
            ),
            "description": "Entità monitorate per la validazione dei consumi e helper per la pubblicazione del costo marginale.",
        }),
        ("Meteo & Normalizzazione Climatica (Open-Meteo)", {
            "fields": ("meteo_citta", "meteo_latitude", "meteo_longitude"),
            "description": "Coordinate per il calcolo automatico della temperatura media e dei Gradi Giorno di Riscaldamento (HDD).",
        }),
        ("Pipeline AI / Parsing Bollette (Ollama)", {
            "fields": ("ollama_base_url", "ollama_model"),
            "description": "Parametri dell'istanza Ollama locale (es. sul PC desktop con RTX 5070 Ti).",
        }),
        ("Netatmo Connect API (Cloud Thermostat)", {
            "fields": (
                "netatmo_client_id", "netatmo_client_secret", "netatmo_refresh_token",
                ("netatmo_home_id", "netatmo_device_id", "netatmo_module_id"),
            ),
            "description": "Credenziali API Cloud per scaricare in tempo reale le ore di accensione caldaia e temperature senza file CSV.",
        }),
    )

    def has_add_permission(self, request):
        # Permetti una sola riga di configurazione (Singleton)
        if ConfigurazioneSistema.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(BollettaElettrica)
class BollettaElettricaAdmin(admin.ModelAdmin):
    list_display = (
        "fornitore", "periodo_inizio", "periodo_fine", "kwh_fatturati",
        "badge_prezzo_medio", "badge_costo_finito", "gradi_giorno",
        "pun_medio_periodo", "badge_spread", "badge_sync_ha", "link_file_pdf",
    )
    list_filter = ("fornitore", "sopra_soglia_accisa", "ha_sync_ok")
    date_hierarchy = "periodo_fine"
    search_fields = ("pod", "numero_fattura", "fornitore")
    fieldsets = (
        ("Dati Generali & Documento", {
            "fields": (
                ("fornitore", "pod"),
                ("numero_fattura", "data_emissione"),
                ("periodo_inizio", "periodo_fine"),
                ("importo_totale", "quota_fissa_mensile"),
                "file_bolletta",
                "note",
            ),
        }),
        ("Consumi & Prezzo Marginale (ADR-002)", {
            "fields": (
                "kwh_fatturati",
                ("prezzo_marginale_base", "accisa_marginale"),
                ("soglia_accisa_kwh", "sopra_soglia_accisa"),
                "prezzo_marginale_medio",
            ),
        }),
        ("Normalizzazione Climatica (Open-Meteo)", {
            "fields": (("gradi_giorno", "temperatura_media"), "kwh_per_gradi_giorno"),
            "classes": ("collapse",),
        }),
        ("Benchmark di Mercato PUN", {
            "fields": (("pun_medio_periodo", "spread_pun"),),
            "classes": ("collapse",),
        }),
        ("Stato Sincronizzazione Home Assistant", {
            "fields": (("prezzo_pubblicato_ha", "ha_sync_ok"), "ha_sync_at"),
            "classes": ("collapse",),
        }),
    )
    actions = (
        "azione_pubblica_questa_bolletta",
        "azione_pubblica_media_mobile",
        "azione_valida_vs_ha",
        "azione_aggiorna_meteo_e_pun",
    )

    @admin.display(description="€/kWh Marginale")
    def badge_prezzo_medio(self, obj):
        return format_html("<strong style='color:#eab308'>{} €</strong>", obj.prezzo_marginale_medio)

    @admin.display(description="€/kWh Tutto Incluso")
    def badge_costo_finito(self, obj):
        tot = obj.costo_unitario_totale
        return f"{tot} €" if tot else "—"

    @admin.display(description="Spread PUN")
    def badge_spread(self, obj):
        if obj.spread_pun is not None:
            color = "#f59e0b" if obj.spread_pun > 0.05 else "#22c55e"
            return format_html("<span style='color:{}'>+{} €</span>", color, obj.spread_pun)
        return "—"

    @admin.display(description="Sync HA")
    def badge_sync_ha(self, obj):
        if obj.ha_sync_ok:
            return format_html("<span style='color:#22c55e;font-weight:600'>✓ OK</span>")
        elif obj.ha_sync_at:
            return format_html("<span style='color:#ef4444;font-weight:600'>In coda</span>")
        return format_html("<span style='color:#94a3b8'>—</span>")

    @admin.display(description="Allegato PDF")
    def link_file_pdf(self, obj):
        if obj.file_bolletta:
            return format_html("<a href='{}' target='_blank'>📄 Scarica</a>", obj.file_bolletta.url)
        return "—"

    @admin.action(description="Pubblica prezzo su HA (bollette selezionate)")
    def azione_pubblica_questa_bolletta(self, request, queryset):
        for bolletta in queryset:
            esito = services.pubblica_prezzo_su_ha(bolletta=bolletta)
            if esito.get("ok"):
                self.message_user(request, f"{bolletta}: inviato a HA.", level=messages.SUCCESS)
            else:
                self.message_user(request, f"{bolletta}: invio fallito.", level=messages.WARNING)

    @admin.action(description="Pubblica media mobile 12m su HA")
    def azione_pubblica_media_mobile(self, request, queryset):
        esito = services.pubblica_prezzo_su_ha(mode="media_mobile_12m")
        if esito.get("ok"):
            self.message_user(request, f"Media 12m pubblicata: {esito['componenti']['prezzo_energia_kwh']} €/kWh.", level=messages.SUCCESS)
        else:
            self.message_user(request, "Pubblicazione fallita.", level=messages.WARNING)

    @admin.action(description="Valida vs HA (consumi contatore)")
    def azione_valida_vs_ha(self, request, queryset):
        for bolletta in queryset:
            esito = services.valida_vs_ha(bolletta)
            livello = messages.SUCCESS if esito["ok"] else messages.WARNING
            self.message_user(request, f"{bolletta}: {esito['messaggio']}", level=livello)

    @admin.action(description="Ricalcola Gradi Giorno e PUN")
    def azione_aggiorna_meteo_e_pun(self, request, queryset):
        for bolletta in queryset:
            services.aggiorna_campi_calcolati(bolletta)
        self.message_user(request, f"{queryset.count()} bollette aggiornate.", level=messages.SUCCESS)


@admin.register(BollettaGas)
class BollettaGasAdmin(admin.ModelAdmin):
    list_display = (
        "fornitore", "periodo_inizio", "periodo_fine", "smc_fatturati",
        "ore_caldaia_badge", "smc_ora_badge", "badge_prezzo_smc", "badge_costo_finito",
        "gradi_giorno", "psv_medio_periodo", "badge_spread", "badge_sync_ha", "link_file_pdf",
    )
    list_filter = ("fornitore", "ha_sync_ok")
    date_hierarchy = "periodo_fine"
    search_fields = ("pdr", "numero_fattura", "fornitore")
    fieldsets = (
        ("Dati Generali & Documento", {
            "fields": (
                ("fornitore", "pdr"),
                ("numero_fattura", "data_emissione"),
                ("periodo_inizio", "periodo_fine"),
                "importo_totale",
                "file_bolletta",
                "note",
            ),
        }),
        ("Consumi & Tariffe Gas Metano", {
            "fields": (
                "smc_fatturati",
                ("coeff_c", "potere_calorifico_pcs"),
                ("quota_materia_prima_smc", "quota_fissa_annua"),
                ("accisa_smc", "prezzo_marginale_medio_smc"),
            ),
        }),
        ("Riscaldamento Netatmo (Ore Caldaia & Rendimento)", {
            "fields": (
                ("ore_caldaia", "smc_ora_caldaia", "costo_ora_caldaia"),
                ("temp_interna_media", "smc_riscaldamento_stimati", "smc_acs_cucina_stimati"),
            ),
        }),
        ("Normalizzazione Climatica (Open-Meteo)", {
            "fields": (("gradi_giorno", "temperatura_media"), "smc_per_gradi_giorno"),
            "classes": ("collapse",),
        }),
        ("Benchmark di Mercato PSV", {
            "fields": (("psv_medio_periodo", "spread_psv"),),
            "classes": ("collapse",),
        }),
        ("Stato Sincronizzazione Home Assistant", {
            "fields": (("prezzo_pubblicato_ha", "ha_sync_ok"), "ha_sync_at"),
            "classes": ("collapse",),
        }),
    )
    actions = ("azione_pubblica_gas_ha", "azione_aggiorna_meteo_gas")

    @admin.display(description="Ore Caldaia")
    def ore_caldaia_badge(self, obj):
        if obj.ore_caldaia is not None:
            return format_html("<span style='color:#f97316;font-weight:600'>{} h</span>", obj.ore_caldaia)
        return "—"

    @admin.display(description="Smc/ora")
    def smc_ora_badge(self, obj):
        if obj.smc_ora_caldaia is not None:
            return f"{obj.smc_ora_caldaia} Smc/h"
        return "—"

    @admin.display(description="€/Smc Marginale")
    def badge_prezzo_smc(self, obj):
        return format_html("<strong style='color:#f97316'>{} €</strong>", obj.prezzo_marginale_medio_smc)

    @admin.display(description="€/Smc Tutto Incluso")
    def badge_costo_finito(self, obj):
        tot = obj.costo_unitario_totale
        return f"{tot} €" if tot else "—"

    @admin.display(description="Spread PSV")
    def badge_spread(self, obj):
        if obj.spread_psv is not None:
            color = "#f59e0b" if obj.spread_psv > 0.10 else "#22c55e"
            return format_html("<span style='color:{}'>+{} €</span>", color, obj.spread_psv)
        return "—"

    @admin.display(description="Sync HA")
    def badge_sync_ha(self, obj):
        if obj.ha_sync_ok:
            return format_html("<span style='color:#22c55e;font-weight:600'>✓ OK</span>")
        elif obj.ha_sync_at:
            return format_html("<span style='color:#ef4444;font-weight:600'>In coda</span>")
        return format_html("<span style='color:#94a3b8'>—</span>")

    @admin.display(description="Allegato PDF")
    def link_file_pdf(self, obj):
        if obj.file_bolletta:
            return format_html("<a href='{}' target='_blank'>📄 Scarica</a>", obj.file_bolletta.url)
        return "—"

    @admin.action(description="Pubblica prezzo Gas su HA")
    def azione_pubblica_gas_ha(self, request, queryset):
        for bolletta in queryset:
            esito = services.pubblica_prezzo_gas_su_ha(bolletta=bolletta)
            if esito.get("ok"):
                self.message_user(request, f"{bolletta}: inviato a HA ({esito['valore']} €/Smc).", level=messages.SUCCESS)
            else:
                self.message_user(request, f"{bolletta}: invio gas fallito.", level=messages.WARNING)

    @admin.action(description="Ricalcola Gradi Giorno e PSV")
    def azione_aggiorna_meteo_gas(self, request, queryset):
        for bolletta in queryset:
            services.aggiorna_campi_calcolati_gas(bolletta)
        self.message_user(request, f"{queryset.count()} bollette gas aggiornate.", level=messages.SUCCESS)


@admin.register(NetatmoRecordGiornaliero)
class NetatmoRecordGiornalieroAdmin(admin.ModelAdmin):
    list_display = (
        "data", "ore_caldaia_badge", "temp_interna_media", "temp_setpoint_media",
        "n_campionamenti", "fonte_file",
    )
    date_hierarchy = "data"
    search_fields = ("fonte_file",)

    @admin.display(description="Ore Caldaia")
    def ore_caldaia_badge(self, obj):
        color = "#f97316" if obj.ore_caldaia > 0 else "#94a3b8"
        return format_html("<strong style='color:{}'>{} h</strong>", color, obj.ore_caldaia)


@admin.register(HaSyncLog)
class HaSyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "creato_il", "modalita", "entity_id", "valore", "esito", "bolletta", "bolletta_gas",
    )
    list_filter = ("esito", "modalita")
    date_hierarchy = "creato_il"
    readonly_fields = (
        "creato_il", "modalita", "entity_id", "valore", "esito",
        "dettaglio_errore", "bolletta", "bolletta_gas",
    )

