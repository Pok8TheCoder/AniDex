from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from localfun.db.repositories import MangaEntry, Repository
from localfun.db.schema import MANGA_STATUSES
from localfun.paths import output_dir
from localfun.ui.search_dialog import SearchDialog
from localfun.ui.threading_util import start_worker
from localfun.ui.widgets import EmptyState, PosterCard, StatusFilterBar, section_label

STATUS_LABELS = [
    ("all", "All"),
    ("reading", "Reading"),
    ("completed", "Completed"),
    ("on_hold", "On Hold"),
    ("dropped", "Dropped"),
    ("plan_to_read", "Plan to Read"),
]


class PdfWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, chapter_url: str, out_root: Path) -> None:
        super().__init__()
        self.chapter_url = chapter_url
        self.out_root = out_root

    def run(self) -> None:
        try:
            from mangadex_to_pdf import export_chapter

            pdf = export_chapter(self.chapter_url, out_root=self.out_root)
            self.done.emit(str(pdf))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class MangaLibrary(QWidget):
    status_message = Signal(str)

    def __init__(self, repo: Repository) -> None:
        super().__init__()
        self.repo = repo
        self._current: MangaEntry | None = None
        self._pdf_worker: PdfWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.filters = StatusFilterBar(STATUS_LABELS)
        self.filters.changed.connect(lambda _s: self.refresh())
        top.addWidget(self.filters, 1)
        self.search_btn = QPushButton("Search MangaDex…")
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
            "No manga yet",
            "Search MangaDex and add series to your local list.",
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
        self.detail_title = QLabel("Select a manga")
        self.detail_title.setObjectName("previewTitle")
        self.detail_title.setWordWrap(True)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("previewMeta")
        self.detail_meta.setWordWrap(True)
        self.synopsis = QLabel("")
        self.synopsis.setObjectName("body")
        self.synopsis.setWordWrap(True)
        self.synopsis.setMaximumHeight(120)
        rl.addWidget(self.detail_title)
        rl.addWidget(self.detail_meta)
        rl.addWidget(self.synopsis)

        rl.addWidget(section_label("Your progress"))
        form = QHBoxLayout()
        self.status_box = QComboBox()
        for s in MANGA_STATUSES:
            self.status_box.addItem(s.replace("_", " ").title(), s)
        self.progress = QSpinBox()
        self.progress.setRange(0, 9999)
        self.score = QSpinBox()
        self.score.setRange(0, 10)
        form.addWidget(QLabel("Status"))
        form.addWidget(self.status_box, 1)
        form.addWidget(QLabel("Ch"))
        form.addWidget(self.progress)
        form.addWidget(QLabel("Score"))
        form.addWidget(self.score)
        rl.addLayout(form)

        rl.addWidget(section_label("Notes"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)
        rl.addWidget(self.notes)

        rl.addWidget(section_label("Chapter PDF (MangaDex URL)"))
        self.chapter_url = QPlainTextEdit()
        self.chapter_url.setPlaceholderText(
            "Paste a MangaDex chapter URL to export as original-quality PDF…"
        )
        self.chapter_url.setFixedHeight(60)
        rl.addWidget(self.chapter_url)
        pdf_row = QHBoxLayout()
        self.pdf_btn = QPushButton("Download chapter PDF")
        self.pdf_btn.setObjectName("primary")
        self.pdf_btn.clicked.connect(self.download_chapter)
        self.open_md_btn = QPushButton("Open on MangaDex")
        self.open_md_btn.clicked.connect(self.open_mangadex)
        pdf_row.addWidget(self.pdf_btn)
        pdf_row.addWidget(self.open_md_btn)
        rl.addLayout(pdf_row)

        rl.addWidget(section_label("Local folder"))
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
        actions.addWidget(self.save_btn)
        actions.addWidget(self.remove_btn)
        rl.addLayout(actions)
        rl.addStretch()
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 3)

        self._set_detail_enabled(False)
        self.refresh()

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (
            self.status_box,
            self.progress,
            self.score,
            self.notes,
            self.save_btn,
            self.remove_btn,
            self.pdf_btn,
            self.open_md_btn,
            self.chapter_url,
        ):
            w.setEnabled(on)

    def open_search(self) -> None:
        dlg = SearchDialog(
            self.repo,
            kind="manga",
            statuses=[(k, lab) for k, lab in STATUS_LABELS if k != "all"],
            parent=self,
        )
        if dlg.exec():
            self.refresh()
            self.status_message.emit("Added manga to your list")

    def refresh(self) -> None:
        status = self.filters.current()
        entries = self.repo.list_user_manga(None if status == "all" else status)
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        if not entries:
            self.scroll.hide()
            self.empty.show()
            self._current = None
            self.detail_title.setText("Select a manga")
            self.detail_meta.setText("")
            self.synopsis.setText("")
            self._set_detail_enabled(False)
            return
        self.empty.hide()
        self.scroll.show()
        cols = 4
        for i, e in enumerate(entries):
            meta = f"{e.list_status.replace('_', ' ')} · ch {e.progress}"
            card = PosterCard(e.user_manga_id, e.title_english or e.title, meta, e.cover_url)
            card.clicked.connect(self.show_entry)
            self.grid.addWidget(card, i // cols, i % cols)

    def show_entry(self, user_manga_id: int) -> None:
        e = self.repo.get_user_manga(user_manga_id)
        if not e:
            return
        self._current = e
        self._set_detail_enabled(True)
        self.detail_title.setText(e.title_english or e.title)
        bits = [e.status or "?", str(e.year or "?")]
        if e.mangadex_id:
            bits.append(e.mangadex_id[:8] + "…")
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
        self.repo.update_user_manga_fields(
            self._current.user_manga_id,
            list_status=self.status_box.currentData(),
            progress=self.progress.value(),
            score=self.score.value(),
            notes=self.notes.toPlainText(),
            local_folder="" if self.folder.text() == "—" else self.folder.text(),
        )
        self.status_message.emit("Saved")
        uid = self._current.user_manga_id
        self.refresh()
        self.show_entry(uid)

    def remove_current(self) -> None:
        if not self._current:
            return
        if (
            QMessageBox.question(self, "Remove", "Remove from your local list?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.repo.remove_user_manga(self._current.user_manga_id)
        self._current = None
        self.refresh()
        self.status_message.emit("Removed")

    def browse_folder(self) -> None:
        if not self._current:
            return
        path = QFileDialog.getExistingDirectory(self, "Local manga folder")
        if path:
            self.folder.setText(path)
            self.repo.update_user_manga_fields(
                self._current.user_manga_id, local_folder=path
            )

    def open_mangadex(self) -> None:
        if self._current and self._current.mangadex_id:
            QDesktopServices.openUrl(
                QUrl(f"https://mangadex.org/title/{self._current.mangadex_id}")
            )

    def download_chapter(self) -> None:
        url = self.chapter_url.toPlainText().strip().splitlines()
        url = next((u.strip() for u in url if u.strip()), "")
        if not url:
            QMessageBox.information(self, "Chapter URL", "Paste a MangaDex chapter URL first.")
            return
        out = output_dir() / "manga"
        if self._current and self._current.local_folder:
            out = Path(self._current.local_folder)
        self.pdf_btn.setEnabled(False)
        self.status_message.emit("Downloading chapter PDF…")
        worker = PdfWorker(url, out)
        self._pdf_worker = worker
        worker.done.connect(self.on_pdf_done)
        worker.failed.connect(self.on_pdf_fail)
        worker.finished.connect(lambda: self.pdf_btn.setEnabled(True))
        start_worker(worker, self)

    @Slot(str)
    def on_pdf_done(self, path: str) -> None:
        self.status_message.emit(f"Saved {path}")
        QMessageBox.information(self, "PDF ready", f"Saved to:\n{path}")
        if self._current and not self._current.local_folder:
            folder = str(Path(path).parent)
            self.folder.setText(folder)
            self.repo.update_user_manga_fields(
                self._current.user_manga_id, local_folder=folder
            )

    @Slot(str)
    def on_pdf_fail(self, err: str) -> None:
        QMessageBox.critical(self, "PDF failed", err)
        self.status_message.emit("PDF failed")
