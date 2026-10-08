"""Routing principale del Portale Bollette: dashboard, archivio, statistiche, configurazioni."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from bollette import views

urlpatterns = [
    # Dashboard & Navigazione Principale
    path("", views.home, name="home"),
    path("archivio/", views.archivio, name="archivio"),
    path("statistiche/", views.statistiche, name="statistiche"),
    path("netatmo/", views.netatmo_dashboard_view, name="netatmo_dashboard"),
    path("netatmo/sync-api/", views.sincronizza_netatmo_api_view, name="netatmo_sync_api"),
    path("netatmo/sincronizza/", views.sincronizza_netatmo_gas_view, name="netatmo_sincronizza"),
    path("netatmo/svuota/", views.svuota_netatmo_view, name="netatmo_svuota"),
    path("netatmo/sync-ha/", views.sincronizza_netatmo_da_ha_view, name="netatmo_sync_ha"),
    path("dispositivi/", views.dispositivi_ha_view, name="dispositivi_ha"),
    path("confronto-ha/", views.confronto_ha_view, name="confronto_ha"),
    path("configurazioni/", views.configurazioni_view, name="configurazioni"),

    # Dettaglio & Eliminazione Bollette
    path("bolletta/<int:pk>/", views.dettaglio_bolletta_luce, name="dettaglio_bolletta_luce"),
    path("bolletta/gas/<int:pk>/", views.dettaglio_bolletta_gas, name="dettaglio_bolletta_gas"),
    path("bolletta/<int:pk>/elimina/", views.elimina_bolletta_luce, name="elimina_bolletta_luce"),
    path("bolletta/gas/<int:pk>/elimina/", views.elimina_bolletta_gas, name="elimina_bolletta_gas"),

    # Ingestion & Nuove Bollette
    path("bolletta/nuova/", views.nuova_bolletta, name="nuova_bolletta"),
    path("bolletta/gas/nuova/", views.nuova_bolletta_gas, name="nuova_bolletta_gas"),
    path("bolletta/upload/", views.upload_pdf_bolletta, name="upload_pdf_bolletta"),
    path("bolletta/caricamento-massivo/", views.caricamento_massivo_view, name="caricamento_massivo"),
    path("api/bolletta/analizza-pdf/", views.analizza_pdf_api, name="analizza_pdf_api"),

    # Esportazione
    path("esporta/csv/", views.esporta_archivio_csv, name="esporta_csv"),

    # API Interattive & Diagnostica
    path("api/ha/discovery/", views.ha_discovery_api, name="ha_discovery_api"),
    path("api/ha/telemetria/", views.ha_telemetria_api, name="ha_telemetria_api"),
    path("api/ha/device-breakdown/", views.ha_device_breakdown_api, name="ha_device_breakdown_api"),
    path("api/ha/toggle/", views.ha_toggle_device_api, name="ha_toggle_device_api"),
    path("api/ha/climate/", views.ha_climate_control_api, name="ha_climate_control_api"),
    path("api/v1/ha/push/", views.ha_push_api, name="ha_push_api"),
    path("api/diagnostica/", views.test_servizio_api, name="test_servizio_api"),

    # Azioni Home Assistant (fail-safe & bidirezionale)
    path("sincronizzazione-bidirezionale/", views.sincronizzazione_bidirezionale_view, name="sincronizzazione_bidirezionale"),
    path("pubblica-media/", views.pubblica_media, name="pubblica_media"),
    path("pubblica-media-gas/", views.pubblica_media_gas, name="pubblica_media_gas"),
    path("bolletta/<int:pk>/pubblica/", views.pubblica_bolletta, name="pubblica_bolletta"),
    path("bolletta/<int:pk>/valida/", views.valida_bolletta, name="valida_bolletta"),
    path("bolletta/gas/<int:pk>/pubblica/", views.pubblica_bolletta_gas, name="pubblica_bolletta_gas"),

    # Admin Django
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
