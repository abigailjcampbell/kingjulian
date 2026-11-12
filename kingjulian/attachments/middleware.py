from django.http import HttpResponse
from django.urls import reverse

from .validators import human_size, max_bytes

REQUEST_OVERHEAD = 1024 * 1024        # multipart framing, the caption and the CSRF token


class UploadSizeMiddleware:
    """Refuse an oversized upload from its Content-Length header, *before* anything reads the body.

    A view cannot do this itself: Django's CSRF middleware (and any view that touches request.POST) parses the
    whole multipart body first. Keep this above CsrfViewMiddleware. A reverse proxy limit (e.g. nginx
    client_max_body_size) is still the first line of defence in production.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._prefix = None

    def __call__(self, request):
        if self._prefix is None:
            self._prefix = reverse("attachment_list") + "upload/"
        if request.method == "POST" and request.path.startswith(self._prefix):
            length = request.META.get("CONTENT_LENGTH", "")
            if not length.isdigit():
                return HttpResponse("Content-Length is required.", status=411, content_type="text/plain")
            if int(length) > max_bytes() + REQUEST_OVERHEAD:
                return HttpResponse(f"That upload is too large (limit {human_size(max_bytes())}).", status=413,
                                    content_type="text/plain")
        return self.get_response(request)
