# __author__  = "Dipendra Magaju"
# __licence__ = "GNU General Public License v2 or later (GPLv2+)"

"""
aptool_style.py — shared look-and-feel for this plugin's dock, matching
the Auckland Catchment Hydrology plugin's visual design: a navy header
bar, blue section/tab accents, white bordered inputs, and blue
pill-shaped action buttons. Purely cosmetic — just the shared colour
constants, stylesheets, and small widget-building helpers the dock pulls
from, so the whole tool reads as one consistent design instead of default
Qt styling.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QWidget, QHBoxLayout, QLabel

# ── Colour constants ─────────────────────────────────────────────────────────
NAV        = '#1A237E'   # header bar / section-title-bar navy
BLUE       = '#1565C0'   # primary accent — buttons, borders, links
BLUE_HOVER = '#1976D2'
BLUE_PRESS = '#0D47A1'
BODY_BG    = '#F5F5F5'
SECTION_BG = '#EEF4FF'
BORDER     = '#B0BEC5'
DISABLED_BG = '#ECEFF1'
DISABLED_FG = '#90A4AE'

_CHECK_ICON = os.path.join(os.path.dirname(__file__), 'assets', 'checkbox_tick.svg').replace('\\', '/')

# ── Global widget stylesheet — apply once via widget.setStyleSheet(WIDGET_STYLE)
WIDGET_STYLE = f"""
    QgsMapLayerComboBox, QgsFieldComboBox, QComboBox, QDoubleSpinBox, QSpinBox,
    QLineEdit, QDateEdit {{
        background-color: white;
        color: black;
        border: 1px solid {BORDER};
        border-radius: 3px;
        padding: 2px 4px;
        font-size: 11px;
    }}
    QComboBox::drop-down, QDateEdit::drop-down {{ border: none; }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}
    QgsFileWidget QToolButton, QgsFileWidget QLineEdit {{
        background-color: white;
        color: black;
        border: 1px solid {BORDER};
        border-radius: 3px;
    }}
    QgsMapLayerComboBox:disabled, QgsFieldComboBox:disabled, QComboBox:disabled,
    QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled,
    QgsFileWidget QToolButton:disabled, QgsFileWidget QLineEdit:disabled {{
        background-color: {DISABLED_BG};
        color: {DISABLED_FG};
    }}
    QCheckBox::indicator {{
        width: 13px;
        height: 13px;
        border: 1px solid #90A4AE;
        border-radius: 2px;
        background-color: white;
    }}
    QCheckBox::indicator:checked {{
        background-color: #E3F2FD;
        border: 1px solid {BLUE};
        image: url({_CHECK_ICON});
    }}
    QCheckBox::indicator:disabled {{
        background-color: {DISABLED_BG};
        border: 1px solid #CFD8DC;
    }}
"""

# ── Tab bar styling — apply to any top-level QTabWidget. Matches Auckland
# Catchment Hydrology's tab look: individual grey pill buttons sitting on a
# navy strip, with the active tab picked out in solid blue + bold white text
# (see AucklandCatchmentHydrology/dock.py's _BTN_ACTIVE/_BTN_INACTIVE).
TAB_STYLE = f"""
    QTabWidget::pane {{ border: none; background: {BODY_BG}; }}
    QTabBar {{ background: {NAV}; }}
    QTabBar::tab {{
        background: #CFD8DC;
        color: #37474F;
        border: 1px solid #90A4AE;
        border-radius: 3px;
        padding: 4px 10px;
        margin: 3px 2px;
        font-size: 11px;
        font-weight: normal;
    }}
    QTabBar::tab:selected {{
        background: {BLUE};
        color: white;
        border: 1px solid #0D47A1;
        font-weight: bold;
    }}
    QTabBar::tab:hover:!selected {{
        background: #B0BEC5;
    }}
"""

# A smaller, lighter variant for nested/secondary tab bars (e.g. Profile
# Windows) so the whole dock doesn't turn into stacked navy bars.
SUBTAB_STYLE = f"""
    QTabWidget::pane {{ border: 1px solid #BDBDBD; border-top: none; }}
    QTabBar::tab {{
        padding: 2px 10px;
        font-size: 10px;
        background: #ECEFF1;
        color: #37474F;
        border: 1px solid {BORDER};
        border-bottom: none;
        border-top-left-radius: 3px;
        border-top-right-radius: 3px;
        margin-right: 1px;
    }}
    QTabBar::tab:hover:!selected {{
        background: #CFD8DC;
    }}
    QTabBar::tab:selected {{
        background: {BLUE};
        color: white;
        font-weight: bold;
        border: 1px solid #0D47A1;
        border-bottom: none;
    }}
"""

# ── Buttons ───────────────────────────────────────────────────────────────────
PRIMARY_BUTTON_STYLE = (
    f'QPushButton {{ background: {BLUE}; color: white; border: none;'
    f'  border-radius: 4px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}'
    f'QPushButton:hover   {{ background: {BLUE_HOVER}; }}'
    f'QPushButton:pressed {{ background: {BLUE_PRESS}; }}'
    f'QPushButton:disabled {{ background: #90A4AE; }}'
    f'QPushButton:checkable:checked {{ background: {BLUE_PRESS}; }}'
)

SECONDARY_BUTTON_STYLE = (
    f'QPushButton {{ background: #ECEFF1; color: #37474F; border: 1px solid {BORDER};'
    f'  border-radius: 4px; padding: 5px 12px; font-size: 11px; font-weight: bold; }}'
    f'QPushButton:hover    {{ background: #CFD8DC; }}'
    f'QPushButton:pressed  {{ background: #B0BEC5; }}'
    f'QPushButton:checkable:checked {{ background: #90A4AE; color: white; border: 1px solid #78909C; }}'
)

# "..." browse buttons — folder/file pickers sitting next to a QLineEdit
# path field (Result Folder, Merge TIFF's output path, etc.). Explicit
# style rather than relying on native OS button rendering, so it can't
# end up looking like anything else (e.g. a colour swatch) regardless of
# theme/OS, and so every new one this plugin adds stays visually
# consistent automatically.
BROWSE_BUTTON_STYLE = (
    f'QPushButton {{ background: white; color: #37474F;'
    f'  border: 1px solid {BORDER}; border-radius: 3px; font-size: 11px; }}'
    f'QPushButton:hover   {{ background: #ECEFF1; }}'
    f'QPushButton:pressed {{ background: #CFD8DC; }}'
)


# ── Header bar ────────────────────────────────────────────────────────────────

def build_header_bar(title, icon_path=None, height=40):
    """Navy header strip with an optional icon + bold white title — the
    same banner style used at the top of each Auckland Catchment Hydrology
    panel."""
    header = QWidget()
    header.setFixedHeight(height)
    header.setStyleSheet(f'background: {NAV};')
    hl = QHBoxLayout(header)
    hl.setContentsMargins(12, 0, 10, 0)
    if icon_path and os.path.exists(icon_path):
        ic = QLabel()
        ic.setPixmap(QIcon(icon_path).pixmap(24, 24))
        ic.setFixedSize(26, 26)
        ic.setStyleSheet('background: transparent;')
        hl.addWidget(ic)
        hl.addSpacing(6)
    t = QLabel(title)
    t.setStyleSheet('color: white; font-size: 12px; font-weight: bold; background: transparent;')
    hl.addWidget(t, 1)
    return header


def section_label_style():
    """Stylesheet string for a bold navy sub-heading label — used by this
    file's own _section_label() helper."""
    return f'font-size:11px;font-weight:bold;color:{NAV};'
