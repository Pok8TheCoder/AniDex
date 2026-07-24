from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from anidex.db.repositories import AnimeEntry, Repository
from anidex.db.schema import ANIME_STATUSES
from anidex.paths import output_dir
from anidex.ui.search_dialog import SearchDialog
from anidex.ui.threading_util import start_worker
from anidex.ui.widgets import EmptyState, PosterCard, StatusFilterBar, section_label

STATUS_LABELS = [
    ("all", "All"),
    ("watching", "Watching"),
    ("completed", "Completed"),
    ("on_hold", "On Hold"),
    ("dropped", "Dropped"),
    ("plan_to_watch", "Plan to Watch"),
]


class AnimeDownloadWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    meta = Signal(object)
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        out_dir: Path,
        resolution: int,
        audio: str,
    ) -> None:
        super().__init__()
        self.url = url
        self.out_dir = out_dir
        self.resolution = resolution
        self.audio = audio

    def run(self) -> None:
        try:
            from anidex.services.anime_download import download_episode

            result = download_episode(
                self.url,
                out_dir=self.out_dir,
                resolution=self.resolution,
                audio=self.audio,
                on_log=lambda m: self.log.emit(m),
                on_progress=lambda done, total: self.progress.emit(done, total),
                on_meta=lambda m: self.meta.emit(m),
            )
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class AnimeLibrary(QWidget):
    status_message = Signal(str)
    open_watch = Signal(int)

    def __init__(self, repo: Repository) -> None:
        super().__init__()
        self.repo = repo
        self._cards: list[PosterCard] = []
        self._current: AnimeEntry | None = None
        self._dl_worker: AnimeDownloadWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        dl_panel = QFrame()
        dl_panel.setObjectName("panel")
        dpl = QVBoxLayout(dl_panel)
        dpl.setContentsMargins(12, 12, 12, 12)
        dpl.setSpacing(8)
        dpl.addWidget(section_label("Download episode (AnimePahe)"))
        tip = QLabel(
            "Paste an AnimePahe play URL. AniDex reads the anime + episode from "
            "the page, names the file, and can add/update your list. "
            "First time: Settings → Pass AnimePahe Cloudflare."
        )
        tip.setObjectName("previewMeta")
        tip.setWordWrap(True)
        dpl.addWidget(tip)
        self.dl_url = QPlainTextEdit()
        self.dl_url.setPlaceholderText(
            "https://animepahe.pw/play/<anime-session>/<episode-session>"
        )
        self.dl_url.setFixedHeight(52)
        dpl.addWidget(self.dl_url)
        self.dl_meta = QLabel("")
        self.dl_meta.setObjectName("previewMeta")
        self.dl_meta.setWordWrap(True)
        dpl.addWidget(self.dl_meta)
        dl_row = QHBoxLayout()
        self.dl_res = QComboBox()
        for r in (1080, 720, 480, 360):
            self.dl_res.addItem(f"{r}p", r)
        self.dl_audio = QComboBox()
        self.dl_audio.addItem("JPN", "jpn")
        self.dl_audio.addItem("ENG", "eng")
        self.dl_link = QCheckBox("Add / link to my list")
        self.dl_link.setChecked(True)
        self.dl_link.setToolTip(
            "Match or create a list entry, set the local folder, and bump "
            "watched progress to this episode."
        )
        self.dl_btn = QPushButton("Download MP4")
        self.dl_btn.setObjectName("primary")
        self.dl_btn.clicked.connect(self.download_from_url)
        dl_row.addWidget(QLabel("Quality"))
        dl_row.addWidget(self.dl_res)
        dl_row.addWidget(QLabel("Audio"))
        dl_row.addWidget(self.dl_audio)
        dl_row.addWidget(self.dl_link)
        dl_row.addStretch()
        dl_row.addWidget(self.dl_btn)
        dpl.addLayout(dl_row)
        self.dl_status = QLabel("")
        self.dl_status.setObjectName("previewMeta")
        self.dl_status.setWordWrap(True)
        dpl.addWidget(self.dl_status)
        self.dl_progress = QProgressBar()
        self.dl_progress.setRange(0, 100)
        self.dl_progress.setValue(0)
        self.dl_progress.setTextVisible(True)
        self.dl_progress.setFormat("%p%")
        self.dl_progress.hide()
        dpl.addWidget(self.dl_progress)
        root.addWidget(dl_panel)

        top = QHBoxLayout()
        self.filters = StatusFilterBar(STATUS_LABELS)
        self.filters.changed.connect(lambda _s: self.refresh())
        top.addWidget(self.filters, 1)
        self.search_btn = QPushButton("Search MAL…")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self.open_search)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.search_btn)
        top.addWidget(self.refresh_btn)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        left = QFrame()
        left.setObjectName("panel")
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        left_l.addWidget(self.scroll)
        self.empty = EmptyState(
            "No anime yet",
            "Search MyAnimeList and add titles to your local list.",
        )
        left_l.addWidget(self.empty)
        self.empty.hide()
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("panel")
        right.setMinimumWidth(300)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 16, 16, 16)
        rl.setSpacing(8)
        rl.addWidget(section_label("Details"))
        self.detail_title = QLabel("Select an anime")
        self.detail_title.setObjectName("previewTitle")
        self.detail_title.setWordWrap(True)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("previewMeta")
        self.detail_meta.setWordWrap(True)
        self.synopsis = QLabel("")
        self.synopsis.setObjectName("body")
        self.synopsis.setWordWrap(True)
        self.synopsis.setMaximumHeight(140)
        rl.addWidget(self.detail_title)
        rl.addWidget(self.detail_meta)
        rl.addWidget(self.synopsis)

        rl.addWidget(section_label("Your progress"))
        form = QHBoxLayout()
        self.status_box = QComboBox()
        for s in ANIME_STATUSES:
            self.status_box.addItem(s.replace("_", " ").title(), s)
        self.progress = QSpinBox()
        self.progress.setRange(0, 9999)
        self.score = QSpinBox()
        self.score.setRange(0, 10)
        form.addWidget(QLabel("Status"))
        form.addWidget(self.status_box, 1)
        form.addWidget(QLabel("Eps"))
        form.addWidget(self.progress)
        form.addWidget(QLabel("Score"))
        form.addWidget(self.score)
        rl.addLayout(form)

        rl.addWidget(section_label("Notes"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(80)
        rl.addWidget(self.notes)

        rl.addWidget(section_label("Local folder (future downloads)"))
        folder_row = QHBoxLayout()
        self.folder = QLabel("—")
        self.folder.setObjectName("previewMeta")
        self.folder.setWordWrap(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_folder)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        rl.addLayout(folder_row)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_current)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self.remove_current)
        self.mal_btn = QPushButton("Open on MAL")
        self.mal_btn.clicked.connect(self.open_mal)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.remove_btn)
        actions.addWidget(self.mal_btn)
        rl.addLayout(actions)

        self.watch_btn = QPushButton("Watch on AnimePahe…")
        self.watch_btn.setObjectName("primary")
        self.watch_btn.clicked.connect(self.open_watch_view)
        rl.addWidget(self.watch_btn)
        rl.addStretch()
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 3)

        self._set_detail_enabled(False)
        self.refresh()

    def download_from_url(self) -> None:
        raw = self.dl_url.toPlainText().strip().splitlines()
        url = next((u.strip() for u in raw if u.strip()), "")
        if not url:
            QMessageBox.information(
                self, "URL required", "Paste an AnimePahe play URL first."
            )
            return
        if self._dl_worker and self._dl_worker.isRunning():
            return
        out = output_dir() / "anime"
        # Prefer selected entry folder only when linking is off; linked downloads
        # create/use a per-show folder under output/anime.
        if (
            not self.dl_link.isChecked()
            and self._current
            and self._current.local_folder
        ):
            out = Path(self._current.local_folder)
        self.dl_btn.setEnabled(False)
        self.dl_meta.setText("")
        self.dl_progress.setValue(0)
        self.dl_progress.setRange(0, 100)
        self.dl_progress.setFormat("Starting…")
        self.dl_progress.show()
        self.dl_status.setText("Starting anime download…")
        self.status_message.emit("Starting anime download...")
        worker = AnimeDownloadWorker(
            url,
            out,
            int(self.dl_res.currentData()),
            str(self.dl_audio.currentData()),
        )
        self._dl_worker = worker
        worker.log.connect(self.on_download_log)
        worker.progress.connect(self.on_download_progress)
        worker.meta.connect(self.on_download_meta)
        worker.done.connect(self.on_download_done)
        worker.failed.connect(self.on_download_fail)
        worker.finished.connect(self.on_download_finished)
        start_worker(worker, self)

    @Slot(str)
    def on_download_log(self, msg: str) -> None:
        self.dl_status.setText(msg)
        self.status_message.emit(msg)

    @Slot(object)
    def on_download_meta(self, meta) -> None:
        try:
            self.dl_meta.setText(f"Detected: {meta.display}")
        except Exception:
            self.dl_meta.setText("Detected episode metadata")

    @Slot(int, int)
    def on_download_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.dl_progress.setRange(0, total)
        self.dl_progress.setValue(min(done, total))
        self.dl_progress.setFormat("%v / %m  (%p%)")

    def on_download_finished(self) -> None:
        self.dl_btn.setEnabled(True)

    @Slot(object)
    def on_download_done(self, result) -> None:
        from anidex.services.anime_download import DownloadResult

        if not isinstance(result, DownloadResult):
            path = Path(str(result))
            meta = None
        else:
            path = Path(result.path)
            meta = result.meta

        self.dl_progress.setRange(0, 100)
        self.dl_progress.setValue(100)
        self.dl_progress.setFormat("Done")
        self.dl_status.setText(f"Saved {path}")
        self.status_message.emit(f"Saved {path}")

        linked = None
        if self.dl_link.isChecked() and meta and meta.anime_title:
            ep = 0
            if meta.episode is not None:
                ep = int(meta.episode)
                if meta.episode2 and meta.episode2 > meta.episode:
                    ep = int(meta.episode2)
            linked = self.repo.ensure_local_anime(
                meta.anime_title,
                episodes=meta.total_episodes,
                cover_url=meta.snapshot or None,
                pahe_session=meta.anime_session,
                list_status="watching",
                progress=ep,
                local_folder=str(path.parent),
            )
            self.refresh()
            self.show_entry(linked.user_anime_id)
        elif self._current and not self._current.local_folder:
            folder = str(path.parent)
            self.folder.setText(folder)
            self.repo.update_user_anime_fields(
                self._current.user_anime_id, local_folder=folder
            )

        msg = f"Saved to:\n{path}"
        if meta:
            msg = f"{meta.display}\n\n{msg}"
        if linked:
            msg += f"\n\nLinked to list: {linked.title_english or linked.title}"
            if meta and meta.episode is not None:
                msg += f" (progress → {linked.progress})"
        QMessageBox.information(self, "Download complete", msg)

    @Slot(str)
    def on_download_fail(self, err: str) -> None:
        self.dl_progress.setFormat("Failed")
        self.dl_status.setText(err.splitlines()[0][:200])
        QMessageBox.critical(self, "Download failed", err)
        self.status_message.emit("Download failed")

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (
            self.status_box,
            self.progress,
            self.score,
            self.notes,
            self.save_btn,
            self.remove_btn,
            self.mal_btn,
            self.watch_btn,
        ):
            w.setEnabled(on)

    def open_watch_view(self) -> None:
        if self._current:
            self.open_watch.emit(self._current.user_anime_id)

    def open_search(self) -> None:
        dlg = SearchDialog(
            self.repo,
            kind="anime",
            statuses=[(k, lab) for k, lab in STATUS_LABELS if k != "all"],
            parent=self,
        )
        if dlg.exec():
            self.refresh()
            self.status_message.emit("Added anime to your list")

    def refresh(self) -> None:
        status = self.filters.current()
        entries = self.repo.list_user_anime(None if status == "all" else status)
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()
        if not entries:
            self.scroll.hide()
            self.empty.show()
            self._current = None
            self.detail_title.setText("Select an anime")
            self.detail_meta.setText("")
            self.synopsis.setText("")
            self._set_detail_enabled(False)
            return
        self.empty.hide()
        self.scroll.show()
        cols = 4
        for i, e in enumerate(entries):
            meta = f"{e.list_status.replace('_', ' ')} · {e.progress}"
            if e.episodes:
                meta += f"/{e.episodes}"
            card = PosterCard(e.user_anime_id, e.title_english or e.title, meta, e.cover_url)
            card.clicked.connect(self.show_entry)
            self.grid.addWidget(card, i // cols, i % cols)
            self._cards.append(card)

    def show_entry(self, user_anime_id: int) -> None:
        e = self.repo.get_user_anime(user_anime_id)
        if not e:
            return
        self._current = e
        self._set_detail_enabled(True)
        self.detail_title.setText(e.title_english or e.title)
        bits = [
            e.media_type or "?",
            e.airing_status or "?",
            f"MAL {e.mal_id}" if e.mal_id else "no MAL id",
        ]
        if e.mean_score:
            bits.append(f"MAL score {e.mean_score:.2f}")
        self.detail_meta.setText(" · ".join(bits))
        self.synopsis.setText(e.synopsis or "No synopsis cached.")
        idx = self.status_box.findData(e.list_status)
        if idx >= 0:
            self.status_box.setCurrentIndex(idx)
        self.progress.setValue(e.progress)
        self.score.setValue(e.score)
        self.notes.setPlainText(e.notes)
        self.folder.setText(e.local_folder or "—")

    def save_current(self) -> None:
        if not self._current:
            return
        self.repo.update_user_anime_fields(
            self._current.user_anime_id,
            list_status=self.status_box.currentData(),
            progress=self.progress.value(),
            score=self.score.value(),
            notes=self.notes.toPlainText(),
            local_folder="" if self.folder.text() == "—" else self.folder.text(),
        )
        self.status_message.emit("Saved")
        self.refresh()
        self.show_entry(self._current.user_anime_id)

    def remove_current(self) -> None:
        if not self._current:
            return
        if (
            QMessageBox.question(self, "Remove", "Remove from your local list?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.repo.remove_user_anime(self._current.user_anime_id)
        self._current = None
        self.refresh()
        self.status_message.emit("Removed")

    def browse_folder(self) -> None:
        if not self._current:
            return
        path = QFileDialog.getExistingDirectory(self, "Local anime folder")
        if path:
            self.folder.setText(path)
            self.repo.update_user_anime_fields(
                self._current.user_anime_id, local_folder=path
            )

    def open_mal(self) -> None:
        if self._current and self._current.mal_id:
            QDesktopServices.openUrl(
                QUrl(f"https://myanimelist.net/anime/{self._current.mal_id}")
            )
