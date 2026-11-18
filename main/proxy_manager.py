import re
import os
import time
import socket
import logging
import concurrent.futures
import urllib.request
import json
from django.core.cache import cache
from django.conf import settings
import yt_dlp
import yt_dlp.extractor.youtube._base as _yt_base
from yt_dlp.utils import GeoRestrictedError, DownloadError

logger = logging.getLogger(__name__)

# Prevent yt-dlp bug KeyError('INNERTUBE_CONTEXT') when client config extraction returns empty
def _safe_fix_embedded_ytcfg(ytcfg):
    if not isinstance(ytcfg, dict):
        return
    if 'INNERTUBE_CONTEXT' not in ytcfg:
        ytcfg['INNERTUBE_CONTEXT'] = {}
    ytcfg['INNERTUBE_CONTEXT'].setdefault('thirdParty', {}).update({
        'embedUrl': 'https://www.reddit.com/',
    })

_yt_base._fix_embedded_ytcfg = _safe_fix_embedded_ytcfg

# ISO 3166-1 alpha-2 mapping of common countries
COUNTRY_NAMES = {
    'AD': 'Andorra', 'AE': 'United Arab Emirates', 'AF': 'Afghanistan', 'AG': 'Antigua and Barbuda',
    'AI': 'Anguilla', 'AL': 'Albania', 'AM': 'Armenia', 'AO': 'Angola', 'AR': 'Argentina',
    'AS': 'American Samoa', 'AT': 'Austria', 'AU': 'Australia', 'AW': 'Aruba', 'AZ': 'Azerbaijan',
    'BA': 'Bosnia and Herzegovina', 'BB': 'Barbados', 'BD': 'Bangladesh', 'BE': 'Belgium',
    'BF': 'Burkina Faso', 'BG': 'Bulgaria', 'BH': 'Bahrain', 'BI': 'Burundi', 'BJ': 'Benin',
    'BM': 'Bermuda', 'BN': 'Brunei', 'BO': 'Bolivia', 'BR': 'Brazil', 'BS': 'Bahamas',
    'BT': 'Bhutan', 'BW': 'Botswana', 'BY': 'Belarus', 'BZ': 'Belize', 'CA': 'Canada',
    'CD': 'Democratic Republic of the Congo', 'CF': 'Central African Republic', 'CG': 'Congo',
    'CH': 'Switzerland', 'CI': "Cote d'Ivoire", 'CL': 'Chile', 'CM': 'Cameroon', 'CN': 'China',
    'CO': 'Colombia', 'CR': 'Costa Rica', 'CU': 'Cuba', 'CV': 'Cape Verde', 'CY': 'Cyprus',
    'CZ': 'Czech Republic', 'DE': 'Germany', 'DJ': 'Djibouti', 'DK': 'Denmark', 'DM': 'Dominica',
    'DO': 'Dominican Republic', 'DZ': 'Algeria', 'EC': 'Ecuador', 'EE': 'Estonia', 'EG': 'Egypt',
    'ES': 'Spain', 'ET': 'Ethiopia', 'FI': 'Finland', 'FJ': 'Fiji', 'FM': 'Micronesia',
    'FR': 'France', 'GA': 'Gabon', 'GB': 'United Kingdom', 'GD': 'Grenada', 'GE': 'Georgia',
    'GH': 'Ghana', 'GM': 'Gambia', 'GN': 'Guinea', 'GR': 'Greece', 'GT': 'Guatemala',
    'GU': 'Guam', 'GY': 'Guyana', 'HK': 'Hong Kong', 'HN': 'Honduras', 'HR': 'Croatia',
    'HT': 'Haiti', 'HU': 'Hungary', 'ID': 'Indonesia', 'IE': 'Ireland', 'IL': 'Israel',
    'IN': 'India', 'IQ': 'Iraq', 'IR': 'Iran', 'IS': 'Iceland', 'IT': 'Italy',
    'JM': 'Jamaica', 'JO': 'Jordan', 'JP': 'Japan', 'KE': 'Kenya', 'KG': 'Kyrgyzstan',
    'KH': 'Cambodia', 'KR': 'South Korea', 'KW': 'Kuwait', 'KY': 'Cayman Islands', 'KZ': 'Kazakhstan',
    'LA': 'Laos', 'LB': 'Lebanon', 'LK': 'Sri Lanka', 'LT': 'Lithuania', 'LU': 'Luxembourg',
    'LV': 'Latvia', 'LY': 'Libya', 'MA': 'Morocco', 'MC': 'Monaco', 'MD': 'Moldova',
    'ME': 'Montenegro', 'MG': 'Madagascar', 'MK': 'North Macedonia', 'ML': 'Mali', 'MM': 'Myanmar',
    'MN': 'Mongolia', 'MO': 'Macau', 'MT': 'Malta', 'MU': 'Mauritius', 'MX': 'Mexico',
    'MY': 'Malaysia', 'MZ': 'Mozambique', 'NA': 'Namibia', 'NG': 'Nigeria', 'NI': 'Nicaragua',
    'NL': 'Netherlands', 'NO': 'Norway', 'NP': 'Nepal', 'NZ': 'New Zealand', 'OM': 'Oman',
    'PA': 'Panama', 'PE': 'Peru', 'PH': 'Philippines', 'PK': 'Pakistan', 'PL': 'Poland',
    'PR': 'Puerto Rico', 'PS': 'Palestine', 'PT': 'Portugal', 'PY': 'Paraguay', 'QA': 'Qatar',
    'RO': 'Romania', 'RS': 'Serbia', 'RU': 'Russia', 'RW': 'Rwanda', 'SA': 'Saudi Arabia',
    'SE': 'Sweden', 'SG': 'Singapore', 'SI': 'Slovenia', 'SK': 'Slovakia', 'SN': 'Senegal',
    'SO': 'Somalia', 'SR': 'Suriname', 'SV': 'El Salvador', 'SY': 'Syria', 'TH': 'Thailand',
    'TJ': 'Tajikistan', 'TM': 'Turkmenistan', 'TN': 'Tunisia', 'TR': 'Turkey', 'TT': 'Trinidad and Tobago',
    'TW': 'Taiwan', 'TZ': 'Tanzania', 'UA': 'Ukraine', 'UG': 'Uganda', 'US': 'United States',
    'UY': 'Uruguay', 'UZ': 'Uzbekistan', 'VE': 'Venezuela', 'VN': 'Vietnam', 'YE': 'Yemen',
    'ZA': 'South Africa', 'ZM': 'Zambia', 'ZW': 'Zimbabwe',
}

NAME_TO_CODE = {v.lower(): k for k, v in COUNTRY_NAMES.items()}

# Verified / known working regional proxies (prioritized ahead of dynamic scrape lists)
KNOWN_PROXIES = {
    'IN': [
        'socks4://49.207.177.65:5678',
    ],
}


def get_country_name(code):
    """Return full country name from 2-letter ISO code."""
    if not code:
        return 'Unknown'
    code_up = str(code).strip().upper()
    return COUNTRY_NAMES.get(code_up, code_up)


def get_country_code(name_or_code):
    """Normalize input (e.g. 'India' or 'in' or 'IN') into 2-letter uppercase ISO code."""
    if not name_or_code:
        return ''
    cleaned = str(name_or_code).strip()
    if len(cleaned) == 2:
        return cleaned.upper()
    return NAME_TO_CODE.get(cleaned.lower(), cleaned.upper())


def is_geo_restricted_error(exc):
    """Detect if an exception is caused by YouTube geo-restriction."""
    if isinstance(exc, GeoRestrictedError):
        return True
    msg = str(exc).lower()
    return any(p in msg for p in (
        'not made this video available in your country',
        'not available in your country',
        'geo-restricted',
        'blocked in your country',
        'this video is available in',
    ))


def extract_countries_from_error(exc):
    """Extract list of ISO country codes from a GeoRestrictedError or error message."""
    countries = []

    # 1. From yt_dlp's orig exception attribute
    orig = getattr(exc, 'exc_info', None)
    if orig and len(orig) > 1 and orig[1]:
        orig_exc = orig[1]
        c = getattr(orig_exc, 'countries', None)
        if c and isinstance(c, (list, tuple)):
            countries.extend([str(x).upper() for x in c])

    c_direct = getattr(exc, 'countries', None)
    if c_direct and isinstance(c_direct, (list, tuple)):
        countries.extend([str(x).upper() for x in c_direct])

    # 2. Parse from string message if countries list wasn't populated
    if not countries:
        msg = str(exc)
        # yt-dlp format: "This video is available in India, Canada."
        for found_str in re.findall(r'available in ([^\.\n]+)', msg, re.IGNORECASE):
            if 'your country' in found_str.lower():
                continue
            parts = re.split(r'[,/]|(?:\band\b)', found_str)
            for part in parts:
                p = part.strip()
                code = get_country_code(p)
                if code and len(code) == 2:
                    countries.append(code)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for c in countries:
        c_up = c.upper()
        if c_up not in seen:
            seen.add(c_up)
            result.append(c_up)

    return result


def detect_video_country(url):
    """Detect what country a video is available in.

    Returns dict:
      {
         'restricted': bool,
         'countries': ['IN'],
         'country_names': ['India'],
         'message': str,
         'title': str or None
      }
    """
    node_path = getattr(settings, 'NODEJS', None)
    opts = {
        'quiet': True,
        'skip_download': True,
        'socket_timeout': 8,
        'extractor_retries': 0,
        'retries': 0,
    }
    if node_path:
        opts['js_runtimes'] = {'node': {'path': node_path}}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title') if info else None
            return {
                'restricted': False,
                'countries': [],
                'country_names': [],
                'message': 'Video is available worldwide or in your current region.',
                'title': title
            }
    except Exception as exc:
        if is_geo_restricted_error(exc):
            countries = extract_countries_from_error(exc)
            country_names = [get_country_name(c) for c in countries]
            c_str = ', '.join(country_names) if country_names else 'other regions'
            return {
                'restricted': True,
                'countries': countries,
                'country_names': country_names,
                'message': f'This video is geo-restricted. It is available in: {c_str}.',
                'title': None
            }
        return {
            'restricted': False,
            'countries': [],
            'country_names': [],
            'message': f'Extraction failed: {str(exc).splitlines()[0]}',
            'title': None
        }


def _fetch_from_monosans(country_code=None):
    """Fetch proxies from monosans/proxy-list GitHub mirror (cached)."""
    cache_key = 'proxy_source_monosans'
    data = cache.get(cache_key)
    if data is None:
        try:
            req = urllib.request.Request(
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies.json',
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode('utf-8'))
                cache.set(cache_key, data, 1800)  # Cache for 30 minutes
        except Exception as e:
            logger.warning('Failed to fetch monosans proxy list: %s', e)
            return []

    proxies = []
    target_cc = country_code.upper() if country_code else None
    for p in data:
        p_cc = p.get('geolocation', {}).get('country', {}).get('iso_code')
        if target_cc and p_cc != target_cc:
            continue
        proto = p.get('protocol', 'http')
        host = p.get('host')
        port = p.get('port')
        if host and port:
            proxies.append(f'{proto}://{host}:{port}')

    return proxies


def _fetch_from_proxyscrape(country_code=None):
    """Fetch proxies from ProxyScrape API."""
    cc_param = country_code.upper() if country_code else 'all'
    cache_key = f'proxy_source_proxyscrape_{cc_param}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    proxies = []
    for proto in ('socks5', 'socks4', 'http'):
        url = f'https://api.proxyscrape.com/v2/?request=displayproxies&protocol={proto}&timeout=5000&country={cc_param}&ssl=all&anonymity=all'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=6) as r:
                lines = [l.strip() for l in r.read().decode('utf-8').splitlines() if l.strip()]
                for line in lines:
                    if ':' in line:
                        proxies.append(f'{proto}://{line}')
        except Exception as e:
            logger.debug('ProxyScrape fetch failed for %s: %s', proto, e)

    cache.set(cache_key, proxies, 900)  # 15 minutes
    return proxies


def _fetch_from_geonode(country_code=None):
    """Fetch proxies from GeoNode API."""
    cc_param = f'&country={country_code.upper()}' if country_code else ''
    cache_key = f'proxy_source_geonode_{country_code or "all"}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f'https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&sort_type=desc{cc_param}'
    proxies = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            payload = json.loads(r.read().decode('utf-8'))
            for item in payload.get('data', []):
                ip = item.get('ip')
                port = item.get('port')
                protocols = item.get('protocols', ['http'])
                proto = 'socks5' if 'socks5' in protocols else ('socks4' if 'socks4' in protocols else 'http')
                if ip and port:
                    proxies.append(f'{proto}://{ip}:{port}')
    except Exception as e:
        logger.debug('GeoNode fetch failed: %s', e)

    cache.set(cache_key, proxies, 900)
    return proxies


def _fetch_from_speedx_and_raw(country_code=None):
    """Fetch proxies from TheSpeedX and hookzof lists and filter by GeoLite2 country."""
    cache_key = f'proxy_source_speedx_{country_code or "all"}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    mmdb_path = os.path.join(settings.BASE_DIR, 'GeoLite2-City.mmdb')
    reader = None
    if os.path.exists(mmdb_path):
        try:
            import geoip2.database
            reader = geoip2.database.Reader(mmdb_path)
        except Exception:
            pass

    target_cc = country_code.upper() if country_code else None
    sources = [
        ('socks5', 'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt'),
        ('socks4', 'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt'),
        ('socks5', 'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt'),
    ]

    proxies = []
    for proto, url in sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=6) as r:
                for line in r.read().decode('utf-8', errors='ignore').splitlines():
                    line = line.strip()
                    if not line or ':' not in line:
                        continue
                    if reader and target_cc:
                        ip = line.split(':')[0]
                        try:
                            geo_res = reader.city(ip)
                            if geo_res.country.iso_code == target_cc:
                                proxies.append(f'{proto}://{line}')
                        except Exception:
                            pass
                    else:
                        proxies.append(f'{proto}://{line}')
        except Exception as e:
            logger.debug('Failed fetching from %s: %s', url, e)

    cache.set(cache_key, proxies, 900)  # 15 minutes
    return proxies


def get_proxies_for_country(country_code, max_proxies=60):
    """Aggregate, deduplicate, and prioritize proxies for a specific country code."""
    if not country_code:
        return []

    country_code = country_code.upper()
    known = KNOWN_PROXIES.get(country_code, [])

    cache_key = f'proxies_final_{country_code}'
    cached = cache.get(cache_key)
    if cached:
        result = list(cached)
        for kp in reversed(known):
            if kp in result:
                result.remove(kp)
            result.insert(0, kp)

        working_proxy = cache.get(f'working_proxy_{country_code}')
        if working_proxy:
            if working_proxy in result:
                result.remove(working_proxy)
            result.insert(0, working_proxy)
        return result[:max_proxies]

    proxies = []
    # 0. Known/verified static proxies
    proxies.extend(known)
    # 1. TheSpeedX & Raw lists (GeoLite2 filtered)
    proxies.extend(_fetch_from_speedx_and_raw(country_code))
    # 2. Monosans
    proxies.extend(_fetch_from_monosans(country_code))
    # 3. ProxyScrape
    proxies.extend(_fetch_from_proxyscrape(country_code))
    # 4. GeoNode
    proxies.extend(_fetch_from_geonode(country_code))

    # Deduplicate while preserving protocol priority: SOCKS5 > SOCKS4 > HTTP
    seen = set()
    socks5_list = []
    socks4_list = []
    http_list = []

    for p in proxies:
        p_clean = p.strip()
        if p_clean and p_clean not in seen:
            seen.add(p_clean)
            if p_clean.startswith('socks5://'):
                socks5_list.append(p_clean)
            elif p_clean.startswith('socks4://'):
                socks4_list.append(p_clean)
            else:
                http_list.append(p_clean)

    ordered = socks5_list + socks4_list + http_list

    # Put known proxies at the very front
    for kp in reversed(known):
        if kp in ordered:
            ordered.remove(kp)
        ordered.insert(0, kp)

    # Known working proxy on top
    working_proxy = cache.get(f'working_proxy_{country_code}')
    if working_proxy:
        if working_proxy in ordered:
            ordered.remove(working_proxy)
        ordered.insert(0, working_proxy)

    cache.set(cache_key, ordered, 600)  # Cache for 10 minutes
    return ordered[:max_proxies]


def _check_tcp(proxy_url, timeout=1.5):
    """Quickly check if the proxy's TCP port is open."""
    try:
        # Extract host and port from protocol://host:port
        match = re.search(r'://([^:]+):(\d+)', proxy_url)
        if not match:
            return None
        host, port = match.group(1), int(match.group(2))
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return proxy_url
    except Exception:
        return None


def filter_alive_proxies(proxy_list, max_workers=25, timeout=1.5):
    """Filter proxies concurrently using quick TCP socket connection."""
    if not proxy_list:
        return []

    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(lambda p: _check_tcp(p, timeout=timeout), proxy_list)
        for r in results:
            if r:
                alive.append(r)
    return alive


def record_working_proxy(country_code, proxy):
    """Cache the successful proxy for this country for fast reuse."""
    if country_code and proxy:
        cache.set(f'working_proxy_{country_code.upper()}', proxy, 3600)  # 1 hour


def extract_or_download_with_proxy_retry(url, opts, download=False, target_country=None, custom_proxy=None, progress_callback=None):
    """Keep trying proxies for target region until yt-dlp extraction or download works."""
    # 1. Custom proxy provided by user
    if custom_proxy:
        proxy_str = custom_proxy.strip()
        if not re.match(r'^[a-zA-Z0-9]+://', proxy_str):
            proxy_str = f'socks4://{proxy_str}'

        if progress_callback:
            progress_callback(f'Trying custom proxy: {proxy_str}...', 1, 1)

        proxy_opts = opts.copy()
        proxy_opts['proxy'] = proxy_str
        proxy_opts['socket_timeout'] = 10
        proxy_opts['extractor_retries'] = 2
        proxy_opts['retries'] = 2
        proxy_opts['nocheckcertificate'] = True
        if 'extractor_args' not in proxy_opts:
            proxy_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'ios', 'web']}}

        for attempt in range(1, 4):
            if progress_callback:
                progress_callback(f'Trying custom proxy [Attempt {attempt}/3]: {proxy_str}...', attempt, 3)
            try:
                with yt_dlp.YoutubeDL(proxy_opts) as ydl:
                    return ydl.extract_info(url, download=download)
            except Exception as e:
                logger.warning('Custom proxy %s failed on attempt %d: %s', proxy_str, attempt, e)
                last_exc = e
                if attempt < 3:
                    time.sleep(0.5)

        if not target_country:
            raise last_exc

    # 2. If no target country given, auto-detect from the video
    countries_to_try = []
    if target_country:
        code = get_country_code(target_country)
        if code:
            countries_to_try.append(code)
    else:
        detection = detect_video_country(url)
        if detection.get('restricted') and detection.get('countries'):
            countries_to_try.extend(detection['countries'])

    if not countries_to_try:
        # Default fallback to popular regions if unstated
        countries_to_try = ['IN', 'US', 'GB']

    last_exc = None
    for country in countries_to_try:
        country_name = get_country_name(country)
        logger.info('Searching proxies for country: %s (%s)', country, country_name)
        if progress_callback:
            progress_callback(f'Locating working proxies for {country_name}...', 0, 0)

        proxies = get_proxies_for_country(country, max_proxies=40)
        alive_proxies = filter_alive_proxies(proxies, max_workers=20, timeout=1.8)

        # Prioritize verified known proxies at the very front of alive_proxies
        known = KNOWN_PROXIES.get(country, [])
        for kp in reversed(known):
            if kp not in alive_proxies:
                alive_proxies.insert(0, kp)
            else:
                alive_proxies.remove(kp)
                alive_proxies.insert(0, kp)

        total_alive = len(alive_proxies)
        if total_alive == 0:
            logger.warning('No alive proxies found for %s', country)
            continue

        logger.info('Found %d alive proxies for %s. Retrying yt-dlp...', total_alive, country)

        for idx, proxy in enumerate(alive_proxies):
            current_num = idx + 1
            proxy_opts = opts.copy()
            proxy_opts['proxy'] = proxy
            proxy_opts['socket_timeout'] = 8
            proxy_opts['extractor_retries'] = 2
            proxy_opts['retries'] = 2
            proxy_opts['nocheckcertificate'] = True
            if 'extractor_args' not in proxy_opts:
                proxy_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'ios', 'web']}}

            for attempt in range(1, 4):
                if progress_callback:
                    progress_callback(
                        f'Trying proxy {current_num}/{total_alive} ({country_name}) [Attempt {attempt}/3]: {proxy}',
                        current_num,
                        total_alive
                    )

                try:
                    with yt_dlp.YoutubeDL(proxy_opts) as ydl:
                        info = ydl.extract_info(url, download=download)
                        record_working_proxy(country, proxy)
                        logger.info('Successfully bypassed region using proxy: %s (%s) on attempt %d', proxy, country, attempt)
                        if progress_callback:
                            progress_callback(f'Connected via {country_name} ({proxy})!', total_alive, total_alive)
                        return info
                except Exception as e:
                    last_exc = e
                    logger.debug('Proxy %s failed on attempt %d for %s: %s', proxy, attempt, url, e)
                    if attempt < 3:
                        time.sleep(0.5)

    # If all proxies failed
    c_names = ', '.join([get_country_name(c) for c in countries_to_try])
    err_msg = (
        f'Could not bypass geo-restriction using free proxies for {c_names}. '
        f'All tested proxies timed out or were blocked by YouTube. '
        f'Please try entering a custom proxy in Settings.'
    )
    if last_exc:
        raise DownloadError(f'{err_msg} (Last error: {last_exc})')
    raise DownloadError(err_msg)
