import datetime
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from main.models import RequestLog


class AnalyticsDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="testpassword123",
        )
        self.client.login(username="admin_user", password="testpassword123")

    def test_analytics_dashboard_renders(self):
        response = self.client.get("/admin/main/analyticsdashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics Dashboard")
        self.assertContains(response, "Unique Visitors (IPs)")
        self.assertContains(response, "Unique IPs & Monthly Performance")
        self.assertContains(response, "Top Visited Pages (User Views)")

    def test_monthly_unique_ips_and_user_views_statistics(self):
        # Create test request logs across different months
        dt_month1 = timezone.make_aware(datetime.datetime(2026, 8, 15, 10, 0, 0))
        dt_month2 = timezone.make_aware(datetime.datetime(2026, 9, 20, 11, 0, 0))

        # Month 1: 2 unique IPs. One is view (/), one is download.
        log1 = RequestLog.objects.create(
            ip_address="192.168.1.1",
            path="/",
            method="GET",
            is_download=False,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        )
        RequestLog.objects.filter(id=log1.id).update(timestamp=dt_month1)

        log2 = RequestLog.objects.create(
            ip_address="192.168.1.2",
            path="/dl_from_opt/",
            method="POST",
            is_download=True,
            download_url="https://www.youtube.com/watch?v=example1",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605.1.15",
        )
        RequestLog.objects.filter(id=log2.id).update(timestamp=dt_month1)

        # Month 2: 2 unique IPs. Both are views.
        log3 = RequestLog.objects.create(
            ip_address="192.168.1.1",
            path="/thumb/",
            method="GET",
            is_download=False,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        )
        RequestLog.objects.filter(id=log3.id).update(timestamp=dt_month2)

        log4 = RequestLog.objects.create(
            ip_address="192.168.1.3",
            path="/terms-privacy/",
            method="GET",
            is_download=False,
            user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
        )
        RequestLog.objects.filter(id=log4.id).update(timestamp=dt_month2)

        response = self.client.get("/admin/main/analyticsdashboard/")
        self.assertEqual(response.status_code, 200)

        context = response.context
        self.assertIn("monthly_stats", context)
        monthly_stats = context["monthly_stats"]

        # Verify monthly breakdown of unique IPs
        # Month 2 (Sept 2026): 2 unique IPs (192.168.1.1 and 192.168.1.3), 2 page views, 0 downloads
        sept_stat = next((m for m in monthly_stats if m["month"].month == 9), None)
        self.assertIsNotNone(sept_stat)
        self.assertEqual(sept_stat["unique_ips"], 2)
        self.assertEqual(sept_stat["page_views"], 2)
        self.assertEqual(sept_stat["downloads"], 0)

        # Month 1 (Aug 2026): 2 unique IPs (192.168.1.1 and 192.168.1.2), 1 view, 1 download
        aug_stat = next((m for m in monthly_stats if m["month"].month == 8), None)
        self.assertIsNotNone(aug_stat)
        self.assertEqual(aug_stat["unique_ips"], 2)
        self.assertEqual(aug_stat["page_views"], 1)
        self.assertEqual(aug_stat["downloads"], 1)

        # Total unique IPs across all months should be 3 (1.1, 1.2, 1.3)
        self.assertEqual(context["unique_ips_total"], 3)
        self.assertEqual(context["total_views"], 3)
        self.assertEqual(context["total_downloads"], 1)

        # Top pages should contain '/', '/thumb/', and '/terms-privacy/'
        top_page_paths = [p["path"] for p in context["top_pages"]]
        self.assertIn("/", top_page_paths)
        self.assertIn("/thumb/", top_page_paths)
        self.assertIn("/terms-privacy/", top_page_paths)

        # Top visitors should list 192.168.1.1 with 2 total requests
        top_ips = [v["ip_address"] for v in context["top_visitors"]]
        self.assertIn("192.168.1.1", top_ips)

        # Device and Browser breakdowns should be populated
        device_names = [d["name"] for d in context["device_breakdown"]]
        self.assertIn("Desktop", device_names)
        self.assertIn("Mobile / Tablet", device_names)
