from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone as dj_timezone
from .timezones import DEFAULT as DEFAULT_TIMEZONE, load_zone, validate_timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Email-login user. Users are global; tenancy comes from Membership."""

    username = None
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_ci_unique"),
        ]

    def __str__(self):
        return self.email


class Account(models.Model):
    """The tenant. Every tenant-owned row points at exactly one Account."""

    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    # What "today" means for due dates, overdue status, reminders and the calendar (IANA name).
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE, validators=[validate_timezone])

    def __str__(self):
        return self.name

    @property
    def zone(self):
        """tzinfo for this account; falls back to UTC if the stored name is unusable."""
        return load_zone(self.timezone)

    def today(self):
        """The account's current calendar date, right now."""
        return dj_timezone.localdate(timezone=self.zone)


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "account"], name="membership_user_account_unique"),
        ]

    @property
    def can_write(self) -> bool:
        return self.role in (self.Role.OWNER, self.Role.MANAGER)

    def __str__(self):
        return f"{self.user} @ {self.account} ({self.role})"
