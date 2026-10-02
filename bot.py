import asyncio
import logging
import os
import random
import aiohttp
import database


class MaksBotPoll:
    def __init__(
            self,
            maks_token: str,
            maks_api_url: str,
            zulip_bridge,
            stop_event: asyncio.Event
    ):
        self.token = maks_token
        self.api_url = maks_api_url
        self.bridge = zulip_bridge
        self.stop_event = stop_event
        self.session = None
        self.last_event_id = 0

        # память состояний сессий пользователей
        # { maks_user_id: {"state": "WAIT_EMAIL" или "WAIT_OTP", "data": {...}} }
        self.fsm_storage = {}

    async def send_message(self, chat_id: str, text: str) -> None:
        # REST-эндпоинт согласно спецификации platform-api2.max.ru
        url = f"{self.api_url}/messages"

        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }

        payload = {
            "chatId": chat_id,
            "text": text
        }

        try:
            # Отправляем JSON-запрос с отключенной проверкой SSL корпоративного шлюза
            await self.session.post(url, json=payload, headers=headers, timeout=3, ssl=False)
        except Exception as e:
            logging.error(f"[MAX Bot] Ошибка отправки сообщения для {chat_id}: {e}")


    async def handle_message(self, chat_id: str, text: str, user_name: str) -> None:
        text = text.strip()
        user_fsm = self.fsm_storage.get(chat_id, {"state": "NORMAL", "data": {}})
        current_state = user_fsm["state"]

        if text.lower() in ["/cancel", "отмена", "🚫 отмена"]:
            if current_state != "NORMAL":
                self.fsm_storage[chat_id] = {"state": "NORMAL", "data": {}}
                await self.send_message(chat_id, "🚫 Ввод отменен. Возвращаем главное меню.")
                return

        if current_state == "WAIT_EMAIL":
            email = text.lower()
            if "@" not in email:
                await self.send_message(chat_id,
                                        "❌ Неверный формат почты! Пожалуйста, введите корректный корпоративный email.")
                return

            domain = email.split("@")[-1]
            allowed_domains = [d.strip() for d in os.getenv("ALLOWED_COMPANY_DOMAINS", "").split(",") if d.strip()]

            if allowed_domains and domain not in allowed_domains:
                await self.send_message(chat_id,
                                        "❌ Доступ отклонен! Регистрация доступна только для сотрудников компании.")
                self.fsm_storage[chat_id] = {"state": "NORMAL", "data": {}}
                return

            await self.send_message(chat_id, "🔍 Проверяем вашу учетную запись в Zulip...")
            user_info = self.bridge.get_user_id_by_email(email)
            if not user_info:
                await self.send_message(chat_id,
                                        "❌ Пользователь с таким email не найден на сервере Zulip. Попробуйте еще раз.")
                return

            zulip_id = user_info["zulip_id"]
            full_name = user_info["full_name"]
            otp_code = str(random.randint(1000, 9999))

            zulip_msg = (
                f"🤖 **Запрос на подключение уведомлений в мессенджер Макс**\n\n"
                f"Уважаемый(ая) {full_name}, ваш секретный одноразовый код подтверждения: **{otp_code}**\n"
                f"Введите его в окне Макс-бота для завершения настройки."
            )

            if self.bridge.send_zulip_private_message(zulip_id, zulip_msg):
                self.fsm_storage[chat_id] = {
                    "state": "WAIT_OTP",
                    "data": {"correct_otp": otp_code, "target_zulip_id": zulip_id}
                }
                await self.send_message(
                    chat_id,
                    f"📧 Код подтверждения отправлен в ваши личные сообщения в Zulip.\n\n"
                    f"Пожалуйста, скопируйте 4-значный код и отправьте его сюда:"
                )
            else:
                await self.send_message(chat_id,
                                        "❌ Ошибка при отправке кода через сервер Zulip. Обратитесь к администратору.")
            return

        elif current_state == "WAIT_OTP":
            correct_otp = user_fsm["data"].get("correct_otp")
            zulip_id = user_fsm["data"].get("target_zulip_id")

            if text != correct_otp:
                await self.send_message(chat_id,
                                        "❌ Неверный код верификации! Попробуйте ввести еще раз или напишите 'Отмена'.")
                return

            try:
                database.add_user(zulip_id, chat_id)
                self.fsm_storage[chat_id] = {"state": "NORMAL", "data": {}}
                await self.send_message(
                    chat_id,
                    f"🎉 **Успешно привязано!**\n\n"
                    f"Ваш аккаунт мессенджера Макс успешно связан с Zulip ID {zulip_id}.\n"
                    f"Теперь пуш-уведомления будут дублироваться в этот чат."
                )
            except Exception as e:
                logging.error(f"[Maks Bot] Ошибка SQL: {e}")
                await self.send_message(chat_id, "❌ Системная ошибка записи в базу данных.")
                self.fsm_storage[chat_id] = {"state": "NORMAL", "data": {}}
            return

        if text in ["/start", "старт"]:
            welcome = (
                f"Привет, {user_name}! 👋\n"
                f"Я шлюз-бот для безопасной отправки push-уведомлений из Zulip.\n\n"
                f"Доступные команды:\n"
                f"• /bind — Привязать аккаунт Zulip (по email)\n"
                f"• /status — Проверить статус привязки\n"
                f"• /unbind — Отключить уведомления"
            )
            await self.send_message(chat_id, welcome)


        elif text in ["/bind", "привязать"]:
            self.fsm_storage[chat_id] = {"state": "WAIT_EMAIL", "data": {}}
            await self.send_message(chat_id, "📝 Введите ваш корпоративный email для прохождения аутентификации:")

        elif text in ["/status", "статус"]:
            assoc_id = database.get_zulip_id_by_tg(chat_id)
            if assoc_id:
                await self.send_message(chat_id,
                                        f"✅ Интеграция активна!\n• Ваш ID в Макс: {chat_id}\n• Ваш Zulip ID: {assoc_id}")
            else:
                await self.send_message(chat_id, "⚠️ Интеграция не настроена. Наберите /bind для активации.")

        elif text in ["/unbind", "отвязать"]:
            if database.remove_user_by_tg(chat_id):
                await self.send_message(chat_id, "📴 Уведомления отключены, связь с Zulip разорвана.")
            else:
                await self.send_message(chat_id, "Ваш аккаунт не был привязан.")

    async def start(self, session):
        self.session = session

        logging.info("[Maks Bot] Проверка валидности токена и инициализация через GET /me...")
        try:
            headers = {"Authorization": self.token}

            # флаг ssl=False для обхода корпоративной подмены сертификатов
            async with self.session.get(f"{self.api_url}/me", headers=headers, timeout=5, ssl=False) as resp:
                if resp.status == 200:
                    bot_info = await resp.json()
                    logging.info(
                        f"🎉 [Maks Bot] Успешное подключение! Имя бота в сети MAX: {bot_info.get('name')} (@{bot_info.get('username')})")
                else:
                    res_text = await resp.text()
                    logging.error(f"❌ [Maks Bot] Сервер отклонил токен (Статус {resp.status}): {res_text}")
                    return
        except Exception as e:
            logging.error(f"❌ [Maks Bot] Не удалось связаться с сервером MAX при запросе /me: {e}")
            return

        async def poll_loop():
            logging.info("[Maks Bot] Фоновый цикл Long Polling для мессенджера MAX успешно запущен.")
            while not self.stop_event.is_set():
                url = f"{self.api_url}/events/get"
                params = {"pollTime": 20}
                if self.last_event_id > 0:
                    params["lastEventId"] = self.last_event_id

                headers = {"Authorization": self.token}

                try:
                    # флаг ssl=False
                    async with self.session.get(url, params=params, headers=headers, timeout=25, ssl=False) as response:
                        if response.status == 200:
                            data = await response.json()
                            events = data.get("events", [])

                            for event in events:
                                self.last_event_id = event.get("eventId", self.last_event_id)
                                if event.get("type") == "newMessage":
                                    msg_payload = event.get("payload", {})
                                    chat_id = msg_payload.get("chat", {}).get("chatId")
                                    text = msg_payload.get("text", "")
                                    from_user = msg_payload.get("from", {})
                                    user_name = from_user.get("firstName", "Коллега")

                                    if chat_id and text:
                                        await self.handle_message(chat_id, text, user_name)
                        elif response.status == 401:
                            logging.error("[Maks Bot] Ошибка авторизации токена MAKS_BOT_TOKEN!")
                            await asyncio.sleep(15)
                except Exception as e:
                    await asyncio.sleep(2)

        asyncio.create_task(poll_loop())


