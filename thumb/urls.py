from django.urls import path
from . import views

app_name = 'thumb'

urlpatterns = [
    path('', views.home, name='home'),
    path('api/info/', views.get_info_api, name='api_info'),
    path('api/thumbnail/', views.download_thumbnail, name='download_thumbnail'),
    path('api/download/', views.download_video, name='download_video'),
    path('api/subtitles/', views.get_subtitles, name='get_subtitles'),
]
