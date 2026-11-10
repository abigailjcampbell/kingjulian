from django import template

register = template.Library()


@register.filter
def cell(obj, path):
    """Resolve 'a.b.c' on obj, calling callables; empty values render as an em dash."""
    value = obj
    for part in path.split("."):
        if value is None:
            break
        value = getattr(value, part, None)
        if callable(value):
            value = value()
    return "—" if value in (None, "") else value
