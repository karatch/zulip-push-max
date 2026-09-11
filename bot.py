import logging
import sys
import asyncio
from pathlib import Path
from nio import AsyncClient, MatrixRoom, RoomMessageText, InviteEvent  # Изменен импорт для инвайтов

import database

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
ZULIPRC_PATH = BASE_DIR / "zuliprc"

matrix_client = None


async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    if event.sender == matrix_client.user_id:
        return

    text = event.body.strip()
    max_user_id = event.sender
    logging.info(f"[Max Bot] Получено сообщение от {max_user_id} в комнате {room.room_id}: '{text}'")

    # Команда: Помощь / Старт
    if text.lower() in ["/start", "привет", "help", "помощь"]:
        help_text = (
            f"Привет! 👋\nЯ бот уведомлений Zulip.\n\n"
            f"**Доступные команды:**\n"
            f"• `статус` — Проверить текущую привязку к Zulip\n"
            f"• `привязать [ваш_Zulip_ID]` — Связать аккаунт (Пример: `привязать 1042`)\n"
            f"• `отвязать` — Удалить привязку и отключить пуши"
        )
        await matrix_client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": help_text}
        )

    # Команда: Статус
    elif text.lower() == "статус":
        associated_zulip_id = database.get_zulip_id_by_tg(max_user_id)
        if associated_zulip_id:
            res = f"✅ **Ваш аккаунт активен!**\n• ID в Макс: `{max_user_id}`\n• Связан с Zulip ID: `{associated_zulip_id}`"
        else:
            res = "⚠️ **Аккаунт не привязан.**\nОтправьте текстовую команду: `привязать [ваш_ID]`."

        await matrix_client.room_send(room_id=room.room_id, content={"msgtype": "m.text", "body": res})

    # Команда: Привязать ID
    elif text.lower().startswith("привязать"):
        args = text.split()
        if len(args) < 2 or not args[1].isdigit():
            logging.warning(f"[Max Bot] Ошибка валидации ID от {max_user_id}: '{text}'")
            await matrix_client.room_send(
                room_id=room.room_id,
                content={"msgtype": "m.text", "body": "❌ Ошибка! Укажите числовой ID.\nПример: `привязать 1042`"}
            )
            return

        zulip_id = args[1]
        try:
            database.add_user(zulip_id, max_user_id)
            logging.info(f"[Max Bot] Успешная привязка: Zulip {zulip_id} <-> Макс {max_user_id}")
            await matrix_client.room_send(
                room_id=room.room_id,
                content={"msgtype": "m.text", "body": f"🎉 **Успешно!** Zulip ID `{zulip_id}` успешно привязан."}
            )
        except Exception as e:
            logging.error(f"[Max Bot] Ошибка записи в БД для {max_user_id}: {e}")
            await matrix_client.room_send(
                room_id=room.room_id,
                content={"msgtype": "m.text", "body": "❌ Ошибка при записи в базу данных."}
            )

    # Команда: Отвязать
    elif text.lower() == "отвязать":
        if database.remove_user_by_tg(max_user_id):
            logging.info(f"[Max Bot] Связь для {max_user_id} удалена.")
            res = "📴 **Готово.** Связь с Zulip разорвана, уведомления отключены."
        else:
            logging.warning(f"[Max Bot] Попытка отвязки пустого аккаунта {max_user_id}")
            res = "Ваш ID не был найден в базе данных."
        await matrix_client.room_send(room_id=room.room_id, content={"msgtype": "m.text", "body": res})


# Кастомный асинхронный обработчик для приглашений в комнаты
async def invite_callback(room: MatrixRoom, event: InviteEvent) -> None:
    logging.info(f"[Max Bot] Получено приглашение в комнату {room.room_id} от {event.sender}. Принимаем...")
    # Так как метод асинхронный, запускаем его как фоновую таску внутри Event Loop
    asyncio.create_task(matrix_client.join(room.room_id))


async def start_max_bot(user_id: str, password: str):
    global matrix_client

    logging.info(f"[Max Bot] Инициализация Matrix-клиента для сервера https://max.ru ...")

    matrix_client = AsyncClient("https://max.ru", user_id)
    matrix_client.add_event_callback(message_callback, RoomMessageText)

    matrix_client.add_by_type_callback(invite_callback, InviteEvent)

    logging.info("[Max Bot] Авторизация на сервере Макс...")
    await matrix_client.login(password)

    logging.info("[Max Bot] Бот успешно запущен в режиме прослушивания событий.")
    await matrix_client.sync_forever(timeout=30000)
