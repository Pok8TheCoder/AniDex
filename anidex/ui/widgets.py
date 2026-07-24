from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from anidex.services.cover_cache import cache_cover
from anidex.ui.threading_util import start_worker


class CoverWorker(QThread):
    ready = Signal(str, str)  # url, path

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        path = cache_cover(self.url)
        if path and not self.isInterruptionRequested():
            self.ready.emit(self.url, str(path))


class CoverLabel(QLabel):
    def __init__(self, width: int = 120, height: int = 170) -> None:
        super().__init__()
        self._w = width
        self._h = height
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background:#0c0b09; border:1px solid #2c2823; border-radius:10px; color:#6b635a;"
        )
        self.setText("No cover")
        self._url: str | None = None
        self._req_id = 0

    def set_cover_url(self, url: str | None) -> None:
        self._req_id += 1
        req = self._req_id
        self._url = url
        if not url:
            self.setPixmap(QPixmap())
            self.setText("No cover")
            return
        # Show cached file immediately if we already have it
        from anidex.services.cover_cache import cover_cache_path

        cached = cover_cache_path(url)
        if cached is not None and cached.exists():
            self.set_cover_path(cached)
        else:
            self.setText("...")
            self.setPixmap(QPixmap())

        worker = CoverWorker(url, parent=self)
        worker.ready.connect(
            lambda u, p, rid=req: self._on_ready(u, p, rid)
        )
        start_worker(worker, self)

    def set_cover_path(self, path: str | Path | None) -> None:
        if not path:
            self.setPixmap(QPixmap())
            self.setText("No cover")
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            self.setText("No cover")
            return
        self.setText("")
        self.setPixmap(
            pix.scaled(
                self._w,
                self._h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_ready(self, url: str, path: str, req_id: int) -> None:
        # Ignore stale downloads from previous selections
        if req_id != self._req_id or url != self._url:
            return
        self.set_cover_path(path)


class StatusFilterBar(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]]) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (key, label) in enumerate(options):
            btn = QPushButton(label)
            btn.setObjectName("chip")
            btn.setCheckable(True)
            btn.setProperty("status_key", key)
            if i == 0:
                btn.setChecked(True)
            self.group.addButton(btn)
            layout.addWidget(btn)
        layout.addStretch()
        self.group.buttonClicked.connect(self._emit)

    def current(self) -> str:
        btn = self.group.checkedButton()
        return btn.property("status_key") if btn else "all"

    def _emit(self, btn: QPushButton) -> None:
        self.changed.emit(btn.property("status_key"))


class EmptyState(QFrame):
    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.setObjectName("previewFrame")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel(title)
        t.setObjectName("hero")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b = QLabel(body)
        b.setObjectName("body")
        b.setWordWrap(True)
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(b)


def section_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("sectionTitle")
    return lab


class PosterCard(QFrame):
    clicked = Signal(int)

    def __init__(self, entry_id: int, title: str, meta: str, cover_url: str | None) -> None:
        super().__init__()
        self.entry_id = entry_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("panel")
        self.setFixedWidth(148)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.cover = CoverLabel(132, 186)
        self.cover.set_cover_url(cover_url)
        title_l = QLabel(title)
        title_l.setWordWrap(True)
        title_l.setStyleSheet("font-weight:600; font-size:12px;")
        title_l.setMaximumHeight(36)
        meta_l = QLabel(meta)
        meta_l.setObjectName("previewMeta")
        meta_l.setWordWrap(True)
        lay.addWidget(self.cover)
        lay.addWidget(title_l)
        lay.addWidget(meta_l)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.entry_id)
        super().mousePressEvent(event)
