from django import template

from attachments.models import Attachment
from attachments.targets import kind_for, TARGETS
from attachments.validators import ALLOWED_EXTENSIONS, human_size, max_bytes

register = template.Library()


@register.inclusion_tag("attachments/_panel.html", takes_context=True)
def attachments_panel(context, obj):
    """A 'Files' box for a record's edit page: list + (for writers) an upload form."""
    request = context.get("request")
    kind = kind_for(obj) if obj is not None else None
    if kind is None or not getattr(obj, "pk", None) or request is None or request.membership is None:
        return {"show": False}
    field = TARGETS[kind][1]
    items = list(Attachment.objects.filter(**{field: obj}).select_related("uploaded_by"))
    return {"show": True, "items": items, "kind": kind, "obj": obj, "request": request,
            "can_write": request.membership.can_write, "csrf_token": context.get("csrf_token"),
            "accept": ",".join("." + e for e in ALLOWED_EXTENSIONS), "limit": human_size(max_bytes())}
