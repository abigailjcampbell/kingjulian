from django import forms

from properties.models import Property

from .models import Contact, ContactProperty


class ContactForm(forms.ModelForm):
    properties = forms.ModelMultipleChoiceField(queryset=Property.objects.none(), required=False)

    class Meta:
        model = Contact
        fields = ["name", "role", "company", "phone", "email", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tenant-scoped, so another account's property ids fail validation.
        self.fields["properties"].queryset = Property.objects.active()
        if self.instance.pk:
            self.fields["properties"].initial = list(
                ContactProperty.objects.filter(contact=self.instance).values_list("property_id", flat=True)
            )

    def _save_m2m(self):
        super()._save_m2m()
        wanted = {p.pk for p in self.cleaned_data["properties"]}
        links = ContactProperty.objects.filter(contact=self.instance)
        existing = set(links.values_list("property_id", flat=True))
        links.exclude(property_id__in=wanted).delete()
        for pid in wanted - existing:
            ContactProperty.objects.create(
                account_id=self.instance.account_id, contact=self.instance, property_id=pid
            )
