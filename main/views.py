import yt_dlp, os, re, random, glob as glob_module, subprocess, logging, requests, zipfile, threading, time, shutil
from datetime import datetime, timedelta, timezone as tz
from importlib.metadata import version
from django.shortcuts import render, redirect
from django.http import FileResponse, JsonResponse
from django.utils import timezone
from django.contrib import messages
from django.http import HttpResponse


from celery.result import AsyncResult
from .tasks import download_video_task, download_playlist_task

from django.core.cache import cache
from django.conf import settings
from .models import ErrorReport

logger = logging.getLogger(__name__)

# Constants are now in settings.py

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0]
    return request.META.get('REMOTE_ADDR')

def report_error(request, error_msg):
    if 'No link provided' in error_msg: return

    messages.error(request, error_msg)
    ip = get_client_ip(request)
    
    # Check for duplicates in the last 5 minutes to prevent spam attacks
    five_min_ago = timezone.now() - timedelta(minutes=5)
    exists = ErrorReport.objects.filter(
        pipv4=ip, 
        error=str(error_msg), 
        date__gte=five_min_ago
    ).exists()
    
    if not exists:
        ErrorReport.objects.create(pipv4=ip, date=timezone.now(), error=str(error_msg))

DIR_DOWNLOAD = settings.DIR_DOWNLOAD
DIR_MIX      = settings.DIR_MIX
DIR_PLAYLIST = settings.DIR_PLAYLIST
GITREPOLINK  = 'https://api.github.com/repos/zuirx/ytdl-dot-lol/commits' # Change this for your fork!

COOKIES_PATH = os.path.abspath(os.path.join(settings.BASE_DIR, 'cookie.txt'))

def _is_youtube(url):
    return 'youtube.com' in url or 'youtu.be' in url

def _inject_cookies(opts, url):
    if not _is_youtube(url):
        return opts
    if os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH
    else:
        opts['cookiesfrombrowser'] = ('chrome', None, None, None)
    return opts

def _age_restricted_error(exc):
    msg = str(exc).lower()
    return any(p in msg for p in ('sign in to confirm your age', 'confirm your age', 'age restriction', 'this video may be inappropriate'))

import subprocess

def _apply_node_js(opts):
    """Ensure yt-dlp can find node.exe for YouTube n-sig challenges."""
    try:
        node_path = subprocess.check_output(['where', 'node'], text=True, stderr=subprocess.DEVNULL).strip().split('\n')[0]
    except Exception:
        node_path = 'node'
    ea = opts.setdefault('extractor_args', {})
    youtube_ea = ea.setdefault('youtube', [])
    if isinstance(youtube_ea, list):
        youtube_ea.append(f'player_js={node_path}')
        youtube_ea.append('js_runtimes=node')
    elif isinstance(youtube_ea, dict):
        youtube_ea.setdefault('player_js', node_path)
        youtube_ea.setdefault('js_runtimes', ['node'])
    return opts

def _format_not_available_error(exc):
    msg = str(exc).lower()
    return 'requested format is not available' in msg

def _extract_with_cookie_fallback(url, opts, download=False):
    """Run yt-dlp without cookies first; retry with cookies on failure,
    then fall back to generic 'best' format if still unavailable."""
    _apply_node_js(opts)
    opts_clean = {k: v for k, v in opts.items() if k not in ('cookiefile', 'cookiesfrombrowser')}
    _apply_node_js(opts_clean)

    def _retry_with_best(ydl_opts):
        logger.warning("Format unavailable – falling back to 'best' for %s", url)
        ydl_opts['format'] = 'best'
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=download)

    def _retry_with_cookies(ydl_opts, err):
        if _is_youtube(url):
            logger.warning("Retrying %s with cookies after: %s", url, err)
            _inject_cookies(ydl_opts, url)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=download)
            except yt_dlp.utils.DownloadError as e2:
                if _format_not_available_error(e2):
                    return _retry_with_best(ydl_opts)
                raise

    try:
        with yt_dlp.YoutubeDL(opts_clean) as ydl:
            return ydl.extract_info(url, download=download)
    except yt_dlp.utils.DownloadError as e:
        if _age_restricted_error(e) or _format_not_available_error(e):
            return _retry_with_cookies(opts, str(e))
        raise

# ---------------------------------------------------------------------------
# File cleanup registry — paths are deleted 1 hour after download
# ---------------------------------------------------------------------------
_pending_deletes: dict = {}
_pending_lock = threading.Lock()

def _schedule_delete(path, delay=3600):
    with _pending_lock:
        _pending_deletes[path] = time.time() + delay

def _cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with _pending_lock:
            expired = [p for p, t in _pending_deletes.items() if now >= t]
        for path in expired:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
                logger.warning("Deleted expired file: %s", path)
            except OSError:
                pass
            with _pending_lock:
                _pending_deletes.pop(path, None)

threading.Thread(target=_cleanup_loop, daemon=True).start()


def _download_reddit_best_video(url, output_dir, video_id, request, noreturn=False):
    os.makedirs(output_dir, exist_ok=True)

    opts = {'quiet': True}
    _apply_node_js(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get('formats', [])
        
        # Find best video-only format
        best_v = None
        for f in formats:
            if f.get('vcodec') != 'none':
                f_size = f.get('filesize') or 0
                best_v_size = best_v.get('filesize', 0) if best_v else 0
                if best_v is None or f_size > best_v_size:
                    best_v = f
        
        # Find best audio-only format
        best_a = None
        for f in formats:
            if f.get('acodec') != 'none':
                f_size = f.get('filesize') or 0
                best_a_size = best_a.get('filesize', 0) if best_a else 0
                if best_a is None or f_size > best_a_size:
                    best_a = f
        
        v_itag = best_v.get('format_id') if best_v else None
        a_itag = best_a.get('format_id') if best_a else None

    if not v_itag or not a_itag:
        raise RuntimeError(f"Could not find separate video and audio streams for Reddit video. Video: {v_itag}, Audio: {a_itag}")

    video_file = download_yt(request, subpath=url, itag=v_itag, noreturn=True, custom_output_dir=output_dir, filename=f'{video_id}_video')
    audio_file = download_yt(request, subpath=url, itag=a_itag, type='audio', noreturn=True, custom_output_dir=output_dir, filename=f'{video_id}_audio')
    output_final = os.path.join(output_dir, f'{video_id}.mp4')

    command = [
        'ffmpeg', '-y',
        '-i', video_file,
        '-i', audio_file,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        output_final,
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg error: {e.stderr or e.stdout}')

    if noreturn:
        return output_final

    _schedule_delete(video_file)
    _schedule_delete(audio_file)
    _schedule_delete(output_final)
    return FileResponse(open(output_final, 'rb'), as_attachment=True, filename=f'{video_id}.mp4')

# ---------------------------------------------------------------------------
# Main functions
# ---------------------------------------------------------------------------

def home_yt(request, subpath=''):
    listvid  = []
    video_id = request.GET.get("v") or request.GET.get("watch")

    if "https://www.youtube.com/watch" in subpath:
        return download_yt(request, subpath, video_id, middle="?v=")
    elif "https://youtu.be" in subpath:
        return download_yt(request, subpath)

    lastup, lastuptxt = '', ''
    lastuptdy = False
    try:
        lastup, lastuptxt = get_last_update_github()
        today = datetime.now(tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if lastup == today: lastuptdy = True
    except: pass

    if request.method == 'POST':
        url    = request.POST.get("yt_link")
        action = request.POST.get("action")

        if not url and action != 'setting-save':
            report_error(request, 'No link provided.')
            return redirect('home_yt')

        if not re.search('http', url) and action != 'setting-save':
            report_error(request, 'Invalid link. (we need the https:// or http://)')
            return redirect('home_yt')

        match action:
            case 'info':
                try:
                    info = _extract_with_cookie_fallback(url, {'quiet': True}, download=False)

                    formats_dict = {'video': {}, 'audio': {}, 'subtitles': []}

                    for f in info.get('formats', []):
                        format_id      = f.get('format_id')
                        filenum        = f.get('filesize')
                        filesize_final = f"{round(float(filenum) / 1024 / 1024, 2)} MB" if filenum else 'N/A'

                        entry = {
                            'ext':        f.get('ext'),
                            'resolution': f.get('resolution'),
                            'fps':        f.get('fps'),
                            'vcodec':     f.get('vcodec'),
                            'acodec':     f.get('acodec'),
                            'filesize':   filesize_final,
                            'url':        url,
                        }

                        if f.get('vcodec') != 'none':
                            formats_dict['video'][format_id] = entry
                        else:
                            formats_dict['audio'][format_id] = entry

                    for lang_code, formats in info.get('subtitles', {}).items():
                        if not any(f.get('url') for f in formats): continue
                        formats_dict['subtitles'].append({
                            'lang': lang_code,
                            'name': formats[0].get('name', lang_code),
                            'type': 'Manual',
                            'url':  url,
                        })

                    video_lang = info.get('language', '')
                    auto_captions = info.get('automatic_captions', {})
                    if video_lang and video_lang in auto_captions:
                        formats = auto_captions[video_lang]
                        if any(f.get('url') for f in formats):
                            formats_dict['subtitles'].append({
                                'lang': video_lang,
                                'name': f"{formats[0].get('name', video_lang)} (Auto)",
                                'type': 'Auto',
                                'url':  url,
                            })

                    title = info.get('title')
                    duration = info.get('duration', 0)

                    if duration > settings.MAX_VIDEO_DURATION:
                        report_error(request, f"Video is too long ({round(duration/3600, 1)}h). Maximum allowed is {int(settings.MAX_VIDEO_DURATION/3600)} hours.")
                        return redirect('home_yt')

                    return render(request, 'main/home.html', {
                        'dl_opts':   formats_dict,
                        'final_url': url,
                        'final_title': title
                    })

                except Exception as e:
                    report_error(request, f"Error: {e}")
                    return redirect('home_yt')
            case 'video':
                return download_yt(request, subpath=url, type='video')
            case 'audio':
                return download_yt(request, subpath=url, type='audio')
            case 'transcript':
                return download_yt(request, subpath=url, type='transcript')
            case 'playlist':
                listvid = retrieve_playlist_yt(request, subpath=url)
            case 'setting-save':
                theme_val = request.POST.get('theme_val')
                lang_val  = request.POST.get('lang_val')
                v_qual    = request.POST.get('video_quality')
                a_qual    = request.POST.get('audio_quality')
                
                response = redirect('home_yt')
                if theme_val is not None:
                    response.set_cookie('theme', theme_val, expires=timezone.now() + timedelta(days=365))
                if lang_val:
                    response.set_cookie('lang', lang_val, expires=timezone.now() + timedelta(days=365))
                if v_qual:
                    response.set_cookie('video_quality', v_qual, expires=timezone.now() + timedelta(days=365))
                if a_qual:
                    response.set_cookie('audio_quality', a_qual, expires=timezone.now() + timedelta(days=365))
                
                messages.success(request, 'Settings saved successfully.')
                return response

    try:
        ytdlpver = version('yt_dlp')
    except:
        ytdlpver = 'unknown'

    return render(request, 'main/home.html', {
        'theme':     request.COOKIES.get('theme'),
        'ytdlpver':  ytdlpver,
        'lastup':    lastup,
        'lastuptxt': lastuptxt,
        'lastuptdy': lastuptdy,
        'listvid':   listvid,
    })


def dl_from_opt(request):
    action   = request.POST.get("action")
    parts    = action.split(' - ')
    itag     = parts[0]
    url      = parts[1]
    typeitag = parts[2]

    if itag.startswith('sub:'):
        return download_yt(request, subpath=url, type='subtitle', itag=itag[4:], typeitag='srt')

    return download_yt(request, subpath=url, itag=itag, typeitag=typeitag)


def get_last_update_github():
    response = requests.get(GITREPOLINK, headers={"Accept": "application/vnd.github.v3+json"}, timeout=2)
    if response.status_code == 200:
        commits = response.json()
        if commits:
            return commits[0]['commit']['committer']['date'], commits[0]['commit']['message']


def download_yt(request, subpath='', video_id='', noreturn=False, middle='', type='video', itag=0, typeitag='', quality='best', custom_output_dir='', filename=''):
    url        = subpath + middle + video_id
    output_dir = custom_output_dir or DIR_DOWNLOAD

    os.makedirs(output_dir, exist_ok=True)

    if not video_id:
        video_id = f'file{random.randrange(100000, 999999)}'
        if '?v=' in subpath and 'youtube.com' in subpath:
            video_id = subpath.split('?v=')[-1]

    if 'reddit.com' in url and type == 'video' and quality == 'best' and not itag:
        return _download_reddit_best_video(url, output_dir, video_id, request, noreturn=noreturn)

    match type:
        case 'video':
            dl_format = 'bestvideo+bestaudio/best'
            filetype  = 'mp4'
        case 'audio':
            dl_format = 'bestaudio'
            filetype  = 'mp3'
        case 'transcript' | 'subtitle':
            dl_format = None
            filetype  = 'srt'
        case _:
            dl_format = 'bestaudio'
            filetype  = 'mp3'

    if itag and type not in ('transcript', 'subtitle'):
        dl_format = itag
    if typeitag:
        filetype = typeitag

    final_path = os.path.join(output_dir, f'{video_id}.{filetype}')
    try:
        os.remove(final_path)
    except:
        pass

    if type in ('transcript', 'subtitle'):
        lang_code = itag if itag else 'en'
        ydl_opts = {
            'quiet':             True,
            'writesubtitles':    True,
            'writeautomaticsub': True,
            'subtitleslangs':    [lang_code],
            'skip_download':     True,
            'ignoreerrors':      True,
            'outtmpl':           os.path.join(output_dir, f'{video_id}.%(ext)s'),
            'postprocessors':    [{'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'}],
        }
    else:
        if noreturn:
            # Downloading a raw stream for mixing — use unique filename, skip postprocessing
            out_stem = filename if filename else video_id
            ydl_opts = {
                'quiet':   True,
                'format':  dl_format,
                'outtmpl': os.path.join(output_dir, f'{out_stem}.%(ext)s'),
            }
        else:
            ydl_opts = {
                'quiet':          True,
                'format':         dl_format,
                'outtmpl':        final_path,
                'writethumbnail': True,
                'postprocessors': [
                    {'key': 'EmbedThumbnail'},
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'},
                    {'key': 'FFmpegMetadata', 'add_metadata': True},
                ],
                'merge_output_format': 'mp4' if (type == 'video' and not itag) else None,
            }
            if type == 'audio':
                ydl_opts['postprocessors'].insert(0, {
                    'key':              'FFmpegExtractAudio',
                    'preferredcodec':   'mp3',
                    'preferredquality': '192',
                })

    try:
        info = _extract_with_cookie_fallback(url, ydl_opts, download=True)

        if type in ('transcript', 'subtitle'):
            subtitle_exts = ('srt', 'vtt', 'ttml', 'srv3', 'srv2', 'srv1', 'json3')
            found = []
            for ext in subtitle_exts:
                found = glob_module.glob(os.path.join(output_dir, f'{video_id}*.{ext}'))
                if found:
                    filetype = ext
                    break
            if found:
                final_path = found[0]
            else:
                raise FileNotFoundError("No subtitle file was downloaded. The video may not have subtitles in the requested language.")
        else:
            try:
                final_path = info['requested_downloads'][0]['filepath']
            except:
                pass

        title = info.get('title') or filename

        if title:
            video_id = title

        if noreturn:
            return final_path

        _schedule_delete(final_path)
        return FileResponse(open(final_path, 'rb'), as_attachment=True, filename=f'{video_id}.{filetype}')

    except Exception as e:
        if noreturn:
            raise
        report_error(request, f"Error: {e}")
        logger.exception("Download failed for %s", url)
        return redirect('home_yt')


def retrieve_playlist_yt(request, subpath):
    if 'youtube.com' not in subpath or 'list=' not in subpath:
        report_error(request, "This is (probably) not a Youtube playlist link.")
        return []

    pl_opts = {'extract_flat': True, 'quiet': True}
    playlist_info = _extract_with_cookie_fallback(subpath, pl_opts, download=False)

    video_list = []
    if 'entries' in playlist_info:
        for entry in playlist_info['entries']:
            video_list.append({
                'url':      entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}",
                'title':    entry.get('title'),
                'duration': entry.get('duration'),
            })

    return [
        {
            'url': v['url'],
            'title': v['title'] or 'Unknown',
            'duration': str(round(float(v['duration'] / 60), 2)) if v['duration'] else '0',
        }
        for v in video_list
    ]


# zip_folder is now imported from .tasks or handled there
from .tasks import zip_folder_task as zip_folder


def dl_sel_playlist_yt(request):
    if request.method != 'POST':
        return redirect('home_yt')

    selected_videos = request.POST.getlist('selected_videos')
    list_vids = []
    for v in selected_videos:
        v = v.strip()
        if not v:
            continue
        if '<a href="' in v:
            parts = v.split('<a href="')
            if len(parts) > 1:
                href_parts = parts[1].split('"')
                if href_parts:
                    list_vids.append(href_parts[0])
        else:
            list_vids.append(v)

    if not list_vids:
        report_error(request, "No valid video URLs found in selection.")
        return redirect('home_yt')

    zip_type  = request.POST.get("download_type", 'audio')

    try:
        num_id      = random.randrange(100000, 999999)
        pl_temp_dir = f"{DIR_PLAYLIST}/{num_id}"
        os.makedirs(DIR_PLAYLIST, exist_ok=True)
        os.makedirs(pl_temp_dir, exist_ok=False)

        if 'audio' in zip_type:
            params = {
                'format':  'bestaudio/best',
                'postprocessors': [{
                    'key':              'FFmpegExtractAudio',
                    'preferredcodec':   'mp3',
                    'preferredquality': '192',
                }],
                'outtmpl':            f'{pl_temp_dir}/%(title)s.%(ext)s',
                'quiet':              False,
                'nocheckcertificate': True,
            }
        else:
            params = {
                'format':              'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
                'outtmpl':             f'{pl_temp_dir}/%(title)s.%(ext)s',
                'quiet':               False,
                'nocheckcertificate':  True,
            }

        for v in list_vids:
            _extract_with_cookie_fallback(v, params, download=True)

        zip_path = f'{DIR_PLAYLIST}/Playlist_{num_id}.zip'
        zip_folder(pl_temp_dir, zip_path)

        _schedule_delete(pl_temp_dir)
        _schedule_delete(zip_path)
        return FileResponse(open(zip_path, 'rb'), as_attachment=True, filename=f'Playlist_{num_id}.zip')

    except Exception as e:
        report_error(request, f"Error: {e}")
        logger.exception("Playlist download failed")
        return redirect('home_yt')


def mix_av(request):
    if request.method != 'POST':
        return redirect('home_yt')

    url           = request.POST.get("yt_link")
    video_options = [k for k, v in request.POST.items() if k.endswith('_vcheck') and v == 'on']
    audio_options = [k for k, v in request.POST.items() if k.endswith('_acheck') and v == 'on']

    if len(video_options) != 1 or len(audio_options) != 1:
        report_error(request, "Select exactly one video and one audio track.")
        return redirect('home_yt')

    random_num = random.randrange(100000, 999999)
    try:
        videofile = download_yt(request, subpath=url, itag=video_options[0].split('_')[0], noreturn=True, custom_output_dir=DIR_MIX, filename=f'videotomix{random_num}')
        audiofile = download_yt(request, subpath=url, itag=audio_options[0].split('_')[0], noreturn=True, custom_output_dir=DIR_MIX, filename=f'audiotomix{random_num}')
    except Exception as e:
        report_error(request, f"Download error: {e}")
        return redirect('home_yt')

    output_final = os.path.join(DIR_MIX, f'filefinal_{random_num}.mp4')

    command = [
        'ffmpeg', '-y',
        '-i', videofile,
        '-i', audiofile,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        output_final,
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        report_error(request, f"FFmpeg error: {e.stderr}")
        return redirect('home_yt')

    _schedule_delete(videofile)
    _schedule_delete(audiofile)
    _schedule_delete(output_final)
    return FileResponse(open(output_final, 'rb'), as_attachment=True, filename=f'mixed_{random_num}.mp4')

# ---------------------------------------------------------------------------
# Async Download Views
# ---------------------------------------------------------------------------

def initiate_download(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    ip = get_client_ip(request)
    cache_key = f"dl_count_{ip}"
    dl_count = cache.get(cache_key, 0)
    MAX_DOWNLOADS_PER_HOUR = 50 # Keep local if preferred or move to settings later

    if dl_count >= MAX_DOWNLOADS_PER_HOUR:
        return JsonResponse({'error': f'Rate limit exceeded. Maximum {MAX_DOWNLOADS_PER_HOUR} downloads per hour allowed.'}, status=429)

    url = request.POST.get('yt_link')
    action = request.POST.get('action')
    selected_videos = request.POST.getlist('selected_videos')
    download_type = request.POST.get("download_type", 'audio')
    v_quality = request.POST.get('video_quality', 'best')
    a_quality = request.POST.get('audio_quality', 'best')

    # If everything is okay, increment the count in cache
    cache.set(cache_key, dl_count + 1, 3600) # 1 hour timeout

    if selected_videos:
        list_vids = []
        for v in selected_videos:
            v = v.strip()
            if not v:
                continue
            if '<a href="' in v:
                parts = v.split('<a href="')
                if len(parts) > 1:
                    href_parts = parts[1].split('"')
                    if href_parts:
                        list_vids.append(href_parts[0])
            else:
                list_vids.append(v)

        if not list_vids:
            return JsonResponse({'error': 'No valid video URLs found in selection.'}, status=400)

        q = v_quality if download_type == 'video' else a_quality
        task = download_playlist_task.delay(list_vids, download_type=download_type, quality=q)
        return JsonResponse({'task_id': task.id})

    itag = 0
    typeitag = ''
    type = 'video'

    if action:
        if ' - ' in action:
            parts = action.split(' - ')
            itag = parts[0]
            url = parts[1]
            typeitag = parts[2]
            if itag.startswith('sub:'):
                type = 'subtitle'
                itag = itag[4:]
                typeitag = 'srt'
        elif action in ['video', 'audio', 'transcript']:
            type = action
        
    if not url:
        return JsonResponse({'error': 'No link provided.'}, status=400)

    q = v_quality if type == 'video' else a_quality
    task = download_video_task.delay(url, type=type, itag=itag, typeitag=typeitag, quality=q)
    return JsonResponse({'task_id': task.id})


def task_status(request, task_id):
    res = AsyncResult(task_id)
    if res.state == 'PROGRESS':
        return JsonResponse({
            'state': res.state,
            'percent': res.info.get('percent', 0),
            'status': res.info.get('status', 'Processing...')
        })
    elif res.state == 'SUCCESS':
        return JsonResponse({
            'state': res.state,
            'percent': 100,
            'result': res.result
        })
    elif res.state == 'FAILURE':
        return JsonResponse({
            'state': res.state,
            'error': str(res.info)
        })
    else:
        return JsonResponse({
            'state': res.state,
            'percent': 0
        })


def get_downloaded_file(request, task_id):
    res = AsyncResult(task_id)
    if res.state == 'SUCCESS':
        result = res.result
        file_path = result['file_path']
        title = result['title']
        ext = result['ext']
        
        if os.path.exists(file_path):
            _schedule_delete(file_path)
            return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=f'{title}.{ext}')
        else:
            return JsonResponse({'error': 'File not found'}, status=404)
    
    return JsonResponse({'error': 'Task not completed'}, status=400)


def cancel_task(request, task_id):
    res = AsyncResult(task_id)
    res.revoke(terminate=True)
    return JsonResponse({'status': 'Task cancelled'})


def health(request):
    response = HttpResponse("OK", content_type="text/plain")
    response["Access-Control-Allow-Origin"] = "*"
    return response

def terms_privacy(request):
    return render(request, 'main/terms-privacy.html')
