from django import forms

from resources.models import Site, WorkCentre
from routing.models import Operation

from .models import Customer, Job, JobItem


class JobForm(forms.ModelForm):
    new_customer = forms.CharField(
        required=False, max_length=200, label="Or add a new customer",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    class Meta:
        model = Job
        fields = ["reference", "title", "customer", "customer_reference", "site", "due_date", "priority", "notes"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    field_order = [
        "reference", "title", "customer", "new_customer", "customer_reference",
        "due_date", "priority", "site", "notes",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].required = False
        self.fields["site"].queryset = Site.objects.filter(is_active=True)
        if not self.instance.pk and not self.initial.get("site"):
            self.initial["site"] = Site.objects.filter(is_active=True).first()

    def clean(self):
        cleaned = super().clean()
        cleaned["new_customer"] = (cleaned.get("new_customer") or "").strip()
        if not cleaned.get("customer") and not cleaned["new_customer"]:
            self.add_error("customer", "Choose a customer or add a new one.")
        return cleaned

    def save(self, commit=True):
        new_customer = self.cleaned_data.get("new_customer")
        if new_customer:
            self.instance.customer, _ = Customer.objects.get_or_create(name=new_customer)
        return super().save(commit)


class JobItemForm(forms.ModelForm):
    class Meta:
        model = JobItem
        fields = ["name", "product_type", "quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_type"].queryset = self.fields["product_type"].queryset.filter(is_active=True)
        self.fields["product_type"].help_text = "Decides which spec fields and route template the item gets."


class OperationForm(forms.ModelForm):
    class Meta:
        model = Operation
        fields = ["name", "work_centre", "estimated_minutes"]
        labels = {"estimated_minutes": "Estimated minutes"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work_centre"].queryset = WorkCentre.objects.filter(is_active=True)
