import argparse
import asyncio
import logging
from pathlib import Path
from .core import load_config
from .store import MoonrakerStore
from .service import Service


def main():
    parser=argparse.ArgumentParser(prog='klipper-telegram')
    parser.add_argument('--config',default=str(Path.home()/'printer_data/config/klipper_telegram.conf'))
    cmd=parser.add_subparsers(dest='command',required=True)
    pairing=cmd.add_parser('pairing')
    pairing.add_argument('action',choices=['create','arm'])
    pairing.add_argument('--seconds',type=int,default=60,help='Duration of console pairing authorization (1-300 sec)')
    cmd.add_parser('status')
    cmd.add_parser('serve')
    args=parser.parse_args()
    cfg=load_config(args.config)
    if args.command=='serve':
        logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
        asyncio.run(Service(args.config).run())
    else:
        async def cli():
            url=cfg.get('moonraker','url',fallback='http://127.0.0.1:7125')
            key=cfg.get('moonraker','api_key',fallback='').strip()
            async with MoonrakerStore(url,key) as store:
                if args.command=='pairing' and args.action=='arm':
                    await store.arm_console(args.seconds)
                    print(f'Console pairing armed for {args.seconds} seconds. Run TELEGRAM_PAIRING_CREATE once in Mainsail.')
                elif args.command=='pairing':
                    code,expiry=await store.generate(cfg.getint('pairing','expiration_seconds',fallback=600))
                    print(f'Printer: {cfg.get("general","printer_name",fallback="Printer")}\nPairing code: {code}\nExpires in: {cfg.getint("pairing","expiration_seconds",fallback=600)} seconds')
                else:
                    print(f'Printer UUID: {await store.identity()}\nSubscribers: {len(await store.subscribers())}')
        asyncio.run(cli())

if __name__=='__main__': main()
