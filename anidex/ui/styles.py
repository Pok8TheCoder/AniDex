STYLESHEET = """
* {
    font-family: "Segoe UI", "IBM Plex Sans", "Helvetica Neue", sans-serif;
}

QMainWindow, QWidget#central {
    background: #141210;
    color: #f3efe8;
}

QLabel#brand {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.4px;
    color: #f7f2ea;
}

QLabel#subtitle {
    color: #9a9084;
    font-size: 12px;
}

QTabWidget::pane {
    border: 1px solid #2c2823;
    border-radius: 14px;
    background: #1b1814;
    top: -1px;
}

QTabBar::tab {
    background: transparent;
    color: #9a9084;
    padding: 10px 22px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    font-weight: 600;
}

QTabBar::tab:selected {
    color: #f7f2ea;
    background: #1b1814;
    border: 1px solid #2c2823;
    border-bottom-color: #1b1814;
}

QTabBar::tab:hover:!selected {
    color: #d9d0c4;
    background: #1a1713;
}

QFrame#panel {
    background: #1b1814;
    border: 1px solid #2c2823;
    border-radius: 14px;
}

QLabel#sectionTitle {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: #8a8176;
}

QTextEdit, QLineEdit, QListWidget, QComboBox, QSpinBox, QPlainTextEdit {
    background: #100e0c;
    border: 1px solid #2c2823;
    border-radius: 10px;
    padding: 8px;
    color: #f3efe8;
    selection-background-color: #8b4518;
}

QTextEdit:focus, QLineEdit:focus, QListWidget:focus, QComboBox:focus,
QSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #d97745;
}

QListWidget::item {
    padding: 10px 12px;
    border-radius: 8px;
    margin: 2px 0;
}

QListWidget::item:selected {
    background: #2a221c;
    border: 1px solid #d97745;
}

QListWidget::item:hover:!selected {
    background: #221d18;
}

QPushButton {
    background: #26211c;
    border: 1px solid #3a332c;
    border-radius: 10px;
    padding: 9px 14px;
    color: #f3efe8;
    font-weight: 600;
}

QPushButton:hover {
    background: #302a24;
    border-color: #d97745;
}

QPushButton:pressed {
    background: #1c1814;
}

QPushButton:disabled {
    color: #6b635a;
    background: #181512;
    border-color: #2a2520;
}

QPushButton#primary {
    background: #d97745;
    border: 1px solid #e8915f;
    color: #1a100a;
}

QPushButton#primary:hover {
    background: #e28555;
}

QPushButton#primary:disabled {
    background: #5a3a28;
    border-color: #6e4632;
    color: #c4a08a;
}

QPushButton#danger {
    background: transparent;
    border: 1px solid #5a3a42;
    color: #e8a0a8;
}

QPushButton#danger:hover {
    background: #2a1a1e;
    border-color: #a85a66;
}

QPushButton#chip {
    border-radius: 16px;
    padding: 6px 14px;
    background: #201c18;
    border: 1px solid #3a332c;
    font-weight: 600;
}

QPushButton#chip:checked {
    background: #3a2418;
    border: 1px solid #d97745;
    color: #f7f2ea;
}

QCheckBox {
    spacing: 8px;
    color: #d4ccc2;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3a332c;
    background: #100e0c;
}

QCheckBox::indicator:checked {
    background: #d97745;
    border-color: #e8915f;
}

QProgressBar {
    background: #100e0c;
    border: 1px solid #2c2823;
    border-radius: 8px;
    text-align: center;
    color: #ebe4da;
    height: 18px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #b85a2e, stop:1 #e8915f);
    border-radius: 7px;
}

QLabel#status {
    color: #9a9084;
    font-size: 12px;
}

QLabel#previewTitle {
    font-size: 16px;
    font-weight: 700;
    color: #f7f2ea;
}

QLabel#previewMeta {
    color: #9a9084;
    font-size: 12px;
}

QFrame#previewFrame {
    background: #0c0b09;
    border: 1px solid #2c2823;
    border-radius: 12px;
}

QSplitter::handle {
    background: #141210;
    width: 6px;
}

QLabel#hero {
    font-size: 28px;
    font-weight: 700;
    color: #f7f2ea;
}

QLabel#body {
    color: #9a9084;
    font-size: 13px;
}

QScrollArea {
    border: none;
    background: transparent;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

QToolTip {
    background: #1b1814;
    color: #f3efe8;
    border: 1px solid #3a332c;
}

/* --- Watch view (streaming layout) --- */
QWidget#watchRoot {
    background: #141210;
}

QFrame#watchSidebar {
    background: #12100e;
    border: 1px solid #2c2823;
    border-radius: 12px;
}

QLabel#watchEpHeader {
    font-size: 13px;
    font-weight: 700;
    color: #f3efe8;
}

QPushButton#epTile {
    background: #1e1b17;
    border: 1px solid #2c2823;
    border-radius: 6px;
    color: #cfc6ba;
    font-weight: 700;
    font-size: 12px;
    min-width: 44px;
    max-width: 52px;
    min-height: 36px;
    max-height: 40px;
    padding: 0;
}

QPushButton#epTile:hover {
    background: #2a241e;
    border-color: #d97745;
    color: #f7f2ea;
}

QPushButton#epTile[active="true"] {
    background: #d97745;
    border: 1px solid #e8915f;
    color: #1a100a;
}

QPushButton#epTile[downloaded="true"] {
    border-color: #8a5a38;
}

QPushButton#epTile[downloaded="true"][active="true"] {
    background: #d97745;
    border-color: #e8915f;
}

QFrame#watchInfo {
    background: #d97745;
    border: none;
    border-radius: 8px;
}

QLabel#watchInfoText {
    color: #1a100a;
    font-size: 13px;
    font-weight: 600;
}

QLabel#serverLabel {
    color: #d97745;
    font-weight: 800;
    font-size: 11px;
    min-width: 56px;
    letter-spacing: 0.6px;
}

QPushButton#serverChip {
    background: #26211c;
    border: 1px solid #3a332c;
    border-radius: 6px;
    padding: 8px 16px;
    color: #f3efe8;
    font-weight: 700;
    min-width: 56px;
}

QPushButton#serverChip:hover {
    border-color: #d97745;
}

QPushButton#serverChip[active="true"] {
    background: #d97745;
    border: 1px solid #e8915f;
    color: #1a100a;
}

QPushButton#watchGhost {
    background: transparent;
    border: none;
    color: #cfc6ba;
    font-weight: 600;
    padding: 6px 10px;
}

QPushButton#watchGhost:hover {
    color: #d97745;
    background: #1e1b17;
    border-radius: 6px;
}

QLabel#watchBreadcrumb {
    color: #9a9084;
    font-size: 12px;
}

QFrame#playerChrome {
    background: #0c0b09;
    border: 1px solid #2c2823;
    border-radius: 10px;
}

QSlider#watchSeek::groove:horizontal {
    height: 6px;
    background: #2c2823;
    border-radius: 3px;
}

QSlider#watchSeek::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
    background: #d97745;
}

QSlider#watchSeek::sub-page:horizontal {
    background: #d97745;
    border-radius: 3px;
}
"""
