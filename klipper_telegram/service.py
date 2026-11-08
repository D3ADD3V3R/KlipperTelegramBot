"""Moonraker agent and Telegram long-polling service."""
import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp
from .core import Store, load_config, render

LOG = logging.getLogger('klipper-telegram')
VERSION = '0.1.0'


class Service:
    def __init__(self, config_path):
        self.cfg = load_config(config_path)
        self.store = Store(str(Path(config_path).expanduser().parent / 'subscribers.db'))
        self.printer = self.cfg.get('general', 'printer_name', fallback='Printer')
        self.moonraker = self.cfg.get('moonraker', 'url', fallback='http://127.0.0.1:7125').rstrip('/')
        self.api_key = self.cfg.get('moonraker', 'api_key', fallback='').strip()
        self.token_file = Path(self.cfg.get('telegram', 'token_file')).expanduser()
        self.token = self.token_file.read_text(encoding='utf-8').strip() if self.token_file.is_file() else ''
        self.bot_name = ''
        self.ws = None
        self.session = None
        self.state = {}
        self.klippy_ready = False
        self.next_id = 1
        self.telegram_offset = None
        self.queue = asyncio.Queue(maxsize=100)
        self.pending_requests = {}

    def headers(self):
        return {'X-Api-Key': self.api_key} if self.api_key else {}

    async def rpc(self, method, params=None, await_result=False):
        if self.ws is None or self.ws.closed:
            raise ConnectionError('Moonraker not connected')
        ident = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future() if await_result else None
        if fut:
            self.pending_requests[ident] = fut
        await self.ws.send_json({'jsonrpc':'2.0','method':method,'params':params or {},'id':ident})
        if fut:
            try:
                return await asyncio.wait_for(fut, timeout=10)
            finally:
                self.pending_requests.pop(ident,None)

    async def console(self, message):
        # M118 yields an ordinary console response, without invoking movements.
        clean = str(message).replace('\r',' ').replace('\n',' ').replace('"', "'")[:300]
        try:
            await self.rpc('printer.gcode.script', {'script':f'M118 {clean}'})
        except (ConnectionError, aiohttp.ClientError) as exc:
            LOG.warning('Unable to send console message: %s', exc)

    def hook(self,name, values=None):
        section = f'on_{name}'
        if not self.cfg.has_section(section) or not self.cfg.getboolean(section,'enabled',fallback=False):
            return
        template = self.cfg.get(section,'message',fallback='')
        if template.strip():
            data = {'printer':self.printer, **self.state, **(values or {})}
            msg = render(template.strip().strip('"'),data)
            try:
                self.queue.put_nowait(msg)
            except asyncio.QueueFull:
                LOG.warning('Notification queue full; dropped event %s',name)

    def pair_console_template(self, event, fallback, values=None):
        section = 'on_pairing_' + event
        if self.cfg.has_section(section) and not self.cfg.getboolean(section, 'enabled', fallback=True):
            return None
        template = self.cfg.get(section, 'console_message', fallback=fallback)
        return render(template.strip().strip('\"'), {'printer':self.printer, 'bot':self.bot_name, **(values or {})})

    async def pairing_created(self):
        code, _ = self.store.generate(self.cfg.getint('pairing','expiration_seconds',fallback=600))
        text = self.pair_console_template('created', 'Telegram pairing code: {code}; bot @{bot}', {'code':code, 'bot':self.bot_name or 'unknown'})
        if text: await self.console(text)
        LOG.info('Pairing code generated (content not logged)')

    async def telegram_request(self, method, payload):
        if not self.token:
            raise RuntimeError('Telegram bot token missing')
        url=f'https://api.telegram.org/bot{self.token}/{method}'
        async with self.session.post(url,json=payload,timeout=aiohttp.ClientTimeout(total=40)) as resp:
            data=await resp.json(content_type=None)
            if resp.status != 200 or not data.get('ok'):
                raise RuntimeError(f'Telegram API {method} failed: HTTP {resp.status} (details suppressed)')
            return data['result']

    async def send(self, chat_id, message):
        await self.telegram_request('sendMessage', {'chat_id':chat_id,'text':message[:4096]})

    async def telegram_loop(self):
        if not self.token:
            LOG.warning('Token missing: Telegram polling disabled; configure token_file')
            return
        try:
            me=await self.telegram_request('getMe',{})
            self.bot_name=me.get('username','')
            LOG.info('Connected to Telegram bot @%s',self.bot_name)
        except Exception as exc:
            LOG.warning('Telegram initialization failed: %s',type(exc).__name__)
        while True:
            try:
                params={'timeout':25,'allowed_updates':['message']}
                if self.telegram_offset is not None:
                    params['offset']=self.telegram_offset
                updates=await self.telegram_request('getUpdates',params)
                for event in updates:
                    self.telegram_offset=event['update_id']+1
                    await self.handle_telegram(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning('Telegram polling problem: %s',type(exc).__name__)
                await asyncio.sleep(5)

    async def handle_telegram(self, event):
        msg=event.get('message',{})
        user=msg.get('from',{})
        chat=msg.get('chat',{})
        if not user or chat.get('type')!='private':
            return
        user_id=user['id']; chat_id=chat['id']; username=user.get('username','')
        raw=msg.get('text','').strip().split(maxsplit=1)
        if not raw:
            return
        cmd=raw[0].split('@')[0].lower(); arg=raw[1].strip() if len(raw)>1 else ''
        if cmd=='/start' and arg:
            # Telegram deep links use /start CODE.
            cmd='/subscribe'
        if cmd=='/subscribe':
            ok, reason=self.store.claim(arg,user_id,chat_id,username,
                self.cfg.getint('pairing','max_attempts',fallback=5))
            if ok:
                await self.send(chat_id,f'✅ Subscribed to {self.printer}.')
                who='@'+username if username else f'User {user_id}'
                text=self.pair_console_template('success', 'Connected to Telegram-User {user}', {'user':who})
                if text: await self.console(text)
            else:
                replies={'invalid':'Invalid pairing code.','expired':'Code expired or not active.',
                         'rate_limited':'Too many attempts. Try again later.'}
                await self.send(chat_id,replies.get(reason,'Pairing failed.'))
            return
        if cmd in ('/start','/help'):
            await self.send(chat_id,'Use /subscribe CODE to connect. /status, /unsubscribe after pairing.')
            return
        if not self.store.is_subscribed(user_id,chat_id):
            await self.send(chat_id,'Unauthorized. Request a pairing code from the printer administrator.')
            return
        if cmd=='/unsubscribe':
            self.store.unsubscribe(user_id)
            await self.send(chat_id,'Subscription removed.')
        elif cmd=='/status':
            state=self.state.get('state','unknown')
            filename=self.state.get('filename','')
            await self.send(chat_id,f'{self.printer}: {state}\n{filename}')
        else:
            await self.send(chat_id,'Commands: /status, /unsubscribe, /help')

    async def sender_loop(self):
        while True:
            message=await self.queue.get()
            for subscriber in self.store.subscribers():
                try:
                    await self.send(subscriber['chat_id'],message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOG.warning('Telegram send failed to user %s: %s',subscriber['user_id'],type(exc).__name__)
            self.queue.task_done()

    async def expiration_loop(self):
        while True:
            if self.store.expire():
                text=self.pair_console_template('expired', 'Telegram pairing code expired; no subscriber connected')
                if text: await self.console(text)
            await asyncio.sleep(1)

    async def update_state(self, payload, bootstrap=False):
        stats=payload.get('print_stats',{})
        old=self.state.get('state')
        self.state.update({k:v for k,v in stats.items() if k in ('state','filename')})
        new=self.state.get('state')
        if new and old is not None and new != old and not bootstrap:
            # Avoid treating startup state as an event; only actual transitions.
            name={'printing':'printing','paused':'paused','complete':'complete',
                  'cancelled':'cancelled','error':'error'}.get(new)
            if name:
                self.hook(name)
        display=payload.get('display_status',{})
        if 'progress' in display:
            self.state['progress']=round(float(display['progress'])*100,1)

    async def moonraker_loop(self):
        url=self.moonraker.replace('http://','ws://').replace('https://','wss://')+'/websocket'
        while True:
            try:
                async with self.session.ws_connect(url,headers=self.headers(),heartbeat=20) as ws:
                    self.ws=ws
                    await self.rpc('server.connection.identify',{'client_name':'klipper-telegram',
                        'version':VERSION,'type':'agent','url':'https://gitlab.com/'})
                    await self.rpc('connection.register_remote_method',{'method_name':'klipper_telegram_pairing_create'})
                    await self.rpc('connection.register_remote_method',{'method_name':'klipper_telegram_status'})
                    await self.rpc('printer.objects.subscribe', {'objects':{'print_stats':None,'display_status':['progress']}},await_result=False)
                    LOG.info('Connected to Moonraker; G-Code methods registered')
                    async for event in ws:
                        if event.type != aiohttp.WSMsgType.TEXT:
                            if event.type in (aiohttp.WSMsgType.ERROR,aiohttp.WSMsgType.CLOSED):
                                break
                            continue
                        data=json.loads(event.data)
                        if 'id' in data and data['id'] in self.pending_requests:
                            fut=self.pending_requests[data['id']]
                            if not fut.done():
                                fut.set_result(data)
                        method=data.get('method','')
                        if method=='klipper_telegram_pairing_create':
                            await self.pairing_created()
                        elif method=='klipper_telegram_status':
                            await self.console(f'Telegram: @{self.bot_name or "not configured"}; subscribers: {len(self.store.subscribers())}')
                        elif method=='notify_status_update':
                            await self.update_state((data.get('params') or [{}])[0])
                        elif method=='notify_klippy_ready':
                            await self.rpc('printer.objects.subscribe',{'objects':{'print_stats':None,'display_status':['progress']}})
                        elif data.get('result',{}).__class__ is dict and isinstance(data['result'],dict) and 'status' in data['result']:
                            await self.update_state(data['result']['status'],bootstrap=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning('Moonraker connection issue: %s',exc)
            finally:
                self.ws=None
                for fut in self.pending_requests.values():
                    if not fut.done():
                        fut.cancel()
                self.pending_requests.clear()
            await asyncio.sleep(5)

    async def run(self):
        async with aiohttp.ClientSession() as self.session:
            await asyncio.gather(self.moonraker_loop(),self.telegram_loop(),self.sender_loop(),self.expiration_loop())
