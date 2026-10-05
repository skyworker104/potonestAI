"""역지오코딩 지명에 시·군·읍·면 한글명 병기.

실사례: 지리산권 사진 182장의 지명이 'Kŏrim 경남 Chungsan Pukch'on'처럼 로마자뿐이라
"산청에서 찍은 사진" 같은 질의가 걸리지 않았다.
"""
import zipfile

import pytest

from backend import geoname


def _row(*cols):
    # GeoNames: id name ascii alternatenames lat lon class code cc cc2 admin1 admin2 admin3
    cols = list(cols) + [""] * (13 - len(cols))
    return "\t".join(cols)


KR = "\n".join([
    _row("1", "Gyeongsangnam-do", "", "경상남도,경남", "35.2", "128.2", "A", "ADM1", "KR", "", "20"),
    _row("2", "Sancheong-gun", "", "Sancheong,산청,산청군", "35.4", "127.9", "A", "ADM2", "KR", "", "20", "38370"),
    _row("3", "Sicheon-myeon", "", "시천면", "35.27", "127.79", "A", "ADM3", "KR", "", "20", "38370", "123"),
    _row("4", "Pukch'on", "Pukch'on", "", "35.28", "127.75", "P", "PPL", "KR", "", "20", "38370", "123"),
]) + "\n"


@pytest.fixture
def gdir(tmp_path, monkeypatch):
    d = tmp_path / "geonames"
    d.mkdir()
    with zipfile.ZipFile(d / "KR.zip", "w") as z:
        z.writestr("KR.txt", KR)
    with zipfile.ZipFile(d / "cities500.zip", "w") as z:
        z.writestr("cities500.txt", "")
    monkeypatch.setattr(geoname, "DIR", d)
    monkeypatch.setattr(geoname, "_cache",
                        {"loaded": False, "lat": None, "lon": None, "names": None,
                         "rebuilt": False})
    return d


def test_romanized_village_gets_korean_admin_names(gdir):
    name = geoname.lookup(35.281, 127.751)
    assert name.startswith("Pukch'on")
    for ko in ("시천면", "산청군", "경남"):
        assert ko in name, ko


def test_short_admin_forms_are_left_out(gdir):
    # '산청'은 '산청군'의 부분일치로 걸린다. 짧은 꼴을 넣으면 '고양'이 '고양이'를 잡는다.
    tokens = geoname.lookup(35.281, 127.751).split()
    assert "산청군" in tokens and "산청" not in tokens


def test_old_index_triggers_one_time_remap(gdir):
    # 버전 표시 없는 옛 색인 → 새로 만들고, 사진 지명을 다시 매기라고 한 번 알린다
    (gdir / "index.npz").write_bytes(b"old")
    (gdir / "names.json").write_text("[]")
    assert geoname.take_rebuilt() is True
    assert geoname.take_rebuilt() is False
    assert (gdir / "index_version").read_text() == str(geoname.INDEX_VERSION)


def test_fresh_install_does_not_ask_for_remap(gdir):
    assert geoname.available()
    assert geoname.take_rebuilt() is False


def test_overseas_town_gets_region_and_country_in_korean(tmp_path, monkeypatch):
    d = tmp_path / "geo"
    d.mkdir()
    with zipfile.ZipFile(d / "KR.zip", "w") as z:
        z.writestr("KR.txt", "")
    world = "\n".join([
        _row("10", "Nagano", "Nagano", "Nagano,나가노", "36.65", "138.18", "P", "PPLA", "JP", "", "20"),
        _row("11", "Tokyo", "Tokyo", "Tokyo,도쿄", "35.69", "139.69", "P", "PPLC", "JP", "", "40"),
        _row("12", "Nukui", "Nukui", "", "35.70", "139.50", "P", "PPL", "JP", "", "40"),
        _row("13", "Matsumoto", "Matsumoto", "", "36.23", "137.97", "P", "PPL", "JP", "", "20"),
    ]) + "\n"
    with zipfile.ZipFile(d / "cities500.zip", "w") as z:
        z.writestr("cities500.txt", world)
    monkeypatch.setattr(geoname, "DIR", d)
    monkeypatch.setattr(geoname, "_cache", {"loaded": False, "lat": None, "lon": None,
                                            "names": None, "rebuilt": False})
    assert geoname.lookup(35.701, 139.501, k=1) == "Nukui 도쿄 일본"
    assert geoname.lookup(36.231, 137.971, k=1) == "Matsumoto 나가노 일본"
