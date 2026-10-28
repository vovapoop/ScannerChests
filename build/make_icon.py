"""Генерация build/icon.ico из assets/chest_open.png (запасной вариант).

Основной источник иконки — готовый файл 'Запуск.ico' в корне проекта:
build.bat использует его при наличии, а make_icon.py — только как fallback.

Требуется Pillow (входит в requirements.txt).
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "chest_open.png")
OUT = os.path.join(ROOT, "build", "icon.ico")


def main():
    if not os.path.isfile(SRC):
        print(f"[make_icon] Не найден источник: {SRC}")
        return 1

    img = Image.open(SRC).convert("RGBA")

    # Центральный квадратный кадр
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))

    # Совместимость с Pillow 9 (Image.LANCZOS) и 10+ (Image.Resampling.LANCZOS)
    resampling = getattr(Image, "Resampling", Image)
    img = img.resize((256, 256), resampling.LANCZOS)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    print(f"[make_icon] OK: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())