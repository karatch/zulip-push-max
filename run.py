#!/usr/bin/env python3
import asyncio
import os
import configparser
import logging
import sys
from pathlib import Path
import aiohttp

import database
from bot import start_max_bot

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

ZULIPRC_PATH = BASE_DIR / "zuliprc"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

from bridge import ZulipMaxBridge


def load_config():
    logging.info(f"[Main] Чтение конфигурационного файла: {ZULIPRC_PATH}")
    if not os.path.exists(ZULIPRC_PATH):
        raise FileNotFoundError(f"Критическая ошибка: Файл {ZULIPRC_PATH} не найден!")

    config = configparser.ConfigParser()
    config.read(ZULIPRC_PATH)
    try:
        return {
            "stream": config.get('ntfy', 'stream'),
            "max_token": config.get('max', 'api_token'),  # Читаем из секции [max]
            "max_user_id": config.get('max', 'bot_username'),
            "max_password": config.get('max', 'bot_password')
        }
    except Exception as e:
        raise KeyError(f"Ошибка чтения секций [ntfy] или [max] в zuliprc: {e}")


async def main():
    loop = asyncio.get_running_loop()

    logging.info("[Main] Инициализация базы данных SQLite...")
    database.init_db()

    try:
        config = load_config()
    except Exception as e:
        logging.critical(f"[Main] Не удалось запустить приложение: {e}")
        return

    bridge = ZulipMaxBridge(
        stream_name=config["stream"],
        max_token=config["max_token"],
        loop=loop,
        zuliprc_path=ZULIPRC_PATH
    )

    async with aiohttp.ClientSession() as session:
        logging.info("[Main] Запуск параллельных процессов: Клиент Макс (Matrix) и Мост Zulip...")

        await asyncio.gather(
            start_max_bot(config["max_user_id"], config["max_password"]),
            bridge.start(session)
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("[Main] Сервис остановлен пользователем.")
    except Exception as e:
        logging.exception(f"[Main] Непредвиденное критическое исключение: {e}")
