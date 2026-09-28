import asyncio
import logging
import urllib.parse
import zulip
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import database

class ZulipMaksBridge:
    def __init__(
            self,
            maks_token: str,
            maks_api_url: str,
            zulip_site: str,
            loop: asyncio.AbstractEventLoop,
            zuliprc_path: Path
    ):
        self.maks_token = maks_token
        self.maks_api_url = maks_api_url
        self.zulip_site = zulip_site
        self.loop = loop
        self.zuliprc_path = zuliprc_path
        self.bot_email = None
        self.zulip_client = None
        self.session = None
        self.semaphore = asyncio.Semaphore(25)

    async def send_maks_push(
            self,
            maks_user_id: str,
            stream_name: str,
            topic: str,
            sender_name: str,
            message_content: str,
            msg_url: str
    ) -> None:
        logging.info(f"[Bridge API] Попытка отправки пуша в Макс для пользователя: {maks_user_id}")

        # Макс нативно поддерживает разметку Markdown
        text = (
            f"🔔 **Новое сообщение в Zulip [{stream_name}]**\n"
            f"**Тема:** {topic}\n"
            f"**От:** {sender_name}\n\n"
            f"{message_content}\n\n"
            f"[Открыть в чате Zulip]({msg_url})"
        )

        url = f"{self.maks_api_url}/messages/sendText"
        payload = {
            "token": self.maks_token,
            "chatId": maks_user_id,  # уникальный идентификатор чата/пользователя в Макс
            "text": text
        }

        async with self.semaphore:
            try:
                async with self.session.post(url, data=payload, timeout=3) as response:
                    if response.status == 200:
                        logging.info(f"[Bridge API] Пуш успешно доставлен в Макс пользователю {maks_user_id}")
                    else:
                        res_text = await response.text()
                        logging.error(f"[Bridge API] Ошибка Макс API (Статус {response.status}) для {maks_user_id}: {res_text}")
            except Exception as e:
                logging.error(f"[Bridge API] Исключение сети при отправке в Макс для {maks_user_id}: {e}")

    def get_stream_subscribers(self, stream_name: str, stream_id: int = None) -> list:
        try:
            result = self.zulip_client.get_subscribers(stream=stream_name)
            if result.get('result') != 'success' and stream_id is not None:
                result = self.zulip_client.get_subscribers(stream_id=stream_id)
            if result.get('result') == 'success':
                return result.get('subscribers', [])
            return []
        except Exception as e:
            logging.error(f"[Bridge] Исключение при получении подписчиков Zulip: {e}")
            return []

    def get_user_id_by_email(self, email: str) -> dict:
        try:
            result = self.zulip_client.get_users()
            if result.get('result') == 'success':
                members = result.get('members', [])
                search_email = email.strip().lower()
                for member in members:
                    actual_email = member.get('delivery_email', '').lower()
                    fallback_email = member.get('email', '').lower()
                    if actual_email == search_email or fallback_email == search_email:
                        if member.get('is_active') and not member.get('is_bot'):
                            return {
                                "zulip_id": str(member.get('user_id')),
                                "full_name": member.get('full_name')
                            }
                return {}
            return {}
        except Exception as e:
            logging.error(f"[Bridge API] Исключение при поиске по email: {e}")
            return {}

    def send_zulip_private_message(self, zulip_id: int, text: str) -> bool:
        try:
            request = {"type": "private", "to": [int(zulip_id)], "content": text}
            result = self.zulip_client.send_message(request)
            return result.get('result') == 'success'
        except Exception as e:
            logging.error(f"[Bridge API] Ошибка отправки сообщения в Zulip: {e}")
            return False

    def process_event(self, event: dict) -> None:
        if event.get('type') != 'message':
            return

        msg = event['message']
        if msg['sender_email'] == self.bot_email or msg['type'] == 'private':
            return

        sender_id = msg['sender_id']
        sender_name = msg['sender_full_name']
        topic = msg.get('subject', 'Без темы')
        content = msg.get('content_raw', msg.get('content', ''))
        stream_name = msg.get('display_recipient', 'Неизвестный стрим')
        stream_id = msg.get('stream_id')
        message_id = msg.get('id')

        if not isinstance(stream_name, str):
            return

        logging.info(f"--- [DEBUG START] ---")
        logging.info(f"[Bridge] Перехвачено сообщение из [{stream_name}]")

        encoded_stream = f"{stream_id}-{stream_name.replace(' ', '.')}"
        encoded_topic = urllib.parse.quote(topic.replace(' ', '.'))
        msg_url = f"{self.zulip_site}/#narrow/stream/{encoded_stream}/topic/{encoded_topic}/near/{message_id}"

        subscribers = self.get_stream_subscribers(stream_name, stream_id)
        sent_counter = 0

        for user_id in subscribers:
            if user_id == sender_id:
                continue

            maks_id = database.get_tg_id_by_zulip(str(user_id))
            if maks_id:
                sent_counter += 1
                self.loop.call_soon_threadsafe(
                    lambda m_id=maks_id, sn=stream_name: asyncio.create_task(
                        self.send_maks_push(m_id, sn, topic, sender_name, content, msg_url)
                    )
                )

        logging.info(f"[Bridge] Всего запланировано пушей в Макс: {sent_counter}")
        logging.info(f"--- [DEBUG END] ---")

    def start_zulip_listener(self):
        try:
            self.zulip_client.call_on_each_event(
                callback=self.process_event, event_types=['message'], all_public_streams=True
            )
        except Exception as e:
            logging.critical(f"[Bridge] Ошибка слушателя Zulip: {e}")

    async def start(self, session):
        self.session = session
        while True:
            try:
                self.zulip_client = await asyncio.wait_for(
                    self.loop.run_in_executor(None, lambda: zulip.Client(config_file=str(self.zuliprc_path))),
                    timeout=10.0
                )
                self.bot_email = self.zulip_client.email
                logging.info(f"[Bridge] Успешная авторизация в Zulip: {self.bot_email}")
                break
            except Exception as e:
                logging.error(f"[Bridge] Ошибка авторизации в Zulip: {e}. Повтор через 15 сек...")
                await asyncio.sleep(15)

        async def safe_listener_loop():
            while True:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ZulipListener")
                for thread in executor._threads:
                    thread.daemon = True
                try:
                    await self.loop.run_in_executor(executor, self.start_zulip_listener)
                except Exception as e:
                    logging.error(f"[Bridge] Поток слушателя упал: {e}")
                finally:
                    executor.shutdown(wait=False)
                await asyncio.sleep(15)

        asyncio.create_task(safe_listener_loop())
