"""사진 속 글자(OCR) 추출 — 스크린샷·문서·간판 등의 텍스트를 검색 대상에 포함.

tesseract(brew install tesseract tesseract-lang)가 있어야 동작한다(선택 기능).
썸네일(640px)은 한글 인식률이 크게 떨어져 원본을 1200px로 축소해 사용한다
(실측: 640px는 인식 실패가 잦고, 원본 그대로는 4000px+ 사진 기준 장당 1~2.5초로
느림 — 1200px가 속도(장당 ~0.3~0.4초)와 인식률의 합리적 절충점).
"""
import re

MAX_SIDE = 1200
LANG = "kor+eng"
MIN_LEN = 4  # 이보다 짧은 결과는 노이즈로 간주(잡음 문자 몇 개 오인식 등)

_available = None


def available():
    global _available
    if _available is None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _available = True
        except Exception:
            _available = False
    return _available


def _clean(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    # 한글/영문/숫자가 거의 없는 결과(기호 잡음)는 버린다
    meaningful = re.sub(r"[^가-힣A-Za-z0-9]", "", text)
    return text if len(meaningful) >= MIN_LEN else None


def extract(image: "PIL.Image.Image"):
    """PIL 이미지에서 텍스트 추출. 실패·무의미 시 None."""
    if not available():
        return None
    try:
        import pytesseract
        img = image.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        text = pytesseract.image_to_string(img, lang=LANG)
        return _clean(text)
    except Exception:
        return None
