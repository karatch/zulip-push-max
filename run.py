#!/usr/bin/env python3
import asyncio
import os
import configparser
import logging
import signal
import sys
import aiohttp
import urllib3
from pathlib import Path
from dotenv import load_dotenv

from bridge import ZulipMaksBridge
from bot import MaksBotPoll


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

ZULIPRC_PATH = BASE_DIR / "zuliprc"
DOTENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=DOTENV_PATH)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def load_config():
    logging.info(f"[Main] Чтение конфигурационного файла: {ZULIPRC_PATH}")
    if not os.path.exists(ZULIPRC_PATH):
        raise FileNotFoundError(f"Критическая ошибка: Файл {ZULIPRC_PATH} не найден!")

    config = configparser.ConfigParser()
    config.read(ZULIPRC_PATH)
    try:
        zulip_site = config.get('api', 'site').rstrip('/')

        maks_token = os.getenv("MAKS_BOT_TOKEN")
        maks_api_url = os.getenv("MAKS_API_URL", "https://company.com").rstrip('/')

        if not maks_token:
            raise ValueError("Переменная MAKS_BOT_TOKEN не найдена в файле .env!")

        logging.info("[Main] Конфигурация успешно загружена.")
        return {
            "maks_token": maks_token,
            "maks_api_url": maks_api_url,
            "zulip_site": zulip_site
        }
    except Exception as e:
        raise KeyError(f"Ошибка чтения конфигурационных параметров: {e}")


async def main():
    stop_event = asyncio.Event()

    def handle_exit_signal():
        print("\n[Система] Сервис остановлен пользователем через Ctrl+C.")
        stop_event.set()
        os._exit(0)

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, handle_exit_signal)
        loop.add_signal_handler(signal.SIGTERM, handle_exit_signal)
    except NotImplementedError:
        pass

    import database
    logging.info("[Main] Инициализация базы данных...")
    database.init_db()

    try:
        config = load_config()
    except Exception as e:
        logging.critical(f"[Main] Не удалось запустить приложение: {e}")
        return

    logging.info("[Main] Инициализация объектов шлюза и бота мессенджера Макс...")

    bridge = ZulipMaksBridge(
        maks_token=config["maks_token"],
        maks_api_url=config["maks_api_url"],
        zulip_site=config["zulip_site"],
        loop=loop,
        zuliprc_path=ZULIPRC_PATH
    )

    bot_poll = MaksBotPoll(
        maks_token=config["maks_token"],
        maks_api_url=config["maks_api_url"],
        zulip_bridge=bridge,
        stop_event=stop_event
    )

    try:
        async with aiohttp.ClientSession() as session:
            logging.info("[Main] Запуск параллельных процессов: polling Макс-бота и bridge...")

            await bridge.start(session)
            await bot_poll.start(session)

            await stop_event.wait()
    except Exception as e:
        logging.exception(f"[Main] Критическая ошибка в основном цикле: {e}")


if __name__ == "__main__":
    asyncio.run(main())
