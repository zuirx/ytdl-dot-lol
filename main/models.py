from django.db import models


class ErrorReport(models.Model):
    pipv4 = models.TextField(blank=True,null=True)
    date = models.DateTimeField(blank=True,null=True)
    error = models.TextField(blank=True,null=True)


class RequestLog(models.Model):
    ip_address = models.GenericIPAddressField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    path = models.CharField(max_length=2048)
    method = models.CharField(max_length=10, default="GET")
    user_agent = models.TextField(blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, db_index=True)
    country_name = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_download = models.BooleanField(default=False, db_index=True)
    download_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["ip_address", "timestamp"]),
            models.Index(fields=["is_download", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.ip_address} @ {self.timestamp:%Y-%m-%d %H:%M}"


class DailySummary(models.Model):
    date = models.DateField(unique=True)
    total_requests = models.PositiveIntegerField(default=0)
    unique_ips = models.PositiveIntegerField(default=0)
    total_downloads = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return str(self.date)


class AnalyticsDashboard(models.Model):
    """Proxy model used solely to create a top-level link in Django admin."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name_plural = "Analytics Dashboard"
