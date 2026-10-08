from decimal import Decimal
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings

from bollette.forms import ConfigurazioneSistemaForm
from bollette.ha_client import _get_ha_config
from bollette.middleware import PortaleRequireLoginMiddleware
from bollette.models import ConfigurazioneSistema


class TestSicurezzaEAutenticazione(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cfg = ConfigurazioneSistema.get_config()
        self.cfg.ha_token = "token_nel_db"
        self.cfg.ha_base_url = "http://db-ha:8123"
        self.cfg.save()

    def test_priorita_env_su_db_per_ha_token(self):
        with override_settings(HA_TOKEN="token_da_env", HA_BASE_URL="https://env-ha:8123"):
            base_url, token, verify, ca_bundle = _get_ha_config()
            self.assertEqual(token, "token_da_env")
            self.assertEqual(base_url, "https://env-ha:8123")

    def test_fallback_db_se_env_vuoto(self):
        with override_settings(HA_TOKEN="", HA_BASE_URL=""):
            base_url, token, verify, ca_bundle = _get_ha_config()
            self.assertEqual(token, "token_nel_db")
            self.assertEqual(base_url, "http://db-ha:8123")

    def test_form_preserva_token_se_inviato_vuoto(self):
        from django.forms.models import model_to_dict

        form_data = model_to_dict(self.cfg)
        form_data["ha_token"] = ""  # campo lasciato vuoto dall'utente
        form = ConfigurazioneSistemaForm(data=form_data, instance=self.cfg)
        self.assertTrue(form.is_valid(), msg=form.errors.as_text())
        self.assertEqual(form.cleaned_data["ha_token"], "token_nel_db")

    def test_middleware_disattivato_di_default(self):
        middleware = PortaleRequireLoginMiddleware(lambda req: "ok")
        request = self.factory.get("/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response, "ok")

    @override_settings(PORTALE_REQUIRE_LOGIN=True, LOGIN_URL="/admin/login/")
    def test_middleware_attivo_reindirizza_anonimo(self):
        middleware = PortaleRequireLoginMiddleware(lambda req: "ok")
        request = self.factory.get("/archivio/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/?next=/archivio/", response.url)

    @override_settings(PORTALE_REQUIRE_LOGIN=True)
    def test_middleware_attivo_blocca_api_con_401(self):
        middleware = PortaleRequireLoginMiddleware(lambda req: "ok")
        request = self.factory.get("/api/v1/ha/push/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 401)
