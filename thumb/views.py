import yt_dlp
import os
import logging
from django.shortcuts import render
from django.http import JsonResponse, FileResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

logger = logging.getLogger(__name__)


def get_video_info(url, extract_thumbnail=True):
    """
    Extract video information using yt-dlp.

    Args:
        url: Video URL
        extract_thumbnail: Whether to extract thumbnail data

    Returns:
        dict with video information
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }

    if not extract_thumbnail:
        ydl_opts['skip_download'] = True

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        if not info:
            return None

        # Extract thumbnails
        thumbnails = info.get('thumbnails', [])
        thumbnail_list = []
        for thumb in thumbnails:
            thumbnail_list.append({
                'id': thumb.get('id'),
                'url': thumb.get('url'),
                'width': thumb.get('width'),
                'height': thumb.get('height'),
                'resolution': thumb.get('resolution'),
            })

        # Extract subtitles
        subtitles = info.get('subtitles', {})
        automatic_captions = info.get('automatic_captions', {})

        subtitle_list = []
        for lang, subs in subtitles.items():
            if subs:
                subtitle_list.append({
                    'language': lang,
                    'type': 'manual',
                    'url': subs[0].get('url') if subs else None,
                })

        for lang, subs in automatic_captions.items():
            if subs and lang not in [s['language'] for s in subtitle_list]:
                subtitle_list.append({
                    'language': lang,
                    'type': 'automatic',
                    'url': subs[0].get('url') if subs else None,
                })

        # Build response
        result = {
            'id': info.get('id'),
            'title': info.get('title'),
            'description': info.get('description'),
            'uploader': info.get('uploader'),
            'uploader_id': info.get('uploader_id'),
            'channel': info.get('channel'),
            'channel_id': info.get('channel_id'),
            'duration': info.get('duration'),
            'view_count': info.get('view_count'),
            'like_count': info.get('like_count'),
            'comment_count': info.get('comment_count'),
            'upload_date': info.get('upload_date'),
            'timestamp': info.get('timestamp'),
            'webpage_url': info.get('webpage_url'),
            'original_url': info.get('original_url'),
            'extractor': info.get('extractor'),
            'extractor_key': info.get('extractor_key'),
            'playlist': info.get('playlist'),
            'playlist_index': info.get('playlist_index'),
            'thumbnail': info.get('thumbnail'),
            'thumbnails': thumbnail_list,
            'subtitles': subtitle_list,
            'tags': info.get('tags', []),
            'categories': info.get('categories', []),
            'age_limit': info.get('age_limit'),
            'is_live': info.get('is_live'),
            'was_live': info.get('was_live'),
        }

        return result


@require_http_methods(["GET", "POST"])
def home(request):
    """
    Main page for thumb app - search for video and display info.
    """
    context = {
        'video_info': None,
        'error': None,
        'url': '',
    }

    if request.method == 'POST':
        url = request.POST.get('video_url', '').strip()
        context['url'] = url

        if not url:
            context['error'] = 'Please provide a valid URL.'
        else:
            try:
                video_info = get_video_info(url)
                if video_info:
                    context['video_info'] = video_info
                else:
                    context['error'] = 'Could not extract video information. The URL may be invalid or the video may be private.'
            except Exception as e:
                logger.exception(f"Error extracting video info: {e}")
                context['error'] = f'Error: {str(e)}'

    return render(request, 'thumb/home.html', context)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def get_info_api(request):
    """
    API endpoint to get video information as JSON.
    """
    if request.method == 'POST':
        url = request.POST.get('url', '').strip()
    else:
        url = request.GET.get('url', '').strip()

    if not url:
        return JsonResponse({'error': 'No URL provided'}, status=400)

    try:
        video_info = get_video_info(url)
        if video_info:
            return JsonResponse(video_info)
        else:
            return JsonResponse({'error': 'Could not extract video information'}, status=404)
    except Exception as e:
        logger.exception(f"API error for URL {url}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def download_thumbnail(request):
    """
    Download thumbnail image for a video.
    """
    url = request.GET.get('url', '').strip()
    quality = request.GET.get('quality', 'maxresdefault')  # maxresdefault, sddefault, hqdefault, mqdefault, default

    if not url:
        return JsonResponse({'error': 'No URL provided'}, status=400)

    try:
        # Extract video ID from YouTube URL
        video_id = None
        if 'youtube.com/watch?v=' in url:
            video_id = url.split('v=')[-1].split('&')[0]
        elif 'youtu.be/' in url:
            video_id = url.split('youtu.be/')[-1].split('?')[0]
        elif 'youtube.com/shorts/' in url:
            video_id = url.split('shorts/')[-1].split('?')[0]

        if not video_id:
            # Try to get thumbnail from yt-dlp
            video_info = get_video_info(url, extract_thumbnail=True)
            if video_info and video_info.get('thumbnail'):
                thumbnail_url = video_info['thumbnail']
            else:
                return JsonResponse({'error': 'Could not extract video ID or thumbnail'}, status=404)
        else:
            # Construct thumbnail URL
            thumbnail_url = f'https://img.youtube.com/vi/{video_id}/{quality}.jpg'

        # Download and return the thumbnail
        import requests
        response = requests.get(thumbnail_url, stream=True, timeout=10)

        if response.status_code == 200:
            return HttpResponse(response.content, content_type='image/jpeg')
        else:
            return JsonResponse({'error': 'Could not download thumbnail'}, status=404)

    except Exception as e:
        logger.exception(f"Thumbnail download error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def download_video(request):
    """
    Download a video in a specific format.
    """
    if request.method == 'POST':
        url = request.POST.get('url', '').strip()
        format_id = request.POST.get('format_id', 'best')
    else:
        url = request.GET.get('url', '').strip()
        format_id = request.GET.get('format_id', 'best')

    if not url:
        return JsonResponse({'error': 'No URL provided'}, status=400)

    try:
        import tempfile
        import glob as glob_module

        # Create temporary directory for download
        temp_dir = tempfile.mkdtemp()

        ydl_opts = {
            'format': format_id,
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # Find the downloaded file
            downloaded_files = glob_module.glob(os.path.join(temp_dir, '*'))

            if downloaded_files:
                file_path = downloaded_files[0]
                title = info.get('title', 'video')
                ext = os.path.splitext(file_path)[1].lstrip('.') or 'mp4'

                # Schedule file for cleanup (using the main app's cleanup if available)
                # For now, we'll just delete after sending
                import threading
                def cleanup():
                    import time
                    time.sleep(3600)  # 1 hour
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        if os.path.exists(temp_dir):
                            os.rmdir(temp_dir)
                    except:
                        pass

                threading.Thread(target=cleanup, daemon=True).start()

                return FileResponse(
                    open(file_path, 'rb'),
                    as_attachment=True,
                    filename=f'{title}.{ext}'
                )
            else:
                return JsonResponse({'error': 'Download failed - no file found'}, status=500)

    except Exception as e:
        logger.exception(f"Download error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_subtitles(request):
    """
    Get subtitles for a video.
    """
    url = request.GET.get('url', '').strip()
    lang = request.GET.get('lang', 'en')

    if not url:
        return JsonResponse({'error': 'No URL provided'}, status=400)

    try:
        import tempfile

        temp_dir = tempfile.mkdtemp()

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [lang],
            'skip_download': True,
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=False)

            # Find subtitle files
            import glob as glob_module
            subtitle_files = glob_module.glob(os.path.join(temp_dir, f'*.{lang}.*'))

            if subtitle_files:
                subtitle_path = subtitle_files[0]
                return FileResponse(
                    open(subtitle_path, 'rb'),
                    as_attachment=True,
                    filename=os.path.basename(subtitle_path)
                )
            else:
                return JsonResponse({'error': f'No subtitles found for language: {lang}'}, status=404)

    except Exception as e:
        logger.exception(f"Subtitle download error: {e}")
        return JsonResponse({'error': str(e)}, status=500)
