import json
from datetime import timedelta
from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from django.db.models import Count, Q, Max, functions
from django.utils import timezone
from .models import RequestLog, ErrorReport, DailySummary, AnalyticsDashboard, SiteSetting


def parse_user_agent(ua_string):
    """Categorize user agent string into device type and browser."""
    if not ua_string:
        return "Unknown", "Unknown"
    ua = ua_string.lower()

    # Device detection
    if any(bot in ua for bot in ("bot", "spider", "crawl", "curl", "wget", "python", "http", "headless")):
        device = "Bot / Tool"
    elif any(m in ua for m in ("iphone", "ipad", "ipod", "android", "mobile", "phone", "tablet")):
        device = "Mobile / Tablet"
    else:
        device = "Desktop"

    # Browser detection
    if "edg" in ua:
        browser = "Edge"
    elif "opr" in ua or "opera" in ua:
        browser = "Opera"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    elif "curl" in ua:
        browser = "cURL"
    elif "python" in ua:
        browser = "Python"
    elif any(b in ua for b in ("bot", "spider", "crawl")):
        browser = "Bot / Crawler"
    else:
        browser = "Other"

    return device, browser


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
        "is_download",
    )
    list_filter = ("is_download", "method", "timestamp")
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
        now = timezone.now()
        today = now.date()
        week_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Overview Totals & Periodic Breakdown
        total_requests = RequestLog.objects.count()
        requests_today = RequestLog.objects.filter(timestamp__date=today).count()
        requests_week = RequestLog.objects.filter(timestamp__gte=week_ago).count()
        requests_month = RequestLog.objects.filter(timestamp__gte=month_start).count()

        unique_ips_total = (
            RequestLog.objects.values("ip_address").distinct().count()
        )
        unique_ips_today = (
            RequestLog.objects.filter(timestamp__date=today)
            .values("ip_address")
            .distinct()
            .count()
        )
        unique_ips_week = (
            RequestLog.objects.filter(timestamp__gte=week_ago)
            .values("ip_address")
            .distinct()
            .count()
        )
        unique_ips_month = (
            RequestLog.objects.filter(timestamp__gte=month_start)
            .values("ip_address")
            .distinct()
            .count()
        )

        total_views = RequestLog.objects.filter(is_download=False).count()
        views_today = (
            RequestLog.objects.filter(is_download=False, timestamp__date=today).count()
        )
        views_week = (
            RequestLog.objects.filter(is_download=False, timestamp__gte=week_ago).count()
        )
        views_month = (
            RequestLog.objects.filter(is_download=False, timestamp__gte=month_start).count()
        )

        total_downloads = RequestLog.objects.filter(is_download=True).count()
        downloads_today = (
            RequestLog.objects.filter(is_download=True, timestamp__date=today).count()
        )
        downloads_week = (
            RequestLog.objects.filter(is_download=True, timestamp__gte=week_ago).count()
        )
        downloads_month = (
            RequestLog.objects.filter(is_download=True, timestamp__gte=month_start).count()
        )

        conversion_rate = (
            round((total_downloads / total_views) * 100, 1) if total_views else 0
        )
        avg_views_per_ip = (
            round(total_views / unique_ips_total, 1) if unique_ips_total else 0
        )
        avg_downloads_per_ip = (
            round(total_downloads / unique_ips_total, 1) if unique_ips_total else 0
        )

        # -------------------------------------------------------------------
        # Monthly Unique IPs & Performance ("unique ip in its months")
        # -------------------------------------------------------------------
        monthly_stats = list(
            RequestLog.objects
            .annotate(month=functions.TruncMonth("timestamp"))
            .values("month")
            .annotate(
                unique_ips=Count("ip_address", distinct=True),
                page_views=Count("id", filter=Q(is_download=False)),
                downloads=Count("id", filter=Q(is_download=True)),
                total_requests=Count("id"),
            )
            .order_by("-month")
        )

        for m in monthly_stats:
            u = m["unique_ips"]
            r = m["total_requests"]
            m["views_per_ip"] = round(m["page_views"] / u, 1) if u else 0
            m["downloads_per_ip"] = round(m["downloads"] / u, 1) if u else 0
            m["conversion_rate"] = round((m["downloads"] / r) * 100, 1) if r else 0

        monthly_chart_data = sorted(monthly_stats, key=lambda x: x["month"])[-12:]
        monthly_chart_labels = json.dumps([m["month"].strftime("%b %Y") for m in monthly_chart_data])
        monthly_chart_ips = json.dumps([m["unique_ips"] for m in monthly_chart_data])
        monthly_chart_views = json.dumps([m["page_views"] for m in monthly_chart_data])
        monthly_chart_downloads = json.dumps([m["downloads"] for m in monthly_chart_data])

        # -------------------------------------------------------------------
        # Daily Traffic Trends
        # -------------------------------------------------------------------
        daily_counts = list(
            RequestLog.objects.filter(timestamp__gte=thirty_days_ago)
            .annotate(date=functions.TruncDate("timestamp"))
            .values("date")
            .annotate(
                total=Count("id"),
                views=Count("id", filter=Q(is_download=False)),
                downloads=Count("id", filter=Q(is_download=True)),
                unique_ips=Count("ip_address", distinct=True),
            )
            .order_by("date")
        )

        if not daily_counts:
            daily_counts = list(
                RequestLog.objects
                .annotate(date=functions.TruncDate("timestamp"))
                .values("date")
                .annotate(
                    total=Count("id"),
                    views=Count("id", filter=Q(is_download=False)),
                    downloads=Count("id", filter=Q(is_download=True)),
                    unique_ips=Count("ip_address", distinct=True),
                )
                .order_by("date")[:30]
            )

        daily_chart_labels = json.dumps([d["date"].strftime("%b %d") for d in daily_counts])
        daily_chart_views = json.dumps([d["views"] for d in daily_counts])
        daily_chart_downloads = json.dumps([d["downloads"] for d in daily_counts])
        daily_chart_ips = json.dumps([d["unique_ips"] for d in daily_counts])

        # -------------------------------------------------------------------
        # Other Users Views Statistics:
        # 1. Top Viewed Pages
        # -------------------------------------------------------------------
        top_pages = list(
            RequestLog.objects.filter(is_download=False)
            .values("path")
            .annotate(
                views=Count("id"),
                unique_ips=Count("ip_address", distinct=True),
            )
            .order_by("-views")[:15]
        )
        for p in top_pages:
            p["percentage"] = (
                round((p["views"] / total_views) * 100, 1) if total_views else 0
            )

        # 2. Top Downloaded Links
        top_links = list(
            RequestLog.objects.filter(is_download=True)
            .exclude(download_url="")
            .values("download_url")
            .annotate(count=Count("id"))
            .order_by("-count")[:15]
        )

        # 3. Top Visitor IPs (Most active users)
        top_visitors = list(
            RequestLog.objects.values("ip_address")
            .annotate(
                total_requests=Count("id"),
                views=Count("id", filter=Q(is_download=False)),
                downloads=Count("id", filter=Q(is_download=True)),
                last_seen=Max("timestamp"),
            )
            .order_by("-total_requests")[:15]
        )

        # 4. Device and Browser Breakdown
        ua_records = list(
            RequestLog.objects.exclude(user_agent="")
            .values("user_agent")
            .annotate(count=Count("id"))
            .order_by("-count")[:200]
        )
        device_stats = {}
        browser_stats = {}
        total_ua_count = 0

        for r in ua_records:
            dev, brw = parse_user_agent(r["user_agent"])
            c = r["count"]
            device_stats[dev] = device_stats.get(dev, 0) + c
            browser_stats[brw] = browser_stats.get(brw, 0) + c
            total_ua_count += c

        device_breakdown = [
            {
                "name": k,
                "count": v,
                "percentage": round((v / total_ua_count) * 100, 1) if total_ua_count else 0,
            }
            for k, v in sorted(device_stats.items(), key=lambda x: x[1], reverse=True)
        ]
        browser_breakdown = [
            {
                "name": k,
                "count": v,
                "percentage": round((v / total_ua_count) * 100, 1) if total_ua_count else 0,
            }
            for k, v in sorted(browser_stats.items(), key=lambda x: x[1], reverse=True)
        ]

        # 5. Recent Page Views
        recent_views = list(
            RequestLog.objects.filter(is_download=False)
            .order_by("-timestamp")
            .values("timestamp", "ip_address", "path", "method", "user_agent")[:15]
        )
        for rv in recent_views:
            dev, brw = parse_user_agent(rv.get("user_agent", ""))
            rv["device"] = dev
            rv["browser"] = brw

        context = {
            **self.admin_site.each_context(request),
            "title": "Analytics Dashboard",
            "opts": self.model._meta,
            "total_requests": total_requests,
            "requests_today": requests_today,
            "requests_week": requests_week,
            "requests_month": requests_month,
            "unique_ips_total": unique_ips_total,
            "unique_ips_today": unique_ips_today,
            "unique_ips_week": unique_ips_week,
            "unique_ips_month": unique_ips_month,
            "total_views": total_views,
            "views_today": views_today,
            "views_week": views_week,
            "views_month": views_month,
            "total_downloads": total_downloads,
            "downloads_today": downloads_today,
            "downloads_week": downloads_week,
            "downloads_month": downloads_month,
            "conversion_rate": conversion_rate,
            "avg_views_per_ip": avg_views_per_ip,
            "avg_downloads_per_ip": avg_downloads_per_ip,
            "monthly_stats": monthly_stats,
            "monthly_chart_labels": monthly_chart_labels,
            "monthly_chart_ips": monthly_chart_ips,
            "monthly_chart_views": monthly_chart_views,
            "monthly_chart_downloads": monthly_chart_downloads,
            "daily_counts": daily_counts,
            "daily_chart_labels": daily_chart_labels,
            "daily_chart_views": daily_chart_views,
            "daily_chart_downloads": daily_chart_downloads,
            "daily_chart_ips": daily_chart_ips,
            "top_pages": top_pages,
            "top_links": top_links,
            "top_visitors": top_visitors,
            "device_breakdown": device_breakdown,
            "browser_breakdown": browser_breakdown,
            "recent_views": recent_views,
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
        return RequestLogAdmin(self.model, self.admin_site).dashboard_view(request)

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

@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ("__str__", "warning_text", "youtube_limit_exception_ips")

    def has_add_permission(self, request):
        # Allow only one instance of SiteSetting
        return not SiteSetting.objects.exists()
