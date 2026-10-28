# План: Быстрые победы — инфраструктура и удобство

Цель: пять независимых улучшений проекта Chest Scanner без изменения логики сканирования.
Файлы: 2 новых (README.md, .gitignore) и 7 правок (requirements.txt, start.bat, app_bootstrap.py, paths.py, config.py, gui/core.py, gui/settings.py).

---

## 1. .gitignore — новый файл в корне

Игнорировать пользовательские данные и окружение, сохранив в репозитории только код и ассеты:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/

# Пользовательские данные (анкоры к корню, чтобы не задеть assets/)
/config.json
/profiles.json
/discord_webhook.json
/items/
/unknown_items/
/results/
/logs/
/profiles/
/assets/custom/

# Обслуживание
*.log
venv_setup.log
startup_error.log

# ОС и редакторы
Thumbs.db
Desktop.ini
.vscode/
.idea/
```

Примечание: служебные директории создаются автоматически через ensure_directories(), поэтому .gitkeep не нужны.

---

## 2. requirements.txt — закрепление версий

Итоговое содержимое (под Python 3.14; верхние границы сняты там, где нужны cp314-колёса):

```text
opencv-python>=4.12
numpy>=2.3
mss>=9,<10
keyboard>=0.13.5,<0.15
requests>=2.31,<3
Pillow>=12
tzdata>=2024.1
```

Решения:
- На Python 3.14.7 numpy ≤ 2.2.6 не имеет wheel (cp314) и собирается из исходников (нужен MSVC) — поэтому `numpy>=2.3` без верхней границы.
- opencv-python 4.12+/4.13+ и Pillow 12+ поставляют cp314-колёса; для Python 3.10–3.13 эти версии тоже подходят.
- pyautogui НЕ добавлять: по результатам поиска по коду он нигде не используется.
- Если какой-то пакет всё ещё собирается из исходников — нет wheel под его Python; запасной путь: 64-битный Python 3.13.
- Опционально (максимальная воспроизводимость): зафиксировать точные версии через `pip freeze` при наличии рабочего окружения.

---

## 3. start.bat — единый источник зависимостей

Убрать ручной список базовых пакетов (сейчас это opencv-python numpy pyautogui keyboard requests Pillow tzdata) и установку свести к одному источнику:

```bat
REM Install dependencies once
if not exist "%DEPS_MARKER%" (
    echo [INFO] Installing dependencies...
    "%PYTHON_EXE%" -m pip install --upgrade pip --disable-pip-version-check
    if exist "%REQUIREMENTS%" (
        "%PYTHON_EXE%" -m pip install --no-cache-dir -r "%REQUIREMENTS%" --disable-pip-version-check
        if errorlevel 1 (
            echo [ERROR] Failed to install requirements.txt.
            pause
            exit /b 1
        )
    )
    echo. > "%DEPS_MARKER%"
    echo [OK] Dependencies installed.
)
```

Обязательно сменить имя маркера с `.deps_installed_v2` на `.deps_installed_v3`, иначе у пользователей с уже созданным .venv зависимости не переустановятся.

---

## 4. DPI-awareness — app_bootstrap.py

Windows-проблема: при масштабировании 125–150% Tkinter работает в логических координатах, а mss снимает в физических — сетка и скриншоты «разъезжаются». Решение — включить DPI-осведомлённость процесса до создания окна.

Добавить в начало модуля (до импорта tkinter) функцию и её вызов:

```python
def _enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_enable_dpi_awareness()
```

Примечания:
- Вызов на уровне модуля гарантирует применение раньше `tk.Tk()` в gui/core.py и раньше первого mss-граба.
- reference_screen_size остаётся опорным разрешением; пересчёт сетки не меняется.

---

## 5. Настраиваемые горячие клавиши

### 5.1 paths.py — DEFAULT_CONFIG

Добавить блок (по умолчанию — текущие значения):

```python
"hotkeys": {"digits": "f6", "grid": "f7", "scan": "f8", "discord": "f9", "exit": "f10"},
```

### 5.2 config.py — validate_config

После валидации discord_message добавить:

```python
HK_DEFAULTS = paths.DEFAULT_CONFIG["hotkeys"]
hk = cfg.get("hotkeys")
if not isinstance(hk, dict):
    cfg["hotkeys"] = dict(HK_DEFAULTS)
    warnings.append("hotkeys повреждён")
else:
    fixed = {}
    for action, default in HK_DEFAULTS.items():
        val = hk.get(action, default)
        if not isinstance(val, str) or not val.strip():
            fixed[action] = ""
        else:
            fixed[action] = val.strip().lower()
    cfg["hotkeys"] = fixed
```

Пустое значение = действие отключено.

### 5.3 gui/core.py

Три места:
- Футер (строка 196): строить из конфига:
  ```python
  hk = self.reader.config.get("hotkeys", {})
  self.hotkeys_label = tk.Label(footer, text=self._hotkeys_hint(), ...)
  ```
  + хелпер `_hotkeys_hint()` собирает строку вида «F6 цифры F7 сетка ...», пропуская пустые значения.
- `_keyboard_loop()` (строка 371): заменить хардкод на чтение конфига при каждом опросе:
  ```python
  hk = self.reader.config.get("hotkeys", {})
  if hk.get("digits") and keyboard.is_pressed(hk["digits"]): ...
  ```
  (аналогично для grid/scan/discord/exit). Пустая клавиша — пропуск.
- Лог при старте (строки 203–206): выводить `_hotkeys_hint()` вместо «F6–F10».

Применение без рестарта: цикл опрашивает конфиг каждые 80 мс — после «Сохранить» новые клавиши работают сразу; футер обновляется в `_save_settings_from_gui`.

### 5.4 gui/settings.py

- `_init_settings_vars()`: добавить `v["hk_digits"]`, `v["hk_grid"]`, `v["hk_scan"]`, `v["hk_discord"]`, `v["hk_exit"]` — `tk.StringVar` из `cfg.get("hotkeys", {})`.
- `_settings_page_general()`: карточка «Горячие клавиши» с 5 полями `_settings_entry` и подсказкой: «Имена клавиш как в библиотеке keyboard: f1–f12, цифры, буквы. Пусто = отключено.»
- `_save_settings_from_gui()`: нормализация (strip, lower, пусто = отключено) и запись в `cfg["hotkeys"]`; после сохранения — обновить футер `_hotkeys_hint()`.

---

## 6. README.md — новый файл в корне

Разделы:
1. Описание: скриншот-сканер сундуков Minecraft: распознавание предметов и количества, база предметов, история и сравнение, экспорт CSV/JSON, отправка в Discord, профили, автоскан.
2. Требования: Windows 10/11, Python 3.10+ (в PATH).
3. Установка и запуск:
   - Быстрый старт: двойной клик по start.bat (сам создаст .venv и установит зависимости).
   - Вручную: команды `py -3 -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt && .venv\Scripts\python.exe main.py`.
   - VS Code: открыть main.py → Run.
4. Первый запуск: мастер профиля и webhook; F8 при открытом сундуке.
5. Горячие клавиши (по умолчанию F6–F10): цифры, сетка, скан, Discord, выход; настройка в «Настройки → Основные».
6. Инструменты панели: калибровка, автошаблоны, история, поиск, экспорт, профили, сброс настроек, лог.
7. Данные и папки: config.json, items.json, items/, results/, unknown_items/, logs/, profiles/.
8. Discord: webhook через файл discord_webhook.json или переменную окружения DISCORD_WEBHOOK_URL.
9. Устранение неполадок: venv_setup.log / startup_error.log, права администратора для клавиш, перекалибровка при смене масштаба экрана.
10. Структура проекта: краткое описание модулей chest_scanner/ и scanner/.

---

## Порядок реализации

1. `.gitignore` (изолировано).
2. `requirements.txt` + `start.bat` (изолировано).
3. DPI-awareness в `app_bootstrap.py` (изолировано).
4. Горячие клавиши: paths.py → config.py → gui/core.py → gui/settings.py.
5. README.md — последним, чтобы отразить итоговые клавиши и инструкцию.
6. Проверка: запуск через start.bat с чистого состояния; смена клавиш и проверка без рестарта; визуальная проверка при масштабировании 150%.

## Критерии готовности

- start.bat создаёт .venv и ставит зависимости только из requirements.txt.
- Настройка клавиш видна в GUI, сохраняется в config.json, применяется без перезапуска; пустое поле отключает действие.
- При масштабировании Windows 125/150% сетка (F7) совпадает с сундуком на экране.
- README.md покрывает установку, запуск, клавиши, Discord и типовые проблемы.
