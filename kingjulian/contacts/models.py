from django.db import models

from core.models import TenantModel
from properties.models import Property


class Contact(TenantModel):
    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        VENDOR = "vendor", "Vendor"
        CONTRACTOR = "contractor", "Contractor"
        OWNER = "owner", "Owner"
        OTHER = "other", "Other"

    name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OTHER)
    company = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    properties = models.ManyToManyField(Property, through="ContactProperty", related_name="contacts")

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["account", "role"])]

    def __str__(self):
        return self.name


class ContactProperty(TenantModel):
    """Through table; tenant-owned so the cross-account FK check applies to both sides."""

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="property_links")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="contact_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["contact", "property"], name="contact_property_unique"),
        ]
