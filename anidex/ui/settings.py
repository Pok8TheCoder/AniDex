from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anidex.db.repositories import Repository
from anidex.paths import app_data_dir, db_path, tokens_path
from anidex.services.mal_api import MalApiError, MalClient
from anidex.services.sync import (
    export_repo_to_xml,
    import_xml_into_repo,
    pull_mal_list,
    push_local_to_mal,
)
from anidex.ui.threading_util import start_worker, stop_worker
from anidex.ui.widgets import section_label


class OAuthWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, client_id: str) -> None:
        super().__init__()
        self.client_id = client_id

    def run(self) -> None:
        try:
            client = MalClient(self.client_id)
            name = client.login_oauth_pkce()
            self.done.emit(name)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class PaheCfWorker(QThread):
    done = Signal(bool)
    failed = Signal(str)

    def run(self) -> None:
        try:
            from anidex.services.pahe_browser import wait_for_cloudflare

            ok = wait_for_cloudflare()
            self.done.emit(ok)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class SyncWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, client_id: str, mode: str) -> None:
        super().__init__()
        self.client_id = client_id
        self.mode = mode

    def run(self) -> None:
        from anidex.db import open_repo

        conn = None
        try:
            client = MalClient(self.client_id)
            if not client.is_authenticated:
                raise MalApiError("Connect MAL with OAuth first.")
            conn, repo = open_repo()
            if self.mode == "pull":
                n = pull_mal_list(repo, client)
                self.done.emit(f"Pulled {n} anime from MAL")
            else:
                ok, failed = push_local_to_mal(repo, client)
                self.done.emit(f"Pushed {ok} entries ({failed} failed)")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            if conn is not None:
                conn.close()


class SettingsView(QWidget):
    lists_changed = Signal()
    status_message = Signal(str)

    def __init__(self, repo: Repository) -> None:
        super().__init__()
        self.repo = repo
        self._workers: list[QThread] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        profile = QFrame()
        profile.setObjectName("panel")
        pl = QVBoxLayout(profile)
        pl.setContentsMargins(16, 16, 16, 16)
        pl.addWidget(section_label("Profile"))
        self.name = QLineEdit()
        pl.addWidget(self.name)
        save_name = QPushButton("Save profile")
        save_name.clicked.connect(self.save_profile)
        pl.addWidget(save_name)
        root.addWidget(profile)

        mal = QFrame()
        mal.setObjectName("panel")
        ml = QVBoxLayout(mal)
        ml.setContentsMargins(16, 16, 16, 16)
        ml.addWidget(section_label("MyAnimeList API"))
        tip = QLabel(
            "Create an API client at https://myanimelist.net/apiconfig\n"
            f"Set redirect URI to: http://127.0.0.1:58432/callback"
        )
        tip.setObjectName("body")
        tip.setWordWrap(True)
        ml.addWidget(tip)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("MAL Client ID")
        ml.addWidget(self.client_id)
        save_cid = QPushButton("Save Client ID")
        save_cid.clicked.connect(self.save_client_id)
        ml.addWidget(save_cid)

        self.mal_status = QLabel("")
        self.mal_status.setObjectName("status")
        ml.addWidget(self.mal_status)

        auth_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect with OAuth…")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self.connect_oauth)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("danger")
        self.disconnect_btn.clicked.connect(self.disconnect)
        auth_row.addWidget(self.connect_btn)
        auth_row.addWidget(self.disconnect_btn)
        ml.addLayout(auth_row)

        sync_row = QHBoxLayout()
        self.pull_btn = QPushButton("Pull list from MAL")
        self.pull_btn.clicked.connect(lambda: self.run_sync("pull"))
        self.push_btn = QPushButton("Push local list to MAL")
        self.push_btn.clicked.connect(lambda: self.run_sync("push"))
        sync_row.addWidget(self.pull_btn)
        sync_row.addWidget(self.push_btn)
        ml.addLayout(sync_row)
        root.addWidget(mal)

        xml = QFrame()
        xml.setObjectName("panel")
        xl = QVBoxLayout(xml)
        xl.setContentsMargins(16, 16, 16, 16)
        xl.addWidget(section_label("MAL XML import / export"))
        xml_row = QHBoxLayout()
        self.import_btn = QPushButton("Import XML…")
        self.import_btn.clicked.connect(self.import_xml)
        self.export_btn = QPushButton("Export XML…")
        self.export_btn.clicked.connect(self.export_xml)
        xml_row.addWidget(self.import_btn)
        xml_row.addWidget(self.export_btn)
        xl.addLayout(xml_row)
        root.addWidget(xml)

        pahe = QFrame()
        pahe.setObjectName("panel")
        pl = QVBoxLayout(pahe)
        pl.setContentsMargins(16, 16, 16, 16)
        pl.addWidget(section_label("AnimePahe downloads"))
        pahe_tip = QLabel(
            "AnimePahe uses Cloudflare. A Chrome window opens — wait until "
            "animepahe.pw fully loads (not 'Just a moment...'). Leave that window open."
        )
        pahe_tip.setObjectName("body")
        pahe_tip.setWordWrap(True)
        pl.addWidget(pahe_tip)
        self.cf_btn = QPushButton("Pass AnimePahe Cloudflare…")
        self.cf_btn.setObjectName("primary")
        self.cf_btn.clicked.connect(self.pass_cloudflare)
        pl.addWidget(self.cf_btn)
        root.addWidget(pahe)

        data = QFrame()
        data.setObjectName("panel")
        dl = QVBoxLayout(data)
        dl.setContentsMargins(16, 16, 16, 16)
        dl.addWidget(section_label("Local data"))
        self.data_path = QLabel(str(app_data_dir()))
        self.data_path.setObjectName("previewMeta")
        self.data_path.setWordWrap(True)
        dl.addWidget(self.data_path)
        backup = QPushButton("Backup database…")
        backup.clicked.connect(self.backup_db)
        dl.addWidget(backup)
        root.addWidget(data)
        root.addStretch()

        self.reload()

    def reload(self) -> None:
        p = self.repo.get_profile()
        self.name.setText(p.display_name)
        self.client_id.setText(p.mal_client_id)
        client = MalClient(p.mal_client_id)
        if client.is_authenticated:
            user = p.mal_username or "connected"
            self.mal_status.setText(f"OAuth: connected as {user}")
        else:
            self.mal_status.setText("OAuth: not connected")

    def save_profile(self) -> None:
        self.repo.update_profile(display_name=self.name.text().strip())
        self.status_message.emit("Profile saved")

    def save_client_id(self) -> None:
        self.repo.update_profile(mal_client_id=self.client_id.text().strip())
        self.status_message.emit("Client ID saved")
        self.reload()

    def connect_oauth(self) -> None:
        cid = self.client_id.text().strip() or self.repo.get_profile().mal_client_id
        if not cid:
            QMessageBox.warning(self, "Client ID", "Save a MAL Client ID first.")
            return
        self.repo.update_profile(mal_client_id=cid)
        self.connect_btn.setEnabled(False)
        self.status_message.emit("Waiting for MAL login in browser…")
        worker = OAuthWorker(cid)
        self._workers.append(worker)
        worker.done.connect(self.on_oauth_done)
        worker.failed.connect(self.on_oauth_fail)
        worker.finished.connect(lambda: self.connect_btn.setEnabled(True))
        start_worker(worker, self)

    @Slot(str)
    def on_oauth_done(self, name: str) -> None:
        self.repo.update_profile(mal_username=name)
        self.reload()
        self.status_message.emit(f"Connected as {name}")
        QMessageBox.information(self, "Connected", f"Signed in as {name}")

    @Slot(str)
    def on_oauth_fail(self, err: str) -> None:
        QMessageBox.critical(self, "OAuth failed", err)
        self.status_message.emit("OAuth failed")

    def disconnect(self) -> None:
        MalClient(self.repo.get_profile().mal_client_id).clear_tokens()
        if tokens_path().exists():
            tokens_path().unlink(missing_ok=True)
        self.reload()
        self.status_message.emit("Disconnected from MAL")

    def run_sync(self, mode: str) -> None:
        cid = self.repo.get_profile().mal_client_id
        worker = SyncWorker(cid, mode)
        self._workers.append(worker)
        self.pull_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        worker.done.connect(self.on_sync_done)
        worker.failed.connect(self.on_sync_fail)
        worker.finished.connect(
            lambda: (self.pull_btn.setEnabled(True), self.push_btn.setEnabled(True))
        )
        start_worker(worker, self)

    @Slot(str)
    def on_sync_done(self, msg: str) -> None:
        self.status_message.emit(msg)
        self.lists_changed.emit()
        QMessageBox.information(self, "Sync", msg)

    @Slot(str)
    def on_sync_fail(self, err: str) -> None:
        QMessageBox.critical(self, "Sync failed", err)

    def import_xml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import MAL XML", "", "XML (*.xml);;All (*.*)"
        )
        if not path:
            return
        try:
            imported, updated = import_xml_into_repo(self.repo, path)
            self.lists_changed.emit()
            msg = f"Imported {imported} new, updated {updated}."
            self.status_message.emit(msg)
            QMessageBox.information(self, "Import", msg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(e))

    def export_xml(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export MAL XML", "animelist.xml", "XML (*.xml)"
        )
        if not path:
            return
        try:
            export_repo_to_xml(self.repo, path)
            self.status_message.emit(f"Exported to {path}")
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))

    def backup_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Backup database", "anidex-backup.db", "SQLite (*.db)"
        )
        if not path:
            return
        shutil.copy2(db_path(), path)
        self.status_message.emit(f"Backup saved to {path}")

    def pass_cloudflare(self) -> None:
        self.cf_btn.setEnabled(False)
        self.status_message.emit("Opening browser — complete the Cloudflare check…")
        worker = PaheCfWorker()
        self._workers.append(worker)
        worker.done.connect(self.on_cf_done)
        worker.failed.connect(self.on_cf_fail)
        worker.finished.connect(lambda: self.cf_btn.setEnabled(True))
        start_worker(worker, self)

    @Slot(bool)
    def on_cf_done(self, ok: bool) -> None:
        if ok:
            QMessageBox.information(
                self,
                "Ready",
                "AnimePahe session is ready. You can download episodes from the Anime tab.",
            )
            self.status_message.emit("AnimePahe Cloudflare passed")
        else:
            QMessageBox.warning(
                self,
                "Timed out",
                "Cloudflare check did not finish in time. Try again and wait for "
                "animepahe.pw to fully load.",
            )

    @Slot(str)
    def on_cf_fail(self, err: str) -> None:
        QMessageBox.critical(self, "Browser error", err)
        self.status_message.emit("AnimePahe browser failed")
