from django.contrib import admin

from .models import Workspace, WorkspaceMember


class WorkspaceMemberInline(admin.TabularInline):
    model = WorkspaceMember
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "owner__username", "owner__email")
    autocomplete_fields = ("owner",)
    inlines = (WorkspaceMemberInline,)


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "workspace", "user", "role")
    list_filter = ("role",)
    search_fields = ("workspace__name", "user__username", "user__email")
    autocomplete_fields = ("workspace", "user")
