"""KlipperTelegramBot service: Moonraker agent and Telegram notifications.

The Moonraker agent receives server-initiated G-code remote methods. This
feature is not exposed by the documented pymoonraker high-level API, so one
isolated aiohttp agent websocket is retained. No second websocket reader.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from .camera import CameraError, camera_list, select_camera, fetch_snapshot
from .core import load_config, render
from .status_format import status_message
from .store import MoonrakerStore
from .telegram_client import TelegramClient

LOG = logging.getLogger('klipper-telegram')
VERSION = '0.4.0'


class Service:
    def __init__(self, config_path):
        self.cfg = load_config(config_path)
        self.printer = self.cfg.get('general', 'printer_name', fallback='Printer')
        self.moonraker = self.cfg.get('moonraker', 'url', fallback='http://127.0.0.1:7125').rstrip('/')
        self.api_key = self.cfg.get('moonraker', 'api_key', fallback='').strip()
        self.store = MoonrakerStore(self.moonraker, self.api_key)
        token_file = Path(self.cfg.get('telegram', 'token_file')).expanduser()
        self.token = token_file.read_text(encoding='utf-8').strip() if token_file.is_file() else ''
        self.bot_name = ''
        self.telegram = TelegramClient(self)
        self.session = None
        self.ws = None
        self.state = {}
        self.next_id = 0
        self.pending = {}
        self.queue = asyncio.Queue(maxsize=100)
        self._subscribed = False
        self._camera_lock = asyncio.Semaphore(2)

    def headers(self):
        return {'X-Api-Key': self.api_key} if self.api_key else {}

    async def rpc(self, method, params=None, wait=False):
        if self.ws is None or self.ws.closed:
            raise ConnectionError('Moonraker disconnected')
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future() if wait else None
        if future is not None:
            self.pending[request_id] = future
        try:
            await self.ws.send_json({'jsonrpc': '2.0', 'method': method,
                                     'params': params or {}, 'id': request_id})
            if future is not None:
                return await asyncio.wait_for(future, timeout=10)
        finally:
            if future is not None:
                self.pending.pop(request_id, None)

    async def console(self, message):
        clean = str(message).replace('\r', ' ').replace('\n', ' ').replace('"', "'")[:300]
        try:
            await self.rpc('printer.gcode.script', {'script': 'M118 ' + clean})
        except (ConnectionError, aiohttp.ClientError):
            LOG.debug('Console unavailable')

    def console_template(self, event, fallback, values=None):
        section = 'on_pairing_' + event
        if not self.cfg.getboolean(section, 'enabled', fallback=True):
            return ''
        template = self.cfg.get(section, 'console_message', fallback=fallback)
        return render(template.strip().strip('"'),
                      {'printer': self.printer, 'bot': self.bot_name, **(values or {})})

    def hook(self, name):
        section = 'on_' + name
        if not self.cfg.getboolean(section, 'enabled', fallback=False):
            return
        values = {'printer': self.printer, **self.state}
        message = render(self.cfg.get(section, 'message', fallback='').strip().strip('"'), values)
        image = self.cfg.getboolean(section, 'include_image', fallback=False)
        if not message and not image:
            return
        try:
            self.queue.put_nowait((message, image, self.cfg.get(section, 'camera', fallback='')))
        except asyncio.QueueFull:
            LOG.warning('Notification queue full; dropped hook %s', name)

    async def pairing_created(self):
        if not await self.store.consume_console_arm():
            await self.console('Telegram pairing DENIED: authorize locally using klipper-telegram pairing arm')
            return
        code, _ = await self.store.generate(self.cfg.getint('pairing', 'expiration_seconds', fallback=600))
        text = self.console_template('created', 'Telegram Pairing Code: {code}; bot @{bot}',
                                     {'code': code, 'bot': self.bot_name or 'unknown'})
        if text:
            await self.console(text)
        LOG.info('Pairing created (code intentionally not logged)')

    async def handle_command(self, cmd, argument, user, chat_id):
        user_id = user.id
        username = user.username or ''
        if cmd == '/start' and argument:
            cmd = '/subscribe'
        if cmd == '/subscribe':
            ok, reason = await self.store.claim(argument, user_id, chat_id, username,
                                                 self.cfg.getint('pairing', 'max_attempts', fallback=5))
            if ok:
                await self.telegram.send(chat_id, f'✅ {self.printer} subscribed.')
                try:
                    await self.telegram.register_subscriber(chat_id)
                except Exception:
                    LOG.warning('Could not set subscriber command menu for %s', user_id)
                who = '@' + username if username else f'User {user_id}'
                text = self.console_template('success', 'Connected to Telegram-User {user}', {'user': who})
                if text:
                    await self.console(text)
            else:
                await self.telegram.send(chat_id, {
                    'invalid': 'Invalid pairing code.', 'expired': 'Pairing code expired or not active.',
                    'rate_limited': 'Too many attempts. Try again later.'
                }.get(reason, 'Pairing failed.'))
            return
        if cmd in ('/start', '/help'):
            await self.telegram.send(chat_id, 'Use /subscribe CODE to connect. Once subscribed: '
                                            '/status, /image [camera], /image list, /notifications, /unsubscribe.')
            return
        if not await self.store.is_subscribed(user_id, chat_id):
            await self.telegram.send(chat_id, 'Unauthorized. Pair with a code from the printer administrator.')
            return
        if cmd == '/unsubscribe':
            await self.store.unsubscribe(user_id)
            await self.telegram.unregister_subscriber(chat_id)
            await self.telegram.send(chat_id, 'Subscription removed.')
        elif cmd == '/status':
            # Query current values on demand so /status isn't tied to stale events.
            stats = dict(self.state)
            display, vsd = {}, {}
            estimated_time = None
            try:
                result = await self.rpc('printer.objects.query', {'objects': {
                    'print_stats': None, 'display_status': ['progress'],
                    'virtual_sdcard': ['progress']}}, wait=True)
                status = (result or {}).get('status', {})
                stats = status.get('print_stats') or stats
                display = status.get('display_status') or {}
                vsd = status.get('virtual_sdcard') or {}
                if stats.get('state') == 'printing' and stats.get('filename'):
                    try:
                        metadata = await self.rpc('server.files.metadata',
                            {'filename': stats['filename']}, wait=True)
                        estimated_time = (metadata or {}).get('estimated_time')
                    except (ConnectionError, RuntimeError, asyncio.TimeoutError, ValueError, TypeError):
                        LOG.debug('File metadata unavailable for /status')
            except (ConnectionError, RuntimeError, asyncio.TimeoutError, ValueError, TypeError):
                LOG.debug('Live printer query unavailable for /status')
                if 'progress' in self.state:
                    display['progress'] = self.state['progress'] / 100
            await self.telegram.send(chat_id,
                status_message(self.printer, stats, display, vsd, estimated_time),
                parse_mode='HTML')
        elif cmd == '/notifications':
            enabled = [s for s in self.cfg.sections() if s.startswith('on_') and
                       not s.startswith('on_pairing_') and self.cfg.getboolean(s, 'enabled', fallback=False)]
            await self.telegram.send(chat_id, 'Enabled events: ' + (', '.join(enabled) or 'none'))
        elif cmd == '/image':
            if not self.cfg.getboolean('image', 'enabled', fallback=True):
                await self.telegram.send(chat_id, 'Camera snapshots are disabled.')
                return
            try:
                cams = await camera_list(self.session, self.moonraker, self.headers())
                if argument.casefold() == 'list':
                    await self.telegram.send(chat_id, 'Cameras: ' + (', '.join(c.get('name', '?') for c in cams) or 'none'))
                    return
                cam = select_camera(cams, argument, self.cfg.get('image', 'default_camera', fallback=''))
                photo = await self.snapshot(cam)
                caption = render(self.cfg.get('image', 'caption', fallback='📷 {printer} – {camera_name}'),
                                 {'printer': self.printer, 'camera_name': cam.get('name', '')})
                await self.telegram.photo(chat_id, photo, caption)
            except (CameraError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                LOG.warning('Camera snapshot failed: %s', type(exc).__name__)
                await self.telegram.send(chat_id, f'Camera unavailable: {exc if isinstance(exc, CameraError) else "connection error"}')
        else:
            await self.telegram.send(chat_id, 'Unknown command. Use /help.')

    async def snapshot(self, cam):
        async with self._camera_lock:
            # Mainsail proxies /webcam/... on the local webserver (usually port 80).
            proxy = self.cfg.get('image', 'proxy_base', fallback='http://127.0.0.1')
            hosts = self.cfg.get('image', 'allowed_hosts', fallback='').split(',')
            return await fetch_snapshot(self.session, cam, proxy,
                self.cfg.getint('image', 'timeout', fallback=10),
                self.cfg.getint('image', 'max_image_size_mb', fallback=8) * 1024 * 1024,
                hosts)

    async def sender_loop(self):
        while True:
            message, image, camera_name = await self.queue.get()
            try:
                subscribers = await self.store.subscribers()
                photo = None
                if image:
                    try:
                        cams = await camera_list(self.session, self.moonraker, self.headers())
                        cam = select_camera(cams, camera_name, self.cfg.get('image', 'default_camera', fallback=''))
                        photo = await self.snapshot(cam)
                    except (CameraError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        LOG.warning('Hook snapshot failed: %s', type(exc).__name__)
                for sub in subscribers:
                    try:
                        if photo:
                            photo.seek(0)
                            await self.telegram.photo(sub['chat_id'], photo, message or self.printer)
                        elif message:
                            await self.telegram.send(sub['chat_id'], message)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOG.exception('Telegram delivery failure to user %s', sub['user_id'])
            finally:
                self.queue.task_done()

    async def expiration_loop(self):
        while True:
            try:
                if await self.store.expire():
                    text = self.console_template('expired', 'Telegram Pairing Code expired')
                    if text:
                        await self.console(text)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('Pairing expiry check failed')
            await asyncio.sleep(5)

    async def update_state(self, payload, bootstrap=False):
        stats = payload.get('print_stats', {})
        old = self.state.get('state')
        self.state.update({k: v for k, v in stats.items() if k in ('state', 'filename')})
        new = self.state.get('state')
        if new and old is not None and old != new and not bootstrap:
            if new in ('printing', 'paused', 'complete', 'cancelled', 'error'):
                self.hook(new)
        display = payload.get('display_status', {})
        if 'progress' in display:
            self.state['progress'] = round(float(display['progress']) * 100, 1)

    async def subscribe_if_ready(self):
        if self._subscribed:
            return
        try:
            async with self.session.get(self.moonraker + '/printer/info', headers=self.headers(),
                                        timeout=aiohttp.ClientTimeout(total=5)) as response:
                data = await response.json(content_type=None)
                if response.status != 200 or data.get('result', {}).get('state') != 'ready':
                    LOG.info('Klippy not ready; deferring print status subscription')
                    return
            result = await self.rpc('printer.objects.subscribe',
                {'objects': {'print_stats': None, 'display_status': ['progress']}}, wait=True)
            self._subscribed = True
            if isinstance(result, dict):
                await self.update_state(result.get('status', {}), bootstrap=True)
            LOG.info('Printer status subscription active')
        except Exception as exc:
            LOG.warning('Printer subscription delayed: %s', type(exc).__name__)

    async def handle_ws_message(self, data):
        if not isinstance(data, dict):
            return
        if 'id' in data:
            future = self.pending.get(data['id'])
            if future is not None and not future.done():
                if 'error' in data:
                    future.set_exception(RuntimeError('Moonraker RPC returned an error'))
                else:
                    future.set_result(data.get('result'))
            elif 'error' in data:
                LOG.warning('Moonraker RPC error for request %s (details omitted)', data['id'])
            return
        method = data.get('method')
        if method == 'klipper_telegram_pairing_create':
            asyncio.create_task(self.pairing_created())
        elif method == 'klipper_telegram_status':
            asyncio.create_task(self.report_status())
        elif method == 'notify_status_update':
            params = data.get('params', [])
            if params and isinstance(params[0], dict):
                await self.update_state(params[0])
        elif method == 'notify_klippy_ready':
            asyncio.create_task(self.subscribe_if_ready())
        elif method == 'notify_klippy_shutdown':
            self._subscribed = False

    async def report_status(self):
        await self.console(f'Telegram @{self.bot_name or "not configured"}; subscribers: {len(await self.store.subscribers())}')

    async def moonraker_loop(self):
        split = urlsplit(self.moonraker)
        uri = ('wss' if split.scheme == 'https' else 'ws') + '://' + split.netloc + split.path.rstrip('/') + '/websocket'
        backoff = 2
        while True:
            try:
                async with self.session.ws_connect(uri, headers=self.headers(), heartbeat=30) as ws:
                    self.ws = ws
                    self._subscribed = False
                    # Reader is running before any synchronous RPC awaits.
                    reader = asyncio.create_task(self.read_messages(ws))
                    try:
                        await self.rpc('server.connection.identify',
                            {'client_name': 'klipper-telegram', 'version': VERSION,
                             'type': 'agent', 'url': 'https://github.com/D3ADD3V3R/KlipperTelegramBot'}, wait=True)
                        for method in ('klipper_telegram_pairing_create', 'klipper_telegram_status'):
                            await self.rpc('connection.register_remote_method', {'method_name': method}, wait=True)
                        LOG.info('Moonraker agent methods registered')
                        await self.subscribe_if_ready()
                        backoff = 2
                        await reader
                    finally:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('Moonraker agent disconnected; retry in %ss', backoff)
            finally:
                self.ws = None
                self._subscribed = False
                for future in self.pending.values():
                    if not future.done():
                        future.cancel()
                self.pending.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def read_messages(self, ws):
        async for event in ws:
            if event.type == aiohttp.WSMsgType.TEXT:
                try:
                    await self.handle_ws_message(json.loads(event.data))
                except Exception:
                    LOG.exception('Unable to process Moonraker event')
            elif event.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break
        raise ConnectionError('Moonraker websocket closed')

    async def telegram_loop(self):
        if not self.token:
            LOG.warning('Telegram bot token missing')
            return
        retry = 5
        while True:
            try:
                await self.telegram.start()
                await asyncio.Future()  # polling is managed by python-telegram-bot
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception('Telegram connection issue; retry in %ss', retry)
                await self.telegram.stop()
                await asyncio.sleep(retry)
                retry = min(retry * 2, 60)
            finally:
                await self.telegram.stop()

    async def run(self):
        async with aiohttp.ClientSession() as self.session:
            self.store.session = self.session
            legacy_path = Path(self.cfg.get('general', 'legacy_sqlite',
                                           fallback=str(Path.home() / 'printer_data/config/subscribers.db'))).expanduser()
            await self.store.migrate_legacy(legacy_path)
            await self.store.identity()
            await asyncio.gather(self.moonraker_loop(), self.telegram_loop(),
                                 self.sender_loop(), self.expiration_loop())
