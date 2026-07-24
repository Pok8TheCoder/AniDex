"""Unit + API tests for AniDex peer sync."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Speed up auth hash in tests that touch the app
os.environ.setdefault("ANIDEX_PBKDF2_ITERS", "1000")


class MergeRulesTests(unittest.TestCase):
    def test_progress_takes_max(self) -> None:
        from anidex.sync.merge import merge_list_entry

        local = {
            "list_status": "watching",
            "progress": 5,
            "score": 8,
            "notes": "a",
            "updated_at": "2024-01-01",
        }
        remote = {
            "list_status": "completed",
            "progress": 12,
            "score": 0,
            "notes": "b",
            "updated_at": "2024-06-01",
        }
        out = merge_list_entry(local, remote)
        self.assertEqual(out["progress"], 12)
        self.assertEqual(out["list_status"], "completed")
        self.assertEqual(out["score"], 8)  # nonzero preferred

    def test_page_index_max(self) -> None:
        from anidex.sync.merge import merge_read_position

        out = merge_read_position(
            {"page_index": 3, "updated_at": "a", "mangadex_id": "m", "chapter_id": "c"},
            {"page_index": 10, "updated_at": "b", "mangadex_id": "m", "chapter_id": "c"},
        )
        self.assertEqual(out["page_index"], 10)

    def test_tombstone_wins(self) -> None:
        from anidex.sync.merge import tombstone_wins

        self.assertTrue(tombstone_wins({"deleted_at": "2024-06-01"}, "2024-01-01"))
        self.assertFalse(tombstone_wins({"deleted_at": "2024-01-01"}, "2024-06-01"))


class SyncApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._db = Path(cls._tmpdir.name) / "sync-test.db"
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
        from anidex.sync.token import ensure_sync_identity, set_sync_token
        from anidex.web.app import app
        from fastapi.testclient import TestClient

        conn = init_db()
        conn.close()
        ensure_sync_identity()
        set_sync_token("test-sync-token-abc")
        cls.token = "test-sync-token-abc"
        cls.client = TestClient(app)
        cls.headers = {
            "Authorization": f"Bearer {cls.token}",
            "X-AniDex-Sync-Token": cls.token,
        }

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.close()
        except Exception:
            pass
        for p in cls._patches:
            p.stop()
        try:
            cls._tmpdir.cleanup()
        except Exception:
            pass

    def test_hello_requires_token(self) -> None:
        r = self.client.get("/api/sync/hello")
        self.assertEqual(r.status_code, 401)

    def test_hello_ok(self) -> None:
        r = self.client.get("/api/sync/hello", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["app"], "AniDex")
        self.assertIn("device_id", body)
        self.assertIn("library", body["capabilities"])

    def test_manifest_and_status(self) -> None:
        r = self.client.get("/api/sync/manifest", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["app"], "AniDex")
        r = self.client.get("/api/sync/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["sync_token"], self.token)

    def test_push_pull_progress_merge(self) -> None:
        from anidex.db.schema import open_repo

        # Seed local anime with progress 3
        conn, repo = open_repo()
        try:
            aid = repo.upsert_anime(mal_id=99901, title="Sync Test Show")
            repo.set_user_anime(aid, list_status="watching", progress=3, score=7)
        finally:
            conn.close()

        # Peer pushes higher progress
        r = self.client.post(
            "/api/sync/push",
            headers=self.headers,
            json={
                "batches": {
                    "anime": [
                        {
                            "mal_id": 99901,
                            "title": "Sync Test Show",
                            "list_status": "watching",
                            "progress": 11,
                            "score": 0,
                            "notes": "",
                            "updated_at": "2099-01-01",
                        }
                    ]
                }
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        conn, repo = open_repo()
        try:
            entries = [e for e in repo.list_user_anime() if e.mal_id == 99901]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].progress, 11)
            self.assertEqual(entries[0].score, 7)  # nonzero kept
        finally:
            conn.close()

        # Pull export
        r = self.client.post(
            "/api/sync/pull",
            headers=self.headers,
            json={"keys": ["mal:99901"]},
        )
        self.assertEqual(r.status_code, 200)
        anime = r.json()["batches"]["anime"]
        self.assertEqual(anime[0]["progress"], 11)


class TwoDbMergeTests(unittest.TestCase):
    """Simulate two devices merging via apply_batches."""

    def test_max_progress_across_dbs(self) -> None:
        from anidex.db import init_db
        from anidex.db.schema import open_repo
        from anidex.sync.manifest import apply_batches, export_entities

        with tempfile.TemporaryDirectory() as td:
            db_a = Path(td) / "a.db"
            db_b = Path(td) / "b.db"

            with mock.patch("anidex.paths.db_path", return_value=db_a), mock.patch(
                "anidex.paths.app_data_dir", return_value=Path(td) / "a"
            ):
                (Path(td) / "a").mkdir()
                conn0 = init_db()
                conn0.close()
                conn, repo = open_repo()
                try:
                    aid = repo.upsert_anime(mal_id=42, title="Two DB")
                    repo.set_user_anime(aid, list_status="watching", progress=4)
                finally:
                    conn.close()
                batch = export_entities(["mal:42"])

            with mock.patch("anidex.paths.db_path", return_value=db_b), mock.patch(
                "anidex.paths.app_data_dir", return_value=Path(td) / "b"
            ):
                (Path(td) / "b").mkdir()
                conn0 = init_db()
                conn0.close()
                conn, repo = open_repo()
                try:
                    aid = repo.upsert_anime(mal_id=42, title="Two DB")
                    repo.set_user_anime(aid, list_status="watching", progress=9)
                finally:
                    conn.close()
                # Apply A's lower progress — should stay at 9
                apply_batches(batch)
                conn, repo = open_repo()
                try:
                    e = next(x for x in repo.list_user_anime() if x.mal_id == 42)
                    self.assertEqual(e.progress, 9)
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
