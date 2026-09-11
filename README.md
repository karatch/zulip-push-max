# zulip-push-max

## Архитектура приложения (Макс)
```text
       +--------------------------------------------+

       |             База данных (SQLite)           |
       |     Таблица: users (zulip_id <-> max_id)   |
       +---------------------+----------------------+
                             |
         +-------------------+-------------------+

         |                                       |
         v                                       v
+-----------------+                     +-----------------+

|   Компонент 1   |                     |   Компонент 2   |
|   Скрипт МОСТА  |                     |  Бот интеграции |
| (Bridge Service)|                     |    (Max Bot)    |
+--------+--------+                     +--------+--------+

         |                                       |
   (Long Polling)                         (Matrix Sync)

         |                                       |
         v                                       v
+-----------------+                     +-----------------+

|   API Zulip     |                     |    API МАКС     |
|  (Сервер Zulip) |                     |  (dev.max.ru)   |
+-----------------+                     +-----------------+
```

## Краткое описание схемы:

* База данных SQLite (bridge.db): Общая точка синхронизации. Хранит связки между 
числовыми zulip_id и строковыми Matrix ID пользователей (например, @user:max.ru).
* Скрипт МОСТА (bridge.py): Фоновый процесс. Слушает выбранный стрим в Zulip. 
При появлении сообщения сопоставляет подписчиков с базой данных и отправляет пуши в 
Макс через REST API (POST /api/v1/messages) с Markdown-форматированием.
* Бот интеграции (bot.py): Интерактивный процесс на протоколе Matrix. Принимает от 
пользователей текстовые команды (привязать, статус, отвязать) и 
записывает/удаляет данные в SQLite.


## Руководство для системных администраторов

### 1. Сборка бинарного файла через PyInstaller

Приложение компилируется в один исполняемый файл, который содержит интерпретатор Python и все необходимые зависимости. Сборку необходимо проводить **строго на той же операционной системе** (и архитектуре), где планируется запуск приложения (например, на целевом Linux-сервере).

## Руководство для системных администраторов

### 1. Сборка бинарного файла через PyInstaller

Приложение компилируется в один исполняемый файл, который содержит интерпретатор Python и все необходимые зависимости. Сборку необходимо проводить **строго на той же операционной системе** (и архитектуре), где планируется запуск приложения (например, на целевом Linux-сервере).

1. Перейдите в каталог проекта, активируйте ваше виртуальное окружение и установите необходимые зависимости:
   ```bash
   # ВАЖНО: Устанавливается именно matrix-nio, а не базовый пакет nio
   pip install matrix-nio
   ```
   Установите PyInstaller в ваше виртуальное окружение:
   ```bash
   pip install pyinstaller
   ```
   
2. Выполните команду для сборки проекта в один файл:
   ```bash
   pyinstaller --clean --onefile \
     --name=max-integration \
     --hidden-import=nio \
     --hidden-import=aiohttp \
     run.py
   ```
   *После успешного завершения сборки готовый бинарник появится в директории `dist/max-integration`.*

2. Выполните команду для сборки проекта в один файл:
   ```bash
   pyinstaller --clean --onefile \
     --name=max-integration \
     --hidden-import=nio \
     --hidden-import=aiohttp \
     run.py
   ```
   *После успешного завершения сборки готовый бинарник появится в директории `dist/max-integration`.*

---

### 2. Структура конфигурационного файла `zuliprc`

Файл конфигурации должен находиться в той же директории, что и исполняемый файл. Он содержит доступы к API серверов Zulip и Макс.

Создайте файл `zuliprc` со следующим содержимым:

```ini
[api]
# Параметры подключения к вашему серверу Zulip
email = bot-name@your-domain.ru
key = abc123xyz456secretkeyzulip
site = https://your-domain.ru

[ntfy]
# Имя канала (Stream) в Zulip, сообщения из которого нужно дублировать
stream = Разработка

[telegram]
# Системный токен Bot API мессенджера Макс (для метода /messages)
bot_token = 1234567890:ABCdefGhIJKlmNoPQRsTUVwXyZ

[max]
# Учетные данные интерактивного бота Макс (Matrix) для обработки команд пользователей
bot_username = @bridge_bot:max.ru
bot_password = YourSuperSecretPassword123
```

> **Важно:** Ограничьте права доступа к файлу конфигурации в Linux, чтобы сторонние пользователи не могли прочитать пароли: `chmod 600 zuliprc`

---

### 3. Развертывание службы через systemd

1. Создайте рабочую директорию проекта и перенесите туда файлы:
   ```bash
   mkdir -p /home/user/zulip_bridge
   cp dist/max-integration /home/user/zulip_bridge/
   # Не забудьте положить файл zuliprc в эту же папку
   ```
2. Дайте бинарному файлу права на исполнение:
   ```bash
   chmod +x /home/user/zulip_bridge/max-integration
   ```
3. Создайте конфигурационный файл службы:
   ```bash
   sudo nano /etc/systemd/system/zulip-integration.service
   ```
4. Вставьте в него следующую конфигурацию (замените `user` и пути на ваши реальные):
   ```ini
   [Unit]
   Description=Zulip to Max Messenger Integration Service
   After=network.target

   [Service]
   Type=simple
   User=user
   WorkingDirectory=/home/user/zulip_bridge
   ExecStart=/home/user/zulip_bridge/max-integration
   Restart=always
   RestartSec=5
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```
5. Зарегистрируйте и запустите службу:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable zulip-integration.service
   sudo systemctl start zulip-integration.service
   ```

---

### 4. Настройка ротации и очистки логов journald

По умолчанию системный журнал `journald` может занимать значительный объем диска. Чтобы логи приложения не переполнили сервер, необходимо настроить их ротацию по размеру.

#### Автоматическая ротация (Рекомендуется)
1. Откройте файл конфигурации системного журнала:
   ```bash
   sudo nano /etc/systemd/journald.conf
   ```
2. Раскомментируйте или добавьте в секцию `[Journal]` следующие параметры для жесткого ограничения логов (например, до 500 МБ):
   ```ini
   [Journal]
   SystemMaxUse=500M
   SystemMaxFileSize=50M
   SystemKeepFree=1G
   ```
3. Перезапустите службу логирования для применения настроек:
   ```bash
   sudo systemctl restart systemd-journald
   ```

#### Ручная очистка логов (Прямо сейчас)
Если на сервере скопилось много старых логов и вам нужно принудительно освободить место, оставив только последние 500 МБ данных, выполните команду:
```bash
sudo journalctl --vacuum-size=500M
```

---

### 5. Полезные команды для администрирования

* **Просмотр логов в реальном времени ("живой поток"):**
  ```bash
  sudo journalctl -u zulip-integration.service -f
  ```
* **Проверить текущий статус работы процесса (Active/Running):**
  ```bash
  sudo systemctl status zulip-integration.service
  ```
* **Перезапустить сервис (например, после изменения `zuliprc`):**
  ```bash
  sudo systemctl restart zulip-integration.service
  ```
* **Остановить службу (выключить процесс прямо сейчас):**
  ```bash
  sudo systemctl stop zulip-integration.service
  ```
* **Отключить автозапуск (дезактивировать запуск вместе с ОС):**
  ```bash
  sudo systemctl disable zulip-integration.service
  ```
  *Примечание: Команда `disable` не останавливает уже запущенный процесс, она лишь отменяет его автоматический старт при следующей перезагрузке сервера.*
* **Полное отключение (и остановить, и убрать из автозапуска):**
  ```bash
  sudo systemctl stop zulip-integration.service && sudo systemctl disable zulip-integration.service
  ```



