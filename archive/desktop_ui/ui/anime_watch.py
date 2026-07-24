"""Watch view: AnimePahe seasons/episodes + in-app HLS player."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from localfun.db import open_repo
from localfun.db.repositories import AnimeEntry, LocalMediaEntry, Repository
from localfun.paths import output_dir
from localfun.ui.threading_util import start_worker, stop_worker


@dataclass
class EpisodeRef:
    anime_session: str
    episode_session: str
    episode: float
    title: str
    season_label: str


class PaheLinkWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        user_anime_id: int,
        query: str,
        *,
        chosen_session: str | None = None,
        refresh_only: bool = False,
    ) -> None:
        super().__init__()
        self.user_anime_id = user_anime_id
        self.query = query
        self.chosen_session = chosen_session
        self.refresh_only = refresh_only

    def run(self) -> None:
        try:
            from localfun.services.pahe_catalog import (
                link_anime_to_pahe,
                refresh_pahe_episodes,
            )

            if self.refresh_only:
                result = refresh_pahe_episodes(self.user_anime_id)
            else:
                result = link_anime_to_pahe(
                    self.user_anime_id,
                    self.query,
                    chosen_session=self.chosen_session,
                )
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class StreamResolveWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    def __init__(
        self,
        play_url: str,
        *,
        resolution: int = 1080,
        audio: str = "jpn",
    ) -> None:
        super().__init__()
        self.play_url = play_url
        self.resolution = resolution
        self.audio = audio

    def run(self) -> None:
        try:
            from localfun.services.animepahe import resolve_play_url
            from localfun.services.hls_proxy import start_hls_proxy

            self.status.emit("Resolving stream…")
            resolved = resolve_play_url(
                self.play_url,
                resolution=self.resolution,
                audio=self.audio,
            )
            self.status.emit("Starting local proxy…")
            proxy, local_url = start_hls_proxy(
                resolved.m3u8, referer=resolved.referer
            )
            self.done.emit({"proxy": proxy, "url": local_url, "resolved": resolved})
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class EpisodeDownloadWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        play_url: str,
        out_dir: Path,
        *,
        user_anime_id: int,
        episode_session: str,
        episode: float | None,
        resolution: int = 1080,
        audio: str = "jpn",
    ) -> None:
        super().__init__()
        self.play_url = play_url
        self.out_dir = out_dir
        self.user_anime_id = user_anime_id
        self.episode_session = episode_session
        self.episode = episode
        self.resolution = resolution
        self.audio = audio

    def run(self) -> None:
        try:
            from localfun.services.anime_download import download_episode

            result = download_episode(
                self.play_url,
                out_dir=self.out_dir,
                resolution=self.resolution,
                audio=self.audio,
                on_log=lambda m: self.log.emit(m),
                on_progress=lambda d, t: self.progress.emit(d, t),
            )
            path = Path(result.path)
            conn, repo = open_repo()
            try:
                repo.add_media(
                    user_anime_id=self.user_anime_id,
                    path=str(path),
                    pahe_episode_session=self.episode_session,
                    episode=self.episode,
                    label=path.name,
                    bytes_size=path.stat().st_size if path.is_file() else 0,
                )
                entry = repo.get_user_anime(self.user_anime_id)
                if entry and not entry.local_folder:
                    repo.update_user_anime_fields(
                        self.user_anime_id, local_folder=str(path.parent)
                    )
            finally:
                conn.close()
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ChangeLinkDialog(QDialog):
    def __init__(self, query: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Link AnimePahe title")
        self.resize(520, 420)
        self.chosen_session: str | None = None
        self._hits: list = []

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"Search results for “{query}”"))
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        self.status = QLabel("Searching…")
        root.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._buttons = buttons
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        self._search = _SearchWorker(query)
        self._search.done.connect(self._on_hits)
        self._search.failed.connect(self._on_fail)
        start_worker(self._search, self)

    def _on_hits(self, hits) -> None:
        self._hits = hits
        self.list.clear()
        if not hits:
            self.status.setText("No results.")
            return
        for h in hits:
            label = f"{h.title}"
            bits = []
            if h.type:
                bits.append(h.type)
            if h.year:
                bits.append(str(h.year))
            if h.episodes:
                bits.append(f"{h.episodes} eps")
            if bits:
                label += f"  ({' · '.join(bits)})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, h.session)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.status.setText(f"{len(hits)} result(s)")

    def _on_fail(self, err: str) -> None:
        self.status.setText(err)

    def accept(self) -> None:  # noqa: A003
        item = self.list.currentItem()
        if item:
            self.chosen_session = item.data(Qt.ItemDataRole.UserRole)
        super().accept()


class _SearchWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query = query

    def run(self) -> None:
        try:
            from localfun.services.pahe_catalog import search_anime

            self.done.emit(search_anime(self.query))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


def _fmt_ep(n: float) -> str:
    if float(n).is_integer():
        return str(int(n))
    return str(n).rstrip("0").rstrip(".")


class AnimeWatchView(QWidget):
    status_message = Signal(str)
    back_requested = Signal()

    def __init__(self, repo: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("watchRoot")
        self.repo = repo
        self._entry: AnimeEntry | None = None
        self._proxy = None
        self._media_by_session: dict[str, LocalMediaEntry] = {}
        self._link_worker: PaheLinkWorker | None = None
        self._stream_worker: StreamResolveWorker | None = None
        self._dl_worker: EpisodeDownloadWorker | None = None
        self._selected: EpisodeRef | None = None
        self._season_eps: list[list[EpisodeRef]] = []
        self._ep_buttons: list[QPushButton] = []
        self._resolution = 1080
        self._audio = "jpn"
        self._auto_next = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Breadcrumb
        crumb = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setObjectName("watchGhost")
        self.back_btn.clicked.connect(self.back_requested.emit)
        self.crumb_lbl = QLabel("Home  ·  Watching")
        self.crumb_lbl.setObjectName("watchBreadcrumb")
        self.change_btn = QPushButton("Change link…")
        self.change_btn.setObjectName("watchGhost")
        self.change_btn.clicked.connect(self.change_link)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("watchGhost")
        self.refresh_btn.clicked.connect(self.refresh_catalog)
        crumb.addWidget(self.back_btn)
        crumb.addWidget(self.crumb_lbl, 1)
        crumb.addWidget(self.refresh_btn)
        crumb.addWidget(self.change_btn)
        root.addLayout(crumb)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        # ----- LEFT: episode list -----
        sidebar = QFrame()
        sidebar.setObjectName("watchSidebar")
        sidebar.setFixedWidth(280)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)

        hdr = QLabel("List of episodes:")
        hdr.setObjectName("watchEpHeader")
        sl.addWidget(hdr)

        filt = QHBoxLayout()
        self.season_box = QComboBox()
        self.season_box.setMinimumWidth(120)
        self.season_box.currentIndexChanged.connect(self._on_season_changed)
        self.ep_search = QLineEdit()
        self.ep_search.setPlaceholderText("Number of Ep")
        self.ep_search.setClearButtonEnabled(True)
        self.ep_search.textChanged.connect(self._rebuild_ep_grid)
        filt.addWidget(self.season_box, 1)
        filt.addWidget(self.ep_search, 1)
        sl.addLayout(filt)

        self.link_lbl = QLabel("")
        self.link_lbl.setObjectName("previewMeta")
        self.link_lbl.setWordWrap(True)
        sl.addWidget(self.link_lbl)

        self.ep_scroll = QScrollArea()
        self.ep_scroll.setWidgetResizable(True)
        self.ep_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.ep_host = QWidget()
        self.ep_grid = QGridLayout(self.ep_host)
        self.ep_grid.setContentsMargins(0, 0, 0, 0)
        self.ep_grid.setSpacing(6)
        self.ep_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.ep_scroll.setWidget(self.ep_host)
        sl.addWidget(self.ep_scroll, 1)
        body.addWidget(sidebar)

        # ----- RIGHT: player + servers -----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        chrome = QFrame()
        chrome.setObjectName("playerChrome")
        cl = QVBoxLayout(chrome)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self.video = QVideoWidget()
        self.video.setMinimumHeight(320)
        self.video.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.video.setStyleSheet("background:#000; border-radius:10px;")
        cl.addWidget(self.video, 1)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self._muted = False
        self._vol_before_mute = 80
        self._fullscreen = False

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 10)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setObjectName("previewMeta")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setObjectName("watchSeek")
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.skip_back = QPushButton("−5")
        self.skip_back.setFixedWidth(40)
        self.skip_back.setToolTip("Back 5s (←)")
        self.skip_back.clicked.connect(lambda: self._skip(-5_000))
        self.skip_fwd = QPushButton("+5")
        self.skip_fwd.setFixedWidth(40)
        self.skip_fwd.setToolTip("Forward 5s (→)")
        self.skip_fwd.clicked.connect(lambda: self._skip(5_000))

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFixedWidth(40)
        self.mute_btn.setToolTip("Mute (M)")
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(90)
        self.vol.setValue(80)
        self.vol.setToolTip("Volume (↑ / ↓)")
        self.audio_out.setVolume(0.8)
        self.vol.valueChanged.connect(self._on_vol_changed)

        self.speed_box = QComboBox()
        self.speed_box.setFixedWidth(78)
        self.speed_box.setToolTip("Playback speed")
        for rate, label in (
            (0.5, "0.5×"),
            (0.75, "0.75×"),
            (1.0, "1×"),
            (1.25, "1.25×"),
            (1.5, "1.5×"),
            (1.75, "1.75×"),
            (2.0, "2×"),
        ):
            self.speed_box.addItem(label, rate)
        self.speed_box.setCurrentIndex(2)
        self.speed_box.currentIndexChanged.connect(self._on_speed_changed)

        self.fs_btn = QPushButton("Fullscreen")
        self.fs_btn.setToolTip("Fullscreen (F)")
        self.fs_btn.clicked.connect(self.toggle_fullscreen)

        bar.addWidget(self.play_btn)
        bar.addWidget(self.skip_back)
        bar.addWidget(self.skip_fwd)
        bar.addWidget(self.seek, 1)
        bar.addWidget(self.time_lbl)
        bar.addWidget(self.mute_btn)
        bar.addWidget(self.vol)
        bar.addWidget(self.speed_box)
        bar.addWidget(self.fs_btn)
        cl.addLayout(bar)
        rl.addWidget(chrome, 1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._install_shortcuts()

        # Secondary toolbar
        tools = QHBoxLayout()
        self.player_status = QLabel("Idle")
        self.player_status.setObjectName("previewMeta")
        self.auto_next_btn = QPushButton("Auto Next Off")
        self.auto_next_btn.setObjectName("watchGhost")
        self.auto_next_btn.clicked.connect(self._toggle_auto_next)
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.clicked.connect(self.play_prev)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self.play_next)
        self.dl_btn = QPushButton("Download")
        self.dl_btn.clicked.connect(self.download_selected)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setObjectName("danger")
        self.del_btn.clicked.connect(self.delete_selected)
        tools.addWidget(self.player_status, 1)
        tools.addWidget(self.auto_next_btn)
        tools.addWidget(self.prev_btn)
        tools.addWidget(self.next_btn)
        tools.addWidget(self.dl_btn)
        tools.addWidget(self.del_btn)
        rl.addLayout(tools)

        # Info + audio/quality (one stream link each — pick audio + quality)
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        info = QFrame()
        info.setObjectName("watchInfo")
        info.setMinimumWidth(260)
        info.setMaximumWidth(360)
        il = QVBoxLayout(info)
        il.setContentsMargins(14, 12, 14, 12)
        self.watch_info = QLabel(
            "Select an episode to start watching.\n"
            "Switch SUB/DUB or quality if the current stream fails."
        )
        self.watch_info.setObjectName("watchInfoText")
        self.watch_info.setWordWrap(True)
        il.addWidget(self.watch_info)
        bottom.addWidget(info)

        servers = QVBoxLayout()
        servers.setSpacing(8)
        self._audio_btns: dict[str, QPushButton] = {}
        self._quality_btns: dict[int, QPushButton] = {}

        audio_row = QHBoxLayout()
        audio_lab = QLabel("AUDIO")
        audio_lab.setObjectName("serverLabel")
        audio_row.addWidget(audio_lab)
        for code, label in (("jpn", "SUB"), ("eng", "DUB")):
            btn = QPushButton(label)
            btn.setObjectName("serverChip")
            btn.setProperty("active", "true" if code == "jpn" else "false")
            btn.clicked.connect(lambda _=False, a=code: self._set_audio(a))
            self._audio_btns[code] = btn
            audio_row.addWidget(btn)
        audio_row.addStretch()
        servers.addLayout(audio_row)

        quality_row = QHBoxLayout()
        q_lab = QLabel("QUALITY")
        q_lab.setObjectName("serverLabel")
        q_lab.setMinimumWidth(56)
        quality_row.addWidget(q_lab)
        for res, chip in ((1080, "1080p"), (720, "720p"), (360, "360p")):
            btn = QPushButton(chip)
            btn.setObjectName("serverChip")
            btn.setProperty("active", "true" if res == 1080 else "false")
            btn.clicked.connect(lambda _=False, r=res: self._set_quality(r))
            self._quality_btns[res] = btn
            quality_row.addWidget(btn)
        quality_row.addStretch()
        servers.addLayout(quality_row)

        bottom.addLayout(servers, 1)
        rl.addLayout(bottom)

        self.ep_status = QLabel("")
        self.ep_status.setObjectName("previewMeta")
        self.ep_status.setWordWrap(True)
        rl.addWidget(self.ep_status)

        body.addWidget(right, 1)
        self._set_actions_enabled(False)
        self._refresh_option_styles()

    def _install_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        def sc(key, slot) -> None:
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(slot)

        sc(Qt.Key.Key_Space, self._shortcut_toggle_play)
        sc(Qt.Key.Key_Left, lambda: self._shortcut_skip(-5_000))
        sc(Qt.Key.Key_Right, lambda: self._shortcut_skip(5_000))
        sc(Qt.Key.Key_Up, lambda: self._nudge_vol(5))
        sc(Qt.Key.Key_Down, lambda: self._nudge_vol(-5))
        sc(Qt.Key.Key_M, self.toggle_mute)
        sc(Qt.Key.Key_F, self.toggle_fullscreen)
        sc(Qt.Key.Key_Escape, self._exit_fullscreen_only)

    def _typing_focus(self) -> bool:
        w = self.focusWidget()
        return isinstance(w, (QLineEdit, QComboBox))

    def _shortcut_toggle_play(self) -> None:
        if not self._typing_focus():
            self.toggle_play()

    def _shortcut_skip(self, ms: int) -> None:
        if not self._typing_focus():
            self._skip(ms)
    def open_anime(self, user_anime_id: int) -> None:
        entry = self.repo.get_user_anime(user_anime_id)
        if not entry:
            return
        self.stop_playback()
        self._entry = entry
        title = entry.title_english or entry.title
        self.crumb_lbl.setText(f"Home  ·  Watching {title}")
        self.ep_status.setText("")
        self.watch_info.setText(
            "Select an episode to start watching.\n"
            "Switch SUB/DUB or quality if the current stream fails."
        )
        self.reload_catalog(auto_link=True)

    def reload_catalog(self, *, auto_link: bool = False) -> None:
        if not self._entry:
            return
        link = self.repo.get_pahe_link(self._entry.user_anime_id)
        self._media_by_session = {
            m.pahe_episode_session: m
            for m in self.repo.list_media(self._entry.user_anime_id)
            if m.pahe_episode_session
        }
        self._season_eps = []
        self.season_box.blockSignals(True)
        self.season_box.clear()
        self.season_box.blockSignals(False)

        if not link:
            self.link_lbl.setText("Not linked to AnimePahe yet.")
            self._rebuild_ep_grid()
            if auto_link:
                self._start_link(None)
            return

        self.link_lbl.setText(f"Linked: {link.pahe_title}")
        seasons = self.repo.list_pahe_seasons(link.id)
        if not seasons and auto_link:
            self._start_link(None)
            return

        self.season_box.blockSignals(True)
        for season in seasons:
            label = season.label or "Season"
            if season.year:
                label = f"{label} ({season.year})"
            eps = [
                EpisodeRef(
                    anime_session=season.pahe_session,
                    episode_session=ep.episode_session,
                    episode=ep.episode,
                    title=ep.title,
                    season_label=label,
                )
                for ep in self.repo.list_pahe_episodes(season.id)
            ]
            self._season_eps.append(eps)
            count = len(eps) or season.episode_count
            self.season_box.addItem(f"{label} · {count} eps", len(self._season_eps) - 1)
        self.season_box.blockSignals(False)

        if self.season_box.count() == 0 and auto_link:
            self._start_link(None)
            return
        self._rebuild_ep_grid()
        self._sync_selection_ui()

    def _current_episodes(self) -> list[EpisodeRef]:
        idx = self.season_box.currentData()
        if idx is None or idx < 0 or idx >= len(self._season_eps):
            return []
        return self._season_eps[int(idx)]

    def _on_season_changed(self, _index: int = 0) -> None:
        self._rebuild_ep_grid()

    def _rebuild_ep_grid(self) -> None:
        while self.ep_grid.count():
            item = self.ep_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._ep_buttons.clear()

        q = self.ep_search.text().strip()
        cols = 4
        row = col = 0
        for ep in self._current_episodes():
            label = _fmt_ep(ep.episode)
            if q and q not in label and q not in (ep.title or "").casefold():
                try:
                    if float(q) != float(ep.episode):
                        continue
                except ValueError:
                    continue
            btn = QPushButton(label)
            btn.setObjectName("epTile")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            media = self._media_by_session.get(ep.episode_session)
            downloaded = bool(media and Path(media.path).is_file())
            btn.setProperty("downloaded", "true" if downloaded else "false")
            btn.setProperty("active", "false")
            btn.setToolTip(
                f"E{label}"
                + (f" — {ep.title}" if ep.title else "")
                + (" · Downloaded" if downloaded else "")
            )
            btn.clicked.connect(lambda _=False, e=ep: self._select_episode(e, play=True))
            self.ep_grid.addWidget(btn, row, col)
            self._ep_buttons.append(btn)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        self._highlight_active_ep()

    def _select_episode(self, ep: EpisodeRef, *, play: bool = False) -> None:
        self._selected = ep
        self._sync_selection_ui()
        self._highlight_active_ep()
        if play:
            self.play_selected()

    def _highlight_active_ep(self) -> None:
        for i, ep in enumerate(self._filtered_episodes()):
            if i >= len(self._ep_buttons):
                break
            btn = self._ep_buttons[i]
            media = self._media_by_session.get(ep.episode_session)
            active = bool(
                self._selected and ep.episode_session == self._selected.episode_session
            )
            btn.setProperty("active", "true" if active else "false")
            btn.setProperty(
                "downloaded",
                "true" if media and Path(media.path).is_file() else "false",
            )
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _filtered_episodes(self) -> list[EpisodeRef]:
        q = self.ep_search.text().strip()
        out: list[EpisodeRef] = []
        for ep in self._current_episodes():
            label = _fmt_ep(ep.episode)
            if q:
                if q not in label and q.casefold() not in (ep.title or "").casefold():
                    try:
                        if float(q) != float(ep.episode):
                            continue
                    except ValueError:
                        continue
            out.append(ep)
        return out

    def _sync_selection_ui(self) -> None:
        if not self._selected:
            self._set_actions_enabled(False)
            return
        ep = self._selected
        n = _fmt_ep(ep.episode)
        self.watch_info.setText(
            f"You are watching Episode {n}.\n"
            "If the current quality doesn’t work, try another server beside."
        )
        media = self._media_by_session.get(ep.episode_session)
        has_file = bool(media and Path(media.path).is_file())
        self.dl_btn.setEnabled(True)
        self.del_btn.setEnabled(has_file)
        self.prev_btn.setEnabled(self._episode_index() > 0)
        self.next_btn.setEnabled(
            0 <= self._episode_index() < len(self._current_episodes()) - 1
        )

    def _episode_index(self) -> int:
        if not self._selected:
            return -1
        eps = self._current_episodes()
        for i, e in enumerate(eps):
            if e.episode_session == self._selected.episode_session:
                return i
        return -1

    def _set_actions_enabled(self, on: bool) -> None:
        self.dl_btn.setEnabled(on)
        self.del_btn.setEnabled(on)
        self.prev_btn.setEnabled(on)
        self.next_btn.setEnabled(on)

    def _set_audio(self, audio: str) -> None:
        if self._audio == audio:
            return
        self._audio = audio
        self._refresh_option_styles()
        self._replay_with_options()

    def _set_quality(self, resolution: int) -> None:
        if self._resolution == resolution:
            return
        self._resolution = resolution
        self._refresh_option_styles()
        self._replay_with_options()

    def _replay_with_options(self) -> None:
        if not self._selected:
            return
        media = self._media_by_session.get(self._selected.episode_session)
        # Local files ignore SUB/DUB/quality — only re-stream remote
        if media and Path(media.path).is_file():
            self.player_status.setText(
                "Playing local file (download again for other audio/quality)"
            )
            return
        self.play_selected()

    def _refresh_option_styles(self) -> None:
        for code, btn in self._audio_btns.items():
            btn.setProperty("active", "true" if code == self._audio else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        for res, btn in self._quality_btns.items():
            btn.setProperty(
                "active", "true" if res == self._resolution else "false"
            )
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _toggle_auto_next(self) -> None:
        self._auto_next = not self._auto_next
        self.auto_next_btn.setText(
            "Auto Next On" if self._auto_next else "Auto Next Off"
        )
        if self._auto_next:
            self.auto_next_btn.setStyleSheet("color:#d97745;")
        else:
            self.auto_next_btn.setStyleSheet("")

    # ----- link workers -----

    def _start_link(
        self,
        chosen_session: str | None,
        *,
        refresh_only: bool = False,
    ) -> None:
        if not self._entry:
            return
        if self._link_worker and self._link_worker.isRunning():
            return
        query = self._entry.title_english or self._entry.title
        if refresh_only:
            self.link_lbl.setText("Refreshing episode lists…")
            self.status_message.emit("Refreshing AnimePahe episodes…")
        else:
            self.link_lbl.setText(f"Searching AnimePahe for “{query}”…")
            self.status_message.emit("Linking AnimePahe…")
        worker = PaheLinkWorker(
            self._entry.user_anime_id,
            query,
            chosen_session=chosen_session,
            refresh_only=refresh_only,
        )
        self._link_worker = worker
        worker.done.connect(self._on_linked)
        worker.failed.connect(self._on_link_fail)
        start_worker(worker, self)

    @Slot(object)
    def _on_linked(self, result: object) -> None:
        title = ""
        if isinstance(result, dict):
            title = str(result.get("pahe_title") or "")
        self.status_message.emit(f"Linked to {title}" if title else "Linked")
        self.repo.conn.commit()
        self.reload_catalog(auto_link=False)

    @Slot(str)
    def _on_link_fail(self, err: str) -> None:
        self.link_lbl.setText(f"Link failed: {err.splitlines()[0][:180]}")
        self.status_message.emit("AnimePahe link failed")
        QMessageBox.warning(self, "AnimePahe link failed", err)

    def change_link(self) -> None:
        if not self._entry:
            return
        query = self._entry.title_english or self._entry.title
        dlg = ChangeLinkDialog(query, self)
        if dlg.exec() and dlg.chosen_session:
            self._start_link(dlg.chosen_session)

    def refresh_catalog(self) -> None:
        if not self._entry:
            return
        link = self.repo.get_pahe_link(self._entry.user_anime_id)
        if not link:
            self._start_link(None)
            return
        self._start_link(None, refresh_only=True)

    # ----- playback -----

    def play_selected(self) -> None:
        if not self._entry or not self._selected:
            return
        ep = self._selected
        media = self._media_by_session.get(ep.episode_session)
        if media and Path(media.path).is_file():
            self.stop_proxy_only()
            self.player.setSource(QUrl.fromLocalFile(str(Path(media.path).resolve())))
            self.player.play()
            self.play_btn.setText("❚❚")
            self.player_status.setText(f"Playing local file · {Path(media.path).name}")
            return
        from localfun.services.pahe_catalog import play_url

        url = play_url(ep.anime_session, ep.episode_session)
        self._start_stream(url)

    def play_prev(self) -> None:
        i = self._episode_index()
        eps = self._current_episodes()
        if i > 0:
            self._select_episode(eps[i - 1], play=True)

    def play_next(self) -> None:
        i = self._episode_index()
        eps = self._current_episodes()
        if 0 <= i < len(eps) - 1:
            self._select_episode(eps[i + 1], play=True)

    def _start_stream(self, url: str) -> None:
        if self._stream_worker and self._stream_worker.isRunning():
            return
        self.stop_playback()
        self.player_status.setText("Resolving…")
        worker = StreamResolveWorker(
            url, resolution=self._resolution, audio=self._audio
        )
        self._stream_worker = worker
        worker.status.connect(self.player_status.setText)
        worker.done.connect(self._on_stream_ready)
        worker.failed.connect(self._on_stream_fail)
        start_worker(worker, self)

    @Slot(object)
    def _on_stream_ready(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._proxy = payload.get("proxy")
        local_url = str(payload.get("url") or "")
        resolved = payload.get("resolved")
        label = "Streaming…"
        if resolved is not None and getattr(resolved, "source", None):
            src = resolved.source
            label = f"Streaming · {src.label}"
        self.player_status.setText(label)
        self.player.setSource(QUrl(local_url))
        self.player.play()
        self.play_btn.setText("❚❚")

    @Slot(str)
    def _on_stream_fail(self, err: str) -> None:
        self.player_status.setText(f"Stream failed: {err.splitlines()[0][:200]}")
        QMessageBox.critical(self, "Stream failed", err)

    def download_selected(self) -> None:
        if not self._entry or not self._selected:
            return
        if self._dl_worker and self._dl_worker.isRunning():
            return
        ep = self._selected
        from localfun.services.pahe_catalog import play_url

        url = play_url(ep.anime_session, ep.episode_session)
        out = output_dir() / "anime"
        if self._entry.local_folder:
            out = Path(self._entry.local_folder)
        self.ep_status.setText("Downloading…")
        self.dl_btn.setEnabled(False)
        worker = EpisodeDownloadWorker(
            url,
            out,
            user_anime_id=self._entry.user_anime_id,
            episode_session=ep.episode_session,
            episode=ep.episode,
            resolution=self._resolution,
            audio=self._audio,
        )
        self._dl_worker = worker
        worker.log.connect(self.ep_status.setText)
        worker.done.connect(self._on_dl_done)
        worker.failed.connect(self._on_dl_fail)
        worker.finished.connect(lambda: self._sync_selection_ui())
        start_worker(worker, self)

    @Slot(object)
    def _on_dl_done(self, result: object) -> None:
        path = getattr(result, "path", result)
        self.ep_status.setText(f"Saved {path}")
        self.status_message.emit(f"Saved {path}")
        self.reload_catalog(auto_link=False)

    @Slot(str)
    def _on_dl_fail(self, err: str) -> None:
        self.ep_status.setText(err.splitlines()[0][:200])
        QMessageBox.critical(self, "Download failed", err)

    def delete_selected(self) -> None:
        if not self._entry or not self._selected:
            return
        media = self._media_by_session.get(self._selected.episode_session)
        if not media:
            return
        if (
            QMessageBox.question(
                self,
                "Delete download",
                f"Delete local file?\n{media.path}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        playing = self.player.source().toLocalFile()
        if playing and Path(playing).resolve() == Path(media.path).resolve():
            self.stop_playback()
        self.repo.delete_media(media.id, delete_file=True)
        self.reload_catalog(auto_link=False)
        self.status_message.emit("Deleted download")

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶")
        else:
            self.player.play()
            self.play_btn.setText("❚❚")

    def _skip(self, ms: int) -> None:
        self.player.setPosition(max(0, self.player.position() + ms))

    def _on_vol_changed(self, v: int) -> None:
        self.audio_out.setVolume(v / 100.0)
        if v > 0:
            self._muted = False
            self.mute_btn.setText("🔊")
            self._vol_before_mute = v

    def _nudge_vol(self, delta: int) -> None:
        if self._typing_focus():
            return
        self.vol.setValue(max(0, min(100, self.vol.value() + delta)))

    def toggle_mute(self) -> None:
        if self._typing_focus():
            return
        if self._muted or self.vol.value() == 0:
            self._muted = False
            self.vol.setValue(self._vol_before_mute or 80)
            self.mute_btn.setText("🔊")
        else:
            self._vol_before_mute = self.vol.value() or 80
            self._muted = True
            self.vol.setValue(0)
            self.mute_btn.setText("🔇")

    def _on_speed_changed(self, _index: int = 0) -> None:
        rate = float(self.speed_box.currentData() or 1.0)
        self.player.setPlaybackRate(rate)

    def toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self.video.setFullScreen(False)
            self._fullscreen = False
            self.fs_btn.setText("Fullscreen")
        else:
            self.video.setFullScreen(True)
            self._fullscreen = True
            self.fs_btn.setText("Exit FS")
            self.video.setFocus()

    def _exit_fullscreen_only(self) -> None:
        if self._fullscreen:
            self.video.setFullScreen(False)
            self._fullscreen = False
            self.fs_btn.setText("Fullscreen")

    def _on_pos(self, pos: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        self._update_time(pos, self.player.duration())

    def _on_dur(self, dur: int) -> None:
        self.seek.setRange(0, max(0, dur))
        self._update_time(self.player.position(), dur)

    def _update_time(self, pos: int, dur: int) -> None:
        self.time_lbl.setText(f"{_ms(pos)} / {_ms(dur)}")

    def _on_media_status(self, status) -> None:
        if (
            self._auto_next
            and status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._selected
        ):
            self.play_next()

    def _on_player_error(self, *_args) -> None:
        err = self.player.errorString() or "Media player error"
        self.player_status.setText(err)

    def stop_proxy_only(self) -> None:
        if self._proxy is not None:
            try:
                self._proxy.stop()
            except Exception:
                pass
            self._proxy = None

    def stop_playback(self) -> None:
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
        self.play_btn.setText("▶")
        self._exit_fullscreen_only()
        self.stop_proxy_only()
        stop_worker(self._stream_worker, 1000)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_playback()
        stop_worker(self._link_worker, 1000)
        stop_worker(self._dl_worker, 1000)
        super().closeEvent(event)


def _ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
