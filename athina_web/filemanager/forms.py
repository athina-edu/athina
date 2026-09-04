from django import forms


class FileFieldForm(forms.Form):
    file_field = forms.FileField(required=False)
