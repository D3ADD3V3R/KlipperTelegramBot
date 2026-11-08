import argparse
import asyncio
import logging
from pathlib import Path
from .core import Store, load_config
from .service import Service


def main():
    parser=argparse.ArgumentParser(prog='klipper-telegram')
    parser.add_argument('--config',default=str(Path.home()/'printer_data/config/klipper_telegram.conf'))
    cmd=parser.add_subparsers(dest='command',required=True)
    pairing=cmd.add_parser('pairing')
    pairing.add_argument('action',choices=['create'])
    cmd.add_parser('status')
    cmd.add_parser('serve')
    args=parser.parse_args()
    cfg=load_config(args.config)
    if args.command=='serve':
        logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
        asyncio.run(Service(args.config).run())
    else:
        store=Store(str(Path(args.config).expanduser().parent/'subscribers.db'))
        if args.command=='pairing':
            code,expiry=store.generate(cfg.getint('pairing','expiration_seconds',fallback=600))
            print(f'Printer: {cfg.get("general","printer_name",fallback="Printer")}\nPairing code: {code}\nExpires in: {cfg.getint("pairing","expiration_seconds",fallback=600)} seconds')
            print('NOTE: CLI pairing does not notify a running daemon until expiry. Prefer TELEGRAM_PAIRING_CREATE in Mainsail.')
        else:
            print(f'Printer UUID: {store.identity()}\nSubscribers: {len(store.subscribers())}')

if __name__=='__main__': main()
