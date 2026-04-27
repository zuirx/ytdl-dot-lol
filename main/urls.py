from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views 

urlpatterns = [
    path('', views.home_yt, name='home_yt'),
    path('dl_from_opt/', views.dl_from_opt, name='dl_from_opt'),
    path('dl_sel_playlist_yt/', views.dl_sel_playlist_yt, name='dl_sel_playlist_yt'),
    path('mix_av/', views.mix_av, name='mix_av'),
    path('initiate_download/', views.initiate_download, name='initiate_download'),
    path('task_status/<str:task_id>/', views.task_status, name='task_status'),
    path('download_ready/<str:task_id>/', views.get_downloaded_file, name='download_ready'),
    path('<path:subpath>/', views.home_yt, name='home_yt'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)