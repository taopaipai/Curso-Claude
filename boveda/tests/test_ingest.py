import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boveda.ingest import importar_instagram, importar_tiktok, importar_urls, importar_youtube
from boveda.ingest.base import canonizar


def test_canonizar_quita_tracking_y_detecta_plataforma():
    assert canonizar("https://www.instagram.com/reel/ABC123/?igshid=1") == (
        "https://www.instagram.com/p/ABC123/", "instagram", "ABC123", None)
    assert canonizar("https://www.tiktok.com/@ana/video/7123?is_from_webapp=1") == (
        "https://www.tiktok.com/@ana/video/7123", "tiktok", "7123", "ana")
    assert canonizar("https://youtu.be/dQw4w9WgXcQ?t=42")[:3] == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ")


def test_instagram_export_json(tmp_path):
    export = {"saved_saved_media": [
        {"title": "creador1", "string_map_data": {"Saved on": {
            "href": "https://www.instagram.com/p/AAA111/", "timestamp": 1690000000}}},
        {"title": "creador2", "string_map_data": {"Saved on": {
            "href": "https://www.instagram.com/reel/BBB222/?igshid=x", "timestamp": 1690000100}}},
    ]}
    ruta = tmp_path / "saved_posts.json"
    ruta.write_text(json.dumps(export), encoding="utf-8")

    items = importar_instagram(ruta, carpeta="copywriting")
    assert [i["url_canonica"] for i in items] == [
        "https://www.instagram.com/p/AAA111/", "https://www.instagram.com/p/BBB222/"]
    assert all(i["carpeta"] == "copywriting" for i in items)
    assert items[0]["guardado_en"].startswith("2023-")


def test_instagram_html_fallback(tmp_path):
    ruta = tmp_path / "saved.html"
    ruta.write_text('<a href="https://www.instagram.com/p/ZZZ999/">post</a>', encoding="utf-8")
    items = importar_instagram(ruta)
    assert items[0]["url_canonica"] == "https://www.instagram.com/p/ZZZ999/"


def test_tiktok_export_json(tmp_path):
    export = {"Activity": {"Favorite Videos": {"FavoriteVideoList": [
        {"Date": "2021-03-04 10:00:00", "Link": "https://www.tiktok.com/@bea/video/999"}]}}}
    ruta = tmp_path / "user_data.json"
    ruta.write_text(json.dumps(export), encoding="utf-8")

    items = importar_tiktok(ruta)
    assert items[0]["url_canonica"] == "https://www.tiktok.com/@bea/video/999"
    assert items[0]["autor"] == "bea"
    assert items[0]["carpeta"] == "favoritos"


def test_youtube_csv_takeout(tmp_path):
    ruta = tmp_path / "Ver mas tarde-videos.csv"
    ruta.write_text(
        "Playlist Id,Channel Id\nPL123,UC123\n\n"
        "Video ID,Playlist Video Creation Timestamp\n"
        "dQw4w9WgXcQ,2019-05-01 12:00:00\nabcdefghijk,2020-06-02 13:00:00\n",
        encoding="utf-8")
    items = importar_youtube(ruta)
    assert len(items) == 2
    assert items[0]["url_canonica"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert items[0]["guardado_en"] == "2019-05-01T12:00:00"


def test_urls_con_cabeceras_de_carpeta(tmp_path):
    ruta = tmp_path / "links.txt"
    ruta.write_text(
        "# ventas\n"
        "https://www.tiktok.com/@ana/video/1\n"
        "https://youtu.be/dQw4w9WgXcQ, storytelling\n"
        "# otra\n"
        "https://www.instagram.com/p/CCC/\n",
        encoding="utf-8")
    items = importar_urls(ruta)
    assert [i["carpeta"] for i in items] == ["ventas", "storytelling", "otra"]
