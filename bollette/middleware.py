import logging
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect

logger = logging.getLogger("bollette.middleware")


class PortaleRequireLoginMiddleware:
    """Middleware opzionale per richiedere autenticazione su tutte le viste del portale.

    Attivabile tramite PORTALE_REQUIRE_LOGIN=True nelle variabili d'ambiente o settings.py.
    Di default è disattivato (False) per mantenere retrocompatibilità con l'uso domestico.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        require_login = getattr(settings, "PORTALE_REQUIRE_LOGIN", False)
        if require_login and not request.user.is_authenticated:
            path = request.path_info
            login_url = getattr(settings, "LOGIN_URL", "/admin/login/")

            # Consenti static, media e la pagina di login stessa
            if (
                path.startswith("/static/")
                or path.startswith("/media/")
                or path.startswith("/admin/login/")
                or path == login_url
            ):
                return self.get_response(request)

            # Per chiamate API AJAX / webhook che richiedono JSON
            if path.startswith("/api/"):
                return JsonResponse(
                    {
                        "ok": False,
                        "errore": "Autenticazione richiesta per accedere alle API del portale.",
                    },
                    status=401,
                )

            # Reindirizza a login con parametro next
            return redirect(f"{login_url}?next={path}")

        return self.get_response(request)
