import asyncio
import logging
import html  # Добавлен для очистки сырых HTML-тегов из Zulip
import zulip
from pathlib import Path

import database


class ZulipMaxBridge:
    def __init__(self, stream_name: str, max_token: str, loop: asyncio.AbstractEventLoop, zuliprc_path: Path):
        self.stream_name = stream_name
        self.max_token = max_token
        self.loop = loop
        self.zuliprc_path = zuliprc_path
        self.bot_email = None
        self.zulip_client = None
        self.session = None
        self.semaphore = asyncio.Semaphore(10)

    async def send_max_push(self, max_chat_id: str, topic: str, sender_name: str, message_content: str) -> None:
        logging.info(f"[Bridge API] Попытка отправки пуша в Макс для пользователя: {max_chat_id}")

        safe_content = html.escape(message_content)

        text = (
            f"🔔 **Новое сообщение в Zulip [{self.stream_name}]**\n"
            f"**Тема:** {topic}\n"
            f"**От:** {sender_name}\n\n"
            f"{safe_content}"
        )

        # Убедиться, что это конечная точка API (/api/v1/messages или похожая)
        url = "https://max.ru"

        headers = {
            "Authorization": f"Bearer {self.max_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "chat_id": max_chat_id,
            "text": text
        }

        async with self.semaphore:
            try:
                async with self.session.post(url, json=payload, headers=headers, timeout=5) as response:
                    if response.status in [200, 201]:
                        logging.info(f"[Bridge API] Пуш успешно доставлен в Макс для {max_chat_id}")
                    else:
                        res_text = await response.text()
                        logging.error(
                            f"[Bridge API] Ошибка API Макс (Статус {response.status}) для {max_chat_id}: {res_text}")
            except Exception as e:
                logging.error(f"[Bridge API] Исключение сети при отправке в Макс для {max_chat_id}: {e}")

    def get_stream_subscribers(self) -> list:
        try:
            result = self.zulip_client.get_subscribers(stream=self.stream_name)
            if result.get('result') == 'success':
                return result.get('subscribers', [])
            return []
        except Exception as e:
            logging.error(f"[Bridge] Исключение при получении подписчиков Zulip: {e}")
            return []

    def process_event(self, event: dict) -> None:
        if event.get('type') != 'message':
            return

        msg = event['message']
        if msg['sender_email'] == self.bot_email or msg['type'] == 'private':
            return

        sender_id = msg['sender_id']
        sender_name = msg['sender_full_name']
        topic = msg.get('subject', 'Без темы')
        content = msg['content']

        logging.info(f"[Bridge] Перехвачено сообщение в Zulip от {sender_name} в тему '{topic}'")

        subscribers = self.get_stream_subscribers()
        for user_id in subscribers:
            if user_id == sender_id:
                continue

            max_id = database.get_tg_id_by_zulip(str(user_id))
            if max_id:
                self.loop.call_soon_threadsafe(
                    lambda m=max_id, t=topic, s=sender_name, c=content: asyncio.create_task(
                        self.send_max_push(m, t, s, c)
                    )
                )

    def start_zulip_listener(self):
        logging.info(f"[Bridge] Запуск call_on_each_event для стрима '{self.stream_name}'...")
        try:
            self.zulip_client.call_on_each_event(
                callback=self.process_event,
                event_types=['message'],
                narrow=[['stream', self.stream_name]]
            )
        except Exception as e:
            logging.critical(f"[Bridge] Ошибка в потоке прослушивания Zulip: {e}")

    async def start(self, session):
        self.session = session
        try:
            self.zulip_client = zulip.Client(config_file=str(self.zuliprc_path))
            self.bot_email = self.zulip_client.email
            logging.info(f"[Bridge] Авторизация в Zulip успешна. Email: {self.bot_email}")
        except Exception as e:
            logging.critical(f"[Bridge] Ошибка авторизации в Zulip: {e}")
            return

        await self.loop.run_in_executor(None, self.start_zulip_listener)
