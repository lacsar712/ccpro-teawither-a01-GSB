from django.contrib import admin

from .models import Garden, Trough, TurnLedger, WitherBatch


@admin.register(Garden)
class GardenAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "altitudeBand")
    search_fields = ("name", "altitudeBand")


@admin.register(Trough)
class TroughAdmin(admin.ModelAdmin):
    list_display = ("id", "garden", "troughCode", "cultivar", "loadKg", "status")
    list_filter = ("status", "garden")
    search_fields = ("troughCode", "cultivar")


@admin.register(WitherBatch)
class WitherBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "trough",
        "startedAt",
        "targetMoisture",
        "actualMoisture",
        "rollGrade",
    )
    list_filter = ("rollGrade",)


@admin.register(TurnLedger)
class TurnLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "trough",
        "seq",
        "plannedAt",
        "actualAt",
        "operator",
        "isSettled",
    )
    list_filter = ("isSettled",)
    search_fields = ("trough__troughCode", "operator")
