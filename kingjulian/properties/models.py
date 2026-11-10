from django.db import models
from django.db.models import Q

from core.models import TenantModel


class Property(TenantModel):
    class Type(models.TextChoices):
        SINGLE_FAMILY = "single_family", "Single family"
        MULTI_FAMILY = "multi_family", "Multi-family"
        CONDO = "condo", "Condo / townhouse"
        COMMERCIAL = "commercial", "Commercial"
        LAND = "land", "Land"
        OTHER = "other", "Other"

    name = models.CharField(max_length=150)
    property_type = models.CharField(max_length=20, choices=Type.choices, default=Type.SINGLE_FAMILY)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"
        indexes = [models.Index(fields=["account", "name"])]

    def __str__(self):
        return self.name


class Unit(TenantModel):
    """Optional layer: apartment, suite, room, or zone inside a property."""

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    name = models.CharField(max_length=100)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "name"],
                condition=Q(archived_at__isnull=True),
                name="unit_name_unique_per_property_active",
            ),
        ]

    def __str__(self):
        return f"{self.property.name} / {self.name}"
