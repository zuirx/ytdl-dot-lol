import yt_dlp, os, re, random, glob as glob_module, subprocess, logging, requests, zipfile, threading, time, shutil # type: ignore
from datetime import datetime, timedelta, timezone as tz
from importlib.metadata import version
from urllib.parse import urlparse
from django.shortcuts import render, redirect
from django.http import FileResponse
from django.utils import timezone
from django.contrib import messages

logger = logging.getLogger(__name__)

DIR_DOWNLOAD = 'content-downloads'
DIR_MIX      = 'content-mix'
DIR_PLAYLIST = 'content-playlist'
GITREPOLINK  = 'https://api.github.com/repos/zuirx/ytdl-dot-lol/commits'

# ---------------------------------------------------------------------------
# File cleanup registry — paths are deleted 1 hour after download
# ---------------------------------------------------------------------------
_pending_deletes: dict = {}   # {path: expiry_unix_time}
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

def home_yt(request, subpath=''):
    listvid  = []
    video_id = request.GET.get("v") or request.GET.get("watch")

    if "https://www.youtube.com/watch" in subpath:
        return download_yt(request, subpath, video_id, "?v=")
    elif "https://youtu.be" in subpath:
        return download_yt(request, subpath)

    lastup, lastuptxt = '', ''
    lastuptdy = False
    try:
        lastup, lastuptxt = get_last_update_github()
        today = datetime.now(tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if lastup == today:
            lastuptdy = True
    except:
        pass

    if request.method == 'POST':
        url    = request.POST.get("yt_link")
        action = request.POST.get("action")

        if not url:
            messages.error(request, 'No link provided.')
            return redirect('home_yt')

        if not re.search('http', url):
            messages.error(request, 'Invalid link. (we need the https:// or http://)')
            return redirect('home_yt')

        match action:
            case 'info':
                try:
                    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                        info = ydl.extract_info(url, download=False)

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
                        if not any(f.get('url') for f in formats):
                            continue
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

                    return render(request, 'main/home.html', {
                        'dl_opts':   formats_dict,
                        'final_url': url
                    })

                except Exception as e:
                    messages.error(request, f"Error: {e}")
                    return redirect('home_yt')

            case 'video':
                return download_yt(request, subpath=url, type='video')
            case 'audio':
                return download_yt(request, subpath=url, type='audio')
            case 'transcript':
                return download_yt(request, subpath=url, type='transcript')
            case 'playlist':
                listvid = retrieve_playlist_yt(request, subpath=url)

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


def user_def_cookie(request):
    color_def    = request.POST.get("color_def")
    language_def = request.POST.get("language_def")
    response     = redirect('home_yt')

    if color_def:
        response.set_cookie('theme', color_def,    expires=timezone.now() + timedelta(days=7))
    if language_def:
        response.set_cookie('theme', language_def, expires=timezone.now() + timedelta(days=365))

    return response


def get_last_update_github():
    response = requests.get(GITREPOLINK, headers={"Accept": "application/vnd.github.v3+json"}, timeout=2)
    if response.status_code == 200:
        commits = response.json()
        if commits:
            return commits[0]['commit']['committer']['date'], commits[0]['commit']['message']


def download_yt(request, subpath='', video_id='', noreturn=False, middle='', type='video', itag=0, typeitag='', custom_output_dir='', filename=''):
    url        = subpath + middle + video_id
    output_dir = custom_output_dir or DIR_DOWNLOAD

    os.makedirs(output_dir, exist_ok=True)

    if not video_id:
        video_id = f'file{random.randrange(100000, 999999)}'
        if '?v=' in subpath and 'youtube.com' in subpath:
            video_id = subpath.split('?v=')[-1]

    match type:
        case 'video':
            dl_format = 'bestvideo+bestaudio/best'
            filetype  = 'webm'
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
            }
            if type == 'audio':
                ydl_opts['postprocessors'].insert(0, {
                    'key':              'FFmpegExtractAudio',
                    'preferredcodec':   'mp3',
                    'preferredquality': '192',
                })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

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
        messages.error(request, f"Error: {e}")
        logger.exception("Download failed for %s", url)
        return redirect('home_yt')


def retrieve_playlist_yt(request, subpath):
    if 'youtube.com' not in subpath or 'list=' not in subpath:
        messages.error(request, "This is (probably) not a Youtube playlist link.")
        return []

    with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
        playlist_info = ydl.extract_info(subpath, download=False)

    video_list = []
    if 'entries' in playlist_info:
        for entry in playlist_info['entries']:
            video_list.append({
                'url':      entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}",
                'title':    entry.get('title'),
                'duration': entry.get('duration'),
            })

    return [
        f"""<a href="{v['url']}" class="listlink" target="_blank">{v['title']}</a>  | {v['duration']}s"""
        for v in video_list
    ]


def zip_folder(folder_path, output_path):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname   = os.path.relpath(file_path, os.path.dirname(folder_path))
                zipf.write(file_path, arcname)


def dl_sel_playlist_yt(request):
    if request.method != 'POST':
        return redirect('home_yt')

    selected_videos = request.POST.getlist('selected_videos')
    list_vids = [v.split('<a href="')[1].split('"')[0] for v in selected_videos]
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
            with yt_dlp.YoutubeDL(params) as ydl:
                ydl.download(v)

        zip_path = f'{DIR_PLAYLIST}/Playlist_{num_id}.zip'
        zip_folder(pl_temp_dir, zip_path)

        _schedule_delete(pl_temp_dir)
        _schedule_delete(zip_path)
        return FileResponse(open(zip_path, 'rb'), as_attachment=True, filename=f'Playlist_{num_id}.zip')

    except Exception as e:
        messages.error(request, f"Error: {e}")
        logger.exception("Playlist download failed")
        return redirect('home_yt')


def mix_av(request):
    if request.method != 'POST':
        return redirect('home_yt')

    url           = request.POST.get("yt_link")
    video_options = [k for k, v in request.POST.items() if k.endswith('_vcheck') and v == 'on']
    audio_options = [k for k, v in request.POST.items() if k.endswith('_acheck') and v == 'on']

    if len(video_options) != 1 or len(audio_options) != 1:
        messages.error(request, "Select exactly one video and one audio track.")
        return redirect('home_yt')

    random_num = random.randrange(100000, 999999)
    try:
        videofile = download_yt(request, subpath=url, itag=video_options[0].split('_')[0], noreturn=True, custom_output_dir=DIR_MIX, filename=f'videotomix{random_num}')
        audiofile = download_yt(request, subpath=url, itag=audio_options[0].split('_')[0], noreturn=True, custom_output_dir=DIR_MIX, filename=f'audiotomix{random_num}')
    except Exception as e:
        messages.error(request, f"Download error: {e}")
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
        messages.error(request, f"FFmpeg error: {e.stderr}")
        return redirect('home_yt')

    _schedule_delete(videofile)
    _schedule_delete(audiofile)
    _schedule_delete(output_final)
    return FileResponse(open(output_final, 'rb'), as_attachment=True, filename=f'mixed_{random_num}.mp4')