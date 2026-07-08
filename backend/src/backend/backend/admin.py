from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import TibavaUser


@admin.register(TibavaUser)
class TibavaUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Tibava limits", {"fields": ("allowance", "max_video_size")}),
    )
    list_display = (
        "username",
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "allowance",
        "max_video_size",
    )
