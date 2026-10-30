"""Утилиты обработки изображений: ресайз, кроп, сравнение по SSIM и расстоянию."""
import cv2
import numpy as np


def resize_image(img, w, h):
    if img is None or img.size == 0:
        return None
    return cv2.resize(img, (max(1, int(w)), max(1, int(h))), interpolation=cv2.INTER_AREA)


def crop_relative(img, rect):
    if img is None or img.size == 0:
        return None
    ih, iw = img.shape[:2]
    x1, y1 = max(0, int(rect[0] * iw)), max(0, int(rect[1] * ih))
    w, h = max(1, int(rect[2] * iw)), max(1, int(rect[3] * ih))
    return img[y1:min(ih, y1 + h), x1:min(iw, x1 + w)]


def compute_ssim(img1, img2):
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    h, w = img1.shape[:2]
    win = min(7, min(h, w))
    if win % 2 == 0:
        win -= 1
    if win < 3:
        win = 3
    sigma = win / 6.0
    mu1 = cv2.GaussianBlur(img1, (win, win), sigma)
    mu2 = cv2.GaussianBlur(img2, (win, win), sigma)
    mu1_sq, mu2_sq, mu1_mu2 = mu1 * mu1, mu2 * mu2, mu1 * mu2
    s1 = cv2.GaussianBlur(img1 * img1, (win, win), sigma) - mu1_sq
    s2 = cv2.GaussianBlur(img2 * img2, (win, win), sigma) - mu2_sq
    s12 = cv2.GaussianBlur(img1 * img2, (win, win), sigma) - mu1_mu2
    num = (2 * mu1_mu2 + C1) * (2 * s12 + C2)
    den = (mu1_sq + mu2_sq + C1) * (s1 + s2 + C2)
    return float((num / den).mean())


def image_distance(a, b):
    if a is None or b is None or a.size == 0 or b.size == 0:
        return float("inf")
    a48, b48 = resize_image(a, 48, 48), resize_image(b, 48, 48)
    if a48 is None or b48 is None:
        return float("inf")
    ga = cv2.cvtColor(a48, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gb = cv2.cvtColor(b48, cv2.COLOR_BGR2GRAY).astype(np.float64)
    ssim_val = compute_ssim(ga, gb)
    structural = (1.0 - ssim_val) * 5000.0
    ha = cv2.cvtColor(a48, cv2.COLOR_BGR2HSV)
    hb = cv2.cvtColor(b48, cv2.COLOR_BGR2HSV)
    h1 = cv2.calcHist([ha], [0, 1], None, [16, 16], [0, 180, 0, 256])
    h2 = cv2.calcHist([hb], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    corr = cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)
    color = (1.0 - max(-1.0, min(1.0, corr))) * 500.0
    return float(structural + color)


def image_distance_coarse(a, b):
    if a is None or b is None or a.size == 0 or b.size == 0:
        return float("inf")
    a16, b16 = resize_image(a, 16, 16), resize_image(b, 16, 16)
    if a16 is None or b16 is None:
        return float("inf")
    ga = cv2.cvtColor(a16, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gb = cv2.cvtColor(b16, cv2.COLOR_BGR2GRAY).astype(np.float64)
    return float((1.0 - compute_ssim(ga, gb)) * 5000.0)


def phash(img, size=32):
    """Перцептивный хэш изображения (64 бита) на основе DCT.

    Устойчив к небольшим изменениям яркости, шуму и масштабу.
    Возвращает int; близость двух изображений оценивается
    расстоянием Хэмминга (см. hamming).
    """
    if img is None or img.size == 0:
        return 0
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    g = cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    dct = cv2.dct(g)
    block = dct[:8, :8].copy()
    block[0, 0] = 0.0  # DC-компонента зависит от общей яркости — неинформативна
    avg = float(block.mean())
    h = 0
    for i in range(block.size):
        if block.flat[i] > avg:
            h |= 1 << i
    return int(h)


def hamming(a, b):
    """Расстояние Хэмминга между двумя целочисленными хэшами."""
    return int(a ^ b).bit_count()


def preprocess_icon(img, clip_limit=2.0, tile_grid=8):
    """CLAHE-нормализация яркости иконки (канал L в LAB).

    Повышает устойчивость распознавания к освещению и гамме:
    контраст выравнивается без изменения цветового тона.
    При некорректном входе возвращается исходное изображение.
    """
    if img is None or img.size == 0:
        return img
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(int(tile_grid), int(tile_grid)))
    l_ch = clahe.apply(l_ch)
    return cv2.cvtColor(cv2.merge((l_ch, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
