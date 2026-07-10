"""Wikimedia Commons에서 실제 CC 사진을 받아 GPS EXIF를 심는 샘플 준비 스크립트.

합성 그림보다 CLIP 인식률이 훨씬 높아 데모 품질이 좋아진다.
(로컬 테스트용 샘플 데이터 준비 목적)
"""
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import piexif
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "photos"
UA = "PhotoNestAI-sample-fetch/0.1 (local dev)"
API = "https://commons.wikimedia.org/w/api.php"

# (검색어, 저장파일명, 촬영시각, 위도, 경도)
SAMPLES = [
    ("Haeundae beach Busan", "haeundae_beach.jpg", "2025:08:03 14:22:10", 35.1587, 129.1604),
    ("sandy beach blue ocean waves", "jeju_hyeopjae.jpg", "2025:07:19 11:05:44", 33.3940, 126.2400),
    ("Seoraksan mountain autumn", "seoraksan_hike.jpg", "2024:10:12 09:31:02", 38.1195, 128.4656),
    ("green mountain hiking trail", "hallasan_trail.jpg", "2025:05:05 10:14:55", 33.3617, 126.5292),
    ("Seoul night skyline", "seoul_night_view.jpg", "2025:11:22 21:48:30", 37.5512, 126.9882),
    ("city night lights skyscraper", "busan_night.jpg", "2024:12:31 23:10:05", 35.0966, 129.0306),
    ("spaghetti pasta dish plate", "lunch_pasta.jpg", "2026:01:14 12:40:21", 37.5240, 127.0276),
    ("Korean barbecue food", "dinner_bbq.jpg", "2025:09:27 19:02:13", 37.5663, 126.9779),
    ("cherry blossom spring park", "spring_flowers.jpg", "2026:04:05 13:25:40", 35.1538, 126.8530),
    ("pink rose garden flowers", "garden_blossom.jpg", "2025:04:09 15:55:08", 37.5796, 126.9770),
    ("snow covered forest winter", "winter_snow.jpg", "2026:01:24 10:08:51", 37.6850, 128.7183),
    ("sunset over the sea orange sky", "sunset_sea.jpg", "2025:10:18 17:51:33", 36.0190, 129.4307),
    ("golden retriever dog park", "my_dog_park.jpg", "2026:03:15 16:20:00", 37.5285, 126.9327),
    ("tabby cat sitting", "my_cat_home.jpg", "2026:02:08 09:12:30", 37.5013, 127.0396),
    ("latte art coffee cup cafe", "cafe_latte.jpg", "2025:12:06 11:33:20", 37.5443, 127.0557),
    ("Gyeongbokgung palace", "gyeongbokgung_trip.jpg", "2025:06:14 13:00:00", 37.5796, 126.9770),
]


def deg_to_dms(deg):
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = round(((deg - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def api_search_thumb(query):
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrlimit": 3,
        "gsrnamespace": 6,
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "iiurlwidth": 1280,
        "format": "json",
    })
    req = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    pages = sorted(
        data.get("query", {}).get("pages", {}).values(),
        key=lambda p: p.get("index", 99),
    )
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") in ("image/jpeg", "image/png") and info.get("thumburl"):
            return info["thumburl"]
    return None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for query, name, dt, lat, lon in SAMPLES:
        if (OUT / name).exists():
            ok += 1
            print(f"exists {name}")
            continue
        try:
            time.sleep(6)  # Commons rate limit 회피
            url = api_search_thumb(query)
            if not url:
                print(f"skip (no result): {query}")
                continue
            img = Image.open(io.BytesIO(fetch(url))).convert("RGB")
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
            img.save(OUT / name, "JPEG", quality=88, exif=piexif.dump(exif))
            ok += 1
            print(f"saved {name}  <-  {query}")
        except Exception as e:  # noqa: BLE001
            print(f"fail: {query}: {e}")
    print(f"\n{ok}/{len(SAMPLES)} real sample photos saved to {OUT}")


if __name__ == "__main__":
    main()
