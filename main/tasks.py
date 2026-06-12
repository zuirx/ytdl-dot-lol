import os
import yt_dlp
import logging
import zipfile
import shutil
import subprocess
import json
from celery import shared_task
from django.conf import settings
import time

YTDLP_CLI = shutil.which('yt-dlp')

def _ytdlp_supports_js_runtimes():
    if not YTDLP_CLI:
        return False
    try:
        result = subprocess.run([YTDLP_CLI, '--help'], capture_output=True, text=True)
        return '--js-runtimes' in result.stdout
    except Exception:
        return False

YTDLP_HAS_JS_RUNTIMES = _ytdlp_supports_js_runtimes()

logger = logging.getLogger(__name__)

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

def _apply_node_js(opts):
    """Ensure yt-dlp can find node.exe for YouTube n-sig challenges."""
    node_path = shutil.which('node')
    if node_path:
        # yt-dlp expects js_runtimes as a top-level dict: {runtime: {config}}
        # Default already includes {'deno': {}}, so we merge rather than replace.
        runtimes = opts.get('js_runtimes') or {}
        if isinstance(runtimes, dict) and 'node' not in runtimes:
            opts['js_runtimes'] = {**runtimes, 'node': {'path': node_path}}
    return opts

def _ytdlp_cli_args(url, opts, download=True):
    """Build yt-dlp CLI argument list from Python opts dict."""
    if not YTDLP_CLI:
        raise RuntimeError("yt-dlp CLI not found in PATH")

    args = [YTDLP_CLI]
    if YTDLP_HAS_JS_RUNTIMES:
        args.extend(['--js-runtimes', 'node'])

    if opts.get('quiet'):
        args.append('-q')
    if opts.get('nocheckcertificate'):
        args.append('--no-check-certificate')

    if not download:
        args.extend(['-J', '--no-warnings'])
    else:
        if opts.get('format'):
            args.extend(['-f', opts['format']])
        if opts.get('outtmpl'):
            args.extend(['-o', opts['outtmpl']])
        if opts.get('merge_output_format'):
            args.extend(['--merge-output-format', opts['merge_output_format']])
        if opts.get('writethumbnail'):
            args.append('--write-thumbnail')

        for pp in opts.get('postprocessors', []):
            key = pp.get('key', '')
            if key == 'FFmpegExtractAudio':
                args.append('-x')
                if pp.get('preferredcodec'):
                    args.extend(['--audio-format', pp['preferredcodec']])
                if pp.get('preferredquality'):
                    args.extend(['--audio-quality', pp['preferredquality']])
            elif key == 'EmbedThumbnail':
                args.append('--embed-thumbnail')
            elif key == 'FFmpegThumbnailsConvertor':
                if pp.get('format'):
                    args.extend(['--convert-thumbnails', pp['format']])
            elif key == 'FFmpegMetadata':
                args.append('--add-metadata')

    # Cookies
    cookiefile = opts.get('cookiefile')
    if cookiefile and os.path.exists(cookiefile):
        args.extend(['--cookies', cookiefile])
    elif opts.get('cookiesfrombrowser'):
        browser = opts['cookiesfrombrowser'][0] if isinstance(opts['cookiesfrombrowser'], tuple) else opts['cookiesfrombrowser']
        args.extend(['--cookies-from-browser', browser])

    args.append(url)
    return args


def _run_ytdlp_cli(url, opts, download=False):
    """Run yt-dlp CLI and return info dict (if not download) or None."""
    args = _ytdlp_cli_args(url, opts, download=download)
    logger.info("Running yt-dlp CLI: %s", ' '.join(args))
    result = subprocess.run(args, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ''
        raise yt_dlp.utils.DownloadError(f"yt-dlp CLI failed: {stderr or 'Unknown error'}")

    if not download:
        output = result.stdout.strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            # yt-dlp sometimes prints warnings before JSON; grab the last JSON object
            for line in reversed(output.splitlines()):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            raise yt_dlp.utils.DownloadError(f"Could not parse yt-dlp JSON output: {output[:500]}")

    return None


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
        copied = {**ydl_opts, 'format': 'best'}
        try:
            with yt_dlp.YoutubeDL(copied) as ydl:
                return ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as e:
            if _format_not_available_error(e):
                raise yt_dlp.utils.DownloadError(
                    f"No playable formats available for {url}. "
                    "Ensure a JavaScript runtime (Node.js or Deno) is installed and in PATH, "
                    "or that the video is not blocked/restricted in your region."
                )
            raise

    def _retry_with_cookies(ydl_opts, err):
        if _is_youtube(url) and _age_restricted_error(err) and YTDLP_CLI:
            logger.warning("Age-restricted YouTube video — switching to yt-dlp CLI: %s", url)
            _inject_cookies(ydl_opts, url)
            return _run_ytdlp_cli(url, ydl_opts, download=download)

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

# Constants are now in settings.py

@shared_task
def cleanup_large_files_task():
    """
    Routine to remove very large files from content directories.
    - Files > 500MB are removed if older than 30 minutes.
    - Total storage limit of 100GB is enforced by removing oldest files.
    """
    dirs_to_clean = [settings.DIR_DOWNLOAD, settings.DIR_MIX, settings.DIR_PLAYLIST]
    LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500 MB
    TIME_THRESHOLD = 30 * 60  # 30 minutes
    TOTAL_STORAGE_LIMIT = 100 * 1024 * 1024 * 1024  # 100 GB
    
    now = time.time()
    all_files = []
    current_total_size = 0

    for d in dirs_to_clean:
        if not os.path.exists(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                path = os.path.join(root, f)
                try:
                    stat = os.stat(path)
                    size = stat.st_size
                    mtime = stat.st_mtime
                    
                    # Rule 1: Remove very large files older than 30 mins
                    if size > LARGE_FILE_THRESHOLD and (now - mtime) > TIME_THRESHOLD:
                        os.remove(path)
                        logger.warning(f"Cleanup: Removed large file {path} (>500MB and >30m old)")
                        continue
                    
                    all_files.append((path, mtime, size))
                    current_total_size += size
                except OSError:
                    pass

    # Rule 2: Enforce total storage limit of 100GB
    if current_total_size > TOTAL_STORAGE_LIMIT:
        logger.info(f"Cleanup: Total size {current_total_size} exceeds limit {TOTAL_STORAGE_LIMIT}. Purging oldest files.")
        # Sort by mtime ascending (oldest first)
        all_files.sort(key=lambda x: x[1])
        
        for path, mtime, size in all_files:
            try:
                os.remove(path)
                current_total_size -= size
                if current_total_size <= TOTAL_STORAGE_LIMIT:
                    break
                logger.warning(f"Cleanup: Purged old file {path} to free space")
            except OSError:
                pass

def zip_folder_task(folder_path, output_path):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname   = os.path.relpath(file_path, os.path.dirname(folder_path))
                zipf.write(file_path, arcname)

def get_format_for_quality(type, quality):
    if type == 'video':
        match quality:
            case 'worst':
                return 'worstvideo+worstaudio/worst'
            case 'low':
                return 'bestvideo[height<=360]+bestaudio/best[height<=360]'
            case 'medium':
                return 'bestvideo[height<=480]+bestaudio/best[height<=480]'
            case 'high':
                return 'bestvideo[height<=720]+bestaudio/best[height<=720]'
            case _:
                return 'bestvideo+bestaudio/best'
    else: # audio
        match quality:
            case 'worst':
                return 'worstaudio/worst'
            case 'low':
                return 'bestaudio[abr<=64]/best'
            case 'medium':
                return 'bestaudio[abr<=128]/best'
            case 'high':
                return 'bestaudio[abr<=192]/best'
            case _:
                return 'bestaudio/best'

def _download_reddit_best_video(url, output_dir, video_id):
    os.makedirs(output_dir, exist_ok=True)

    video_template = os.path.join(output_dir, f'{video_id}_video.%(ext)s')
    audio_template = os.path.join(output_dir, f'{video_id}_audio.%(ext)s')

    def _download_stream(format_selector, outtmpl):
        opts = {
            'quiet': True,
            'format': format_selector,
            'outtmpl': outtmpl,
        }
        _apply_node_js(opts)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        try:
            # Use the actual filepath from the info dict if available
            if 'requested_downloads' in info and info['requested_downloads']:
                return info['requested_downloads'][0]['filepath']
            return ydl.prepare_filename(info)
        except Exception:
            raise RuntimeError('Could not determine downloaded file path for Reddit stream.')

    # Use 'bestvideo' and 'bestaudio' instead of hardcoded itags
    video_file = _download_stream('bestvideo', video_template)
    audio_file = _download_stream('bestaudio', audio_template)
    final_path = os.path.join(output_dir, f'{video_id}.mp4')

    command = [
        'ffmpeg', '-y',
        '-i', video_file,
        '-i', audio_file,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        final_path,
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg error: {e.stderr or e.stdout}')

    return final_path

@shared_task(bind=True)
def download_playlist_task(self, video_urls, download_type='audio', quality='best', output_dir='content-playlist'):
    os.makedirs(output_dir, exist_ok=True)
    task_id = self.request.id
    pl_temp_dir = os.path.join(output_dir, task_id)
    os.makedirs(pl_temp_dir, exist_ok=True)
    
    total_vids = len(video_urls)
    
    if download_type == 'audio':
        params = {
            'format':  get_format_for_quality('audio', quality),
            'postprocessors': [{
                'key':              'FFmpegExtractAudio',
                'preferredcodec':   'mp3',
                'preferredquality': '192',
            }],
            'outtmpl':            f'{pl_temp_dir}/%(title)s.%(ext)s',
            'quiet':              True,
            'nocheckcertificate': True,
        }
    else:
        params = {
            'format':              get_format_for_quality('video', quality),
            'merge_output_format': 'mp4',
            'outtmpl':             f'{pl_temp_dir}/%(title)s.%(ext)s',
            'quiet':               True,
            'nocheckcertificate':  True,
        }

    for i, v_url in enumerate(video_urls):
        current = i + 1
        self.update_state(state='PROGRESS', meta={
            'percent': (i / total_vids) * 100,
            'status': f'Downloading video {current} of {total_vids}...'
        })

        try:
            # Check duration before download (retries with cookies on age restriction)
            info = _extract_with_cookie_fallback(v_url, params, download=False)
            duration = info.get('duration', 0)
            if duration > settings.MAX_VIDEO_DURATION:
                logger.warning(f"Skipping video {v_url} because it exceeds duration limit.")
                continue
            # Download with same fallback logic (cookies + CLI for age-restricted YouTube)
            _extract_with_cookie_fallback(v_url, params, download=True)
        except Exception as e:
            logger.error(f"Failed to download {v_url}: {e}")

    self.update_state(state='PROGRESS', meta={
        'percent': 95,
        'status': 'Zipping files...'
    })
    
    zip_path = os.path.join(output_dir, f'Playlist_{task_id}.zip')
    zip_folder_task(pl_temp_dir, zip_path)
    
    # Cleanup temp dir
    shutil.rmtree(pl_temp_dir, ignore_errors=True)
    
    return {
        'status': 'Finished',
        'file_path': zip_path,
        'title': f'Playlist_{task_id}',
        'ext': 'zip'
    }

@shared_task(bind=True)
def download_video_task(self, url, type='video', itag=0, typeitag='', quality='best', output_dir='content-downloads'):
    
    os.makedirs(output_dir, exist_ok=True)
    
    video_id = 'dl_' + self.request.id
    reddit_best = 'reddit.com' in url and type == 'video' and quality == 'best' and not itag

    if reddit_best:
        dl_format = 'bestvideo+bestaudio/best'
        filetype = 'mp4'
    else:
        match type:
            case 'video':
                dl_format = get_format_for_quality('video', quality)
                filetype  = 'mp4'
            case 'audio':
                dl_format = get_format_for_quality('audio', quality)
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
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            
            # Fallback to parsing _percent_str if bytes are missing
            if not total:
                p = d.get('_percent_str', '0%').replace('%', '').strip()
                # Clean ANSI escape sequences if any
                import re
                p = re.sub(r'\x1b\[[0-9;]*m', '', p)
                try:
                    percent = float(p)
                except:
                    percent = 0
            else:
                percent = (downloaded / total) * 100

            filename = os.path.basename(d.get('filename', 'file'))
            self.update_state(state='PROGRESS', meta={
                'percent': percent,
                'status': f'Downloading {filename}...'
            })
        elif d['status'] == 'finished':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': 'Download finished, preparing for next step...'
            })

    def postprocessor_hook(d):
        pp_name = d.get('postprocessor')
        if d['status'] == 'started':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': f'Processing: {pp_name}...'
            })
        elif d['status'] == 'finished':
            self.update_state(state='PROGRESS', meta={
                'percent': 100,
                'status': f'Finished processing {pp_name}.'
            })

    # Pre-check duration
    title = video_id
    try:
        info = _extract_with_cookie_fallback(url, {'quiet': True}, download=False)
        duration = info.get('duration', 0)
        if duration > settings.MAX_VIDEO_DURATION:
            raise ValueError(f"Video is too long ({round(duration/3600, 1)}h). Max limit is {int(settings.MAX_VIDEO_DURATION/3600)}h.")
        title = info.get('title') or video_id
    except Exception as e:
        if "Video is too long" in str(e): raise
        # If it's another error, we'll let the main download try and catch it (it might be a playlist/unsupported URL)

    if reddit_best:
        final_path = _download_reddit_best_video(url, output_dir, video_id)
        return {
            'status': 'Finished',
            'file_path': final_path,
            'title': title,
            'ext': 'mp4'
        }

    ydl_opts = {
        'quiet':               True,
        'format':              dl_format,
        'outtmpl':             final_path,
        'writethumbnail':      True,
        'progress_hooks':      [progress_hook],
        'postprocessor_hooks': [postprocessor_hook],
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

        # When CLI handles the download, info is None.
        # Fallback to the outtmpl path and pre-fetched title.
        if info:
            try:
                final_path = info['requested_downloads'][0]['filepath']
            except Exception:
                pass
            title = info.get('title') or title

        ext = final_path.split('.')[-1]
        
        return {
            'status': 'Finished',
            'file_path': final_path,
            'title': title,
            'ext': ext
        }
    except Exception as e:
        logger.exception("Download task failed")
        raise

@shared_task
def update_ytdlp_task():
    try:
        subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True)
        logger.info("yt-dlp updated successfully.")
    except Exception as e:
        logger.error(f"Failed to update yt-dlp: {e}")
