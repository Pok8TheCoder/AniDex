from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from localfun.db.repositories import Repository
from localfun.services.mal_api import MalApiError, MalClient, MalAnimeResult
from localfun.services.mangadex import MangaDexClient, MangaDexError, MangaDexResult
from localfun.ui.threading_util import start_worker, stop_worker
from localfun.ui.widgets import CoverLabel, section_label


@dataclass
class SearchHit:
    kind: str  # anime | manga
    key: str
    title: str
    meta: str
    cover_url: str | None
    payload: object


class SearchWorker(QThread):
    finished_ok = Signal(object)  # list[SearchHit]
    failed = Signal(str)

    def __init__(self, kind: str, query: str, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.query = query
        self.client_id = client_id

    def run(self) -> None:
        try:
            hits: list[SearchHit] = []
            if self.isInterruptionRequested():
                return
            if self.kind == "anime":
                client = MalClient(self.client_id)
                for r in client.search_anime(self.query):
                    if self.isInterruptionRequested():
                        return
                    hits.append(
                        SearchHit(
                            kind="anime",
                            key=str(r.mal_id),
                            title=r.title_english or r.title,
                            meta=(
                                f"{r.media_type or '?'} · {r.airing_status or '?'} · "
                                f"{r.episodes or '?'} eps · MAL {r.mal_id}"
                            ),
                            cover_url=r.cover_url,
                            payload=r,
                        )
                    )
            else:
                client = MangaDexClient()
                for r in client.search(self.query):
                    if self.isInterruptionRequested():
                        return
                    hits.append(
                        SearchHit(
                            kind="manga",
                            key=r.mangadex_id,
                            title=r.title_english or r.title,
                            meta=(
                                f"{r.status or '?'} · {r.year or '?'} · "
                                f"MD {r.mangadex_id[:8]}..."
                            ),
                            cover_url=r.cover_url,
                            payload=r,
                        )
                    )
            if not self.isInterruptionRequested():
                self.finished_ok.emit(hits)
        except (MalApiError, MangaDexError, Exception) as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))


class SearchDialog(QDialog):
    def __init__(
        self,
        repo: Repository,
        *,
        kind: str,
        statuses: list[tuple[str, str]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.kind = kind
        self.hits: list[SearchHit] = []
        self._worker: SearchWorker | None = None
        self.setWindowTitle("Search anime" if kind == "anime" else "Search manga")
        self.resize(720, 560)

        root = QVBoxLayout(self)
        root.addWidget(section_label("Fuzzy search"))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Type a title...")
        self.query.returnPressed.connect(self.do_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self.do_search)
        row.addWidget(self.query, 1)
        row.addWidget(self.search_btn)
        root.addLayout(row)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.on_select)
        body.addWidget(self.list, 3)

        side = QVBoxLayout()
        self.cover = CoverLabel(160, 220)
        self.title = QLabel("Select a result")
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("")
        self.meta.setObjectName("previewMeta")
        self.meta.setWordWrap(True)
        side.addWidget(self.cover)
        side.addWidget(self.title)
        side.addWidget(self.meta)
        side.addWidget(section_label("Add as"))
        self.status = QComboBox()
        for key, label in statuses:
            self.status.addItem(label, key)
        side.addWidget(self.status)
        self.add_btn = QPushButton("Add to list")
        self.add_btn.setObjectName("primary")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.add_selected)
        side.addWidget(self.add_btn)
        side.addStretch()
        body.addLayout(side, 2)
        root.addLayout(body, 1)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setObjectName("status")
        root.addWidget(self.status_lbl)

        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def do_search(self) -> None:
        q = self.query.text().strip()
        if not q:
            return
        profile = self.repo.get_profile()
        if self.kind == "anime" and not profile.mal_client_id:
            QMessageBox.warning(
                self,
                "MAL Client ID required",
                "Add your MAL API Client ID in Settings to search anime.\n"
                "Create one at https://myanimelist.net/apiconfig",
            )
            return
        stop_worker(self._worker, 3000)
        self._worker = None
        self.search_btn.setEnabled(False)
        self.status_lbl.setText("Searching...")
        self.list.clear()
        self.hits = []
        worker = SearchWorker(self.kind, q, profile.mal_client_id, parent=self)
        self._worker = worker
        worker.finished_ok.connect(self.on_results)
        worker.failed.connect(self.on_fail)
        worker.finished.connect(self._on_worker_finished)
        start_worker(worker, self)

    def _on_worker_finished(self) -> None:
        self.search_btn.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        stop_worker(self._worker, 5000)
        self._worker = None
        super().closeEvent(event)

    def reject(self) -> None:
        stop_worker(self._worker, 5000)
        self._worker = None
        super().reject()

    def accept(self) -> None:
        stop_worker(self._worker, 5000)
        self._worker = None
        super().accept()

    @Slot(object)
    def on_results(self, hits: object) -> None:
        if not isinstance(hits, list):
            return
        self.hits = hits
        self.list.clear()
        for h in hits:
            self.list.addItem(QListWidgetItem(f"{h.title}\n{h.meta}"))
        self.status_lbl.setText(f"{len(hits)} result(s)")

    @Slot(str)
    def on_fail(self, err: str) -> None:
        self.status_lbl.setText(err)
        QMessageBox.warning(self, "Search failed", err)

    def on_select(self, row: int) -> None:
        if row < 0 or row >= len(self.hits):
            self.add_btn.setEnabled(False)
            return
        h = self.hits[row]
        self.title.setText(h.title)
        self.meta.setText(h.meta)
        self.cover.set_cover_url(h.cover_url)
        self.add_btn.setEnabled(True)

    def add_selected(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        h = self.hits[row]
        status = self.status.currentData()
        try:
            if h.kind == "anime":
                r: MalAnimeResult = h.payload  # type: ignore[assignment]
                anime_id = self.repo.upsert_anime(
                    mal_id=r.mal_id,
                    title=r.title,
                    title_english=r.title_english,
                    media_type=r.media_type,
                    airing_status=r.airing_status,
                    episodes=r.episodes,
                    mean_score=r.mean_score,
                    cover_url=r.cover_url,
                    synopsis=r.synopsis,
                    cached=r.raw,
                )
                self.repo.set_user_anime(anime_id, list_status=status)
            else:
                r2: MangaDexResult = h.payload  # type: ignore[assignment]
                manga_id = self.repo.upsert_manga(
                    mangadex_id=r2.mangadex_id,
                    title=r2.title,
                    title_english=r2.title_english,
                    status=r2.status,
                    year=r2.year,
                    cover_url=r2.cover_url,
                    synopsis=r2.synopsis,
                    cached=r2.raw,
                )
                self.repo.set_user_manga(manga_id, list_status=status)
            self.accept()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not add", str(e))
