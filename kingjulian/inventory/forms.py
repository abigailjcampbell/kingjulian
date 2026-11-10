from django import forms

from properties.models import Property, Unit

from .models import InventoryItem

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["property", "unit", "name", "category", "quantity", "serial_number",
                  "purchase_date", "cost", "condition", "warranty_expires_on", "notes"]
        widgets = {
            "purchase_date": _DATE,
            "warranty_expires_on": _DATE,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.active()
        self.fields["unit"].queryset = Unit.objects.active().select_related("property")
