from django.core.exceptions import ValidationError
from django.db import models

from core.models import TenantModel
from properties.models import Property, Unit


class InventoryItem(TenantModel):
    class Category(models.TextChoices):
        APPLIANCE = "appliance", "Appliance"
        HVAC = "hvac", "HVAC"
        PLUMBING = "plumbing", "Plumbing"
        ELECTRICAL = "electrical", "Electrical"
        FURNITURE = "furniture", "Furniture"
        TOOL = "tool", "Tool / equipment"
        SAFETY = "safety", "Safety (smoke detector, extinguisher)"
        OTHER = "other", "Other"

    class Condition(models.TextChoices):
        NEW = "new", "New"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        POOR = "poor", "Poor"
        NEEDS_REPLACEMENT = "replace", "Needs replacement"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="inventory_items")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="inventory_items")
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    quantity = models.PositiveIntegerField(default=1)
    serial_number = models.CharField(max_length=100, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    condition = models.CharField(max_length=20, choices=Condition.choices, default=Condition.GOOD)
    warranty_expires_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "name"]
        indexes = [
            models.Index(fields=["account", "property"]),
            models.Index(fields=["account", "warranty_expires_on"]),
        ]

    def __str__(self):
        return self.name

    def validate_unit_matches_property(self):
        if self.unit_id and self.property_id and self.unit.property_id != self.property_id:
            raise ValidationError({"unit": "That unit does not belong to the selected property."})

    def clean(self):
        super().clean()
        self.validate_unit_matches_property()

    def save(self, *args, **kwargs):
        self.validate_unit_matches_property()   # enforced even outside forms
        super().save(*args, **kwargs)
