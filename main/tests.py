import datetime
from unittest.mock import patch
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
            path="/health/",
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

        # Top pages should contain '/', '/thumb/', and '/health/'
        top_page_paths = [p["path"] for p in context["top_pages"]]
        self.assertIn("/", top_page_paths)
        self.assertIn("/thumb/", top_page_paths)
        self.assertIn("/health/", top_page_paths)

        # Top visitors should list 192.168.1.1 with 2 total requests
        top_ips = [v["ip_address"] for v in context["top_visitors"]]
        self.assertIn("192.168.1.1", top_ips)

        # Device and Browser breakdowns should be populated
        device_names = [d["name"] for d in context["device_breakdown"]]
        self.assertIn("Desktop", device_names)
        self.assertIn("Mobile / Tablet", device_names)


class RegionalProxyTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_country_name_and_code_lookups(self):
        from main.proxy_manager import get_country_name, get_country_code
        self.assertEqual(get_country_name('IN'), 'India')
        self.assertEqual(get_country_name('US'), 'United States')
        self.assertEqual(get_country_code('India'), 'IN')
        self.assertEqual(get_country_code('brazil'), 'BR')
        self.assertEqual(get_country_code('US'), 'US')

    def test_known_india_proxy(self):
        from main.proxy_manager import get_proxies_for_country, KNOWN_PROXIES
        self.assertIn('IN', KNOWN_PROXIES)
        self.assertIn('socks4://49.207.177.65:5678', KNOWN_PROXIES['IN'])
        proxies = get_proxies_for_country('IN')
        self.assertIn('socks4://49.207.177.65:5678', proxies)
        self.assertEqual(proxies[0], 'socks4://49.207.177.65:5678')

    def test_extract_countries_from_error(self):
        from main.proxy_manager import extract_countries_from_error
        err_msg = "ERROR: [youtube] MT8UDCwsH8A: The uploader has not made this video available in your country\nThis video is available in India."
        countries = extract_countries_from_error(Exception(err_msg))
        self.assertIn('IN', countries)

    def test_detect_region_view(self):
        response = self.client.get('/detect_region/')
        self.assertEqual(response.status_code, 400)

        with patch('main.views.detect_video_country') as mock_detect:
            mock_detect.return_value = {
                'restricted': True,
                'countries': ['IN'],
                'country_names': ['India'],
                'message': 'Available in: India'
            }
            res = self.client.get('/detect_region/?url=https://www.youtube.com/watch?v=MT8UDCwsH8A')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data['restricted'])
            self.assertIn('IN', data['countries'])

    def test_get_proxies_view(self):
        response = self.client.get('/get_proxies/')
        self.assertEqual(response.status_code, 400)

        with patch('main.views.get_proxies_for_country') as mock_proxies:
            mock_proxies.return_value = ['socks4://1.2.3.4:1080', 'socks5://5.6.7.8:1080']
            res = self.client.get('/get_proxies/?country=IN')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data['country'], 'IN')
            self.assertEqual(data['country_name'], 'India')
            self.assertEqual(len(data['proxies']), 2)

    def test_proxy_retry_loop_success(self):
        from main.proxy_manager import extract_or_download_with_proxy_retry
        with patch('main.proxy_manager.get_proxies_for_country') as mock_get_proxies, \
             patch('main.proxy_manager.filter_alive_proxies') as mock_alive, \
             patch('main.proxy_manager.yt_dlp.YoutubeDL') as mock_ydl_cls:
            
            mock_get_proxies.return_value = ['socks4://1.1.1.1:1080', 'socks5://2.2.2.2:1080']
            mock_alive.return_value = ['socks4://1.1.1.1:1080', 'socks5://2.2.2.2:1080']
            
            mock_ydl = mock_ydl_cls.return_value.__enter__.return_value
            # First proxy fails with timeout, second succeeds
            mock_ydl.extract_info.side_effect = [
                Exception("timed out"),
                {'title': 'Test Geo Video', 'id': 'MT8UDCwsH8A'}
            ]
            
            result = extract_or_download_with_proxy_retry(
                'https://www.youtube.com/watch?v=MT8UDCwsH8A',
                {'quiet': True},
                target_country='IN'
            )
            self.assertEqual(result['title'], 'Test Geo Video')
            self.assertEqual(mock_ydl.extract_info.call_count, 2)

    def test_proxy_retries_at_least_three_times_before_next_proxy(self):
        from main.proxy_manager import extract_or_download_with_proxy_retry
        with patch('main.proxy_manager.get_proxies_for_country') as mock_get_proxies, \
             patch('main.proxy_manager.filter_alive_proxies') as mock_alive, \
             patch('main.proxy_manager.yt_dlp.YoutubeDL') as mock_ydl_cls, \
             patch('main.proxy_manager.time.sleep'):
            
            mock_get_proxies.return_value = ['socks5://1.1.1.1:1080', 'socks5://2.2.2.2:1080']
            mock_alive.return_value = ['socks5://1.1.1.1:1080', 'socks5://2.2.2.2:1080']
            
            mock_ydl = mock_ydl_cls.return_value.__enter__.return_value
            # First proxy fails 3 times; second proxy succeeds on attempt 1
            mock_ydl.extract_info.side_effect = [
                Exception("Proxy 1 attempt 1 timeout"),
                Exception("Proxy 1 attempt 2 timeout"),
                Exception("Proxy 1 attempt 3 timeout"),
                {'title': 'Success on Proxy 2', 'id': 'MT8UDCwsH8A'}
            ]
            
            result = extract_or_download_with_proxy_retry(
                'https://www.youtube.com/watch?v=MT8UDCwsH8A',
                {'quiet': True},
                target_country='US'
            )
            self.assertEqual(result['title'], 'Success on Proxy 2')
            self.assertEqual(mock_ydl.extract_info.call_count, 4)

    def test_geoblock_ui_conditional_display(self):
        # By default, geoblock toggle should be unchecked and proxy bar hidden
        response = self.client.get('/', HTTP_HOST='127.0.0.1')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('id="geoblock_toggle"', content)
        self.assertIn('This video may be blocked in my region', content)
        self.assertIn('id="proxy-bar-container" style="display: none;"', content)
        self.assertNotIn('id="geoblock_toggle" name="enable_geoblock" onchange="toggleGeoblockOptions(this.checked)" checked', content)

    @patch('main.views.download_video_task.delay')
    def test_initiate_download_with_and_without_geoblock(self, mock_delay):
        from unittest.mock import MagicMock
        mock_task = MagicMock()
        mock_task.id = 'test-task-123'
        mock_delay.return_value = mock_task

        # 1. Without enable_geoblock, proxy and country must be blanked
        res1 = self.client.post('/initiate_download/', {
            'yt_link': 'https://www.youtube.com/watch?v=MT8UDCwsH8A',
            'action': 'video',
            'country': 'IN',
            'proxy': 'socks4://1.2.3.4:1080'
        }, HTTP_HOST='127.0.0.1')
        self.assertEqual(res1.status_code, 200)
        mock_delay.assert_called_with(
            'https://www.youtube.com/watch?v=MT8UDCwsH8A',
            type='video',
            itag=0,
            typeitag='',
            quality='best',
            proxy='',
            country=''
        )

        # 2. With enable_geoblock=on, proxy and country must be preserved
        res2 = self.client.post('/initiate_download/', {
            'yt_link': 'https://www.youtube.com/watch?v=MT8UDCwsH8A',
            'action': 'video',
            'enable_geoblock': 'on',
            'country': 'IN',
            'proxy': 'socks4://1.2.3.4:1080'
        }, HTTP_HOST='127.0.0.1')
        self.assertEqual(res2.status_code, 200)
        mock_delay.assert_called_with(
            'https://www.youtube.com/watch?v=MT8UDCwsH8A',
            type='video',
            itag=0,
            typeitag='',
            quality='best',
            proxy='socks4://1.2.3.4:1080',
            country='IN'
        )

