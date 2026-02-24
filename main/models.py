from django.db import models


class ErrorReport(models.Model):
    pipv4 = models.TextField(blank=True,null=True)
    date = models.DateTimeField(blank=True,null=True)
    error = models.TextField(blank=True,null=True)
