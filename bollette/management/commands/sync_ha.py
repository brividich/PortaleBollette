"""
Ripresa della coda di sync verso HA (ADR-002 punto e), sia per Luce che per Gas.

Esempi:
    python manage.py sync_ha            # ritenta tutte le bollette in coda (luce e gas)
    python manage.py sync_ha --media    # pubblica anche le medie mobili 12m (luce e gas)
"""
from django.core.management.base import BaseCommand

from bollette import services
from bollette.models import BollettaElettrica, BollettaGas


class Command(BaseCommand):
    help = "Ritenta le sync HA in coda (ha_sync_ok=False) per luce e gas e/o pubblica le medie mobili."

    def add_arguments(self, parser):
        parser.add_argument(
            "--media", action="store_true",
            help="Pubblica anche le medie mobili 12m oltre alla coda.")

    def handle(self, *args, **options):
        # 1. Coda Luce
        in_coda_luce = BollettaElettrica.objects.filter(ha_sync_ok=False)
        self.stdout.write(f"Bollette elettriche in coda: {in_coda_luce.count()}")
        for b in in_coda_luce:
            esito = services.pubblica_prezzo_su_ha(bolletta=b)
            if esito.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"  OK Luce: {b}"))
            else:
                self.stdout.write(self.style.WARNING(f"  KO Luce: {b} ({esito.get('errore')})"))

        # 2. Coda Gas
        in_coda_gas = BollettaGas.objects.filter(ha_sync_ok=False)
        self.stdout.write(f"Bollette gas in coda: {in_coda_gas.count()}")
        for g in in_coda_gas:
            esito = services.pubblica_prezzo_gas_su_ha(bolletta=g)
            if esito.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"  OK Gas: {g}"))
            else:
                self.stdout.write(self.style.WARNING(f"  KO Gas: {g} ({esito.get('errore')})"))

        # 3. Medie mobili
        if options["media"]:
            esito_l = services.pubblica_prezzo_su_ha(mode="media_mobile_12m")
            if esito_l.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"Media Luce pubblicata: {esito_l['componenti']['prezzo_energia_kwh']} €/kWh"))
            esito_g = services.pubblica_prezzo_gas_su_ha(mode="media_mobile_12m")
            if esito_g.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"Media Gas pubblicata: {esito_g['valore']} €/Smc"))
