import os
import io

import cv2
import numpy as np
import imutils
import fitz  # PyMuPDF
from PIL import Image
from skimage.filters import threshold_local

from pyimagesearch.transform import four_point_transform

# Поддержка HEIC (с телефона). Если библиотека не установлена - HEIC-файлы
# можно не принимать, остальное будет работать.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False


def load_image_bgr(path):
    """Загружает JPG/PNG/HEIC/PDF (первая страница) как BGR-изображение."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif"):
        pil_img = Image.open(path).convert("RGB")
        arr = np.array(pil_img)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if ext == ".pdf":
        doc = fitz.open(path)
        pix = doc[0].get_pixmap(dpi=200)
        doc.close()
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    img = cv2.imread(path)
    if img is None:
        raise ValueError("Не удалось прочитать файл: " + path)
    return img


def find_screen_contour(image):
    """Ищет 4-угольный контур листа. Возвращает 4 точки или None."""
    ratio = image.shape[0] / 500.0
    small = imutils.resize(image, height=500)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 75, 200)
    cnts = cv2.findContours(edged.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]
    for c in cnts:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2) * ratio
    return None


def binarize(warped_bgr):
    gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY)
    T = threshold_local(gray, 11, offset=10, method="gaussian")
    return (gray > T).astype("uint8") * 255


def save_pdf(gray_img, out_path):
    Image.fromarray(gray_img).save(out_path, "PDF", resolution=150.0)


def process_document(src_path, out_path):
    """Автоматическое сканирование. True - успех, False - контур не найден."""
    image = load_image_bgr(src_path)
    pts = find_screen_contour(image)
    if pts is None:
        return False
    warped = four_point_transform(image, pts)
    save_pdf(binarize(warped), out_path)
    return True


def transform_with_points(src_path, points, out_path):
    """Сканирование с ручными 4 точками (из браузера)."""
    image = load_image_bgr(src_path)
    pts = np.array(points, dtype="float32")
    warped = four_point_transform(image, pts)
    save_pdf(binarize(warped), out_path)


def ensure_thumbnail(pdf_path, thumb_path):
    """Создает PNG-миниатюру первой страницы PDF, если её нет или она устарела."""
    if os.path.exists(thumb_path) and \
       os.path.getmtime(thumb_path) > os.path.getmtime(pdf_path):
        return
    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.4, 0.4))
    pix.save(thumb_path)
    doc.close()


def to_png_bytes(path):
    """PNG-представление файла для ручной разметки углов в браузере."""
    img = load_image_bgr(path)
    ok, buf = cv2.imencode(".png", img)
    return io.BytesIO(buf.tobytes())
