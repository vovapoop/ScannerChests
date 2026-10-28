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
