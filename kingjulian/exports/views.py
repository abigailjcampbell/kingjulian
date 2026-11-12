from django.http import Http404
from django.views import View
from django.views.generic import TemplateView

from core.crud import TenantViewMixin

from .datasets import DATASETS
from .responses import csv_response, zip_response


class ExportHub(TenantViewMixin, TemplateView):
    requires_write = True                      # owners and managers
    template_name = "exports/hub.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["datasets"] = [(d, d.model.objects.count()) for d in DATASETS.values()]
        return ctx


class ExportDataset(TenantViewMixin, View):
    requires_write = True

    def get(self, request, key):
        if key not in DATASETS:
            raise Http404
        return csv_response(key, None, request.account)


class ExportAll(TenantViewMixin, View):
    requires_write = True

    def get(self, request):
        return zip_response(request.account)
