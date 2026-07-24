from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from anidex.db import init_db
from anidex.db.repositories import Repository
from anidex.ui.anime_library import AnimeLibrary
from anidex.ui.anime_watch import AnimeWatchView
from anidex.ui.manga_library import MangaLibrary
from anidex.ui.onboarding import OnboardingView
from anidex.ui.settings import SettingsView


def make_app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor("#141210"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#d97745"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, 48, 48, 14, 14)
    painter.setPen(QColor("#1a100a"))
    font = QFont("Segoe UI", 18, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "LF")
    painter.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AniDex")
        self.resize(1280, 820)
        self.setMinimumSize(1024, 680)

        self.conn = init_db()
        self.repo = Repository(self.conn)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        brand = QLabel("AniDex")
        brand.setObjectName("brand")
        self.subtitle = QLabel("Your anime & manga library — local first")
        self.subtitle.setObjectName("subtitle")
        titles.addWidget(brand)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        root.addLayout(header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.onboarding = OnboardingView(self.repo)
        self.onboarding.finished.connect(self.show_app)
        self.stack.addWidget(self.onboarding)

        self.app_page = QWidget()
        app_l = QVBoxLayout(self.app_page)
        app_l.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.anime = AnimeLibrary(self.repo)
        self.manga = MangaLibrary(self.repo)
        self.settings = SettingsView(self.repo)
        self.tabs.addTab(self.anime, "Anime")
        self.tabs.addTab(self.manga, "Manga")
        self.tabs.addTab(self.settings, "Settings")
        app_l.addWidget(self.tabs)
        self.stack.addWidget(self.app_page)

        self.watch = AnimeWatchView(self.repo)
        self.stack.addWidget(self.watch)

        self.anime.status_message.connect(self.show_status)
        self.anime.open_watch.connect(self.open_watch)
        self.watch.status_message.connect(self.show_status)
        self.watch.back_requested.connect(self.close_watch)
        self.manga.status_message.connect(self.show_status)
        self.settings.status_message.connect(self.show_status)
        self.settings.lists_changed.connect(self.refresh_libraries)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.show_status("Ready")

        if self.repo.get_profile().onboarded:
            self.show_app()
        else:
            self.stack.setCurrentWidget(self.onboarding)

    def show_app(self) -> None:
        p = self.repo.get_profile()
        self.subtitle.setText(
            f"Hey {p.display_name or 'Otaku'} — lists stay on this PC"
        )
        self.refresh_libraries()
        self.settings.reload()
        self.stack.setCurrentWidget(self.app_page)

    def refresh_libraries(self) -> None:
        self.anime.refresh()
        self.manga.refresh()

    def show_status(self, text: str) -> None:
        self.status.showMessage(text, 8000)

    def open_watch(self, user_anime_id: int) -> None:
        self.tabs.setCurrentWidget(self.anime)
        self.watch.open_anime(user_anime_id)
        self.stack.setCurrentWidget(self.watch)

    def close_watch(self) -> None:
        self.watch.stop_playback()
        self.stack.setCurrentWidget(self.app_page)
        self.anime.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802
        from anidex.services.pahe_browser import close_browser
        from anidex.ui.threading_util import stop_worker

        self.watch.stop_playback()
        close_browser()
        for owner in (self, self.anime, self.manga, self.settings, self.watch):
            for w in list(getattr(owner, "_alive_workers", [])):
                stop_worker(w, 2000)
        super().closeEvent(event)
