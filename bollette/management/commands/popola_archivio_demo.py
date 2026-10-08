"""
Comando Django per popolare l'archivio con uno storico realistico (2025 - 2026) di bollette Luce e Gas.
Scarica automaticamente i dati storici Open-Meteo (HDD) e calcola gli indici PUN/PSV.
"""
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand
from bollette.models import BollettaElettrica, BollettaGas
from bollette import services


class Command(BaseCommand):
    help = "Popola l'archivio con bollette di esempio 2025-2026 per testare gli algoritmi smart"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Cancella prima le bollette esistenti",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write("Cancellazione bollette esistenti...")
            BollettaElettrica.objects.all().delete()
            BollettaGas.objects.all().delete()

        # Dati Luce (Bimestrali 2025-2026)
        dati_luce = [
            # 2025
            {"inizio": date(2025, 1, 1), "fine": date(2025, 2, 28), "kwh": 480, "totale": Decimal("148.50"), "n_fatt": "FAT-LUCE-2025-01"},
            {"inizio": date(2025, 3, 1), "fine": date(2025, 4, 30), "kwh": 390, "totale": Decimal("122.00"), "n_fatt": "FAT-LUCE-2025-02"},
            {"inizio": date(2025, 5, 1), "fine": date(2025, 6, 30), "kwh": 360, "totale": Decimal("115.80"), "n_fatt": "FAT-LUCE-2025-03"},
            {"inizio": date(2025, 7, 1), "fine": date(2025, 8, 31), "kwh": 510, "totale": Decimal("162.40"), "n_fatt": "FAT-LUCE-2025-04"},
            {"inizio": date(2025, 9, 1), "fine": date(2025, 10, 31), "kwh": 370, "totale": Decimal("118.90"), "n_fatt": "FAT-LUCE-2025-05"},
            {"inizio": date(2025, 11, 1), "fine": date(2025, 12, 31), "kwh": 490, "totale": Decimal("154.20"), "n_fatt": "FAT-LUCE-2025-06"},
            # 2026
            {"inizio": date(2026, 1, 1), "fine": date(2026, 2, 28), "kwh": 465, "totale": Decimal("152.00"), "n_fatt": "FAT-LUCE-2026-01"},
        ]

        # Dati Gas (2025-2026)
        dati_gas = [
            # 2025
            {"inizio": date(2025, 1, 1), "fine": date(2025, 1, 31), "smc": Decimal("135.00"), "totale": Decimal("142.50"), "n_fatt": "FAT-GAS-2025-01"},
            {"inizio": date(2025, 2, 1), "fine": date(2025, 2, 28), "smc": Decimal("115.00"), "totale": Decimal("124.00"), "n_fatt": "FAT-GAS-2025-02"},
            {"inizio": date(2025, 3, 1), "fine": date(2025, 3, 31), "smc": Decimal("85.00"), "totale": Decimal("96.50"), "n_fatt": "FAT-GAS-2025-03"},
            {"inizio": date(2025, 4, 1), "fine": date(2025, 5, 31), "smc": Decimal("35.00"), "totale": Decimal("52.00"), "n_fatt": "FAT-GAS-2025-04"},
            {"inizio": date(2025, 6, 1), "fine": date(2025, 9, 30), "smc": Decimal("40.00"), "totale": Decimal("68.00"), "n_fatt": "FAT-GAS-2025-05"},
            {"inizio": date(2025, 10, 1), "fine": date(2025, 10, 31), "smc": Decimal("45.00"), "totale": Decimal("60.50"), "n_fatt": "FAT-GAS-2025-06"},
            {"inizio": date(2025, 11, 1), "fine": date(2025, 11, 30), "smc": Decimal("98.00"), "totale": Decimal("108.00"), "n_fatt": "FAT-GAS-2025-07"},
            {"inizio": date(2025, 12, 1), "fine": date(2025, 12, 31), "smc": Decimal("140.00"), "totale": Decimal("149.00"), "n_fatt": "FAT-GAS-2025-08"},
            # 2026
            {"inizio": date(2026, 1, 1), "fine": date(2026, 1, 31), "smc": Decimal("125.00"), "totale": Decimal("136.00"), "n_fatt": "FAT-GAS-2026-01"},
        ]

        self.stdout.write("Inserimento bollette elettriche...")
        for d in dati_luce:
            b, created = BollettaElettrica.objects.get_or_create(
                periodo_inizio=d["inizio"],
                periodo_fine=d["fine"],
                defaults={
                    "fornitore": "Acea Energia",
                    "pod": "IT001E12345678",
                    "numero_fattura": d["n_fatt"],
                    "kwh_fatturati": d["kwh"],
                    "importo_totale": d["totale"],
                    "prezzo_marginale_base": Decimal("0.175000"),
                    "accisa_marginale": Decimal("0.022700"),
                    "quota_fissa_mensile": Decimal("9.50"),
                }
            )
            services.aggiorna_campi_calcolati(b)
            self.stdout.write(f"  + Luce {b.pk}: marginale {b.prezzo_marginale_medio} EUR/kWh, HDD {b.gradi_giorno}")

        self.stdout.write("Inserimento bollette gas...")
        for g in dati_gas:
            b, created = BollettaGas.objects.get_or_create(
                periodo_inizio=g["inizio"],
                periodo_fine=g["fine"],
                defaults={
                    "fornitore": "Plenitude",
                    "pdr": "01234567890123",
                    "numero_fattura": g["n_fatt"],
                    "smc_fatturati": g["smc"],
                    "importo_totale": g["totale"],
                    "quota_materia_prima_smc": Decimal("0.480000"),
                    "quota_fissa_annua": Decimal("120.00"),
                    "accisa_smc": Decimal("0.185000"),
                }
            )
            services.aggiorna_campi_calcolati_gas(b)
            self.stdout.write(f"  + Gas {b.pk}: marginale {b.prezzo_marginale_medio_smc} EUR/Smc, HDD {b.gradi_giorno}")

        self.stdout.write(self.style.SUCCESS("Archivio storico popolato con successo!"))
