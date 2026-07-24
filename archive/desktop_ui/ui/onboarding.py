from __future__ import annotations

from PySide6.QtCore import Signal
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

from localfun.db.repositories import Repository
from localfun.services.sync import import_xml_into_repo
from localfun.ui.widgets import section_label


class OnboardingView(QWidget):
    finished = Signal()

    def __init__(self, repo: Repository) -> None:
        super().__init__()
        self.repo = repo
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(18)
        root.addStretch()

        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMaximumWidth(560)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(28, 28, 28, 28)
        pl.setSpacing(14)

        brand = QLabel("Welcome to LocalFun")
        brand.setObjectName("hero")
        sub = QLabel(
            "Your anime & manga lists live on this PC — so you never lose them again.\n"
            "Pull titles from MAL / MangaDex when you need them."
        )
        sub.setObjectName("body")
        sub.setWordWrap(True)
        pl.addWidget(brand)
        pl.addWidget(sub)

        pl.addWidget(section_label("What should we call you?"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Display name")
        pl.addWidget(self.name_edit)

        pl.addWidget(section_label("Get started"))
        row = QHBoxLayout()
        self.empty_btn = QPushButton("Start empty")
        self.empty_btn.setObjectName("primary")
        self.empty_btn.clicked.connect(self._start_empty)
        self.import_btn = QPushButton("Import MAL XML…")
        self.import_btn.clicked.connect(self._import_xml)
        self.later_btn = QPushButton("Connect MAL later")
        self.later_btn.clicked.connect(self._start_empty)
        row.addWidget(self.empty_btn)
        row.addWidget(self.import_btn)
        row.addWidget(self.later_btn)
        pl.addLayout(row)

        tip = QLabel(
            "Tip: export your list from myanimelist.net (Export) then import the XML here.\n"
            "OAuth sync can be enabled anytime in Settings."
        )
        tip.setObjectName("body")
        tip.setWordWrap(True)
        pl.addWidget(tip)

        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(panel)
        wrap.addStretch()
        root.addLayout(wrap)
        root.addStretch()

    def _name(self) -> str:
        return self.name_edit.text().strip() or "Otaku"

    def _finish(self) -> None:
        self.repo.update_profile(display_name=self._name(), onboarded=True)
        self.finished.emit()

    def _start_empty(self) -> None:
        self._finish()

    def _import_xml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import MAL XML",
            "",
            "XML files (*.xml);;All files (*.*)",
        )
        if not path:
            return
        try:
            imported, updated = import_xml_into_repo(self.repo, path)
            QMessageBox.information(
                self,
                "Import complete",
                f"Imported {imported} new, updated {updated} existing entries.",
            )
            self._finish()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(e))
