from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from django.db.models import Count, functions
from django.utils import timezone
from datetime import timedelta
from .models import RequestLog, ErrorReport, DailySummary, AnalyticsDashboard


@admin.register(ErrorReport)
class ErrorReportAdmin(admin.ModelAdmin):
    list_display = ("pipv4", "date", "error")
    list_filter = ("date",)
    search_fields = ("pipv4", "error")


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "ip_address",
        "timestamp",
        "path",
        "country_name",
        "city",
        "is_download",
    )
    list_filter = ("is_download", "country_name", "method", "timestamp")
    search_fields = ("ip_address", "path", "download_url", "user_agent")
    date_hierarchy = "timestamp"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "dashboard/",
                self.admin_site.admin_view(self.dashboard_view),
                name="requestlog_dashboard",
            ),
        ]
        return custom_urls + urls

    def dashboard_view(self, request):
        today = timezone.now().date()
        week_ago = timezone.now() - timedelta(days=7)
        month_ago = timezone.now() - timedelta(days=30)

        total_requests = RequestLog.objects.count()
        requests_today = RequestLog.objects.filter(timestamp__date=today).count()
        requests_week = RequestLog.objects.filter(timestamp__gte=week_ago).count()
        unique_ips_total = (
            RequestLog.objects.values("ip_address").distinct().count()
        )
        unique_ips_today = (
            RequestLog.objects.filter(timestamp__date=today)
            .values("ip_address")
            .distinct()
            .count()
        )
        total_downloads = RequestLog.objects.filter(is_download=True).count()
        downloads_today = (
            RequestLog.objects.filter(is_download=True, timestamp__date=today).count()
        )

        top_countries = (
            RequestLog.objects.exclude(country_name="")
            .values("country_name", "country_code")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        daily_counts = (
            RequestLog.objects.filter(timestamp__gte=week_ago)
            .annotate(date=functions.TruncDate("timestamp"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        top_links = (
            RequestLog.objects.filter(is_download=True)
            .exclude(download_url="")
            .values("download_url")
            .annotate(count=Count("id"))
            .order_by("-count")[:15]
        )

        map_data = list(
            RequestLog.objects.exclude(country_code="")
            .values("country_code", "country_name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "total_requests": total_requests,
            "requests_today": requests_today,
            "requests_week": requests_week,
            "unique_ips_total": unique_ips_total,
            "unique_ips_today": unique_ips_today,
            "total_downloads": total_downloads,
            "downloads_today": downloads_today,
            "top_countries": top_countries,
            "daily_counts": daily_counts,
            "top_links": top_links,
            "map_data": map_data,
        }
        return render(
            request, "admin/main/requestlog/dashboard.html", context
        )


@admin.register(DailySummary)
class DailySummaryAdmin(admin.ModelAdmin):
    list_display = ("date", "total_requests", "unique_ips", "total_downloads")
    date_hierarchy = "date"


class AnalyticsDashboardAdmin(admin.ModelAdmin):
    """
    A dummy admin entry that hijacks the changelist view and renders
    the same rich dashboard.  This gives us a friendly sidebar link.
    """

    change_list_template = "admin/main/analyticsdashboard/change_list.html"

    def changelist_view(self, request, extra_context=None):
        # Re-use the real dashboard logic
        return RequestLogAdmin(RequestLog, self.admin_site).dashboard_view(request)

    def get_queryset(self, request):
        # Prevent the default queryset machinery from complaining
        return RequestLog.objects.none()

    def get_changelist_instance(self, request):
        # Skip the normal changelist instance construction
        return None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AnalyticsDashboard, AnalyticsDashboardAdmin)
