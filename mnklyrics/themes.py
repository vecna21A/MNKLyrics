"""Control-center color themes and the stylesheet generated from them."""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

THEMES = {
    "Neon Night": dict(bg0="#07070D", bg1="#0D0D18", bg2="#141426", bg3="#1D1D36", border="#2A2A4C",
                       text="#EDEBFF", dim="#8C89B3", accent="#00E5FF", accent2="#B026FF", danger="#FF4D6D", dark=True),
    "Emerald": dict(bg0="#050806", bg1="#0B110D", bg2="#121A15", bg3="#1A261F", border="#26392D",
                    text="#E9FFF0", dim="#86A391", accent="#1ED760", accent2="#00B4D8", danger="#FF4D6D", dark=True),
    "Synthwave": dict(bg0="#0E0518", bg1="#160926", bg2="#200D36", bg3="#2C1249", border="#46226E",
                      text="#FFE9F8", dim="#B48FCB", accent="#FF2E97", accent2="#FF9E3D", danger="#FF4D6D", dark=True),
    "Aurora": dict(bg0="#040F10", bg1="#081719", bg2="#0E2124", bg3="#142C30", border="#1F4146",
                   text="#E6FFFB", dim="#7FA8A6", accent="#2EF2C2", accent2="#7B61FF", danger="#FF5C7A", dark=True),
    "Crimson": dict(bg0="#0F0507", bg1="#18090C", bg2="#220E12", bg3="#2E1419", border="#4A2129",
                    text="#FFECEE", dim="#B38990", accent="#FF3B5C", accent2="#FF8A3D", danger="#FF3B5C", dark=True),
    "Ocean": dict(bg0="#040A14", bg1="#08111F", bg2="#0D192C", bg3="#13233B", border="#1E3657",
                  text="#E8F3FF", dim="#7F98B8", accent="#3DA9FF", accent2="#3DFFD8", danger="#FF5C7A", dark=True),
    "Gold Luxe": dict(bg0="#0B0A07", bg1="#13110C", bg2="#1C1912", bg3="#27231A", border="#3D3626",
                      text="#FFF6E2", dim="#B3A585", accent="#F5C451", accent2="#FF8F3D", danger="#FF5C5C", dark=True),
    "Sakura": dict(bg0="#120A10", bg1="#1A0F18", bg2="#241521", bg3="#301C2C", border="#4A2C44",
                   text="#FFEFF6", dim="#BB93AE", accent="#FF8FB1", accent2="#C38FFF", danger="#FF5C7A", dark=True),
    "Mono": dict(bg0="#08080A", bg1="#101013", bg2="#17171B", bg3="#212126", border="#33333B",
                 text="#F2F2F5", dim="#8D8D99", accent="#FFFFFF", accent2="#9A9AA8", danger="#FF5C5C", dark=True),
    "Arctic Light": dict(bg0="#E6ECF4", bg1="#F0F4F9", bg2="#FFFFFF", bg3="#EAF0F7", border="#D2DCE8",
                         text="#141A26", dim="#66718A", accent="#2F6BFF", accent2="#8A3FFC", danger="#E5394F", dark=False),
}


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, name: str = "Neon Night"):
        super().__init__()
        self.name = name if name in THEMES else "Neon Night"
        self.t = THEMES[self.name]

    def set_theme(self, name: str) -> None:
        if name not in THEMES or name == self.name:
            return
        self.name = name
        self.t = THEMES[name]
        self.changed.emit()

    def c(self, key: str, alpha: float = 1.0) -> QColor:
        color = QColor(self.t[key])
        if alpha < 1.0:
            color.setAlphaF(max(0.0, alpha))
        return color

    @property
    def dark(self) -> bool:
        return self.t["dark"]

    def stylesheet(self) -> str:
        t = self.t
        return f"""
        QWidget {{ color: {t['text']}; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel#dim {{ color: {t['dim']}; }}
        QLabel#cardTitle {{ font-size: 15px; font-weight: 600; }}
        QLabel#cardSub {{ color: {t['dim']}; font-size: 12px; }}
        QLabel#rowLabel {{ font-size: 13px; }}
        QLabel#rowDesc {{ color: {t['dim']}; font-size: 11px; }}
        QLabel#value {{ color: {t['accent']}; font-weight: 600; font-family: "Cascadia Mono", "Consolas"; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
        QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 40px; }}
        QScrollBar::handle:vertical:hover {{ background: {t['accent']}; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
        QScrollBar:horizontal {{ height: 0; }}
        QComboBox, QFontComboBox, QLineEdit {{
            background: {t['bg3']}; border: 1px solid {t['border']}; border-radius: 9px;
            padding: 7px 12px; min-height: 20px; selection-background-color: {t['accent']};
            selection-color: {t['bg0']};
        }}
        QComboBox:hover, QFontComboBox:hover, QLineEdit:hover, QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
        QComboBox::drop-down {{ border: none; width: 26px; }}
        QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; border-right: 5px solid transparent;
            border-top: 6px solid {t['dim']}; width: 0; height: 0; margin-right: 10px; }}
        QComboBox QAbstractItemView {{
            background: {t['bg2']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px;
            selection-background-color: {t['bg3']}; selection-color: {t['accent']}; outline: none;
        }}
        QToolTip {{ background: {t['bg2']}; color: {t['text']}; border: 1px solid {t['accent']}; border-radius: 6px; padding: 6px; }}
        QMenu {{ background: {t['bg1']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; }}
        QMenu::item {{ padding: 8px 26px 8px 16px; border-radius: 6px; }}
        QMenu::item:selected {{ background: {t['bg3']}; color: {t['accent']}; }}
        QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
        QColorDialog, QDialog {{ background: {t['bg1']}; }}
        QColorDialog QPushButton, QDialog QPushButton {{ background: {t['bg3']}; border: 1px solid {t['border']};
            border-radius: 8px; padding: 6px 14px; }}
        QColorDialog QPushButton:hover, QDialog QPushButton:hover {{ border-color: {t['accent']}; }}
        QSpinBox {{ background: {t['bg3']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 3px; }}
        QListView#searchPopup {{ background: {t['bg2']}; border: 1px solid {t['accent']}; border-radius: 10px;
            padding: 4px; outline: none; }}
        QListView#searchPopup::item {{ padding: 8px 10px; border-radius: 6px; }}
        QListView#searchPopup::item:selected, QListView#searchPopup::item:hover {{
            background: {t['bg3']}; color: {t['accent']}; }}
        QLineEdit#search {{ border-radius: 10px; padding: 8px 12px 8px 34px; margin: 0 12px; }}
        QMessageBox QLabel, QInputDialog QLabel {{ font-size: 13px; }}
        QPlainTextEdit, QTableWidget {{
            background: {t['bg3']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px;
            selection-background-color: {t['accent']}; selection-color: {t['bg0']};
        }}
        QPlainTextEdit:focus, QTableWidget:focus {{ border: 1px solid {t['accent']}; }}
        QTableWidget {{ gridline-color: {t['border']}; padding: 2px; }}
        QTableWidget::item {{ padding: 4px 6px; }}
        QTableWidget::item:selected {{ background: {t['bg2']}; color: {t['accent']}; }}
        QHeaderView::section {{ background: {t['bg2']}; color: {t['dim']}; border: none;
            border-bottom: 1px solid {t['border']}; padding: 6px; font-weight: 600; }}
        QTableCornerButton::section {{ background: {t['bg2']}; border: none; }}
        """
