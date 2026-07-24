"""Smoke tests for AniDex web overhaul."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


class PickSourceTests(unittest.TestCase):
    def test_eng_not_fallback_to_jpn(self) -> None:
        from anidex.services.animepahe import PaheSource, pick_source

        sources = [
            PaheSource(url="j", resolution=1080, audio="jpn", fansub="A"),
            PaheSource(url="e", resolution=720, audio="eng", fansub="B"),
        ]
        self.assertEqual(pick_source(sources, resolution=1080, audio="eng").url, "e")

    def test_missing_dub_raises(self) -> None:
        from anidex.services.animepahe import PaheSource, pick_source

        sources = [PaheSource(url="j", resolution=1080, audio="jpn", fansub="A")]
        with self.assertRaises(RuntimeError) as ctx:
            pick_source(sources, resolution=1080, audio="eng")
        self.assertIn("ENG", str(ctx.exception).upper())

    def test_norm_audio_aliases(self) -> None:
        from anidex.services.animepahe import PaheSource, pick_source

        sources = [PaheSource(url="e", resolution=1080, audio="en", fansub="B")]
        self.assertEqual(pick_source(sources, audio="eng").url, "e")


class ApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._db = Path(cls._tmpdir.name) / "test.db"
        # Patch paths before importing app
        cls._patches = [
            mock.patch("anidex.paths.db_path", return_value=cls._db),
            mock.patch(
                "anidex.paths.app_data_dir",
                return_value=Path(cls._tmpdir.name),
            ),
        ]
        for p in cls._patches:
            p.start()

        from anidex.db import init_db
        from anidex.web.app import app

        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        for p in cls._patches:
            p.stop()
        cls._tmpdir.cleanup()

    def test_meta(self) -> None:
        r = self.client.get("/api/meta")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["app"], "AniDex")

    def test_profile_and_onboarding(self) -> None:
        r = self.client.get("/api/profile")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(
            "/api/onboarding",
            json={"display_name": "Tester", "mode": "empty"},
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/profile")
        self.assertTrue(r.json()["onboarded"])
        self.assertEqual(r.json()["display_name"], "Tester")

    def test_anime_list_empty(self) -> None:
        r = self.client.get("/api/anime")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_manga_list_empty(self) -> None:
        r = self.client.get("/api/manga")
        self.assertEqual(r.status_code, 200)

    def test_pages(self) -> None:
        for path in (
            "/anime",
            "/discover",
            "/upcoming",
            "/downloads",
            "/manga",
            "/manga/upcoming",
            "/manga/read/00000000-0000-0000-0000-000000000001",
            "/settings",
            "/onboarding",
            "/login",
            "/watch/1",
        ):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("text/html", r.headers["content-type"])

    def test_downloads_empty(self) -> None:
        r = self.client.get("/api/downloads")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["items"], [])

    def test_meta_no_home_leak_shape(self) -> None:
        r = self.client.get("/api/meta")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["app"], "AniDex")

    def test_docs_disabled(self) -> None:
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)

    def test_lan_requires_login(self) -> None:
        from anidex.web import security as sec
        from anidex.web import auth as lf_auth

        sec.enable_lan_mode()
        lf_auth.ensure_auth_defaults()
        with mock.patch.object(sec, "client_is_loopback", return_value=False):
            with mock.patch.object(sec, "client_ip", return_value="192.168.1.50"):
                denied = self.client.get("/api/meta")
                self.assertEqual(denied.status_code, 401)
                bad = self.client.post(
                    "/api/auth/login",
                    json={"username": "user", "password": "wrong"},
                )
                self.assertEqual(bad.status_code, 401)
                ok = self.client.post(
                    "/api/auth/login",
                    json={"username": "user", "password": "pwd"},
                )
                self.assertEqual(ok.status_code, 200)
                meta = self.client.get("/api/meta")
                self.assertEqual(meta.status_code, 200)
                body = meta.json()
                self.assertEqual(body.get("data_dir"), "(local only)")
                self.assertEqual(body.get("db_path"), "(local only)")

    def test_lan_lockout_after_three_fails(self) -> None:
        from anidex.web import security as sec
        from anidex.web import auth as lf_auth

        sec.enable_lan_mode()
        lf_auth.ensure_auth_defaults()
        lf_auth.unlock_ip("10.9.8.7")
        with mock.patch.object(sec, "client_is_loopback", return_value=False):
            with mock.patch.object(sec, "client_ip", return_value="10.9.8.7"):
                for _ in range(3):
                    r = self.client.post(
                        "/api/auth/login",
                        json={"username": "user", "password": "nope"},
                    )
                self.assertEqual(r.status_code, 403)
                locked = self.client.get("/api/meta")
                self.assertEqual(locked.status_code, 403)
                # Cannot login even with correct password while locked
                still = self.client.post(
                    "/api/auth/login",
                    json={"username": "user", "password": "pwd"},
                )
                self.assertEqual(still.status_code, 403)

    def test_media_delete_404(self) -> None:
        r = self.client.delete("/api/media/999999")
        self.assertEqual(r.status_code, 404)

    def test_stream_missing_404(self) -> None:
        r = self.client.get("/stream/does-not-exist/index.m3u8")
        self.assertEqual(r.status_code, 404)

    def test_hls_session_clear_playlist(self) -> None:
        from anidex.services.stream_sessions import STREAM_STORE

        playlist = (
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:6.0,\n"
            "https://example.test/seg0.ts\n#EXTINF:6.0,\n"
            "https://example.test/seg1.ts\n#EXT-X-ENDLIST\n"
        )

        class FakeResp:
            ok = True
            text = playlist
            content = b"segdata"

            def __init__(self, **kw):
                self.__dict__.update(kw)

        class FakeHttp:
            def get(self, url, timeout=60):
                if url.endswith(".ts"):
                    return FakeResp(content=b"AAAA")
                return FakeResp(text=playlist)

        with mock.patch(
            "anidex.services.stream_sessions._curl_session",
            return_value=FakeHttp(),
        ):
            session = STREAM_STORE.create(
                "https://example.test/index.m3u8",
                referer="https://kwik.test/",
                warm_seconds=6.0,
            )
        self.assertIn(b"seg/0.ts", session.playlist_body)
        self.assertNotIn(b"EXT-X-KEY", session.playlist_body)
        self.assertTrue(session._cache_path(0).is_file())
        session.set_playhead(0.0)
        r = self.client.get(f"/stream/{session.id}/index.m3u8")
        self.assertEqual(r.status_code, 200)
        self.assertIn("seg/0.ts", r.text)
        r2 = self.client.post(
            f"/stream/{session.id}/playhead", json={"seconds": 3.0}
        )
        self.assertEqual(r2.status_code, 200)
        STREAM_STORE.drop(session.id)
        self.assertFalse(session.cache_dir.exists())


if __name__ == "__main__":
    unittest.main()
