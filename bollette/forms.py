"""
Form per inserimento, archiviazione e configurazione del Portale Bollette.
"""
from decimal import Decimal
from django import forms

from .models import BollettaElettrica, BollettaGas, ConfigurazioneSistema


class BollettaElettricaForm(forms.ModelForm):
    """Inserimento e modifica dati bolletta elettrica con allegato PDF."""

    class Meta:
        model = BollettaElettrica
        fields = [
            "fornitore", "pod", "numero_fattura", "data_emissione",
            "periodo_inizio", "periodo_fine",
            "importo_totale", "kwh_fatturati", "quota_fissa_mensile",
            "prezzo_marginale_base", "accisa_marginale", "soglia_accisa_kwh",
            "file_bolletta", "note",
        ]
        widgets = {
            "periodo_inizio": forms.DateInput(attrs={"type": "date"}),
            "periodo_fine": forms.DateInput(attrs={"type": "date"}),
            "data_emissione": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["prezzo_marginale_base"].initial = Decimal("0.1934")
        self.fields["accisa_marginale"].initial = Decimal("0.0250")
        self.fields["soglia_accisa_kwh"].initial = 150
        self.fields["quota_fissa_mensile"].initial = Decimal("10.00")

    def clean(self):
        dati = super().clean()
        inizio, fine = dati.get("periodo_inizio"), dati.get("periodo_fine")
        if inizio and fine and fine < inizio:
            self.add_error("periodo_fine", "La fine periodo non può precedere l'inizio periodo.")
        return dati


class BollettaGasForm(forms.ModelForm):
    """Inserimento e modifica dati bolletta gas metano con allegato PDF."""

    class Meta:
        model = BollettaGas
        fields = [
            "fornitore", "pdr", "numero_fattura", "data_emissione",
            "periodo_inizio", "periodo_fine",
            "importo_totale", "smc_fatturati",
            "quota_materia_prima_smc", "quota_fissa_annua", "accisa_smc",
            "coeff_c", "potere_calorifico_pcs",
            "file_bolletta", "note",
        ]
        widgets = {
            "periodo_inizio": forms.DateInput(attrs={"type": "date"}),
            "periodo_fine": forms.DateInput(attrs={"type": "date"}),
            "data_emissione": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quota_materia_prima_smc"].initial = Decimal("0.4500")
        self.fields["accisa_smc"].initial = Decimal("0.1800")
        self.fields["quota_fissa_annua"].initial = Decimal("120.00")
        self.fields["coeff_c"].initial = Decimal("1.000000")
        self.fields["potere_calorifico_pcs"].initial = Decimal("0.038520")

    def clean(self):
        dati = super().clean()
        inizio, fine = dati.get("periodo_inizio"), dati.get("periodo_fine")
        if inizio and fine and fine < inizio:
            self.add_error("periodo_fine", "La fine periodo non può precedere l'inizio periodo.")
        return dati


class UploadBollettaForm(forms.Form):
    """Form per upload rapido di file PDF bolletta con estrazione automatica."""
    file_bolletta = forms.FileField(
        label="Seleziona bolletta in formato PDF",
        help_text="PDF fornito dall'operatore (es. Acea, Enel, Plenitude, ecc.)",
    )


class MultipleFileInput(forms.FileInput):
    """Widget che abilita la selezione di file multipli in Django."""
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Campo form per upload di una lista di file contemporaneamente."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={
            "multiple": True,
            "accept": ".pdf",
            "class": "file-input-multiple",
        }))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [single_file_clean(data, initial)]
        return result


class CaricamentoMassivoForm(forms.Form):
    """Form per caricamento multiplo (batch) di più file PDF contemporaneamente."""
    file_bollette = MultipleFileField(
        label="Seleziona o trascina uno o più file PDF",
        help_text="Puoi selezionare anche 10, 20 o più file PDF contemporaneamente (Luce e/o Gas).",
    )
    sovrascrivi_esistenti = forms.BooleanField(
        label="Aggiorna se già presente (stesso periodo di fatturazione)",
        required=False,
        initial=False,
        help_text="Se disattivato, le bollette con lo stesso periodo già presenti verranno ignorate per evitare duplicati.",
    )


class ConfigurazioneSistemaForm(forms.ModelForm):
    """Form per la gestione delle impostazioni del sistema direttamente dalla web app."""
    class Meta:
        model = ConfigurazioneSistema
        fields = [
            "ha_base_url", "ha_token", "ha_tls_verify", "ha_ca_bundle",
            "prezzo_ha_mode", "ha_sync_auto",
            "ha_entity_kwh_consumo", "ha_entity_smc_consumo",
            "ha_entity_prezzo_singolo", "ha_entity_prezzo_gas", "ha_entity_pun",
            "meteo_citta", "meteo_latitude", "meteo_longitude",
            "netatmo_client_id", "netatmo_client_secret", "netatmo_refresh_token",
        ]
        widgets = {
            "ha_token": forms.PasswordInput(render_value=True, attrs={
                "id": "id_ha_token",
                "placeholder": "Incolla il Long-Lived Access Token (es. eyJhbGciOiJIUzI1NiIs...)",
                "autocomplete": "off",
                "style": "font-family: var(--font-mono); font-size: .86rem;",
            }),
            "ha_base_url": forms.TextInput(attrs={
                "id": "id_ha_base_url",
                "placeholder": "http://homeassistant.local:8123 oppure http://192.168.1.xxx:8123",
                "style": "font-family: var(--font-mono);",
            }),
            "ha_ca_bundle": forms.TextInput(attrs={
                "id": "id_ha_ca_bundle",
                "placeholder": "Opzionale: es. C:\\certificati\\ha_custom.crt",
                "style": "font-family: var(--font-mono);",
            }),
            "prezzo_ha_mode": forms.Select(attrs={
                "id": "id_prezzo_ha_mode",
                "style": "cursor: pointer;",
            }),
            "ha_entity_kwh_consumo": forms.TextInput(attrs={
                "id": "id_ha_entity_kwh_consumo",
                "placeholder": "es. sensor.shelly_em_channel_1_energy",
                "style": "font-family: var(--font-mono);",
            }),
            "ha_entity_smc_consumo": forms.TextInput(attrs={
                "id": "id_ha_entity_smc_consumo",
                "placeholder": "es. sensor.contatore_gas_smc",
                "style": "font-family: var(--font-mono);",
            }),
            "ha_entity_prezzo_singolo": forms.TextInput(attrs={
                "id": "id_ha_entity_prezzo_singolo",
                "placeholder": "input_number.prezzo_energia_kwh",
                "style": "font-family: var(--font-mono);",
            }),
            "ha_entity_prezzo_gas": forms.TextInput(attrs={
                "id": "id_ha_entity_prezzo_gas",
                "placeholder": "input_number.prezzo_gas_smc",
                "style": "font-family: var(--font-mono);",
            }),
            "ha_entity_pun": forms.TextInput(attrs={
                "id": "id_ha_entity_pun",
                "placeholder": "sensor.pun_mono",
                "style": "font-family: var(--font-mono);",
            }),
            "meteo_citta": forms.TextInput(attrs={
                "id": "id_meteo_citta",
                "placeholder": "es. Roma, Milano, Napoli...",
            }),
            "meteo_latitude": forms.NumberInput(attrs={
                "id": "id_meteo_latitude",
                "placeholder": "41.9028",
                "step": "0.0001",
                "style": "font-family: var(--font-mono);",
            }),
            "meteo_longitude": forms.NumberInput(attrs={
                "id": "id_meteo_longitude",
                "placeholder": "12.4964",
                "step": "0.0001",
                "style": "font-family: var(--font-mono);",
            }),
            "netatmo_client_id": forms.TextInput(attrs={
                "id": "id_netatmo_client_id",
                "placeholder": "Client ID da dev.netatmo.com/apps",
                "style": "font-family: var(--font-mono);",
            }),
            "netatmo_client_secret": forms.PasswordInput(render_value=True, attrs={
                "id": "id_netatmo_client_secret",
                "placeholder": "Client Secret da dev.netatmo.com/apps",
                "style": "font-family: var(--font-mono);",
            }),
            "netatmo_refresh_token": forms.PasswordInput(render_value=True, attrs={
                "id": "id_netatmo_refresh_token",
                "placeholder": "Refresh Token generato su dev.netatmo.com",
                "style": "font-family: var(--font-mono); font-size: .86rem;",
            }),
        }


class UploadNetatmoForm(forms.Form):
    """Form per il caricamento di file CSV esportati da Netatmo Energy WebApp."""
    file_netatmo = forms.FileField(
        label="File CSV Netatmo",
        help_text="Seleziona il file .csv esportato dalla WebApp Netatmo (Settings > Data Management > Download)",
        widget=forms.FileInput(attrs={
            "accept": ".csv,.txt",
            "class": "file-input-netatmo",
        }),
    )

