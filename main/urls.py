from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views 

urlpatterns = [
    path('', views.home_yt, name='home_yt'),
    path('dl_from_opt/', views.dl_from_opt, name='dl_from_opt'),
    path('dl_sel_playlist_yt/', views.dl_sel_playlist_yt, name='dl_sel_playlist_yt'),
    path('mix_av/', views.mix_av, name='mix_av'),
    path('user_def_cookie/', views.user_def_cookie, name='user_def_cookie'),
    path('start-download/', views.start_download, name='start_download'),
    path('download-progress/<str:task_id>/', views.download_progress, name='download_progress'),
    path('serve-download/<str:task_id>/', views.serve_download, name='serve_download'),
    path('<path:subpath>/', views.home_yt, name='home_yt'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)