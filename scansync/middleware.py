"""
Custom middlewares for ScanSync.
"""
import time
from importlib import import_module

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date


class SeparateAdminSessionMiddleware(SessionMiddleware):
    """
    Separates the Django Admin (/admin/) session from the main website session.

    By using a distinct session cookie for the Django admin site, logging in or
    out of /admin/ as a superuser does not overwrite or interfere with the active
    user session on the main application (e.g. students or teachers scanning in),
    and vice versa.
    """

    ADMIN_COOKIE_NAME = "scansync_admin_sessionid"

    def get_cookie_name(self, request):
        path = getattr(request, "path_info", "") or ""
        if path.startswith("/admin/") or path == "/admin":
            return self.ADMIN_COOKIE_NAME
        return settings.SESSION_COOKIE_NAME

    def process_request(self, request):
        cookie_name = self.get_cookie_name(request)
        session_key = request.COOKIES.get(cookie_name)
        request.session = self.SessionStore(session_key)

    def process_response(self, request, response):
        """
        If request.session was modified, or if the configuration is to save the
        session every time, save the changes and set the route-appropriate session
        cookie, or delete it if the session has been emptied.
        """
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response

        cookie_name = self.get_cookie_name(request)

        # The session should be deleted only if the session is entirely empty.
        if cookie_name in request.COOKIES and empty:
            response.delete_cookie(
                cookie_name,
                path=settings.SESSION_COOKIE_PATH,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            need_vary_cookie = True
        else:
            need_vary_cookie = accessed
            if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                if request.session.get_expire_at_browser_close():
                    max_age = None
                    expires = None
                else:
                    max_age = request.session.get_expiry_age()
                    expires_time = time.time() + max_age
                    expires = http_date(expires_time)

                if response.status_code < 500:
                    try:
                        request.session.save()
                    except UpdateError:
                        raise SessionInterrupted(
                            "The request's session was deleted before the "
                            "request completed. The user may have logged "
                            "out in a concurrent request, for example."
                        )
                    response.set_cookie(
                        cookie_name,
                        request.session.session_key,
                        max_age=max_age,
                        expires=expires,
                        domain=settings.SESSION_COOKIE_DOMAIN,
                        path=settings.SESSION_COOKIE_PATH,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
                    need_vary_cookie = True

        if need_vary_cookie:
            patch_vary_headers(response, ("Cookie",))
        return response


def csrf_failure(request, reason=""):
    """
    Handles CSRF verification failures gracefully.

    When an outdated or rotated CSRF token is submitted on a login or registration
    page (e.g. from an idle tab or browser history), automatically redirect the
    browser to a fresh GET request so valid tokens are loaded seamlessly without
    stranding the user on a 403 error screen.
    """
    path = getattr(request, "path_info", "") or ""
    if request.method == "POST" and any(p in path for p in ("/admin/login", "/login", "/register")):
        if "csrf_retry" not in request.GET:
            from django.http import HttpResponseRedirect

            full_path = request.get_full_path()
            sep = "&" if "?" in full_path else "?"
            return HttpResponseRedirect(f"{full_path}{sep}csrf_retry=1")

    from django.views.csrf import csrf_failure as default_failure

    return default_failure(request, reason=reason)

