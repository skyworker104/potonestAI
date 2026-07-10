"""GPS EXIF가 포함된 샘플 사진 생성기.

PIL로 장면별 합성 이미지를 그리고 piexif로 촬영시각·GPS를 심는다.
CLIP이 색/구도를 대략 인식할 수 있도록 장면마다 시각적 특징을 다르게 구성.
"""
import random
from pathlib import Path

import piexif
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "photos"
W, H = 1280, 960

random.seed(42)


def deg_to_dms(deg):
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = round(((deg - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def save_with_exif(img, name, dt, lat, lon):
    exif = {
        "0th": {piexif.ImageIFD.Make: b"PhotoNest", piexif.ImageIFD.Model: b"SamplePhone"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: dt.encode()},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: deg_to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: deg_to_dms(lon),
        },
    }
    img.save(OUT / name, "JPEG", quality=90, exif=piexif.dump(exif))
    print("created", name)


def vgrad(top, bottom, height=H):
    img = Image.new("RGB", (W, height))
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / height
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    return img


def beach():
    img = vgrad((110, 180, 240), (170, 220, 250), H // 2)
    full = Image.new("RGB", (W, H))
    full.paste(img, (0, 0))
    d = ImageDraw.Draw(full)
    # 바다
    for y in range(H // 2, int(H * 0.75)):
        t = (y - H // 2) / (H * 0.25)
        d.line([(0, y), (W, y)], fill=(int(20 + 40 * t), int(110 + 60 * t), int(190 + 30 * t)))
    # 모래사장
    for y in range(int(H * 0.75), H):
        d.line([(0, y), (W, y)], fill=(235, 215, 170))
    # 파도 거품
    for i in range(14):
        x = random.randint(0, W)
        d.ellipse([x, H * 0.73, x + random.randint(40, 120), H * 0.76], fill=(250, 250, 250))
    # 태양
    d.ellipse([W - 280, 60, W - 160, 180], fill=(255, 240, 180))
    # 파라솔
    d.polygon([(300, 700), (380, 580), (460, 700)], fill=(230, 70, 70))
    d.line([(380, 580), (380, 860)], fill=(120, 80, 50), width=8)
    return full.filter(ImageFilter.GaussianBlur(1))


def mountain():
    full = vgrad((150, 190, 235), (220, 235, 245), H)
    d = ImageDraw.Draw(full)
    d.polygon([(0, H), (250, 320), (560, H)], fill=(90, 110, 90))
    d.polygon([(350, H), (720, 220), (1080, H)], fill=(70, 95, 75))
    d.polygon([(640, 340), (720, 220), (800, 340)], fill=(245, 248, 250))  # 설산 정상
    d.polygon([(850, H), (1150, 420), (1280, H)], fill=(105, 125, 95))
    for x in range(40, W, 90):  # 침엽수
        h0 = H - random.randint(40, 130)
        d.polygon([(x, h0), (x + 25, h0 - 90), (x + 50, h0)], fill=(35, 75, 45))
    return full.filter(ImageFilter.GaussianBlur(1))


def city_night():
    full = vgrad((10, 10, 35), (30, 25, 60), H)
    d = ImageDraw.Draw(full)
    for i in range(16):
        bw = random.randint(70, 150)
        bh = random.randint(250, 650)
        x = random.randint(0, W - bw)
        d.rectangle([x, H - bh, x + bw, H], fill=(20, 22, 40))
        for wy in range(H - bh + 20, H - 20, 44):
            for wx in range(x + 12, x + bw - 16, 34):
                if random.random() < 0.6:
                    d.rectangle([wx, wy, wx + 18, wy + 26], fill=(255, 210, 110))
    d.ellipse([W - 220, 70, W - 140, 150], fill=(240, 240, 220))  # 달
    return full


def food():
    full = Image.new("RGB", (W, H), (180, 140, 110))  # 나무 테이블
    d = ImageDraw.Draw(full)
    d.ellipse([240, 160, 1040, 820], fill=(245, 245, 245))  # 접시
    d.ellipse([320, 230, 960, 750], fill=(225, 120, 60))    # 음식
    for i in range(20):
        x = random.randint(380, 880)
        y = random.randint(300, 660)
        d.ellipse([x, y, x + 36, y + 36], fill=(170, 60, 30))
    d.ellipse([880, 600, 1000, 720], fill=(90, 160, 70))    # 채소 고명
    return full.filter(ImageFilter.GaussianBlur(1))


def flowers():
    full = vgrad((200, 230, 255), (120, 180, 110), H)
    d = ImageDraw.Draw(full)
    for i in range(34):
        x = random.randint(30, W - 60)
        y = random.randint(H // 3, H - 60)
        r = random.randint(18, 44)
        col = random.choice([(245, 130, 180), (255, 90, 120), (250, 200, 90), (230, 100, 200)])
        for ang in range(0, 360, 60):
            import math
            px = x + int(r * math.cos(math.radians(ang)))
            py = y + int(r * math.sin(math.radians(ang)))
            d.ellipse([px - r // 2, py - r // 2, px + r // 2, py + r // 2], fill=col)
        d.ellipse([x - r // 3, y - r // 3, x + r // 3, y + r // 3], fill=(255, 220, 80))
    return full.filter(ImageFilter.GaussianBlur(1))


def snow():
    full = vgrad((190, 200, 220), (245, 248, 252), H)
    d = ImageDraw.Draw(full)
    d.polygon([(0, H), (380, 380), (760, H)], fill=(235, 240, 248))
    d.polygon([(560, H), (980, 300), (1280, H)], fill=(225, 232, 244))
    for i in range(120):  # 눈송이
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(2, 6)
        d.ellipse([x, y, x + r, y + r], fill=(255, 255, 255))
    d.ellipse([500, 600, 640, 740], fill=(255, 255, 255))  # 눈사람
    d.ellipse([520, 500, 620, 600], fill=(255, 255, 255))
    return full


def sunset():
    full = vgrad((250, 150, 60), (120, 40, 80), H)
    d = ImageDraw.Draw(full)
    d.ellipse([W // 2 - 90, 320, W // 2 + 90, 500], fill=(255, 110, 50))
    for y in range(int(H * 0.62), H):  # 바다 반사
        t = (y - H * 0.62) / (H * 0.38)
        d.line([(0, y), (W, y)], fill=(int(90 - 40 * t), int(40 + 10 * t), int(90 + 20 * t)))
    d.rectangle([W // 2 - 50, int(H * 0.62), W // 2 + 50, H], fill=(255, 140, 70))
    return full.filter(ImageFilter.GaussianBlur(2))


def dog():
    full = vgrad((170, 215, 150), (110, 170, 100), H)
    d = ImageDraw.Draw(full)
    cx, cy = W // 2, H // 2 + 80
    d.ellipse([cx - 220, cy - 100, cx + 220, cy + 180], fill=(190, 140, 80))   # 몸통
    d.ellipse([cx + 120, cy - 260, cx + 330, cy - 60], fill=(200, 150, 90))    # 머리
    d.polygon([(cx + 150, cy - 250), (cx + 130, cy - 350), (cx + 200, cy - 270)], fill=(150, 100, 60))
    d.polygon([(cx + 280, cy - 255), (cx + 320, cy - 350), (cx + 330, cy - 240)], fill=(150, 100, 60))
    d.ellipse([cx + 190, cy - 200, cx + 215, cy - 175], fill=(30, 30, 30))     # 눈
    d.ellipse([cx + 270, cy - 200, cx + 295, cy - 175], fill=(30, 30, 30))
    d.ellipse([cx + 225, cy - 150, cx + 265, cy - 115], fill=(40, 30, 30))     # 코
    for lx in (-160, -60, 60, 160):                                            # 다리
        d.rectangle([cx + lx, cy + 120, cx + lx + 45, cy + 290], fill=(180, 130, 75))
    return full.filter(ImageFilter.GaussianBlur(1))


SAMPLES = [
    # (생성함수, 파일명, 촬영시각, 위도, 경도, 설명)
    (beach, "haeundae_beach.jpg", "2025:08:03 14:22:10", 35.1587, 129.1604, "부산 해운대"),
    (beach, "jeju_hyeopjae.jpg", "2025:07:19 11:05:44", 33.3940, 126.2400, "제주 협재"),
    (mountain, "seoraksan_hike.jpg", "2024:10:12 09:31:02", 38.1195, 128.4656, "설악산"),
    (mountain, "hallasan_trail.jpg", "2025:05:05 10:14:55", 33.3617, 126.5292, "한라산"),
    (city_night, "seoul_night_view.jpg", "2025:11:22 21:48:30", 37.5512, 126.9882, "남산 야경"),
    (city_night, "busan_night.jpg", "2024:12:31 23:10:05", 35.0966, 129.0306, "부산 야경"),
    (food, "lunch_pasta.jpg", "2026:01:14 12:40:21", 37.5240, 127.0276, "강남 점심"),
    (food, "dinner_bbq.jpg", "2025:09:27 19:02:13", 37.5663, 126.9779, "시청 저녁"),
    (flowers, "spring_flowers.jpg", "2026:04:05 13:25:40", 35.1538, 126.8530, "광주 봄꽃"),
    (flowers, "garden_blossom.jpg", "2025:04:09 15:55:08", 37.5796, 126.9770, "경복궁 꽃"),
    (snow, "winter_snow.jpg", "2026:01:24 10:08:51", 37.6850, 128.7183, "평창 설경"),
    (sunset, "sunset_sea.jpg", "2025:10:18 17:51:33", 36.0190, 129.4307, "포항 일몰"),
    (dog, "my_dog_park.jpg", "2026:03:15 16:20:00", 37.5285, 126.9327, "한강공원 강아지"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for fn, name, dt, lat, lon, _desc in SAMPLES:
        save_with_exif(fn(), name, dt, lat, lon)
    print(f"\n{len(SAMPLES)} sample photos created in {OUT}")


if __name__ == "__main__":
    main()
