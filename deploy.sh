#!/usr/bin/env bash

# остановить выполнение скрипта, если любая команда завершилась ошибкой
set -e


PROJECT_DIR="/home/user/zulip_bridge"               # Папка проекта с исходным кодом
VENV_PATH="/home/user/zulip_bridge/venv"            # Путь к виртуальному окружению Python
SERVICE_NAME="zulip-integration.service"            # Имя вашей системной службы
BINARY_NAME="max-integration"                       # Имя итогового бинарного файла
USER_NAME="user"                                    # Имя пользователя Linux владельца процесса

echo "=== Запуск процесса автоматического деплоя (МАКС) ==="

cd "$PROJECT_DIR"

if [ -f "$VENV_PATH/bin/activate" ]; then
    echo "[1/4] Активация виртуального окружения Python..."
    source "$VENV_PATH/bin/activate"
else
    echo "❌ Ошибка: Не найдено venv по пути $VENV_PATH"
    exit 1
fi

echo "[2/4] Компиляция приложения в единый бинарный файл..."
pyinstaller --clean --onefile \
  --name="$BINARY_NAME" \
  --hidden-import=nio \
  --hidden-import=aiohttp \
  run.py

echo "[3/4] Обновление исполняемого файла в рабочей директории..."
if [ -f "dist/$BINARY_NAME" ]; then
    cp "dist/$BINARY_NAME" "$PROJECT_DIR/$BINARY_NAME"
    chmod +x "$PROJECT_DIR/$BINARY_NAME"
    chown "$USER_NAME:$USER_NAME" "$PROJECT_DIR/$BINARY_NAME"
else
    echo "❌ Ошибка: Файл сборки не обнаружен в директории dist/"
    exit 1
fi

echo "[4/4] Перезапуск фоновой службы Linux..."
sudo systemctl restart "$SERVICE_NAME"

echo "Чистка временных файлов компиляции..."
rm -rf build/
rm -f "$BINARY_NAME.spec"

echo "=== ✅ Деплой успешно завершен! ==="
echo "Служба $SERVICE_NAME успешно обновлена и запущена в фоне."
echo "Посмотреть логи: sudo journalctl -u $SERVICE_NAME -f"
