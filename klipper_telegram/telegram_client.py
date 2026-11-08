"""Telegram adapter using python-telegram-bot, with per-chat command menus."""
import logging

from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import Application, CommandHandler

LOG = logging.getLogger('klipper-telegram')
PUBLIC = [BotCommand('start', 'Bot starten'), BotCommand('subscribe', 'Drucker abonnieren'),
          BotCommand('help', 'Hilfe anzeigen')]
PRIVATE = [BotCommand('status', 'Druckerstatus'), BotCommand('image', 'Kamerabild aufnehmen'),
           BotCommand('notifications', 'Benachrichtigungen anzeigen'),
           BotCommand('unsubscribe', 'Abonnement beenden'), BotCommand('help', 'Hilfe anzeigen')]


class TelegramClient:
    def __init__(self, owner):
        self.owner = owner
        self.application = None

    async def start(self):
        if not self.owner.token:
            LOG.warning('Telegram token not configured; polling disabled')
            return
        app = Application.builder().token(self.owner.token).build()
        self.application = app
        for name in ('start', 'subscribe', 'unsubscribe', 'status', 'image', 'notifications', 'help'):
            app.add_handler(CommandHandler(name, self.dispatch))
        await app.initialize()
        try:
            me = await app.bot.get_me()
            self.owner.bot_name = me.username or ''
            await self.sync_commands()
            await app.start()
            if app.updater is None:
                raise RuntimeError('Telegram updater unavailable')
            await app.updater.start_polling(drop_pending_updates=False)
            LOG.info('Telegram polling started for @%s', self.owner.bot_name)
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        if self.application:
            app = self.application
            self.application = None
            if app.updater and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            await app.shutdown()

    async def sync_commands(self):
        if not self.owner.cfg.getboolean('telegram_commands', 'auto_register', fallback=True):
            return
        bot = self.application.bot
        if await bot.get_my_commands() != PUBLIC:
            await bot.set_my_commands(PUBLIC)
        for subscriber in await self.owner.store.subscribers():
            await self.register_subscriber(subscriber['chat_id'])

    async def register_subscriber(self, chat_id):
        if self.application and self.owner.cfg.getboolean('telegram_commands', 'use_subscriber_scopes', fallback=True):
            scope = BotCommandScopeChat(chat_id=int(chat_id))
            if await self.application.bot.get_my_commands(scope=scope) != PRIVATE:
                await self.application.bot.set_my_commands(PRIVATE, scope=scope)

    async def unregister_subscriber(self, chat_id):
        if self.application and self.owner.cfg.getboolean('telegram_commands', 'use_subscriber_scopes', fallback=True):
            await self.application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(chat_id)))

    async def dispatch(self, update, context):
        if update.effective_chat is None or update.effective_chat.type != 'private' or update.effective_user is None:
            return
        message = update.effective_message
        command = message.text.split(maxsplit=1)[0].split('@')[0].lower()
        args = message.text.split(maxsplit=1)
        argument = args[1].strip() if len(args) > 1 else ''
        await self.owner.handle_command(command, argument, update.effective_user, update.effective_chat.id)

    async def send(self, chat_id, text):
        if self.application:
            await self.application.bot.send_message(chat_id=int(chat_id), text=text[:4096])

    async def photo(self, chat_id, data, caption):
        if self.application:
            await self.application.bot.send_photo(chat_id=int(chat_id), photo=data, caption=caption[:1024])
