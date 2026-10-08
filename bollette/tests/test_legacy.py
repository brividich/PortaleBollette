"""Test suite per Portale Bollette: Home Assistant bridge e monitoraggio dispositivi."""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from bollette.models import BollettaElettrica, BollettaGas, ConfigurazioneSistema, NetatmoRecordGiornaliero
from bollette import ha_client


class HomeAssistantDispositiviTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.cfg = ConfigurazioneSistema.get_config()
        self.cfg.ha_base_url = "http://192.168.1.206:8123"
        self.cfg.ha_token = "dummy_test_token"
        self.cfg.save()

        # Bolletta elettrica di prova per determinare la tariffa marginale
        BollettaElettrica.objects.create(
            fornitore="Acea Energia",
            periodo_inizio="2026-07-01",
            periodo_fine="2026-08-31",
            importo_totale=Decimal("120.50"),
            kwh_fatturati=350,
            prezzo_marginale_medio=Decimal("0.192610"),
        )

    def test_dispositivi_ha_view(self):
        url = reverse("dispositivi_ha")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "bollette/dispositivi_ha.html")
        self.assertIn("breakdown", resp.context)
        self.assertIn("baseload", resp.context["breakdown"])
        self.assertIn("carichi", resp.context["breakdown"])

    def test_ha_telemetria_api(self):
        url = reverse("ha_telemetria_api")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("online", data)

    def test_ha_device_breakdown_api(self):
        url = reverse("ha_device_breakdown_api")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("carichi", data)
        self.assertIn("totale_kwh", data)
        self.assertIn("baseload", data)
        self.assertIn("categorie", data)

    def test_sincronizza_netatmo_da_ha(self):
        from unittest.mock import patch
        with patch("bollette.ha_client.get_state") as mock_state:
            mock_state.side_effect = lambda eid: {
                "climate.netatmo_smart_thermostat": {"state": "heat", "attributes": {"current_temperature": 21.5, "temperature": 20.0, "hvac_action": "heating"}},
                "sensor.netatmo_smart_thermostat_current_temperature": {"state": "21.5"},
            }.get(eid)
            url = reverse("netatmo_sync_ha")
            resp = self.client.post(url)
            self.assertEqual(resp.status_code, 302)  # Redirect al chiamante / dispositivi
            record_count = NetatmoRecordGiornaliero.objects.count()
            self.assertGreaterEqual(record_count, 1)

    def test_confronto_ha_view(self):
        url = reverse("confronto_ha")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "bollette/confronto_ha.html")

    def test_configurazioni_view(self):
        url = reverse("configurazioni")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "bollette/configurazioni.html")
        self.assertIn("form", resp.context)
        self.assertIn("ha_configured", resp.context)
        self.assertIn("webhook_url", resp.context)

    def test_diagnostica_api(self):
        url = reverse("test_servizio_api") + "?servizio=meteo&citta=Roma&lat=41.9028&lon=12.4964"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))

    def test_nuova_bolletta_views(self):
        resp_luce = self.client.get(reverse("nuova_bolletta"))
        self.assertEqual(resp_luce.status_code, 200)
        self.assertTemplateUsed(resp_luce, "bollette/nuova_bolletta.html")

        resp_gas = self.client.get(reverse("nuova_bolletta_gas"))
        self.assertEqual(resp_gas.status_code, 200)
        self.assertTemplateUsed(resp_gas, "bollette/nuova_bolletta_gas.html")

    def test_analizza_pdf_api_no_file(self):
        url = reverse("analizza_pdf_api")
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 400)


class ParserRobustezzaTests(TestCase):
    """Test suite per la robustezza del parser deterministico di bollette italiane."""

    def test_estrazione_luce_enel_con_fasce(self):
        from bollette.parser_service import estrai_euristico
        testo = """
        Enel Energia S.p.A. - Mercato Libero dell'Energia
        Fattura n. 4125098765 del 15/02/2026
        Data emissione: 15/02/2026 - Scadenza: 10/03/2026
        Codice POD: IT001E12345678
        Periodo di fatturazione: dal 01/01/2026 al 31/01/2026
        Totale consumi: 450 kWh
        F1: 150 kWh
        F2: 120 kWh
        F3: 180 kWh
        Totale da pagare: 135,40 €
        Spesa per la materia energia: 85,20 €
        Spesa per il trasporto e gestione contatore: 22,10 €
        Spesa per oneri di sistema: 14,30 €
        Totale imposte e IVA: 13,80 €
        """
        d = estrai_euristico(testo)
        self.assertEqual(d["tipo"], "luce")
        self.assertEqual(d["fornitore"], "Enel Energia")
        self.assertEqual(d["pod"], "IT001E12345678")
        self.assertEqual(d["numero_fattura"], "4125098765")
        self.assertEqual(d["data_emissione"].isoformat(), "2026-02-15")
        self.assertEqual(d["periodo_inizio"].isoformat(), "2026-01-01")
        self.assertEqual(d["periodo_fine"].isoformat(), "2026-01-31")
        self.assertEqual(d["consumo"], 450)
        self.assertEqual(d["fasce"], {"f1": 150, "f2": 120, "f3": 180})
        self.assertEqual(d["importo_totale"], Decimal("135.40"))
        self.assertGreaterEqual(d["score"], 80)

    def test_estrazione_gas_plenitude_con_parametri_tecnici(self):
        from bollette.parser_service import estrai_euristico
        testo = """
        Eni Plenitude S.p.A. Società Benefit
        Fattura N° 2026/GAS/0045612
        Data di emissione: 18 Gennaio 2026
        Fornitura di Gas Naturale
        PDR: 01234567890123
        Periodo di competenza: dal 01/12/2025 al 31/12/2025
        Smc fatturati: 182,50 Smc
        Coefficiente C: 1,027234
        PCS: 0,038520 GJ/Smc
        Totale bolletta: 198,75 €
        """
        d = estrai_euristico(testo)
        self.assertEqual(d["tipo"], "gas")
        self.assertEqual(d["fornitore"], "Eni Plenitude")
        self.assertEqual(d["pdr"], "01234567890123")
        self.assertEqual(d["numero_fattura"], "2026/GAS/0045612")
        self.assertEqual(d["data_emissione"].isoformat(), "2026-01-18")
        self.assertEqual(d["periodo_inizio"].isoformat(), "2025-12-01")
        self.assertEqual(d["periodo_fine"].isoformat(), "2025-12-31")
        self.assertEqual(d["consumo"], Decimal("182.50"))
        self.assertEqual(d["coeff_c"], Decimal("1.027234"))
        self.assertEqual(d["potere_calorifico_pcs"], Decimal("0.038520"))
        self.assertEqual(d["importo_totale"], Decimal("198.75"))
        self.assertGreaterEqual(d["score"], 80)

    def test_estrazione_pod_con_spazi_e_somma_fasce(self):
        from bollette.parser_service import estrai_euristico
        testo = """
        Octopus Energy Italia
        Documento n. OCT-2026-992
        Data emissione: 02.03.2026
        Punto di prelievo (POD): IT 001 E 88776655
        Periodo: dal 01/02/2026 al 28/02/2026
        F1: 100 kWh
        F2: 80 kWh
        F3: 120 kWh
        Totale da pagare: 78,50 €
        """
        d = estrai_euristico(testo)
        self.assertEqual(d["tipo"], "luce")
        self.assertEqual(d["fornitore"], "Octopus Energy")
        self.assertEqual(d["pod"], "IT001E88776655")
        # Il consumo deve essere dedotto dalla somma F1+F2+F3
        self.assertEqual(d["consumo"], 300)
        self.assertEqual(d["importo_totale"], Decimal("78.50"))

    def test_estrazione_nota_di_credito(self):
        from bollette.parser_service import estrai_euristico
        testo = """
        Acea Energia
        Nota di credito n. NC-9988
        Data emissione: 10/05/2026
        POD: IT001E99988877
        Periodo: dal 01/04/2026 al 30/04/2026
        Energia fatturata: 0 kWh
        Totale a credito: -45,00 €
        """
        d = estrai_euristico(testo)
        self.assertEqual(d["fornitore"], "Acea Energia")
        self.assertEqual(d["importo_totale"], Decimal("-45.00"))

    def test_model_date_inversion_safe(self):
        b = BollettaElettrica(
            fornitore="Test",
            periodo_inizio="2026-08-31",
            periodo_fine="2026-08-01",  # Invertita
            kwh_fatturati=100,
            importo_totale=Decimal("50.00"),
        )
        b.clean()
        self.assertEqual(str(b.periodo_inizio), "2026-08-01")
        self.assertEqual(str(b.periodo_fine), "2026-08-31")
        self.assertEqual(b.giorni_periodo, 31)

    def test_corrupted_pdf_handling(self):
        import io
        from bollette.parser_service import analizza_documento
        res = analizza_documento(io.BytesIO(b"not a valid pdf header"))
        self.assertFalse(res["ok"])
        self.assertIn("errore", res)

    def test_periodo_da_mese_competenza(self):
        from bollette.parser_service import estrai_euristico
        testo = """
        A2A Energia S.p.A.
        Fattura n. A2A-88192 del 05/04/2026
        POD: IT001E99887766
        Mese di competenza: Marzo 2026
        Totale consumi: 220 kWh
        Totale da pagare: 64,50 €
        """
        d = estrai_euristico(testo)
        self.assertEqual(d["fornitore"], "A2A Energia")
        self.assertEqual(d["periodo_inizio"].isoformat(), "2026-03-01")
        self.assertEqual(d["periodo_fine"].isoformat(), "2026-03-31")
        self.assertEqual(d["consumo"], 220)
        self.assertEqual(d["importo_totale"], Decimal("64.50"))

    def test_analizza_pdf_api_con_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from pypdf import PdfWriter
        import io
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)

        pdf_file = SimpleUploadedFile("bolletta_test.pdf", buf.read(), content_type="application/pdf")
        url = reverse("analizza_pdf_api")
        resp = self.client.post(url, {"file_bolletta": pdf_file})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Per un PDF completamente vuoto/senza testo vettoriale, il parser segnala correttamente errore chiaro
        self.assertFalse(data.get("ok"))
        self.assertIn("errore", data)
        self.assertIn("testo vettoriale", data["errore"])

    def test_multi_fornitore_parser_dispatcher(self):
        from bollette.parser_service import estrai_euristico, get_fornitori_supportati
        from bollette.parsers.registry import get_parser_registry

        registry = get_parser_registry()

        # 1. Test Dispatcher Enel
        p_enel = registry.rileva_parser("Enel Energia S.p.A. - Fattura 4125000000", "")
        self.assertEqual(p_enel.__class__.__name__, "EnelParser")

        # 2. Test Dispatcher Plenitude
        p_eni = registry.rileva_parser("Eni Plenitude S.p.A. - Fornitura Gas", "")
        self.assertEqual(p_eni.__class__.__name__, "EniPlenitudeParser")

        # 3. Test Dispatcher SEN
        p_sen = registry.rileva_parser("Servizio Elettrico Nazionale - Maggior Tutela", "")
        self.assertEqual(p_sen.__class__.__name__, "ServizioElettricoNazionaleParser")

        # 4. Test Dispatcher Acea
        p_acea = registry.rileva_parser("Acea Energia SpA - Roma", "")
        self.assertEqual(p_acea.__class__.__name__, "AceaParser")

        # 5. Test Dispatcher Octopus
        p_oct = registry.rileva_parser("Octopus Energy Italia - OCT-2026", "")
        self.assertEqual(p_oct.__class__.__name__, "OctopusParser")

        # 6. Test Dispatcher NeN
        p_nen = registry.rileva_parser("NeN Mercato Libero - La tua rata", "")
        self.assertEqual(p_nen.__class__.__name__, "NeNParser")

        # 7. Test Fallback per operatore minore in catalogo
        p_gen = registry.rileva_parser("Qualche fornitore sconosciuto", "")
        self.assertEqual(p_gen.__class__.__name__, "GenericFallbackParser")

        # 8. Test catalogo fornitori supportati
        fornitori = get_fornitori_supportati()
        self.assertGreaterEqual(len(fornitori), 25)
        nomi = [f["nome"] for f in fornitori]
        self.assertIn("Enel Energia", nomi)
        self.assertIn("Eni Plenitude", nomi)
        self.assertIn("Octopus Energy", nomi)
        self.assertIn("A2A Energia", nomi)
        self.assertIn("Acea Energia", nomi)
