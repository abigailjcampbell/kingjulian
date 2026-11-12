from django import template

from audit.models import ActivityLog
from audit.registry import SPECS

register = template.Library()
TYPES = {m._meta.model_name for m in SPECS}


@register.inclusion_tag("audit/_history.html", takes_context=True)
def history_panel(context, obj, limit=8):
    """Recent history of a record, for owners and managers."""
    request = context.get("request")
    if (obj is None or not getattr(obj, "pk", None) or request is None or request.membership is None
            or not request.membership.can_write or obj._meta.model_name not in TYPES):
        return {"show": False}
    key = obj._meta.model_name
    entries = list(ActivityLog.objects.filter(target_type=key, target_id=obj.pk)[:limit])
    return {"show": True, "entries": entries, "object_key": f"{key}:{obj.pk}"}
