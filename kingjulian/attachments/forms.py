from django import forms


class UploadForm(forms.Form):
    file = forms.FileField()
    caption = forms.CharField(required=False, max_length=200)
