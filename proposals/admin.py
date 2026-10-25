"""Admin: proposals and papers can be browsed; the logs (model calls, steps) are read-only."""

from django.contrib import admin

from proposals.models import LLMCall, Paper, Proposal, Reference, StepLog


class ReadOnlyAdmin(admin.ModelAdmin):
    """Logs are evidence: nobody edits them by hand."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class ReferenceInline(admin.TabularInline):
    model = Reference
    extra = 0
    can_delete = False
    readonly_fields = ("number", "token", "text", "pmid", "url")


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "language", "client_ref", "created_at", "finished_at")
    list_filter = ("status", "language")
    search_fields = ("title", "client_ref", "id")
    readonly_fields = ("id", "created_at", "started_at", "finished_at", "heartbeat_at", "current_step")
    inlines = [ReferenceInline]


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = ("proposal", "role", "token", "pmid", "year", "stage", "selected")
    list_filter = ("role", "stage", "selected")
    search_fields = ("pmid", "title", "proposal__title")
    raw_id_fields = ("proposal",)


@admin.register(Reference)
class ReferenceAdmin(ReadOnlyAdmin):
    list_display = ("proposal", "number", "token", "pmid")
    raw_id_fields = ("proposal",)


@admin.register(LLMCall)
class LLMCallAdmin(ReadOnlyAdmin):
    list_display = ("proposal", "step", "model", "status", "prompt_tokens", "completion_tokens", "latency_ms")
    list_filter = ("status", "model", "step")
    raw_id_fields = ("proposal",)


@admin.register(StepLog)
class StepLogAdmin(ReadOnlyAdmin):
    list_display = ("proposal", "step", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("proposal",)
