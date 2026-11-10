from django import forms

from .models import Property, Unit


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ["name", "property_type", "address_line1", "address_line2",
                  "city", "state", "postal_code", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["property", "name", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.active()
