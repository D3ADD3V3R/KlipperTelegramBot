"""Snapshot fetcher for Moonraker-registered cameras; no arbitrary URL input."""
import ipaddress
import io
from urllib.parse import urljoin, urlsplit

import aiohttp


class CameraError(Exception):
    pass


def safe_snapshot_url(snapshot_url: str, proxy_base: str, allowed_hosts=()):
    """Only loopback and explicitly configured hosts; no redirects, credentials or fragments."""
    if not snapshot_url or not snapshot_url.strip():
        raise CameraError('Camera has no snapshot URL')
    raw = snapshot_url.strip()
    # Browser-relative paths are served by the local Moonraker/Mainsail proxy.
    if raw.startswith('//'):
        raise CameraError('Protocol-relative camera URL not allowed')
    url = urljoin(proxy_base.rstrip('/') + '/', raw)
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise CameraError('Unsupported snapshot URL')
    host = parsed.hostname.lower()
    try:
        ip = ipaddress.ip_address(host)
        valid = ip.is_loopback
    except ValueError:
        valid = host == 'localhost'
    allowed = {h.strip().lower() for h in allowed_hosts if h.strip()}
    if not (valid or host in allowed):
        raise CameraError('Camera host not allowed; configure [image] allowed_hosts')
    return url


async def camera_list(session, moonraker_url, headers=None):
    async with session.get(moonraker_url.rstrip('/') + '/server/webcams/list', headers=headers or {},
                           timeout=aiohttp.ClientTimeout(total=8)) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)
        if 'error' in data:
            raise CameraError('Moonraker webcam API returned an error')
        return [cam for cam in data.get('result', {}).get('webcams', []) if cam.get('enabled', True)]


def select_camera(cameras, name='', default=''):
    if not cameras:
        raise CameraError('No enabled cameras found in Moonraker')
    wanted = (name or default).strip()
    if not wanted:
        return cameras[0]
    for cam in cameras:
        if cam.get('name', '').casefold() == wanted.casefold():
            return cam
    raise CameraError('Unknown camera: ' + wanted)


async def fetch_snapshot(session, camera, proxy_base, timeout=10, max_bytes=8 * 1024 * 1024, allowed_hosts=()):
    url = safe_snapshot_url(camera.get('snapshot_url', ''), proxy_base, allowed_hosts)
    async with session.get(url, allow_redirects=False,
                           timeout=aiohttp.ClientTimeout(total=timeout)) as response:
        if 300 <= response.status < 400:
            raise CameraError('Camera redirect is not permitted')
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
        if content_type not in ('image/jpeg', 'image/png'):
            raise CameraError('Camera did not return a JPEG/PNG image')
        data = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise CameraError('Snapshot exceeds configured size limit')
        if not data or not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n')):
            raise CameraError('Snapshot has an invalid image signature')
        bio = io.BytesIO(data)
        bio.name = 'snapshot.jpg' if content_type == 'image/jpeg' else 'snapshot.png'
        return bio
