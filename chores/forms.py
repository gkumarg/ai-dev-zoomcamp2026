from django import forms

from .models import Chore, Person


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["name"]

    def clean_name(self):
        # Trim whitespace before the uniqueness check runs, so "Alex" and
        # "Alex " don't sneak past as distinct people.
        return self.cleaned_data["name"].strip()


class ChoreForm(forms.ModelForm):
    class Meta:
        model = Chore
        fields = ["name", "effort"]
        widgets = {
            "effort": forms.NumberInput(attrs={"min": 1, "max": 5}),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()
