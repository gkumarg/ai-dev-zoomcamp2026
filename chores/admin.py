from django.contrib import admin

from .models import Chore, History, Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(Chore)
class ChoreAdmin(admin.ModelAdmin):
    list_display = ["name", "effort", "status", "assigned_to"]
    list_filter = ["status", "effort"]
    search_fields = ["name"]


@admin.register(History)
class HistoryAdmin(admin.ModelAdmin):
    list_display = ["person", "chore", "effort", "completed_at"]
    list_filter = ["person"]
    date_hierarchy = "completed_at"
