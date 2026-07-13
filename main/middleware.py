import logging
from .views import get_client_ip
from .models import RequestLog

logger = logging.getLogger(__name__)


class AnalyticsMiddleware:
    """Capture every request to the RequestLog table."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        path = request.path
        # Skip static files, media, admin internals and health probes
        if path.startswith(("/static/", "/media/", "/admin/", "/health")):
            return response

        ip = get_client_ip(request)
        is_download = any(
            frag in path
            for frag in (
                "download",
                "dl_from_opt",
                "dl_sel_playlist_yt",
                "mix_av",
                "initiate_download",
            )
        )

        download_url = ""
        if is_download and request.method == "POST":
            download_url = (
                request.POST.get("yt_link", "")
                or request.POST.get("url", "")
                or request.POST.get("action", "")
            )[:2048]

        try:
            RequestLog.objects.create(
                ip_address=ip,
                path=path[:2048],
                method=request.method,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
                is_download=is_download,
                download_url=download_url,
            )
        except Exception:
            logger.exception("Analytics logging failed")

        return response
