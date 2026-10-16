# __author__  = "Dipendra Magaju"
# __licence__ = "GNU General Public License v2 or later (GPLv2+)"

"""
plugin.py — Advanced Profile Tool dock plugin.

Raster layers auto-populated from QGIS project via QListWidget (checkable).
Cross-Section Analysis: cut/fill shading + hydraulic area engine (Riemann sum).
c-c water-level spinbox; d-d auto-set to Zmin in window.
Stage-Area side subplot with proper Elevation (m AD) axis; no duplicate legend.
Live chart preview; hover sync to map canvas; scroll zoom; middle-mouse pan.
"""

__version__  = '1.0'
TOOL_ID      = 'advanced_profile_tool'
DISPLAY_NAME = 'Advanced Profile Tool'
GROUP_NAME   = 'Advanced Profile Tool'

import bisect
import csv
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime

from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer, QSizeF, QPointF, QObject, QEvent, QRect, QPoint, QSize, QVariant
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QDoubleSpinBox, QPushButton, QLineEdit,
    QFileDialog, QGroupBox, QFrame, QSizePolicy, QMessageBox,
    QApplication, QCheckBox, QComboBox, QColorDialog, QScrollArea,
    QListWidget, QListWidgetItem, QTabWidget, QInputDialog, QProgressBar,
    QSpinBox, QDialog, QMenu, QLayout, QStackedWidget,
    QToolButton, QTextBrowser,
)
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap, QPainter, QPen, QStandardItem, QStandardItemModel, QTextDocument, QCursor

from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsRubberBand, QgsVertexMarker
from qgis.core import (
    Qgis, QgsProject, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsPoint, QgsLineString,
    QgsSpatialIndex, QgsFieldProxyModel, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsMapLayer, QgsRectangle, QgsFeatureRequest,
    QgsVectorFileWriter, QgsFields, QgsField, QgsFeature, QgsVectorLayer,
    QgsCoordinateReferenceSystem, QgsProcessingUtils, QgsFeedback,
    QgsCoordinateTransform, QgsProviderRegistry,
)
from qgis.analysis import QgsInterpolator, QgsTinInterpolator, QgsGridFileWriter

# ---------------------------------------------------------------------------
# Qt6 / PyQt6 enum compatibility
# ---------------------------------------------------------------------------
try:
    _HLINE    = QFrame.Shape.HLine
    _SUNKEN   = QFrame.Shadow.Sunken
    _NO_FRAME = QFrame.Shape.NoFrame
except AttributeError:
    _HLINE    = QFrame.HLine      # type: ignore[attr-defined]
    _SUNKEN   = QFrame.Sunken     # type: ignore[attr-defined]
    _NO_FRAME = QFrame.NoFrame    # type: ignore[attr-defined]

try:
    _EXPAND = QSizePolicy.Policy.Expanding
except AttributeError:
    _EXPAND = QSizePolicy.Expanding  # type: ignore[attr-defined]

try:
    _RIGHT_DOCK  = Qt.DockWidgetArea.RightDockWidgetArea
    _BOTTOM_DOCK = Qt.DockWidgetArea.BottomDockWidgetArea
except AttributeError:
    _RIGHT_DOCK  = Qt.RightDockWidgetArea   # type: ignore[attr-defined]
    _BOTTOM_DOCK = Qt.BottomDockWidgetArea  # type: ignore[attr-defined]

try:
    _LINE_GEOM    = Qgis.GeometryType.Line
    _POINT_GEOM   = Qgis.GeometryType.Point
    _POLYGON_GEOM = Qgis.GeometryType.Polygon
except AttributeError:
    _LINE_GEOM    = QgsWkbTypes.LineGeometry    # type: ignore[attr-defined]
    _POINT_GEOM   = QgsWkbTypes.PointGeometry   # type: ignore[attr-defined]
    _POLYGON_GEOM = QgsWkbTypes.PolygonGeometry # type: ignore[attr-defined]

try:
    _ICON_CIRCLE = QgsRubberBand.IconType.ICON_CIRCLE
    _ICON_X      = QgsRubberBand.IconType.ICON_X
    _ICON_BOX    = QgsRubberBand.IconType.ICON_BOX
except AttributeError:
    _ICON_CIRCLE = QgsRubberBand.ICON_CIRCLE  # type: ignore[attr-defined]
    _ICON_X      = QgsRubberBand.ICON_X       # type: ignore[attr-defined]
    _ICON_BOX    = QgsRubberBand.ICON_BOX     # type: ignore[attr-defined]

try:
    _VECTOR_FILTER = QgsMapLayerProxyModel.Filter.VectorLayer
except AttributeError:
    _VECTOR_FILTER = QgsMapLayerProxyModel.VectorLayer  # type: ignore[attr-defined]

try:
    from qgis.PyQt.QtGui import QShortcut, QKeySequence
except ImportError:
    from qgis.PyQt.QtWidgets import QShortcut, QKeySequence  # type: ignore[attr-defined]

try:
    _KEY_ESCAPE = Qt.Key.Key_Escape
    _KEY_RETURN = Qt.Key.Key_Return
    _KEY_ENTER  = Qt.Key.Key_Enter
except AttributeError:
    _KEY_ESCAPE = Qt.Key_Escape  # type: ignore[attr-defined]
    _KEY_RETURN = Qt.Key_Return  # type: ignore[attr-defined]
    _KEY_ENTER  = Qt.Key_Enter   # type: ignore[attr-defined]

try:
    _MB_YES = QMessageBox.StandardButton.Yes
    _MB_NO  = QMessageBox.StandardButton.No
except AttributeError:
    _MB_YES = QMessageBox.Yes  # type: ignore[attr-defined]
    _MB_NO  = QMessageBox.No   # type: ignore[attr-defined]

# Top-level tab indices (self._active_tab) — named rather than bare
# numbers so the actual on-screen tab ORDER (set once, in _build_ui's own
# tabs.addTab(...) calls) can be changed without having to hunt down every
# place that checks 'which tab is this' throughout the file. Layer
# Profile is always first (0); the other three can be reordered freely as
# long as these three constants are kept in sync with the addTab() order.
_TAB_LAYER   = 0   # Layer Profile
_TAB_CHANNEL = 1   # Channel Surface (internally still named 'terrain' throughout)
_TAB_EMBANK  = 2   # Embankment Surface
_TAB_SHAPES  = 3   # Shapes and Polygon Surface (internally still named 'polygon' where it exists)

# Nominal placeholder rises for the Modify Terrain cross-section preview —
# NOT a real engineering rule, just enough to visualise the four shape
# parameters relative to each other until that geometry is specified.
_TERRAIN_XS_INNER_RISE = 2.0
_TERRAIN_XS_OUTER_RISE = 1.0

# Cap on the miter-join amplification factor (1 / cos(half the turn
# angle)) used at a sharp, un-filleted alignment vertex when building the
# Create TIFF corridor — see _terrain_vertex_miter_bisector. 4.0 matches
# the common default 'miter limit' used by CAD/vector-graphics stroke
# joins (SVG's stroke-miterlimit default is also 4); past that the turn
# is sharp enough (cos(half-angle) <= 0.25, i.e. roughly a 150°+ turn)
# that a true miter point would shoot out an unreasonable distance from
# the corner, so the amplification is simply capped there instead.
_TERRAIN_MITER_LIMIT = 4.0

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer  # type: ignore[attr-defined]

try:
    _WAIT_CURSOR = Qt.CursorShape.WaitCursor
except AttributeError:
    _WAIT_CURSOR = Qt.WaitCursor  # type: ignore[attr-defined]

try:
    _LINE_FILTER = QgsMapLayerProxyModel.Filter.LineLayer
except AttributeError:
    _LINE_FILTER = QgsMapLayerProxyModel.LineLayer  # type: ignore[attr-defined]

try:
    _POINT_FILTER = QgsMapLayerProxyModel.Filter.PointLayer
except AttributeError:
    _POINT_FILTER = QgsMapLayerProxyModel.PointLayer  # type: ignore[attr-defined]

try:
    _NUMERIC_FILTER = QgsFieldProxyModel.Filter.Numeric
except AttributeError:
    _NUMERIC_FILTER = QgsFieldProxyModel.Numeric  # type: ignore[attr-defined]

try:
    _CHECKED        = Qt.CheckState.Checked
    _UNCHECKED      = Qt.CheckState.Unchecked
    _ITEM_FLAGS     = (Qt.ItemFlag.ItemIsEnabled |
                       Qt.ItemFlag.ItemIsUserCheckable |
                       Qt.ItemFlag.ItemIsSelectable)
    _USER_ROLE      = Qt.ItemDataRole.UserRole
except AttributeError:
    _CHECKED        = Qt.Checked         # type: ignore[attr-defined]
    _UNCHECKED      = Qt.Unchecked       # type: ignore[attr-defined]
    _ITEM_FLAGS     = (Qt.ItemIsEnabled |  # type: ignore[attr-defined]
                       Qt.ItemIsUserCheckable |
                       Qt.ItemIsSelectable)
    _USER_ROLE      = Qt.UserRole        # type: ignore[attr-defined]

try:
    _STRONG_FOCUS     = Qt.FocusPolicy.StrongFocus           # Qt6
    _KEY_PRESS_TYPE   = QEvent.Type.KeyPress                 # Qt6
    _KEY_RELEASE_TYPE = QEvent.Type.KeyRelease               # Qt6
    _KEY_CTRL         = Qt.Key.Key_Control                   # Qt6
    _CTRL_MOD         = Qt.KeyboardModifier.ControlModifier  # Qt6
except AttributeError:
    _STRONG_FOCUS     = Qt.StrongFocus        # type: ignore[attr-defined]  # Qt5
    _KEY_PRESS_TYPE   = QEvent.KeyPress       # type: ignore[attr-defined]  # Qt5
    _KEY_RELEASE_TYPE = QEvent.KeyRelease     # type: ignore[attr-defined]  # Qt5
    _KEY_CTRL         = Qt.Key_Control        # type: ignore[attr-defined]  # Qt5
    _CTRL_MOD         = Qt.ControlModifier    # type: ignore[attr-defined]  # Qt5

# ---------------------------------------------------------------------------
# Matplotlib
# ---------------------------------------------------------------------------
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle as _MplRect, Ellipse as _MplEllipse
    from matplotlib.transforms import blended_transform_factory as _blended_tf
    import matplotlib.path as _mpath
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False
    _mpath = None

_NavToolbar = None
if MATPLOTLIB_AVAILABLE:
    try:
        from matplotlib.backends.backend_qt import NavigationToolbar2QT as _NavToolbar
    except ImportError:
        try:
            from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as _NavToolbar
        except ImportError:
            _NavToolbar = None

from .profile_line_tool import ProfileLineTool, VertexSelectTool, ShapeDrawTool, ShapeRotateTool
from . import aptool_style as _style

_CHART_COLORS = [
    '#2196F3', '#F44336', '#4CAF50', '#FF9800',
    '#9C27B0', '#00BCD4', '#795548', '#607D8B',
    '#E91E63', '#009688', '#FF5722', '#3F51B5',
]

_LINESTYLES = [
    ('-',  'Solid ─'),
    ('--', 'Dashed - -'),
    (':',  'Dotted · · ·'),
    ('-.', 'Dash-dot -·-'),
]

_BLUE = '#1565C0'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep():
    line = QFrame()
    line.setFrameShape(_HLINE)
    line.setFrameShadow(_SUNKEN)
    return line


def _thin_sep():
    line = QFrame()
    line.setFrameShape(_HLINE)
    line.setStyleSheet('color:#E0E0E0;')
    return line


def _data_to_axfrac(ax, xdata, ydata):
    """Convert data-space coords to axes-fraction (0–1) coords."""
    disp = ax.transData.transform((xdata, ydata))
    return tuple(ax.transAxes.inverted().transform(disp))


def _to_hex_color(c) -> str:
    """Convert any matplotlib color spec (including RGBA tuple) to '#rrggbb'."""
    try:
        import matplotlib.colors as _mc
        return _mc.to_hex(c)
    except Exception:
        return '#000000'


_LEVEL_CURSOR   = None   # module-level cache; built once on first use
_TRI_TIP_PATH   = None   # custom marker: down-triangle with tip at (0,0)


def _get_tri_tip_path():
    """Inverted triangle marker path whose tip vertex is at the origin (0, 0).

    When used as a matplotlib marker this means the pointed tip lands exactly
    on the data coordinate, rather than the bounding-box centre.
    """
    global _TRI_TIP_PATH
    if _TRI_TIP_PATH is None:
        try:
            import numpy as _np2
            from matplotlib.path import Path as _P2
            # tip at (0,0); base corners at (±1, 2) → 2 units above in marker space
            _TRI_TIP_PATH = _P2(
                _np2.array([[0., 0.], [-1., 2.], [1., 2.], [0., 0.]]),
                [_P2.MOVETO, _P2.LINETO, _P2.LINETO, _P2.CLOSEPOLY])
        except Exception:
            _TRI_TIP_PATH = 'v'   # safe fallback
    return _TRI_TIP_PATH


def _get_level_cursor():
    """Return a QCursor shaped as a downward-pointing triangle (▼)."""
    global _LEVEL_CURSOR
    if _LEVEL_CURSOR is not None:
        return _LEVEL_CURSOR
    try:
        from qgis.PyQt.QtCore import QPoint
        from qgis.PyQt.QtGui import QPolygon
        s = 22
        pix = QPixmap(s, s)
        pix.fill(QColor(0, 0, 0, 0))   # transparent background
        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
        except AttributeError:
            p.setRenderHint(QPainter.Antialiasing)  # type: ignore
        pen = QPen(QColor('#111111'))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.setBrush(QColor('#111111'))
        poly = QPolygon([QPoint(s // 2, s - 2), QPoint(2, 2), QPoint(s - 2, 2)])
        p.drawPolygon(poly)
        p.end()
        _LEVEL_CURSOR = QCursor(pix, s // 2, s - 2)   # hotspot at bottom tip
    except Exception:
        try:
            _LEVEL_CURSOR = QCursor(Qt.CursorShape.CrossCursor)
        except AttributeError:
            _LEVEL_CURSOR = QCursor(Qt.CrossCursor)   # type: ignore
    return _LEVEL_CURSOR


def _snap_level(ax, xd, yd, vertex_frac=0.03, segment_frac=0.05):
    """Snap to the nearest vertex or segment of any visible Line2D.

    Vertex pass first (normalised 2-D distance < vertex_frac).
    If no vertex found, falls back to segment interpolation (Y distance
    < segment_frac of Y range).

    Returns (snapped_x, snapped_y, color_hex, snap_type) where snap_type
    is 'vertex', 'segment', or None (no snap).
    """
    try:
        import numpy as _np
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        x_range = max(abs(xr - xl), 1e-9)
        y_range = max(abs(yt - yb), 1e-9)

        # --- Vertex pass ---
        v_thresh_sq = vertex_frac ** 2
        best_vd_sq = v_thresh_sq
        best_vx, best_vy, best_vcol = None, None, None
        for line in ax.get_lines():
            if not line.get_visible():
                continue
            xdata = _np.asarray(line.get_xdata(), dtype=float)
            ydata = _np.asarray(line.get_ydata(), dtype=float)
            if len(xdata) < 1:
                continue
            dx = (xdata - xd) / x_range
            dy = (ydata - yd) / y_range
            dist_sq = dx * dx + dy * dy
            idx = int(_np.argmin(dist_sq))
            if dist_sq[idx] < best_vd_sq:
                best_vd_sq = dist_sq[idx]
                best_vx = float(xdata[idx])
                best_vy = float(ydata[idx])
                best_vcol = _to_hex_color(line.get_color())
        if best_vx is not None:
            return (best_vx, best_vy, best_vcol, 'vertex')

        # --- Segment pass (interpolation along line) ---
        seg_thresh = segment_frac * y_range
        best_sd = seg_thresh
        best_sx, best_sy, best_scol = None, None, None
        for line in ax.get_lines():
            if not line.get_visible():
                continue
            xdata = _np.asarray(line.get_xdata(), dtype=float)
            ydata = _np.asarray(line.get_ydata(), dtype=float)
            if len(xdata) < 2:
                continue
            xmin, xmax = float(xdata.min()), float(xdata.max())
            if xd < xmin or xd > xmax:
                continue
            yi = float(_np.interp(xd, xdata, ydata))
            dist = abs(yi - yd)
            if dist < best_sd:
                best_sd = dist
                best_sx = xd
                best_sy = yi
                best_scol = _to_hex_color(line.get_color())
        if best_sx is not None:
            return (best_sx, best_sy, best_scol, 'segment')

        return (xd, yd, None, None)
    except Exception:
        return (xd, yd, None, None)


class _InlineValueEdit(QLineEdit):
    """A QLineEdit that floats directly over a matplotlib chart label so a
    value can be typed in place instead of through a popup dialog. Commits
    on Enter or on losing focus (editingFinished, connected by the
    caller); Escape cancels via this signal rather than committing."""
    escapePressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == _KEY_ESCAPE:
            self.escapePressed.emit()
            return
        super().keyPressEvent(event)


def _color_btn(hex_color: str, tooltip: str = 'Change colour') -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(22, 22)
    btn.setToolTip(tooltip)
    btn.setStyleSheet(
        f'background-color:{hex_color};border:1px solid #888;border-radius:2px;'
    )
    return btn


def _rm_btn(tip: str = 'Remove') -> QPushButton:
    b = QPushButton('×')
    b.setFixedSize(22, 22)
    b.setToolTip(tip)
    b.setStyleSheet('color:#E53935;font-weight:bold;font-size:13px;')
    return b


def _section_label(text: str, pixmap: QPixmap = None) -> QWidget:
    """Small bold sub-heading with an optional type badge — used to tell
    the Raster/Vector sections apart now that they share one scrollable
    list instead of separate tabs."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
    if pixmap is not None:
        badge = QLabel(); badge.setPixmap(pixmap); badge.setFixedSize(14, 14)
        h.addWidget(badge)
    lbl = QLabel(text)
    lbl.setStyleSheet(_style.section_label_style())
    h.addWidget(lbl)
    h.addStretch()
    return w


def _color_pix(color: QColor, size: int = 14) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(color)
    return QIcon(pix)


def _raster_type_pix(size: int = 14) -> QPixmap:
    """Grid-cross pixmap used as raster-layer type badge."""
    pix = QPixmap(size, size)
    pix.fill(QColor('#546E7A'))
    p = QPainter(pix)
    p.setPen(QPen(QColor(255, 255, 255, 170), 1))
    mid = size // 2
    p.drawLine(mid, 1, mid, size - 1)
    p.drawLine(1, mid, size - 1, mid)
    p.end()
    return pix


def _prune_mid(name: str, max_len: int = 28) -> str:
    """Shorten a long label by removing the middle, keeping start and end."""
    if len(name) <= max_len:
        return name
    keep = (max_len - 1) // 2
    return name[:keep] + '…' + name[-(max_len - keep - 1):]


def _vector_type_pix(size: int = 14) -> QPixmap:
    """Diagonal-line pixmap used as vector-layer type badge."""
    pix = QPixmap(size, size)
    pix.fill(QColor('#2E7D32'))
    p = QPainter(pix)
    p.setPen(QPen(QColor(255, 255, 255, 200), 2))
    p.drawLine(2, size - 2, size - 2, 2)
    p.end()
    return pix


def _xsec_type_pix(size: int = 14) -> QPixmap:
    """Cross-hair pixmap used as cross-section tab badge."""
    pix = QPixmap(size, size)
    pix.fill(QColor('#00838F'))
    p = QPainter(pix)
    p.setPen(QPen(QColor(255, 255, 255, 200), 2))
    mid = size // 2
    p.drawLine(1, mid, size - 1, mid)
    p.drawLine(mid, 1, mid, size - 1)
    p.end()
    return pix


# ---------------------------------------------------------------------------
# Global Ctrl-key tracker (application-level event filter)
# ---------------------------------------------------------------------------

class _CtrlKeyTracker(QObject):
    """Tracks whether Ctrl is currently held via canvas-level key events."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl_held = False

    def eventFilter(self, obj, event):
        t = event.type()
        if t == _KEY_PRESS_TYPE and event.key() == _KEY_CTRL:
            self.ctrl_held = True
        elif t == _KEY_RELEASE_TYPE and event.key() == _KEY_CTRL:
            self.ctrl_held = False
        return False  # never consume events


# ---------------------------------------------------------------------------
# Multi-select checkable combo (used in Profile Windows "Data" selector)
# ---------------------------------------------------------------------------

class _CheckCombo(QComboBox):
    """Drop-down combo with per-item checkboxes for multi-column window assignment.

    Empty checked list → behaves as "All" (no filtering).
    """

    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mdl = QStandardItemModel(self)
        self.setModel(self._mdl)
        self.setEditable(True)
        le = self.lineEdit()
        le.setReadOnly(True)
        le.setPlaceholderText('— All —')
        self._skip_hide = False
        self.view().pressed.connect(self._on_item_pressed)

    # ------------------------------------------------------------------
    def _on_item_pressed(self, index):
        item = self._mdl.itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            _UNCHECKED if item.checkState() == _CHECKED else _CHECKED
        )
        self._refresh_text()
        self._skip_hide = True          # prevent popup closing on item click
        self.selectionChanged.emit()

    def hidePopup(self):
        if self._skip_hide:
            self._skip_hide = False
            return
        super().hidePopup()

    # ------------------------------------------------------------------
    def _refresh_text(self):
        checked = [
            self._mdl.item(i).text()
            for i in range(self._mdl.rowCount())
            if self._mdl.item(i) and self._mdl.item(i).checkState() == _CHECKED
        ]
        le = self.lineEdit()
        if not checked:
            le.setText('')
        elif len(checked) == 1:
            le.setText(checked[0])
        elif len(checked) == 2:
            le.setText(f'{checked[0]}, {checked[1]}')
        else:
            le.setText(f'{checked[0]}, +{len(checked) - 1} more')

    # ------------------------------------------------------------------
    def populate(self, col_names, keep_checked=None):
        """Rebuild items, preserving previously-checked columns."""
        keep = set(keep_checked or [])
        self._mdl.clear()
        for name in col_names:
            item = QStandardItem(name)
            item.setFlags(_ITEM_FLAGS)
            item.setCheckState(_CHECKED if name in keep else _UNCHECKED)
            item.setData(name, _USER_ROLE)
            self._mdl.appendRow(item)
        self._refresh_text()

    def checked_cols(self):
        """Return checked column names; empty list means show all."""
        return [
            self._mdl.item(i).data(_USER_ROLE)
            for i in range(self._mdl.rowCount())
            if self._mdl.item(i) and self._mdl.item(i).checkState() == _CHECKED
        ]

    def uncheck(self, col_name):
        """Programmatically uncheck an item by name. Returns True if it was checked."""
        for i in range(self._mdl.rowCount()):
            item = self._mdl.item(i)
            if item and item.data(_USER_ROLE) == col_name and item.checkState() == _CHECKED:
                item.setCheckState(_UNCHECKED)
                self._refresh_text()
                return True
        return False

    def check_all_except(self, exclude_set):
        """Switch from All-mode to explicit selection, excluding named columns."""
        changed = False
        for i in range(self._mdl.rowCount()):
            item = self._mdl.item(i)
            if item is None:
                continue
            want = _UNCHECKED if item.data(_USER_ROLE) in exclude_set else _CHECKED
            if item.checkState() != want:
                item.setCheckState(want)
                changed = True
        if changed:
            self._refresh_text()

    def wheelEvent(self, e):
        e.ignore()   # prevent accidental scroll changes


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when it runs out of
    horizontal space, instead of clipping content or forcing a horizontal
    scrollbar. Standard/well-known Qt layout pattern (Qt's own C++
    examples ship an equivalent 'flow layout'); this is a PyQt port of it.

    Used for rows in the Modify Terrain tab that hold several fixed-width
    controls (buttons, labelled spin boxes) side by side — a plain
    QHBoxLayout has nowhere to put that content when the dock narrows
    below the row's full width except clip it or scroll to it, neither of
    which lets the user actually SEE and use every control while keeping
    the dock narrow (to leave more room for the map canvas). Each child
    widget keeps its own real minimum size — this only changes how many
    fit on one line, never shrinks a control below what it actually
    needs."""

    def __init__(self, parent=None, margin=0, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self):
        return self._h_spacing

    def verticalSpacing(self):
        return self._v_spacing

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        try:
            return Qt.Orientations(0)
        except Exception:
            return Qt.Orientation(0)  # type: ignore[attr-defined]

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    @staticmethod
    def _is_visible(item):
        # Qt's own layouts automatically skip hidden child widgets when
        # computing geometry (a hidden widget takes no space) — a custom
        # QLayout has to do this explicitly. This matters here: several
        # rows converted to FlowLayout are SHARED across tabs and hide
        # different buttons per tab (see _on_tab_changed) — without this
        # check, a hidden button would still occupy a wrap slot and leave
        # a phantom gap, or throw off wrapping for the buttons that ARE
        # visible.
        #
        # isHidden(), NOT isVisible(): isVisible() is also False for a
        # widget that's merely not on-screen because it sits on a
        # QTabWidget page that isn't current yet — nothing here ever
        # explicitly hid it. That falsely zeroed out this row's size the
        # very first time it was laid out (while its tab was still
        # inactive), and since nothing forces a relayout just because
        # the tab later became current (only a real resize would),
        # it stayed collapsed permanently — e.g. Embankment Surface's
        # Right Half action row (and the space it was sharing with the
        # section rows above it) reading as entirely blank. isHidden()
        # only reflects an explicit hide()/setVisible(False) on THIS
        # widget, which is what the shared-row use case above actually
        # needs.
        w = item.widget()
        return w is None or not w.isHidden()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not self._is_visible(item):
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0
        for item in self._items:
            if not self._is_visible(item):
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Dock widget
# ---------------------------------------------------------------------------

class NormalProfileDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__('Advanced Profile Tool', parent)
        self.iface  = iface
        self.canvas = iface.mapCanvas()

        # Profile state
        self.profile_geom        = None
        self._perm_band          = None
        self._map_line_color     = QColor(57, 255, 20)   # profile map rubber band colour
        self._map_line_width     = 3      # profile map rubber band width (px)
        self._map_line_opacity   = 255    # 0-255
        self._prev_tool          = None
        self._extracting         = False
        self._cursor_lines       = []     # one axvline per active profile window
        self._cc_hlines          = []     # one axhline per active profile window
        self._xsec_hline         = None
        self._xsec_fill_data     = []     # [{z_w, col_str, hatch, valid}] per layer
        self._xsec_fill_cols     = []     # live PolyCollection objects (removed on next hover)
        self._xsec_xs_w          = None   # chainage array in window (for fill redraw)
        self._xsec_z_top         = None   # rectangle ceiling (clamp cap for dynamic fill)
        self._xsec_curves_store  = {}     # {col: (elevs, areas, color)} for area interpolation
        self._xsec_area_text     = None   # floating Text artist in stage-area panel
        self._check_levels       = []     # user-defined static check level elevations
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._plot_cols_per_ax   = {}   # {axis_index: [col_name, ...]} in plot order
        self._pan_press_px       = None
        self._pan_xlim0          = None
        self._pan_ylim0          = None
        self._pan_transform      = None

        # Sketch tool state
        self._sketch_mode       = None    # 'pen' | 'line' | 'arrow' | 'text' | 'rect' | None
        self._sketch_color      = '#E53935'
        self._sketch_objects    = []      # all sketch artists, for bulk clear
        self._sketch_press_data = None    # (xdata, ydata) at mouse-press
        self._sketch_current    = None    # in-progress artist tuple (varies by mode)
        self._sketch_pen_pts    = ([], [])
        self._sketch_pressed    = False
        self._pen_poly_mode     = False   # True while Ctrl+pen polyline is active
        self._pen_poly_pts      = ([], []) # committed anchor points
        self._pen_poly_art      = None    # the polyline artist
        self._ctrl_tracker      = _CtrlKeyTracker(self)  # installed on canvas after canvas is built
        self._level_snap_art    = None    # temporary snap-indicator for level tool
        self._sketch_btns        = {}      # {mode: QPushButton}
        self._sketch_color_btn   = None    # active-color swatch
        self._sketch_drag_info   = None    # {obj, type, ...} set by 'move' press handler
        self._sketch_lw             = 2.0     # line width (pt)
        self._sketch_ls             = '-'     # line style: '-' '--' ':' '-.'
        self._sketch_lw_spin        = None    # widget ref
        self._sketch_ls_combo       = None    # widget ref
        self._sketch_pending_specs  = None    # saved across _rebuild_figure + _refresh_plot
        self._ch_cursors          = []   # [{'chainage': float, 'ax_idx': int, 'name': str}, ...]
        self._ch_cursor_artists   = []   # [list_of_artists_per_cursor, ...] parallel list
        self._ch_cursor_map_bands = []   # QgsRubberBand markers on the map canvas
        self._ch_cursor_annotations = [] # QgsTextAnnotation name labels on map canvas
        self._profile_markers      = []   # [{'chainage': float, 'point': QgsPointXY, 'name': str}, ...]
        self._profile_marker_artists = []  # matplotlib artists on the profile plot, one per marker
        self._profile_marker_bands = []   # QgsVertexMarker on the map canvas, one per marker
        self._xs_dialogs          = []   # open XSectionDialog instances for cut/fill sync
        self._terrain_check_xs_dialogs = []   # open TerrainCheckXSDialog instances
        self._embank_check_xs_dialogs = []   # open EmbankCheckXSDialog instances
        self._color_idx          = 0
        self._has_xsec_plot      = False
        self._xsec_dd            = None

        self._active_tab = 0   # 0 = Profile (raster+vector merged); see _on_tab_changed for the rest

        # Raster rows (manual, like vector rows)
        self._raster_rows = []

        # Vector rows
        self._vector_rows = []


        # ---- Modify Terrain (channel design) state -----------------------
        # Phase 1 scope only: draw the channel's horizontal alignment and
        # smooth its sharp bends with a fillet radius. No cross-section
        # shape or vertical levels yet — that comes in a later phase.
        self._terrain_raster_rows    = []   # DEM(s) — own list, own layout
        self._terrain_raw_pts        = []   # raw clicked vertices, pre-fillet
        self._terrain_geom           = None # QgsGeometry — filleted alignment in use
        self._terrain_align_band     = None # QgsRubberBand — the alignment line
        self._terrain_major_band     = None # QgsRubberBand — major chainage ticks
        self._terrain_minor_band     = None # QgsRubberBand — minor chainage ticks
        self._terrain_chainage_annotations = []  # (QgsAnnotationLayer, item_id) labels
        self._terrain_fillet_radius  = 0.0    # default radius, used where no override exists
        self._terrain_major_interval = 20.0
        self._terrain_minor_interval = 5.0
        self._terrain_show_major_ch  = True
        self._terrain_show_minor_ch  = True

        # Per-vertex fillet override — sharp bends can each be rounded by
        # a different amount instead of one radius for the whole
        # alignment. Keyed by index into self._terrain_raw_pts (interior
        # vertices only, i.e. never the first/last point); a vertex not in
        # this dict just uses _terrain_fillet_radius.
        self._terrain_vertex_radii          = {}
        self._terrain_selected_vertex       = None   # raw_pts index, or None
        self._terrain_vertex_bands          = []      # QgsRubberBand, one per interior vertex
        self._terrain_vertex_selected_band  = None    # QgsRubberBand — highlight for the selected one
        self._terrain_draw_start_extent     = None    # QgsRectangle — canvas extent from BEFORE 'Draw Line' was pressed

        self._terrain_map_tool = ProfileLineTool(self.canvas)
        self._terrain_map_tool.line_completed.connect(self._on_terrain_line_captured)
        self._terrain_vertex_tool = VertexSelectTool(self.canvas)
        self._terrain_vertex_tool.vertex_clicked.connect(self._on_terrain_vertex_clicked)
        self._terrain_vertex_tool.vertex_moved.connect(self._on_terrain_vertex_moved)
        self._terrain_vertex_tool.vertex_deleted.connect(self._on_terrain_vertex_deleted)

        # ---- Vertical alignment (channel invert) state --------------------
        # The invert is a polyline in chainage/level space: (0, us_level),
        # any interior break points (PVIs) the user adds, then
        # (length, ds_level). Drawn as an additive decoration on top of
        # whatever _plot() just drew — same pattern as the 1D Profile tab's
        # pipe/manhole decorations — so it survives independently of the
        # ground-profile redraw and is cheap to recompute on every edit.
        self._terrain_us_level       = None   # invert level at chainage 0, or None until seeded/set
        self._terrain_ds_level       = None   # invert level at the end chainage
        self._terrain_invert_breaks  = []     # [{'chainage': float, 'level': float}, ...] sorted
        self._terrain_ground_col     = None   # column name used as the cut/fill ground reference
        self._terrain_invert_artists = []     # matplotlib artists — invert line, cut/fill, break markers
        self._terrain_break_map_bands = []    # QgsRubberBand, one per break point
        self._terrain_break_annotations = []  # (QgsAnnotationLayer, item_id) — break chainage labels
        self._terrain_add_break_mode = False  # whether 'Add Vertical Break' tool is active
        self._terrain_break_drag_obj = None    # the break dict (by reference) currently being dragged, or None
        self._terrain_control_drag_obj = None  # the control-section dict (by reference) being dragged ALONG CHAINAGE ONLY, or None — no level of its own to drag
        self._terrain_add_control_mode = False  # whether 'Add Control Section' tool is active
        self._terrain_show_bank_analysis = False  # 'Show Bank Elevation Analysis' toggle
        self._terrain_show_diff      = False   # 'Show Difference (excavation depth)' toggle
        self._terrain_diff_ann       = None    # matplotlib text artist — live ΔY (ground - invert) readout
        self._terrain_endpoint_drag  = None    # 'us' | 'ds' | None — end-level triangle being dragged
        # Matplotlib artists that can be clicked to type a value directly
        # (only checked when 'Add Vertical Break' is off, so it never
        # fights with placing a new break) — rebuilt on every redraw.
        self._terrain_level_click_targets = []  # [{'artist':.., 'handle':(ch,level,kind,ref)}]
        self._terrain_slope_click_targets = []  # [{'artist':.., 'v0':handle, 'v1':handle}]
        self._terrain_inline_editor       = None  # _InlineValueEdit floating over the chart, or None
        self._terrain_inline_commit_fn    = None  # callable(str) run on commit, paired with the above

        # ---- Cross-Section (per vertical-alignment STATION) state --------
        # Each station — the US end, every break point, and the DS end —
        # gets its own cross-section shape, not each reach between them
        # (a reach's actual shape is whatever gets interpolated between
        # its two bounding stations later, at raster-generation time — not
        # this preview's job). Stations are identified directly: 'us', a
        # break's own id() (mutated in place, never replaced), or 'ds' —
        # never a positional index, since indices would shift every time a
        # break is added/removed elsewhere on the alignment.
        self._terrain_xs_default = {
            'bottom_width': 2.0, 'channel_height': 2.0,
            'left_inner_slope': 3.0, 'left_outer_slope': 3.0, 'left_berm_width': 0.0,
            'right_inner_slope': 3.0, 'right_outer_slope': 3.0, 'right_berm_width': 0.0,
            'left_option': 'flush', 'right_option': 'flush',
            # Earthwork (shared, not per-side) — a Fixed Channel Height
            # berm only makes sense as an embankment built up to reach
            # existing ground that's too low to reach it on its own. When
            # the actual required fill (the berm's own elevation minus
            # ground at the berm's own midpoint) is shallower than
            # min_embankment_height, building a separate berm+outer-slope
            # structure isn't worth it — instead this backfills flat at
            # the berm's own elevation, searching sideways for real ground
            # up to backfill_threshold metres before giving up and
            # reverting to the normal berm construction.
            'min_embankment_height': 0.3, 'backfill_threshold': 3.0,
        }
        self._terrain_xs_params        = {}     # key -> {..., 'left_option'/'right_option': 'flush'|'fixed'}
        self._terrain_xs_clipboard     = None   # dict copy of a station's params, or None — see _on_terrain_xs_copy_section/_paste_section
        self._terrain_selected_xs_key  = None   # currently selected segment's key, or None
        # [{'chainage': float}, ...] — CONTROL SECTIONS: lightweight extra
        # stations placed only to steer the reach-interpolation blend
        # (both numeric params AND Topo Adjustment — see
        # _terrain_xs_all_anchor_list), with a FULL cross-section of their
        # own (stored in self._terrain_xs_params, keyed by id() exactly
        # like a break point). Unlike a break point: no level/geometry of
        # its own (always reads the invert's own level via
        # _terrain_invert_level_at) and can't be dragged — only added,
        # selected, edited (same Cross-Section grid as any station) and
        # removed (right-click), same as break points otherwise.
        self._terrain_control_sections = []
        self._terrain_xs_map_band      = None   # QgsRubberBand — 'X-S' marker at the selected segment's centre
        self._terrain_xs_annotation    = None   # (QgsAnnotationLayer, item_id) — its label
        self._terrain_xs_ground        = None   # {'xs':[...], 'data':{col:[...]}, 'col_meta':{...}} or None
        self._terrain_xs_pan_press_px  = None   # middle-mouse pan state, own copy (separate from the main chart's)
        self._terrain_xs_pan_xlim0     = None
        self._terrain_xs_pan_ylim0     = None
        self._terrain_xs_pan_transform = None
        self._terrain_xs_elev_mode     = False   # 'Elev' click-to-read-elevation tool, on/off
        self._terrain_xs_elev_markers  = []      # [{'x','y','marker','text'}, ...] — one per spot, independent
        self.btn_terrain_xs_elev       = None
        # 'Add' comparison overlay — lets the user pick any raster (e.g. a
        # just-created channel-design TIFF) and see it sampled along the
        # SAME perpendicular line as the current preview, to check the
        # actual rasterised surface against the design/ground it was
        # built from. self._terrain_last_tiff_layer remembers the most
        # recently Create-TIFF'd layer, offered as the default pick.
        self.btn_terrain_xs_add_compare = None
        self._terrain_xs_compare_layer  = None
        self._terrain_xs_compare        = None
        self._terrain_last_tiff_layer   = None
        # Vertical exaggeration — same idea as QGIS's own terrain Z
        # factor: 1 = true (equal-aspect) scale, 2 = vertical doubled,
        # etc. This embedded preview's OWN value — Check Cross-Section
        # keeps its own independent one (TerrainCheckXSDialog._z_factor),
        # on purpose, per explicit request that the two not affect each
        # other.
        self._terrain_xs_z_factor      = 1.0
        self.zf_terrain_xs_widget      = None
        self.spin_terrain_xs_zfactor   = None
        # The currently OPEN channel-design project folder (the one last
        # Saved to or Opened from), or None if nothing's open yet. Lets
        # 'Save' re-save straight into the same folder without re-asking
        # for a name every time — only the first Save (or Save after a
        # Close) prompts for one. See _on_terrain_project_save/open/close.
        self._terrain_project_folder    = None
        self.figure_terrain_xs = None
        self.ax_terrain_xs     = None
        self.canvas_terrain_xs = None


        # ---- Embankment Surface state -------------------------------------
        # Same general architecture as Channel Surface (own independent
        # alignment/vertical/cross-section/corridor state, nothing shared
        # except the generic _extract/_plot engine and map tools) — see
        # _build_embank_tab.
        self._embank_raster_rows    = []   # DEM(s) — own list, own layout
        self._embank_raw_pts        = []   # raw clicked vertices, pre-fillet
        self._embank_geom           = None # QgsGeometry — filleted alignment in use
        self._embank_align_band     = None # QgsRubberBand — the alignment line
        self._embank_extend_band_us = None # QgsRubberBand — dashed blue Extend US zone, map canvas
        self._embank_extend_band_ds = None # QgsRubberBand — dashed blue Extend DS zone, map canvas
        self._embank_major_band     = None # QgsRubberBand — major chainage ticks
        self._embank_minor_band     = None # QgsRubberBand — minor chainage ticks
        self._embank_chainage_annotations = []  # (QgsAnnotationLayer, item_id) labels
        self._embank_fillet_radius  = 0.0    # default radius, used where no override exists
        self._embank_major_interval = 20.0
        self._embank_minor_interval = 5.0
        self._embank_show_major_ch  = True
        self._embank_show_minor_ch  = True

        # Per-vertex fillet override — sharp bends can each be rounded by
        # a different amount instead of one radius for the whole
        # alignment. Keyed by index into self._embank_raw_pts (interior
        # vertices only, i.e. never the first/last point); a vertex not in
        # this dict just uses _embank_fillet_radius.
        self._embank_vertex_radii          = {}
        self._embank_selected_vertex       = None   # raw_pts index, or None
        self._embank_vertex_bands          = []      # QgsRubberBand, one per interior vertex
        self._embank_vertex_selected_band  = None    # QgsRubberBand — highlight for the selected one
        self._embank_draw_start_extent     = None    # QgsRectangle — canvas extent from BEFORE 'Draw Line' was pressed

        self._embank_map_tool = ProfileLineTool(self.canvas)
        self._embank_map_tool.line_completed.connect(self._on_embank_line_captured)
        self._embank_vertex_tool = VertexSelectTool(self.canvas)
        self._embank_vertex_tool.vertex_clicked.connect(self._on_embank_vertex_clicked)
        self._embank_vertex_tool.vertex_moved.connect(self._on_embank_vertex_moved)
        self._embank_vertex_tool.vertex_deleted.connect(self._on_embank_vertex_deleted)

        # ---- Vertical alignment (channel invert) state --------------------
        # The invert is a polyline in chainage/level space: (0, us_level),
        # any interior break points (PVIs) the user adds, then
        # (length, ds_level). Drawn as an additive decoration on top of
        # whatever _plot() just drew — same pattern as the 1D Profile tab's
        # pipe/manhole decorations — so it survives independently of the
        # ground-profile redraw and is cheap to recompute on every edit.
        self._embank_us_level       = None   # invert level at chainage 0, or None until seeded/set
        self._embank_ds_level       = None   # invert level at the end chainage
        self._embank_invert_breaks  = []     # [{'chainage': float, 'level': float}, ...] sorted
        self._embank_ground_col     = None   # column name used as the cut/fill ground reference
        # How far PAST each end of the alignment the ground profile is
        # sampled/drawn — a pure visualisation aid, see
        # _embank_extended_ground_geom. 0 = today's behaviour exactly.
        self._embank_extend_us      = 0.0
        self._embank_extend_ds      = 0.0
        # When ON, Create TIFF closes the embankment out past each real
        # end (US/DS) over the SAME Extend US/DS distance above — crest
        # level held CONSTANT (no tapering down), each side's own section
        # stack reused from its nearest real end, so the existing ground-
        # aware 'no cut' logic (_embank_xs_section_endpoint) collapses
        # the design on its own as real ground rises to meet that level —
        # a smooth closure into the natural ground, like a dam's
        # abutments, rather than an abrupt flat end face. OFF (the
        # default) reproduces today's behaviour exactly regardless of
        # Extend US/DS (that pair stays a pure visualisation aid on its
        # own). See _embank_build_channel_corridor_data.
        self._embank_end_adjustment = False
        self._embank_invert_artists = []     # matplotlib artists — invert line, cut/fill, break markers
        self._embank_break_map_bands = []    # QgsRubberBand, one per break point
        self._embank_break_annotations = []  # (QgsAnnotationLayer, item_id) — break chainage labels
        self._embank_add_break_mode = False  # whether 'Add Vertical Break' tool is active
        self._embank_break_drag_obj = None    # the break dict (by reference) currently being dragged, or None
        self._embank_add_control_mode = False  # whether 'Add Control Section' tool is active
        self._embank_show_diff      = False   # 'Fill Depth [m]' toggle
        self._embank_diff_ann       = None    # matplotlib text artist — live Fill Depth (crest - ground) readout
        self._embank_endpoint_drag  = None    # 'us' | 'ds' | None — end-level triangle being dragged
        self._embank_endpoint_drag_chainage = None  # live preview chainage while dragging horizontally — committed to the real alignment only on release, see _on_mouse_release
        # Matplotlib artists that can be clicked to type a value directly
        # (only checked when 'Add Vertical Break' is off, so it never
        # fights with placing a new break) — rebuilt on every redraw.
        self._embank_level_click_targets = []  # [{'artist':.., 'handle':(ch,level,kind,ref)}]
        self._embank_slope_click_targets = []  # [{'artist':.., 'v0':handle, 'v1':handle}]
        self._embank_inline_editor       = None  # _InlineValueEdit floating over the chart, or None
        self._embank_inline_commit_fn    = None  # callable(str) run on commit, paired with the above

        # ---- Cross-Section (per vertical-alignment STATION) state --------
        # Each station — the US end, every break point, and the DS end —
        # gets its own cross-section shape, not each reach between them
        # (a reach's actual shape is whatever gets interpolated between
        # its two bounding stations later, at raster-generation time — not
        # this preview's job). Stations are identified directly: 'us', a
        # break's own id() (mutated in place, never replaced), or 'ds' —
        # never a positional index, since indices would shift every time a
        # break is added/removed elsewhere on the alignment.
        # CROSS-SECTION — each side (left/right, independently) is built
        # as an ordered STACK of sections outward/downward from the
        # crest-edge vertex (half the crest width out from centreline),
        # not a fixed slope/berm/slope template. Each section is
        # {'slope': H:V ratio, 'berm_width': float, 'height': float|None}
        # — there's no separate 'wall' kind; a wall is just a section
        # with slope 0 (a plain vertical face), so one builder
        # (_embank_xs_build_side_sections) handles every case. 'height'
        # is the vertical extent of THIS section's own slope run; None is
        # only valid on the LAST section in the list and means 'flush' —
        # run at this section's own slope until it meets existing ground.
        # 'berm_width' is a flat bench BEFORE this section's own slope
        # starts (0 = none) — the section always begins at the END of
        # its own leading berm, so the very FIRST section (starting
        # right at the crest edge, nothing before it) never has one.
        # Built per explicit request for a 'stack sections one at a
        # time, independently per side' workflow in place of the old
        # fixed Side-slope/Berm/Batter-slope template, which capped a
        # side at exactly one berm, mirrored both sides identically, and
        # couldn't represent a tall embankment's real multi-bench batter.
        self._embank_xs_default = {
            'bottom_width': 2.0,
            'left_sections':  [{'slope': 3.0, 'berm_width': 0.0, 'height': None}],
            'right_sections': [{'slope': 3.0, 'berm_width': 0.0, 'height': None}],
        }
        self._embank_xs_params        = {}     # key -> {'bottom_width': float, 'left_sections': [...], 'right_sections': [...]}
        self._embank_selected_xs_key  = None   # currently selected segment's key, or None
        # [{'chainage': float}, ...] — CONTROL SECTIONS: lightweight extra
        # stations placed only to steer the reach-interpolation blend
        # (both numeric params AND Topo Adjustment — see
        # _embank_xs_all_anchor_list), with a FULL cross-section of their
        # own (stored in self._embank_xs_params, keyed by id() exactly
        # like a break point). Unlike a break point: no level/geometry of
        # its own (always reads the invert's own level via
        # _embank_invert_level_at) and can't be dragged — only added,
        # selected, edited (same Cross-Section grid as any station) and
        # removed (right-click), same as break points otherwise.
        self._embank_control_sections = []
        self._embank_xs_map_band      = None   # QgsRubberBand — 'X-S' marker at the selected segment's centre
        self._embank_xs_annotation    = None   # (QgsAnnotationLayer, item_id) — its label
        self._embank_xs_ground        = None   # {'xs':[...], 'data':{col:[...]}, 'col_meta':{...}} or None
        self._embank_xs_pan_press_px  = None   # middle-mouse pan state, own copy (separate from the main chart's)
        self._embank_xs_pan_xlim0     = None
        self._embank_xs_pan_ylim0     = None
        self._embank_xs_pan_transform = None
        self._embank_xs_elev_mode     = False   # 'Elev' click-to-read-elevation tool, on/off
        self._embank_xs_elev_markers  = []      # [{'x','y','marker','text'}, ...] — one per spot, independent
        self.btn_embank_xs_elev       = None
        # 'Add' comparison overlay — lets the user pick any raster (e.g. a
        # just-created channel-design TIFF) and see it sampled along the
        # SAME perpendicular line as the current preview, to check the
        # actual rasterised surface against the design/ground it was
        # built from. self._embank_last_tiff_layer remembers the most
        # recently Create-TIFF'd layer, offered as the default pick.
        self.btn_embank_xs_add_compare = None
        self._embank_xs_compare_layer  = None
        self._embank_xs_compare        = None
        self._embank_last_tiff_layer   = None
        # Vertical exaggeration — same idea as QGIS's own embankment Z
        # factor: 1 = true (equal-aspect) scale, 2 = vertical doubled,
        # etc. This embedded preview's OWN value — Check Cross-Section
        # keeps its own independent one (EmbankCheckXSDialog._z_factor),
        # on purpose, per explicit request that the two not affect each
        # other.
        self._embank_xs_z_factor      = 1.0
        self.zf_embank_xs_widget      = None
        self.spin_embank_xs_zfactor   = None
        # The currently OPEN channel-design project folder (the one last
        # Saved to or Opened from), or None if nothing's open yet. Lets
        # 'Save' re-save straight into the same folder without re-asking
        # for a name every time — only the first Save (or Save after a
        # Close) prompts for one. See _on_embank_project_save/open/close.
        self._embank_project_folder    = None
        self.figure_embank_xs = None
        self.ax_embank_xs     = None
        self.canvas_embank_xs = None

        # ---- Shapes and Polygon Surface — own map tool (ShapeDrawTool,
        # one tool shared across all 5 shape modes rather than a separate
        # QgsMapTool per shape) and own state, same overall conventions as
        # Embankment/Channel Surface above but with no alignment/chainage
        # concept at all — just one drawn shape, one flat target elevation
        # (relative to ground or absolute), and one edge slope out to
        # existing ground.
        self._shapes_map_tool = ShapeDrawTool(self.canvas)
        self._shapes_map_tool.shape_completed.connect(self._on_shapes_shape_completed)
        # Rotate is a SEPARATE map tool (angle-only drag, see
        # ShapeRotateTool) activated in place of _shapes_map_tool while
        # the Rotate button is checked — not another ShapeDrawTool mode,
        # since rotating isn't drawing.
        self._shapes_rotate_tool = ShapeRotateTool(self.canvas)
        self._shapes_rotate_tool.rotate_preview.connect(self._on_shapes_rotate_preview)
        self._shapes_rotate_tool.rotate_committed.connect(self._on_shapes_rotate_committed)
        self._shapes_geom            = None   # QgsGeometry (polygon) — the current drawn shape, or None
        self._shapes_band            = None   # QgsRubberBand — the committed shape outline on the map
        self._shapes_elevation_mode  = 'relative'   # 'relative' | 'absolute'
        self._shapes_elevation_value = 2.0
        self._shapes_slope           = 2.0    # H:V batter out to ground; 0 = vertical edge
        self._shapes_rotation_deg    = 0.0    # current rotation from as-drawn orientation
        self._shapes_rotate_base_geom     = None   # snapshot of _shapes_geom at the start of a rotate drag
        self._shapes_rotate_centroid      = None   # that snapshot's own centroid — rotation pivot stays fixed
        self._shapes_current_mode         = None   # which _shapes_tool_btns key is active, or None
        self._shapes_last_tiff_layer = None

        # ---- Wetland Pond — a CLOSED-ring boundary workflow nested
        # under Polygon, own Plan (draw + fillet, like Horizontal/
        # Vertical Alignment) and Section (per-vertex section-stack,
        # like Embankment's) mini-tabs, same overall architecture as
        # Channel/Embankment Surface — see _build_wetland_tab. First
        # structural pass per explicit request ('we will change what to
        # add in each section later on') — no surface generation wired
        # up yet, no Elevation/Create TIFF of its own.
        self._wetland_draw_tool = ShapeDrawTool(self.canvas)
        self._wetland_draw_tool.shape_completed.connect(self._on_wetland_polygon_completed)
        self._wetland_vertex_tool = VertexSelectTool(self.canvas)
        self._wetland_vertex_tool.vertex_clicked.connect(self._on_wetland_vertex_clicked)
        self._wetland_vertex_tool.vertex_moved.connect(self._on_wetland_vertex_moved)
        self._wetland_vertex_tool.vertex_deleted.connect(self._on_wetland_vertex_deleted)
        self._wetland_raw_pts       = []     # [QgsPointXY, ...] raw clicked boundary vertices, pre-fillet
        self._wetland_geom          = None   # QgsGeometry (polygon) — filleted ring in use
        self._wetland_band          = None   # QgsRubberBand — the boundary outline on the map
        self._wetland_fillet_radius = 0.0
        self._wetland_vertex_radii  = {}     # vertex index -> override radius
        # ONE selection shared by Plan's per-vertex fillet override AND
        # Section's per-vertex section-stack — there's only one set of
        # points here (unlike Embankment, where the alignment's own
        # vertices and the invert's break points are two different point
        # sets needing two different selections).
        self._wetland_selected_vertex = None
        self._wetland_xs_params     = {}     # vertex index -> {'sections': [...]}
        self._wetland_xs_sel_idx    = None   # which row within the selected vertex's own stack
        self._wetland_vertex_bands  = []     # QgsRubberBand per vertex — every vertex's own marker
        self._wetland_vertex_selected_band = None   # QgsRubberBand — the currently selected vertex's highlight

        # Extra profile axes for split-window mode (populated by _rebuild_figure)
        self._extra_axes = []
        # Profile-window configs (populated by _build_ui)
        self._win_cfgs = []

        self.ax_xsec = None

        self._hover_band = QgsRubberBand(self.canvas, _POINT_GEOM)
        self._hover_band.setColor(QColor(211, 47, 47, 230))
        self._hover_band.setIcon(_ICON_CIRCLE)
        self._hover_band.setIconSize(14)

        self._map_tool = ProfileLineTool(self.canvas)
        self._map_tool.line_completed.connect(self._on_line_captured)
        self._map_tool.vertex_added.connect(self._on_live_update)

        self._cleanup_stray_terrain_annotations()
        self._cleanup_stray_embank_annotations()
        self._build_ui()
        self.visibilityChanged.connect(self._on_visibility_changed)

        # Escape cancels whichever Embankment Surface tool is currently
        # active (Draw Line / Edit Vertices / Vertical Break / Control
        # Section / spot-elevation) — same effect as clicking that tool's
        # own button again, per explicit request so a mode never has to
        # be manually toggled back off. Bound to the dock itself (default
        # WindowShortcut context) so it fires regardless of which child
        # widget — the map canvas, the chart, a spin box — currently has
        # focus, as long as the dock's own top-level window does; a no-op
        # when nothing's active (see _embank_escape_deactivate_tool), so
        # it never steals Escape from something unrelated.
        self._embank_escape_shortcut = QShortcut(QKeySequence(_KEY_ESCAPE), self)
        self._embank_escape_shortcut.activated.connect(self._embank_escape_deactivate_tool)

        # Same as above, for Channel Surface's own tools (Draw Line / Edit
        # Vertices / Vertical Break / Control Section / spot-elevation).
        self._terrain_escape_shortcut = QShortcut(QKeySequence(_KEY_ESCAPE), self)
        self._terrain_escape_shortcut.activated.connect(self._terrain_escape_deactivate_tool)

        # Same as above, for Shapes and Polygon Surface's own shape tools.
        self._shapes_escape_shortcut = QShortcut(QKeySequence(_KEY_ESCAPE), self)
        self._shapes_escape_shortcut.activated.connect(self._shapes_escape_deactivate_tool)

    # ------------------------------------------------------------------ colour

    def _next_color(self) -> str:
        c = _CHART_COLORS[self._color_idx % len(_CHART_COLORS)]
        self._color_idx += 1
        return c

    # ------------------------------------------------------------------ raster rows

    def _add_raster_row(self, rows_list=None, layout=None,
                         on_data_change=None, on_style_change=None):
        """Builds one raster-layer row. Defaults reproduce the original
        Profile-tab behaviour exactly; rows_list/layout/callbacks can be
        overridden so this widget-building code can be reused by another
        tab's own independent row list without duplicating it."""
        rows_list       = self._raster_rows if rows_list is None else rows_list
        layout          = self._raster_layout if layout is None else layout
        on_data_change  = self._trigger_update if on_data_change is None else on_data_change
        on_style_change = self._refresh_plot if on_style_change is None else on_style_change

        row = {'_col': None}
        frame = QFrame()
        frame.setStyleSheet(
            'QFrame{border:1px solid #BDBDBD;border-radius:3px;margin-top:2px;}'
        )
        fl = QHBoxLayout(frame)
        fl.setContentsMargins(4, 3, 4, 3); fl.setSpacing(3)

        tog = QCheckBox(); tog.setChecked(True)
        tog.stateChanged.connect(on_style_change)

        badge = QLabel()
        badge.setPixmap(_raster_type_pix(14))
        badge.setFixedSize(16, 16)
        badge.setToolTip('Raster layer')

        lc = QgsMapLayerComboBox()
        lc.setFilters(_RASTER_FILTER)
        lc.setAllowEmptyLayer(True)
        lc.setCurrentIndex(0)
        lc.layerChanged.connect(lambda _: on_data_change())
        lc.wheelEvent = lambda e: e.ignore()

        ls_combo = QComboBox()
        ls_combo.setFixedWidth(88)
        for code, label in _LINESTYLES:
            ls_combo.addItem(label, code)
        ls_combo.currentIndexChanged.connect(on_style_change)
        ls_combo.wheelEvent = lambda e: e.ignore()

        hex_c = self._next_color()
        row['color'] = QColor(hex_c)
        c_btn = _color_btn(hex_c)
        c_btn.clicked.connect(lambda: self._pick_raster_color(row, on_style_change))

        lw_spin = QDoubleSpinBox()
        lw_spin.setRange(0.5, 5.0); lw_spin.setValue(1.5); lw_spin.setSingleStep(0.5)
        lw_spin.setDecimals(1); lw_spin.setFixedWidth(48)
        lw_spin.setToolTip('Line width')
        lw_spin.valueChanged.connect(on_style_change)

        al_spin = QSpinBox()
        al_spin.setRange(10, 100); al_spin.setValue(100); al_spin.setSuffix('%')
        al_spin.setFixedWidth(52)
        al_spin.setToolTip('Opacity')
        al_spin.valueChanged.connect(on_style_change)

        rm = _rm_btn('Remove raster layer')
        rm.clicked.connect(lambda: self._remove_raster_row(row, rows_list, on_data_change))

        fl.addWidget(tog); fl.addWidget(badge); fl.addWidget(lc, 1)
        fl.addWidget(ls_combo); fl.addWidget(lw_spin); fl.addWidget(al_spin)
        fl.addWidget(c_btn); fl.addWidget(rm)

        row.update({'widget': frame, 'toggle': tog,
                    'layer_combo': lc, 'ls_combo': ls_combo, 'color_btn': c_btn,
                    'lw_spin': lw_spin, 'al_spin': al_spin})
        layout.insertWidget(layout.count() - 1, frame)
        rows_list.append(row)

    def _remove_raster_row(self, row, rows_list=None, on_change=None):
        rows_list = self._raster_rows if rows_list is None else rows_list
        on_change = self._trigger_update if on_change is None else on_change
        rows_list.remove(row)
        row['widget'].setParent(None); row['widget'].deleteLater()
        on_change()

    def _pick_raster_color(self, row, on_change=None):
        on_change = self._refresh_plot if on_change is None else on_change
        c = QColorDialog.getColor(row['color'], self)
        if c.isValid():
            row['color'] = c
            row['color_btn'].setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;'
            )
            on_change()

    # ------------------------------------------------------------------ profile windows

    def _n_active_wins(self):
        count = 1
        for cfg in (self._win_cfgs[1:] if len(self._win_cfgs) > 1 else []):
            if cfg['enabled_cb'].isChecked():
                count += 1
        return min(count, 3)

    def _on_win_enable_changed(self):
        for i, cfg in enumerate(self._win_cfgs):
            if i == 0:
                continue
            win_on = cfg['enabled_cb'].isChecked()
            auto   = cfg['auto_cb'].isChecked()
            cfg['auto_cb'].setEnabled(win_on)
            cfg['ymin'].setEnabled(win_on and not auto)
            cfg['ymax'].setEnabled(win_on and not auto)
        if MATPLOTLIB_AVAILABLE:
            self._rebuild_figure(self._has_xsec_plot)
            self._refresh_plot()

    def _on_win_auto_changed(self, i):
        cfg    = self._win_cfgs[i]
        win_on = (i == 0) or cfg['enabled_cb'].isChecked()
        auto   = cfg['auto_cb'].isChecked()
        cfg['ymin'].setEnabled(win_on and not auto)
        cfg['ymax'].setEnabled(win_on and not auto)
        self._refresh_plot()

    def _on_win_col_changed(self, src_i):
        """When columns are checked in window src_i, remove them from all other windows.
        If another window is in All-mode (nothing checked), switch it to explicit mode
        keeping everything except the claimed columns."""
        if getattr(self, '_win_col_updating', False):
            return
        self._win_col_updating = True
        try:
            claimed = set(self._win_cfgs[src_i]['col_combo'].checked_cols())
            if not claimed:
                # Source unchecked everything — went back to All-mode, nothing to claim
                return
            for j, cfg in enumerate(self._win_cfgs):
                if j == src_i:
                    continue
                combo = cfg['col_combo']
                if not combo.checked_cols():
                    # Window is in All-mode: switch to explicit, exclude claimed cols
                    combo.check_all_except(claimed)
                else:
                    for col in claimed:
                        combo.uncheck(col)
        finally:
            self._win_col_updating = False
        self._refresh_plot()

    def _maybe_prompt_save_on_tab_leave(self, old_idx):
        """Offers to save unsaved work when leaving Embankment Surface or
        Channel Surface for a different tab — only when there's actually
        a design to save (an alignment has been drawn on that tab), never
        just from switching through an already-blank one. Per explicit
        request: this is a plain offer, not a blocking 'unsaved changes'
        gate — the tab switch always proceeds either way; Save/Don't Save
        only decides whether _on_embank_project_save/_on_terrain_project_
        save (the SAME Save used elsewhere, folder-name prompt and all)
        runs first. Shapes and Polygon Surface (_TAB_SHAPES) has no
        design/save flow yet, so there's nothing to offer there."""
        if old_idx == _TAB_EMBANK and self._embank_geom is not None:
            reply = QMessageBox.question(
                self, 'Advanced Profile Tool',
                'Save the current Embankment design before leaving this tab?',
                _MB_YES | _MB_NO)
            if reply == _MB_YES:
                self._on_embank_project_save()
        elif old_idx == _TAB_CHANNEL and self._terrain_geom is not None:
            reply = QMessageBox.question(
                self, 'Advanced Profile Tool',
                'Save the current Channel design before leaving this tab?',
                _MB_YES | _MB_NO)
            if reply == _MB_YES:
                self._on_terrain_project_save()

    def _on_tab_changed(self, idx):
        old_idx = self._active_tab
        if old_idx != idx:
            self._maybe_prompt_save_on_tab_leave(old_idx)
        self._active_tab = idx
        # Clear all profile state — each tab starts fresh. Modify Terrain
        # (idx 3) is the deliberate EXCEPTION: its channel design is a
        # real, multi-step piece of work meant to survive the whole
        # session (and be saved/opened as a project — see
        # _terrain_collect_save_state / _on_terrain_project_save) rather
        # than a throwaway per-visit scratch profile like Layer Profile's
        # drawn line or the 1D Profile's pipe chain. This used to call
        # _clear_terrain_alignment() unconditionally here, silently
        # wiping the whole design (alignment, invert, cross-sections,
        # control sections) just from switching to another tab and back —
        # that's fixed below by never clearing it on a switch, only
        # re-drawing it from the still-intact self._terrain_* state when
        # Modify Terrain becomes active again (see the end of this
        # method).
        self.profile_geom = None
        if self._perm_band:
            try:
                self.canvas.scene().removeItem(self._perm_band)
            except Exception:
                pass
            self._perm_band = None
        # Properly deactivate EVERY checkable 'mode' tool on every tab, not
        # just the one belonging to whichever tab is becoming active — a
        # tool left active on a tab you just switched AWAY from used to
        # keep receiving map clicks (e.g. Channel Surface's own Draw Line
        # still adding vertices while Layer Profile was on screen), since
        # unchecking a button's VISUAL state alone doesn't unset it as the
        # canvas's actual QgsMapTool. _embank_deactivate_all_tools/
        # _terrain_deactivate_all_tools each already call every one of
        # their own tools' real toggle handler (not just setChecked),
        # which is what actually calls canvas.unsetMapTool.
        if self.canvas.mapTool() is self._map_tool:
            self.canvas.unsetMapTool(self._map_tool)
        self._map_tool.reset()
        self.btn_draw.setChecked(False)
        if hasattr(self, '_embank_deactivate_all_tools'):
            self._embank_deactivate_all_tools()
        if hasattr(self, '_terrain_deactivate_all_tools'):
            self._terrain_deactivate_all_tools()
        if hasattr(self, '_shapes_deactivate_all_tools'):
            self._shapes_deactivate_all_tools()
        if hasattr(self, '_wetland_deactivate_all_tools'):
            self._wetland_deactivate_all_tools()
        # Ghost break-point/control-section hover bands and the selected-
        # station 'X-S' marker + label are redrawn fresh every time the
        # embedded vertical chart redraws (_draw_terrain/embank_vertical_
        # decorations already clears them first) — but that only happens
        # while that tab is active, so whichever of these was showing at
        # the exact moment of switching away used to stay orphaned on the
        # map canvas indefinitely, visible even on an unrelated tab.
        if hasattr(self, '_clear_terrain_break_map_bands'):
            self._clear_terrain_break_map_bands()
            self._clear_terrain_xs_map_marker()
        if hasattr(self, '_clear_embank_break_map_bands'):
            self._clear_embank_break_map_bands()
            self._clear_embank_xs_map_marker()
        # Any open Cross-Section popup (XSectionDialog) owns its own map
        # rubber band/hover dot, cleaned up only in its own closeEvent
        # (see _close_all_xs_dialogs' docstring, which already documented
        # 'switching tabs' as one of the times this should run — it just
        # was never actually wired up here). Without this, a dialog
        # opened from a chainage-cursor marker on Layer Profile stayed
        # open — and its map marker visible — no matter which tab was
        # switched to afterwards, reading as that marker 'bleeding' into
        # e.g. Modify Terrain when it was really just an orphaned window
        # still sitting on top of everything.
        self._close_all_xs_dialogs()
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
        self._profile_markers        = []
        self._profile_marker_artists = []
        self._clear_marker_map_points()
        self.lbl_line.setText('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_status.setText('')
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._hide_hover()
        if MATPLOTLIB_AVAILABLE:
            self._cursor_lines = []
            self._cc_hlines    = []
            self._xsec_hline   = None
            self._reset_axes()
            self.canvas_plot.draw()
        if self.btn_save_png is not None:
            self.btn_save_png.setEnabled(False)
        for combo in (self.cutfill_y1, self.cutfill_y2):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        # Tab-specific controls: 'Draw Profile Line'/'From layer' only make
        # sense for the Layer Profile tab. Channel Surface and Embankment
        # Surface each have their own embedded Draw Line/Add Vertical
        # Break buttons in their own sections, and share only Refresh/
        # Chart/Clear with Layer Profile here (wired by _active_tab
        # dispatch); neither has a 'From layer' row or a Profile Windows
        # (split-window) section — an alignment is always hand-drawn, and
        # only ever shows one ground-profile window. Shapes and Polygon
        # Surface gets none of this until its own UI is built.
        is_layer   = (idx == _TAB_LAYER)
        is_embank  = (idx == _TAB_EMBANK)
        is_terrain = (idx == _TAB_CHANNEL)
        is_shapes  = (idx == _TAB_SHAPES)
        if hasattr(self, '_line_controls_widget'):
            self._line_controls_widget.setVisible(is_layer)
        if hasattr(self, 'btn_draw'):
            # Channel/Embankment Surface each have their own embedded Draw
            # Line button now — this shared one is Layer Profile-only.
            # Clear stays shared across those three (_clear_line dispatches
            # by _active_tab) — Shapes and Polygon Surface has its own
            # separate, narrower 'Clear Shape' button instead, since this
            # shared one has no idea how to clear a drawn shape.
            self.btn_draw.setVisible(is_layer)
            self.btn_clear.setVisible(is_layer or is_embank or is_terrain)
        if hasattr(self, 'btn_open_chart'):
            # Channel/Embankment Surface each have their own 'Chart'
            # button embedded in the Vertical Alignment section (fits
            # that workflow's own order better than the shared bottom
            # row) — this shared one hides there so there aren't two.
            # Shapes and Polygon Surface has no profile chart at all (no
            # linear alignment to show one of).
            self.btn_open_chart.setVisible(not (is_embank or is_terrain or is_shapes))
        if hasattr(self, 'btn_embank_create_tiff'):
            self.btn_embank_create_tiff.setVisible(is_embank)
            self.btn_embank_merge_tiff.setVisible(is_embank)
        if hasattr(self, 'btn_embank_project_save'):
            self.btn_embank_project_save.setVisible(is_embank)
            self.btn_embank_project_open.setVisible(is_embank)
            self.btn_embank_project_close.setVisible(is_embank)
        if hasattr(self, 'btn_terrain_create_tiff'):
            self.btn_terrain_create_tiff.setVisible(is_terrain)
            self.btn_terrain_merge_tiff.setVisible(is_terrain)
        if hasattr(self, 'btn_terrain_project_save'):
            self.btn_terrain_project_save.setVisible(is_terrain)
            self.btn_terrain_project_open.setVisible(is_terrain)
            self.btn_terrain_project_close.setVisible(is_terrain)
        if hasattr(self, 'btn_shapes_create_tiff'):
            self.btn_shapes_create_tiff.setVisible(is_shapes)
        if hasattr(self, '_map_line_style_widgets'):
            for w in self._map_line_style_widgets:
                w.setVisible(is_layer)
        if hasattr(self, 'interval_spin'):
            # Channel/Embankment Surface each have their own dedicated
            # Interval field in their own Cross-Section area, and Shapes
            # and Polygon Surface has its own Resolution field — this
            # shared one does nothing on any of them, so showing both is
            # just confusing (two identical fields on screen at once).
            self.interval_spin.setVisible(not (is_embank or is_terrain or is_shapes))
            self._interval_spin_lbl.setVisible(not (is_embank or is_terrain or is_shapes))
        if hasattr(self, '_win_tabs_widget'):
            # Only Layer Profile uses the Profile Windows (split-window)
            # section — Shapes and Polygon Surface is a placeholder for
            # now, Channel/Embankment Surface always show just the one
            # ground-profile window instead (see is_terrain/is_embank
            # above).
            self._win_tabs_widget.setVisible(is_layer)

        # Redraw the design from its still-intact self._terrain_*/
        # self._embank_* state now that this tab is active again — the
        # generic reset above wiped the shared self.profile_geom/
        # _profile_chainages/chart (needed for Layer Profile's own 'fresh
        # start' convention) without touching that state, so a plain
        # re-draw is all that's needed; nothing was actually lost.
        if is_embank and self._embank_geom is not None:
            self._rebuild_embank_alignment()
        if is_terrain and self._terrain_geom is not None:
            self._rebuild_terrain_alignment()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        root = QWidget()
        root.setMinimumWidth(320)
        root.setStyleSheet(_style.WIDGET_STYLE)
        self.setWidget(root)

        dock_v = QVBoxLayout(root)
        dock_v.setContentsMargins(0, 0, 0, 0)
        dock_v.setSpacing(0)

        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        dock_v.addWidget(_style.build_header_bar('Advanced Profile Tool', icon_path))

        body = QWidget()
        dock_v.addWidget(body, 1)

        outer = QVBoxLayout(body)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # ---- Scrollable top section (layers + cross-section) -------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_NO_FRAME)
        sc_root = QWidget()
        sc = QVBoxLayout(sc_root)
        sc.setContentsMargins(2, 2, 2, 2)
        sc.setSpacing(4)
        scroll.setWidget(sc_root)
        outer.addWidget(scroll, stretch=1)   # takes all available vertical space

        # ---- Top-level tabs (Profile / 1D Profile) -----------------------
        tabs = QTabWidget()
        tabs.setStyleSheet(_style.TAB_STYLE)

        # Profile tab — raster AND vector layers together, one scrollable
        # list (previously two separate Raster/Vector tabs — merged so a
        # profile can freely mix LiDAR/DEM rasters with vector layers).
        profile_tab = QWidget()
        pt_outer_l  = QVBoxLayout(profile_tab)
        pt_outer_l.setContentsMargins(0, 0, 0, 0)
        pt_sc = QScrollArea(); pt_sc.setWidgetResizable(True)
        pt_sc.setFrameShape(_NO_FRAME)
        pt_inner = QWidget()
        pt_l = QVBoxLayout(pt_inner)
        pt_l.setContentsMargins(4, 6, 4, 4); pt_l.setSpacing(3)

        pt_l.addWidget(_section_label('Raster Layers', _raster_type_pix(12)))
        self._raster_layout = QVBoxLayout()
        self._raster_layout.setSpacing(3)
        add_r = QPushButton('+ Add Raster Layer')
        add_r.setStyleSheet('color:#2196F3;font-size:11px;border:none;padding:2px;')
        add_r.clicked.connect(lambda: self._add_raster_row())
        self._raster_layout.addWidget(add_r)
        pt_l.addLayout(self._raster_layout)

        pt_l.addSpacing(6)
        pt_l.addWidget(_section_label('Vector Layers', _vector_type_pix(12)))
        self._vector_layout = QVBoxLayout()
        self._vector_layout.setSpacing(4)
        add_v = QPushButton('+ Add Vector Layer')
        add_v.setStyleSheet('color:#43A047;font-size:11px;border:none;padding:2px;')
        add_v.clicked.connect(lambda: self._add_vector_row())
        self._vector_layout.addWidget(add_v)
        pt_l.addLayout(self._vector_layout)

        pt_l.addStretch()
        pt_sc.setWidget(pt_inner)
        pt_outer_l.addWidget(pt_sc)
        tabs.addTab(profile_tab, QIcon(QPixmap(_raster_type_pix(12))), 'Layer Profile')

        # Tab order from here on (Channel, Embankment, Shapes and Polygon)
        # is deliberate, per explicit request — matched by the _TAB_*
        # constants defined near the top of this file, which is what every
        # self._active_tab == _TAB_... check throughout the _terrain_*/
        # _embank_* code actually depends on, not the addTab() call order
        # itself. Reorder tabs by changing BOTH together.
        tabs.addTab(self._build_terrain_tab(), 'Channel Surface')

        # Embankment Surface tab — same general architecture as Channel
        # Surface (see _build_embank_tab), fill-dominant terminology, plus
        # Retaining Wall support.
        tabs.addTab(self._build_embank_tab(), 'Embankment Surface')

        # Shapes and Polygon Surface tab — see _build_shapes_tab.
        tabs.addTab(self._build_shapes_tab(), 'Shapes and Polygon Surface')

        # ---- X-Section tab removed — hidden stubs keep internal references valid
        self.xsec_cb       = QCheckBox()   # always unchecked; xsec never activates
        self.conveyance_cb = QCheckBox()
        self.cutfill_y1    = QComboBox();  self.cutfill_y1.wheelEvent  = lambda e: e.ignore()
        self.cutfill_y2    = QComboBox();  self.cutfill_y2.wheelEvent  = lambda e: e.ignore()
        self.xsec_widget   = QWidget()
        self.xsec_cc       = QDoubleSpinBox(); self.xsec_cc.setValue(15.0)
        self.xsec_from     = QDoubleSpinBox(); self.xsec_from.setValue(0.0)
        self.xsec_to       = QDoubleSpinBox(); self.xsec_to.setValue(100.0)
        self.lbl_dd        = QLabel()
        self.check_level_spin = QDoubleSpinBox()
        self.check_level_list = QListWidget()

        tabs.setMinimumHeight(160)
        self._tabs = tabs
        tabs.currentChanged.connect(self._on_tab_changed)
        sc.addWidget(tabs, 1)   # stretch=1 — the layer list gets all extra
                                # vertical space; Profile Windows below stays
                                # at its own compact (tabbed) natural height.

        # ---- Profile Windows — one small tab per window instead of all
        # three stacked at once (rarely all used together, and stacking
        # them was the single biggest space cost in this dock). -----------
        win_tabs = QTabWidget()
        self._win_tabs_widget = win_tabs
        win_tabs.setStyleSheet(_style.SUBTAB_STYLE)
        self.pw = win_tabs

        _default_win_names = ['Elevation [masl]', 'Depth [m]', 'Velocity [m/s]']

        self._win_cfgs = []
        for i in range(3):
            page = QWidget()
            pw_l = QVBoxLayout(page)
            pw_l.setContentsMargins(6, 6, 6, 6)
            pw_l.setSpacing(3)

            row = QHBoxLayout()
            en_cb = QCheckBox('Enabled')
            en_cb.setChecked(i == 0)
            if i == 0:
                en_cb.setEnabled(False)
            else:
                en_cb.stateChanged.connect(self._on_win_enable_changed)

            auto_cb = QCheckBox('Auto')
            auto_cb.setChecked(True)
            auto_cb.setEnabled(i == 0)
            auto_cb.stateChanged.connect(lambda _state, idx=i: self._on_win_auto_changed(idx))

            ymin_sp = QDoubleSpinBox()
            ymin_sp.setRange(-9999999, 9999999)
            ymin_sp.setValue(0.0); ymin_sp.setDecimals(2)
            ymin_sp.setFixedWidth(72); ymin_sp.setEnabled(False)
            ymin_sp.valueChanged.connect(self._refresh_plot)

            ymax_sp = QDoubleSpinBox()
            ymax_sp.setRange(-9999999, 9999999)
            ymax_sp.setValue(100.0); ymax_sp.setDecimals(2)
            ymax_sp.setFixedWidth(72); ymax_sp.setEnabled(False)
            ymax_sp.valueChanged.connect(self._refresh_plot)

            row.addWidget(en_cb)
            row.addWidget(auto_cb)
            row.addWidget(QLabel('Y:')); row.addWidget(ymin_sp)
            row.addWidget(QLabel('–')); row.addWidget(ymax_sp)
            row.addStretch()
            pw_l.addLayout(row)

            # Name (used as Y-axis label) + Data assignment, same row
            row_nd = QHBoxLayout()
            row_nd.addWidget(QLabel('Name:'))
            name_edit = QLineEdit(_default_win_names[i])
            name_edit.setPlaceholderText('Y-axis label')
            name_edit.setFixedHeight(22)
            name_edit.textChanged.connect(self._refresh_plot)
            row_nd.addWidget(name_edit, 1)
            pw_l.addLayout(row_nd)

            row2 = QHBoxLayout()
            row2.addWidget(QLabel('Data:'))
            col_combo = _CheckCombo()
            col_combo.setToolTip('Check columns to show in this window; nothing checked = All')
            col_combo.selectionChanged.connect(lambda _idx=i: self._on_win_col_changed(_idx))
            row2.addWidget(col_combo, 1)
            pw_l.addLayout(row2)

            # Per-window difference shading — renamed from 'Cut/fill' since
            # that term didn't mean much outside earthworks; this is just
            # Y2 minus Y1, shaded red where positive / blue where negative.
            row_cf = QHBoxLayout()
            cf_cb = QCheckBox('Show Difference (Y2-Y1)')
            cf_cb.stateChanged.connect(self._refresh_plot)
            row_cf.addWidget(cf_cb)
            pw_l.addLayout(row_cf)
            row_cf2 = QHBoxLayout()
            row_cf2.addWidget(QLabel('Y1:'))
            cf_y1 = QComboBox(); cf_y1.wheelEvent = lambda e: e.ignore()
            cf_y1.currentIndexChanged.connect(self._refresh_plot)
            row_cf2.addWidget(cf_y1, 1)
            row_cf2.addWidget(QLabel('Y2:'))
            cf_y2 = QComboBox(); cf_y2.wheelEvent = lambda e: e.ignore()
            cf_y2.currentIndexChanged.connect(self._refresh_plot)
            row_cf2.addWidget(cf_y2, 1)
            pw_l.addLayout(row_cf2)
            pw_l.addStretch()

            win_tabs.addTab(page, f'Win {i + 1}')

            self._win_cfgs.append({
                'enabled_cb': en_cb, 'auto_cb': auto_cb,
                'ymin': ymin_sp,    'ymax': ymax_sp,
                'name_edit': name_edit,
                'col_combo': col_combo,
                'cutfill_cb': cf_cb,
                'cf_y1':      cf_y1,
                'cf_y2':      cf_y2,
            })

        sc.addWidget(win_tabs)

        # ---- Shared action row — Draw/Click-Pipes + Refresh + Chart + Clear,
        # all in one row. Tab-specific buttons are toggled by visibility in
        # _on_tab_changed rather than living in separate per-tab rows, per
        # the user's explicit request to stop this taking so much vertical
        # space. Order matches what was asked for on the Profile tab: Draw,
        # Refresh, Chart, Clear. Labelled 'Refresh' rather than 'Run' so it
        # isn't mistaken for running a hydraulic/hydrologic model. ---------
        self.btn_draw = QPushButton('Draw Line')
        self.btn_draw.setCheckable(True)
        self.btn_draw.setToolTip(
            'Draw Profile Line.\nLeft-click to add vertices.\n'
            'Right-click or double-click to finish.\n'
            'Ctrl+Z to undo the last vertex.'
        )
        self.btn_draw.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_draw.clicked.connect(self._toggle_digitizing)
        self.btn_clear = QPushButton('Clear')
        self.btn_clear.setFixedWidth(50)
        self.btn_clear.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_clear.clicked.connect(self._clear_line)

        self.btn_run = QPushButton('Refresh')
        self.btn_run.setToolTip(
            'Refresh Profile — re-extracts the profile from the current layers.\n'
            'This does not run any hydraulic/hydrologic model.'
        )
        self.btn_run.setStyleSheet(_style.PRIMARY_BUTTON_STYLE)
        self.btn_run.clicked.connect(self._run)

        self.btn_open_chart = QPushButton('Chart')
        self.btn_open_chart.setToolTip('Show / Hide Chart')
        self.btn_open_chart.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_open_chart.clicked.connect(self._on_open_chart)

        # Modify Terrain-only — Save/Open/Close the whole multi-step
        # channel 'project' (horizontal alignment, vertical alignment/
        # breaks, cross-section params per station, control sections, DEM
        # pick) to/from a JSON file inside a user-named folder, so work
        # isn't lost mid-session and doesn't need redoing by hand on every
        # test run. See _terrain_collect_save_state / _terrain_apply_save_
        # state and _on_terrain_project_save/open/close. Sit AFTER Clear
        # in this shared row, per explicit request.
        self.btn_terrain_project_save = QPushButton('Save')
        self.btn_terrain_project_save.setToolTip(
            'Save the current channel design. The first save (or after\n'
            'Close) asks for a folder name under the Result Folder path\n'
            'below; after that, Save just re-saves into the same folder.'
        )
        self.btn_terrain_project_save.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_project_save.clicked.connect(self._on_terrain_project_save)
        self.btn_terrain_project_save.setVisible(False)   # idx 0 (Layer Profile) is the default tab

        self.btn_terrain_project_open = QPushButton('Open')
        self.btn_terrain_project_open.setToolTip(
            'Open a previously saved channel design folder, replacing the\n'
            'current one on this tab.'
        )
        self.btn_terrain_project_open.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_project_open.clicked.connect(self._on_terrain_project_open)
        self.btn_terrain_project_open.setVisible(False)   # idx 0 (Layer Profile) is the default tab

        self.btn_terrain_project_close = QPushButton('Close')
        self.btn_terrain_project_close.setToolTip(
            'Close the current channel design — clears the alignment,\n'
            'invert, cross-sections and control sections back to blank\n'
            '(same as Clear) and forgets which folder was open, so the\n'
            'next Save asks for a new name.'
        )
        self.btn_terrain_project_close.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_project_close.clicked.connect(self._on_terrain_project_close)
        self.btn_terrain_project_close.setVisible(False)   # idx 0 (Layer Profile) is the default tab

        # Modify Terrain-only — sit to the LEFT of Refresh/Clear in this
        # shared row (Check Cross-Section moved OUT of here, into the
        # Cross-Section grid's own Earthwork column instead — see
        # _build_terrain_tab). Visibility toggled alongside btn_draw etc.
        # in _on_tab_changed; real TIFF generation isn't built yet
        # (_on_terrain_create_tiff / _on_terrain_merge_tiff are stubs for
        # now), these are just wired up ready for it.
        self.btn_terrain_create_tiff = QPushButton('Create TIFF')
        self.btn_terrain_create_tiff.setToolTip(
            'Generate a TIFF of the designed channel surface from the\n'
            'current alignment, cross-sections and control sections.'
        )
        self.btn_terrain_create_tiff.setStyleSheet(_style.PRIMARY_BUTTON_STYLE)
        self.btn_terrain_create_tiff.clicked.connect(self._on_terrain_create_tiff)
        self.btn_terrain_create_tiff.setVisible(False)   # idx 0 (Layer Profile) is the default tab

        self.btn_terrain_merge_tiff = QPushButton('Merge TIFF')
        self.btn_terrain_merge_tiff.setToolTip(
            'Paste a previously created channel-design TIFF into a copy\n'
            'of the existing ground DEM, replacing only the corridor\'s\n'
            'own footprint.'
        )
        self.btn_terrain_merge_tiff.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_merge_tiff.clicked.connect(self._on_terrain_merge_tiff)
        self.btn_terrain_merge_tiff.setVisible(False)   # idx 0 (Layer Profile) is the default tab

        # Embankment Surface's own Save/Open/Close/Create TIFF/Merge TIFF —
        # same mechanics as Channel Surface's (just above), independent
        # state/files, shown only on that tab (see _on_tab_changed).
        self.btn_embank_project_save = QPushButton('Save')
        self.btn_embank_project_save.setToolTip(
            'Save the current embankment design. The first save (or after\n'
            'Close) asks for a folder name under the Result Folder path\n'
            'below; after that, Save just re-saves into the same folder.'
        )
        self.btn_embank_project_save.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_project_save.clicked.connect(self._on_embank_project_save)
        self.btn_embank_project_save.setVisible(False)

        self.btn_embank_project_open = QPushButton('Open')
        self.btn_embank_project_open.setToolTip(
            'Open a previously saved embankment design folder, replacing\n'
            'the current one on this tab.'
        )
        self.btn_embank_project_open.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_project_open.clicked.connect(self._on_embank_project_open)
        self.btn_embank_project_open.setVisible(False)

        self.btn_embank_project_close = QPushButton('Close')
        self.btn_embank_project_close.setToolTip(
            'Close the current embankment design — clears the alignment,\n'
            'invert, cross-sections, control sections and walls back to\n'
            'blank (same as Clear) and forgets which folder was open, so\n'
            'the next Save asks for a new name.'
        )
        self.btn_embank_project_close.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_project_close.clicked.connect(self._on_embank_project_close)
        self.btn_embank_project_close.setVisible(False)

        self.btn_embank_create_tiff = QPushButton('Create TIFF')
        self.btn_embank_create_tiff.setToolTip(
            'Generate a TIFF of the designed embankment surface from the\n'
            'current alignment, cross-sections and control sections.'
        )
        self.btn_embank_create_tiff.setStyleSheet(_style.PRIMARY_BUTTON_STYLE)
        self.btn_embank_create_tiff.clicked.connect(self._on_embank_create_tiff)
        self.btn_embank_create_tiff.setVisible(False)

        self.btn_embank_merge_tiff = QPushButton('Merge TIFF')
        self.btn_embank_merge_tiff.setToolTip(
            'Paste a previously created embankment-design TIFF into a copy\n'
            'of the existing ground DEM, replacing only the corridor\'s\n'
            'own footprint.'
        )
        self.btn_embank_merge_tiff.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_merge_tiff.clicked.connect(self._on_embank_merge_tiff)
        self.btn_embank_merge_tiff.setVisible(False)

        # Shapes and Polygon Surface's own Create TIFF — same shared-row
        # placement as Embankment/Channel Surface's, sitting just before
        # Refresh, per explicit request. No Merge TIFF/Save/Open/Close
        # yet for this tab (see _build_shapes_tab).
        self.btn_shapes_create_tiff = QPushButton('Create TIFF')
        self.btn_shapes_create_tiff.setToolTip(
            "Builds a standalone GeoTIFF of this shape's surface only\n"
            '(not merged into the existing DEM).'
        )
        self.btn_shapes_create_tiff.setStyleSheet(_style.PRIMARY_BUTTON_STYLE)
        self.btn_shapes_create_tiff.clicked.connect(self._on_shapes_create_tiff)
        self.btn_shapes_create_tiff.setVisible(False)

        # Modify Terrain's own Draw Line/Add Vertical Break buttons live
        # embedded inside their own sections (Horizontal/Vertical
        # Alignment) instead of this shared row — see _build_terrain_tab
        # (self.btn_terrain_draw / self.btn_terrain_break), built before
        # this point since the tabs are constructed first.
        # FlowLayout — this row is shared across all 4 tabs and hides a
        # different subset of these buttons per tab (see _on_tab_changed),
        # but even the visible subset can be more than fits once the dock
        # is narrowed to leave more room for the map canvas; wrapping onto
        # a second line keeps every button reachable without shrinking or
        # clipping any of them.
        action_row = FlowLayout(h_spacing=4, v_spacing=4)
        for btn in (self.btn_draw,
                    self.btn_embank_create_tiff, self.btn_embank_merge_tiff,
                    self.btn_terrain_create_tiff, self.btn_terrain_merge_tiff,
                    self.btn_shapes_create_tiff,
                    self.btn_run, self.btn_open_chart,
                    self.btn_clear,
                    self.btn_embank_project_save, self.btn_embank_project_open,
                    self.btn_embank_project_close,
                    self.btn_terrain_project_save, self.btn_terrain_project_open,
                    self.btn_terrain_project_close):
            action_row.addWidget(btn)
        outer.addLayout(action_row)
        self.btn_save_png = None  # created in _setup_chart_dock

        # ---- Layer-based profile line selection (Profile tab only) ------
        ll = QHBoxLayout()
        ll.setSpacing(4)
        ll.addWidget(QLabel('From layer:'))
        self._line_layer_combo = QgsMapLayerComboBox()
        self._line_layer_combo.setFilters(_LINE_FILTER)
        self._line_layer_combo.setAllowEmptyLayer(True)
        self._line_layer_combo.setCurrentIndex(0)
        self._line_layer_combo.setToolTip(
            'Select a line layer containing the profile alignment.\n'
            'The layer should have a single line feature (or only the first\n'
            'feature will be used).'
        )
        self._line_layer_combo.wheelEvent = lambda e: e.ignore()
        ll.addWidget(self._line_layer_combo, 1)
        btn_use_layer = QPushButton('Use')
        btn_use_layer.setFixedWidth(42)
        btn_use_layer.setToolTip('Load the line from the selected layer as the profile line')
        btn_use_layer.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        btn_use_layer.clicked.connect(self._use_line_layer)
        ll.addWidget(btn_use_layer)

        self.lbl_line = QLabel('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')

        self._line_controls_widget = QWidget()
        _lcw = QVBoxLayout(self._line_controls_widget)
        _lcw.setContentsMargins(0, 0, 0, 0); _lcw.setSpacing(4)
        _lcw.addLayout(ll)
        _lcw.addWidget(self.lbl_line)
        outer.addWidget(self._line_controls_widget)

        # ---- Map line style + Sampling Interval, one row. Map line style
        # only affects the Profile tab's drawn/selected line; Sampling
        # Interval (raster/vector point-sampling) stays visible regardless
        # of which map-line widgets show. ---------------------------------
        style_row = QHBoxLayout()
        style_row.setSpacing(4)
        self._map_line_style_widgets = []

        def _ml(label_text, widget):
            lbl = QLabel(label_text)
            style_row.addWidget(lbl)
            style_row.addWidget(widget)
            self._map_line_style_widgets.extend([lbl, widget])

        self._map_lw_spin = QDoubleSpinBox()
        self._map_lw_spin.setRange(0.5, 10.0)
        self._map_lw_spin.setValue(3.0)
        self._map_lw_spin.setSingleStep(0.5)
        self._map_lw_spin.setDecimals(1)
        self._map_lw_spin.setFixedWidth(48)
        self._map_lw_spin.setToolTip('Profile line width on map canvas (px)')
        self._map_lw_spin.valueChanged.connect(self._apply_map_line_style)
        _ml('Line:', self._map_lw_spin)

        self._map_op_spin = QSpinBox()
        self._map_op_spin.setRange(5, 100)
        self._map_op_spin.setValue(100)
        self._map_op_spin.setSuffix('%')
        self._map_op_spin.setFixedWidth(52)
        self._map_op_spin.setToolTip('Profile line opacity on map canvas')
        self._map_op_spin.valueChanged.connect(self._apply_map_line_style)
        _ml('Op:', self._map_op_spin)

        self._map_line_color_btn = _color_btn('#39FF14', 'Map line colour')
        self._map_line_color_btn.clicked.connect(self._pick_map_line_color)
        style_row.addWidget(self._map_line_color_btn)
        self._map_line_style_widgets.append(self._map_line_color_btn)

        style_row.addSpacing(6)
        self._interval_spin_lbl = QLabel('Interval:')
        style_row.addWidget(self._interval_spin_lbl)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.001, 100000.0)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setDecimals(3)
        self.interval_spin.setSuffix(' m')
        self.interval_spin.setFixedWidth(90)
        style_row.addWidget(self.interval_spin)
        style_row.addStretch()
        outer.addLayout(style_row)

        # ---- Fixed bottom section ----------------------------------------
        outer.addWidget(_sep())

        cr = QHBoxLayout()
        cr.addWidget(QLabel('Result Folder:'))
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText('Leave blank to auto-save to Downloads/ProfilePlot_<Date>')
        btn_csv = QPushButton('...')
        btn_csv.setFixedWidth(28)
        btn_csv.setStyleSheet(_style.BROWSE_BUTTON_STYLE)
        btn_csv.clicked.connect(self._browse_result_folder)
        cr.addWidget(self.csv_edit); cr.addWidget(btn_csv)
        outer.addLayout(cr)

        self.lbl_status = QLabel('')
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet('font-size:11px;')
        outer.addWidget(self.lbl_status)

        # ---- Footer (progress + version strip) ---------------------------
        footer = QWidget()
        footer.setStyleSheet(
            'QWidget { background: #ECEFF1; border-top: 1px solid #CFD8DC; }'
        )
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(14, 8, 14, 10)
        fl.setSpacing(5)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            'QProgressBar { background: #CFD8DC; border-radius: 3px; border: none; }'
            f'QProgressBar::chunk {{ background: {_BLUE}; border-radius: 3px; }}'
        )
        fl.addWidget(self.progress)

        try:
            _qgis_ver = Qgis.QGIS_VERSION.split('-')[0]
        except Exception:
            _qgis_ver = '—'
        ver_lbl = QLabel(
            f'Developer: D Magaju   ·   Version: {__version__}   ·   QGIS: {_qgis_ver}'
        )
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter
                             if hasattr(Qt, 'AlignmentFlag') else Qt.AlignCenter)
        ver_lbl.setStyleSheet('font-size: 9px; color: #90A4AE; background: transparent;'
                              ' border: none;')
        fl.addWidget(ver_lbl)

        outer.addWidget(footer)

        # ---- Chart widgets — layout deferred to _setup_chart_dock() -------
        # Created here so _plot, _hide_hover etc can reference them any time.
        # _setup_chart_dock() is called from initGui() after both docks are
        # registered with iface, which is the correct moment to call
        # iface.addDockWidget for the second (chart) window.
        self._nav_toolbar = None

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(7, 4), dpi=100)
            self.ax = self.figure.add_subplot(111)
            self._reset_axes()
            self.canvas_plot = FigureCanvas(self.figure)
            self.canvas_plot.setMinimumHeight(240)
            self.canvas_plot.setSizePolicy(_EXPAND, _EXPAND)
            self.canvas_plot.setFocusPolicy(_STRONG_FOCUS)
            self.canvas_plot.installEventFilter(self._ctrl_tracker)
            self.canvas_plot.mpl_connect('scroll_event',         self._on_scroll_zoom)
            self.canvas_plot.mpl_connect('resize_event',         self._on_chart_resize)
            self.canvas_plot.mpl_connect('button_press_event',   self._on_mouse_press)
            self.canvas_plot.mpl_connect('button_release_event', self._on_mouse_release)
            self.canvas_plot.mpl_connect('motion_notify_event',  self._on_chart_hover)
            self.canvas_plot.mpl_connect('axes_leave_event',     self._on_chart_leave)

        # Qt's currentChanged only fires on an actual tab SWITCH — since tab
        # 0 is selected by default with no switch ever happening, the
        # tab-specific button visibility set up in _on_tab_changed (e.g.
        # hiding 'Click Pipes'/'Undo'/1D Profile's Clear on the Profile tab)
        # would otherwise never run at all, leaving every button from BOTH
        # tabs visible at once. Call it once explicitly to establish the
        # correct initial state for whichever tab is actually current.
        self._on_tab_changed(self._tabs.currentIndex())

    # ------------------------------------------------------------------ chart dock

    def _setup_chart_dock(self, chart_dock):
        """Populate the chart QDockWidget and wire Save PNG.
        Called from initGui() after both docks are registered with iface."""
        plot_root  = QWidget()
        plot_outer = QVBoxLayout(plot_root)
        plot_outer.setContentsMargins(2, 2, 2, 2)
        plot_outer.setSpacing(2)

        if MATPLOTLIB_AVAILABLE:
            top_bar = QHBoxLayout()
            if _NavToolbar is not None:
                self._nav_toolbar = _NavToolbar(self.canvas_plot, plot_root)
                for act in self._nav_toolbar.actions():
                    if act.text() in ('Subplots', 'Customize', 'Save'):
                        self._nav_toolbar.removeAction(act)
                top_bar.addWidget(self._nav_toolbar)
            self.btn_save_png = QPushButton('Save Plot')
            self.btn_save_png.setFixedWidth(80)
            self.btn_save_png.setEnabled(False)
            self.btn_save_png.setStyleSheet(_style.PRIMARY_BUTTON_STYLE)
            self.btn_save_png.clicked.connect(self._save_plot)
            top_bar.addWidget(self.btn_save_png)
            plot_outer.addLayout(top_bar)

            # ── Sketch toolbar — Row 1: tools ─────────────────────────────────
            _sk_bar = QHBoxLayout()
            _sk_bar.setSpacing(3)
            _sk_bar.setContentsMargins(4, 1, 4, 1)

            _sk_lbl = QLabel('Sketch:')
            _sk_lbl.setStyleSheet('font-size:10px; color:#546E7A;')
            _sk_bar.addWidget(_sk_lbl)

            _SKETCH_TOOLS = [
                ('pen',    'Pen',    'Freehand pen — click and drag.\n'
                                      'Or Ctrl+click a chain of points; double-click\n'
                                      '(or click without Ctrl) to finish.'),
                ('line',   'Line',   'Straight line — click and drag.\n'
                                      'Or Ctrl+click a chain of points; double-click\n'
                                      '(or click without Ctrl) to finish.'),
                ('arrow',  '→',      'Arrow — drag from tail to head'),
                ('text',   'Text',   'Text annotation — click to place'),
                ('level',  '▽',      'Level marker — click (or near a profile line to snap) to place a water-level line'),
                ('rect',   'Rect',   'Rectangle — click and drag'),
                ('circle', '○',      'Circle / Ellipse — drag from centre; hold Shift for perfect circle'),
                ('eraser', '✕',      'Eraser — click or drag over annotations to remove'),
                ('move',   '⇔',      'Move — click and drag any annotation to reposition'),
                ('edit',   '✎',      'Edit — click an annotation to change its colour, thickness or text style'),
                ('cursor', '⌇',      'Chainage cursor — click to drop a permanent reference line with C/F value'),
            ]
            _tool_style = (
                'QPushButton{font-size:10px;border:1px solid #B0BEC5;'
                'border-radius:3px;background:#FAFAFA;}'
                'QPushButton:checked{background:#1565C0;color:white;border-color:#1565C0;}'
                'QPushButton:hover:!checked{background:#E3F2FD;}'
            )
            for _mode, _label, _tip in _SKETCH_TOOLS:
                _sb = QPushButton(_label)
                _sb.setCheckable(True)
                _sb.setFixedSize(38, 22)
                _sb.setToolTip(_tip)
                _sb.setStyleSheet(_tool_style)
                _sb.clicked.connect(
                    lambda chk, m=_mode: self._sketch_activate(m) if chk else self._sketch_deactivate()
                )
                _sk_bar.addWidget(_sb)
                self._sketch_btns[_mode] = _sb

            _sk_bar.addStretch()

            _btn_clr = QPushButton('Clear')
            _btn_clr.setFixedSize(45, 22)
            _btn_clr.setToolTip('Remove all sketch annotations')
            _btn_clr.setStyleSheet(
                'font-size:10px;border:1px solid #EF9A9A;border-radius:3px;'
                'background:#FFF3F3;color:#C62828;'
            )
            _btn_clr.clicked.connect(self._sketch_clear)
            _sk_bar.addWidget(_btn_clr)

            _btn_xs = QPushButton('⊥ XS')
            _btn_xs.setFixedSize(48, 22)
            _btn_xs.setToolTip('Show cross-section at a placed chainage cursor')
            _btn_xs.setStyleSheet(
                'font-size:10px;border:1px solid #90CAF9;border-radius:3px;'
                'background:#E3F2FD;color:#1565C0;'
            )
            _btn_xs.clicked.connect(self._open_xs_menu)
            self._xs_btn = _btn_xs
            _sk_bar.addWidget(_btn_xs)

            # Marker tool — sits right next to the Cross-Section ('⊥ XS')
            # button per explicit request. Behaves like any other sketch
            # tool (checkable, managed by _sketch_activate/_sketch_
            # deactivate via self._sketch_btns) but its own click handling
            # lives in _sketch_on_press's 'marker' branch: drops a point on
            # BOTH the profile chart and the map canvas (interpolated along
            # self.profile_geom at the clicked chainage), saved later as
            # its own 'marker' shapefile alongside the plot (see
            # _write_markers_shp / _save_plot).
            _btn_marker = QPushButton('📍')
            _btn_marker.setCheckable(True)
            _btn_marker.setFixedSize(38, 22)
            _btn_marker.setToolTip(
                'Marker — click on the profile to drop a point marker on\n'
                'the map canvas at that chainage. Saved as its own \'marker\'\n'
                'shapefile alongside the plot when you click Save Plot.'
            )
            _btn_marker.setStyleSheet(_tool_style)
            _btn_marker.clicked.connect(
                lambda chk: self._sketch_activate('marker') if chk else self._sketch_deactivate()
            )
            _sk_bar.addWidget(_btn_marker)
            self._sketch_btns['marker'] = _btn_marker

            plot_outer.addLayout(_sk_bar)

            # ── Sketch toolbar — Row 2: style options + colours ────────────────
            _sk_bar2 = QHBoxLayout()
            _sk_bar2.setSpacing(3)
            _sk_bar2.setContentsMargins(4, 0, 4, 2)

            _lbl_style = QLabel('Line:')
            _lbl_style.setStyleSheet('font-size:10px; color:#546E7A;')
            _sk_bar2.addWidget(_lbl_style)

            # Line width spinbox
            self._sketch_lw_spin = QDoubleSpinBox()
            self._sketch_lw_spin.setRange(0.5, 10.0)
            self._sketch_lw_spin.setSingleStep(0.5)
            self._sketch_lw_spin.setValue(self._sketch_lw)
            self._sketch_lw_spin.setDecimals(1)
            self._sketch_lw_spin.setFixedWidth(56)
            self._sketch_lw_spin.setFixedHeight(22)
            self._sketch_lw_spin.setToolTip('Line thickness (pt)')
            self._sketch_lw_spin.setStyleSheet('font-size:10px;')
            self._sketch_lw_spin.valueChanged.connect(
                lambda v: setattr(self, '_sketch_lw', v)
            )
            _sk_bar2.addWidget(self._sketch_lw_spin)

            _sk_bar2.addSpacing(4)

            # Line style combo
            self._sketch_ls_combo = QComboBox()
            self._sketch_ls_combo.setFixedWidth(112)
            self._sketch_ls_combo.setFixedHeight(22)
            self._sketch_ls_combo.setToolTip('Line style')
            self._sketch_ls_combo.setStyleSheet('font-size:10px;')
            for _lsv, _lslbl in [
                ('-',  '─────  Solid'),
                ('--', '- - -  Dashed'),
                (':',  '·····  Dotted'),
                ('-.', '-·-·-  Dash-dot'),
            ]:
                self._sketch_ls_combo.addItem(_lslbl, _lsv)
            self._sketch_ls_combo.currentIndexChanged.connect(
                lambda i: setattr(self, '_sketch_ls',
                                  self._sketch_ls_combo.itemData(i))
            )
            _sk_bar2.addWidget(self._sketch_ls_combo)

            _sk_bar2.addSpacing(8)

            # Expanded colour palette (12 presets)
            _PALETTE = [
                ('#E53935', 'Red'),       ('#C62828', 'Dark Red'),
                ('#1565C0', 'Blue'),      ('#0D47A1', 'Dark Blue'),
                ('#2E7D32', 'Green'),     ('#FF8C00', 'Orange'),
                ('#6A1B9A', 'Purple'),    ('#00838F', 'Teal'),
                ('#F57F17', 'Amber'),     ('#37474F', 'Slate'),
                ('#000000', 'Black'),     ('#FFFFFF', 'White'),
            ]
            for _hc, _ct in _PALETTE:
                _cb = QPushButton()
                _cb.setFixedSize(16, 16)
                _cb.setToolTip(_ct)
                _cb.setStyleSheet(
                    f'background:{_hc};border:1px solid #888;border-radius:2px;'
                )
                _cb.clicked.connect(lambda _, c=_hc: self._sketch_set_color(c))
                _sk_bar2.addWidget(_cb)

            # Custom colour picker
            self._sketch_color_btn = QPushButton()
            self._sketch_color_btn.setFixedSize(22, 16)
            self._sketch_color_btn.setToolTip('Custom colour…')
            self._sketch_color_btn.setStyleSheet(
                f'background:{self._sketch_color};border:2px solid #555;border-radius:2px;'
            )
            self._sketch_color_btn.clicked.connect(self._sketch_pick_color)
            _sk_bar2.addWidget(self._sketch_color_btn)

            _sk_bar2.addStretch()
            plot_outer.addLayout(_sk_bar2)

            plot_outer.addWidget(self.canvas_plot, stretch=1)
        else:
            plot_outer.addWidget(QLabel(
                'matplotlib not installed — chart unavailable.\n'
                'Install via OSGeo4W shell: pip install matplotlib'
            ))

        chart_dock.setWidget(plot_root)
        self._chart_dock_widget = chart_dock

    def _on_open_chart(self):
        cw = getattr(self, '_chart_dock_widget', None)
        if not cw:
            return
        if cw.isVisible():
            cw.hide()
        else:
            cw.show()
            cw.raise_()

    # ------------------------------------------------------------------ axes / figure

    def _reset_axes(self):
        self._sketch_objects = []
        self._sketch_current = None
        self._sketch_pressed = False
        self._plot_cols_per_ax = {}
        self._cf_ann_texts = {}
        all_p = [self.ax] + self._extra_axes
        for i, ax_i in enumerate(all_p):
            ax_i.clear()
            cfg_i    = self._win_cfgs[i] if i < len(self._win_cfgs) else None
            win_name = cfg_i['name_edit'].text().strip() if cfg_i else ''
            ax_i.set_ylabel(win_name or 'Z value', fontsize=9)
            ax_i.grid(True, alpha=0.3)
            ax_i.tick_params(labelsize=8)
            if i < len(all_p) - 1:
                ax_i.tick_params(labelbottom=False)   # hide x labels on non-bottom axes
            else:
                ax_i.set_xlabel('Chainage [m]', fontsize=9)
        _title = ('Horizontal Alignment — Ground Profile' if self._active_tab in (1, 3) else
                  'Longitudinal Profile')
        self.ax.set_title(_title, fontsize=10, fontweight='bold')

        if self.ax_xsec is not None:
            self.ax_xsec.clear()
            self.ax_xsec.set_xlabel('Area (m²)', fontsize=8)
            self.ax_xsec.set_title('Stage–Area', fontsize=9, fontweight='bold')
            self.ax_xsec.yaxis.tick_right()
            self.ax_xsec.yaxis.set_label_position('right')
            self.ax_xsec.set_ylabel('Elevation (m AD)', fontsize=8,
                                     fontweight='bold', rotation=270, labelpad=14)
            self.ax_xsec.tick_params(labelleft=False, labelsize=7)
            self.ax_xsec.grid(True, alpha=0.3)
        self._xsec_hline = None

    def _rebuild_figure(self, want_xsec: bool):
        n_wins   = self._n_active_wins()
        cur_wins = 1 + len(self._extra_axes)
        if want_xsec == self._has_xsec_plot and n_wins == cur_wins:
            return
        self._sketch_pending_specs = self._sketch_serialise()  # save before figure wipe
        self.figure.clear()
        self._ch_cursor_artists = []   # artists gone after figure.clear()
        self._cursor_lines   = []
        self._cc_hlines      = []
        self._xsec_hline     = None
        self._xsec_area_text = None
        self._xsec_fill_cols = []
        self._xsec_fill_data = []
        self._extra_axes     = []

        hspace = 0.15 if n_wins > 1 else 0.08
        if want_xsec:
            gs = self.figure.add_gridspec(
                n_wins, 2, width_ratios=[3, 1], wspace=0.08, hspace=hspace
            )
            self.ax      = self.figure.add_subplot(gs[0, 0])
            self._extra_axes = [
                self.figure.add_subplot(gs[i, 0], sharex=self.ax)
                for i in range(1, n_wins)
            ]
            self.ax_xsec = self.figure.add_subplot(gs[:, 1])
        else:
            if n_wins == 1:
                self.ax = self.figure.add_subplot(111)
            else:
                gs = self.figure.add_gridspec(n_wins, 1, hspace=hspace)
                self.ax = self.figure.add_subplot(gs[0])
                self._extra_axes = [
                    self.figure.add_subplot(gs[i], sharex=self.ax)
                    for i in range(1, n_wins)
                ]
            self.ax_xsec = None

        self._has_xsec_plot = want_xsec
        self._reset_axes()

    def _on_xsec_toggled(self, state):
        self.xsec_widget.setVisible(bool(state))
        if MATPLOTLIB_AVAILABLE:
            self._rebuild_figure(bool(state))
            self._refresh_plot()

    # ------------------------------------------------------------------ entries

    def _collect_entries(self, raster_rows=None, vector_rows=None):
        """Raster AND vector rows are now always collected together — the
        Raster/Vector split is purely a UI grouping (one scrollable Profile
        tab, two labelled sections) since the tab merge, not two mutually
        exclusive data sources. Accepts explicit row lists so the same logic
        can be reused for another tab's own independent row list (e.g.
        Channel Surface's self._terrain_raster_rows) without duplicating
        this method."""
        raster_rows = self._raster_rows if raster_rows is None else raster_rows
        vector_rows = self._vector_rows if vector_rows is None else vector_rows
        raster_entries, vector_entries, col_meta = [], [], {}
        used = set()

        def uniq(name):
            if name not in used:
                used.add(name); return name
            i = 1
            while f'{name} ({i})' in used:
                i += 1
            n = f'{name} ({i})'; used.add(n); return n

        for row in raster_rows:
            lyr = row['layer_combo'].currentLayer()
            if not isinstance(lyr, QgsRasterLayer):
                continue
            col = uniq(lyr.name())
            row['_col'] = col
            raster_entries.append((lyr, col))
            col_meta[col] = {
                'color':     row['color'],
                'visible':   row['toggle'].isChecked(),
                'linestyle': row['ls_combo'].currentData(),
                'linewidth': row['lw_spin'].value() if 'lw_spin' in row else 1.5,
                'alpha':     row['al_spin'].value() / 100.0 if 'al_spin' in row else 1.0,
            }

        for vec in vector_rows:
            lyr = vec['layer_combo'].currentLayer()
            if lyr is None:
                continue
            for zf in vec['z_fields']:
                field = zf['combo'].currentField()
                if not field:
                    continue
                col = uniq(f'{lyr.name()} [{field}]')
                zf['_col'] = col
                vector_entries.append((lyr, field, col))
                col_meta[col] = {
                    'color':     zf['color'],
                    'visible':   zf['toggle'].isChecked(),
                    'linestyle': zf['ls_combo'].currentData() if 'ls_combo' in zf else '-',
                    'linewidth': zf['lw_spin'].value() if 'lw_spin' in zf else 1.5,
                    'alpha':     zf['al_spin'].value() / 100.0 if 'al_spin' in zf else 1.0,
                }

        return raster_entries, vector_entries, col_meta

    def _collect_plot_meta(self, raster_rows=None, vector_rows=None):
        raster_rows = self._raster_rows if raster_rows is None else raster_rows
        vector_rows = self._vector_rows if vector_rows is None else vector_rows
        meta = {}
        for row in raster_rows:
            if row.get('_col'):
                meta[row['_col']] = {
                    'color':     row['color'],
                    'visible':   row['toggle'].isChecked(),
                    'linestyle': row['ls_combo'].currentData(),
                    'linewidth': row['lw_spin'].value() if 'lw_spin' in row else 1.5,
                    'alpha':     row['al_spin'].value() / 100.0 if 'al_spin' in row else 1.0,
                }
        for vec in vector_rows:
            for zf in vec['z_fields']:
                if zf.get('_col'):
                    meta[zf['_col']] = {
                        'color':     zf['color'],
                        'visible':   zf['toggle'].isChecked(),
                        'linestyle': zf['ls_combo'].currentData() if 'ls_combo' in zf else '-',
                        'linewidth': zf['lw_spin'].value() if 'lw_spin' in zf else 1.5,
                        'alpha':     zf['al_spin'].value() / 100.0 if 'al_spin' in zf else 1.0,
                    }
        return meta

    # ------------------------------------------------------------------ cut/fill combos

    def _update_win_col_combos(self, col_names):
        for cfg in self._win_cfgs:
            cb = cfg['col_combo']
            cb.populate(col_names, keep_checked=cb.checked_cols())
            win_cols = cb.checked_cols() or list(col_names)
            for combo in (cfg['cf_y1'], cfg['cf_y2']):
                cur = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                for n in win_cols:
                    combo.addItem(n)
                idx = combo.findText(cur)
                combo.setCurrentIndex(max(0, idx) if idx >= 0 else 0)
                combo.blockSignals(False)
            if cfg['cf_y2'].count() >= 2 and cfg['cf_y2'].currentIndex() == 0:
                cfg['cf_y2'].blockSignals(True)
                cfg['cf_y2'].setCurrentIndex(1)
                cfg['cf_y2'].blockSignals(False)

    def _refresh_cutfill_combos(self, col_names):
        first_fill = self.cutfill_y1.count() == 0
        for combo in (self.cutfill_y1, self.cutfill_y2):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            for n in col_names:
                combo.addItem(n)
            idx = combo.findText(cur)
            combo.setCurrentIndex(max(0, idx) if idx >= 0 else 0)
            combo.blockSignals(False)
        if first_fill and self.cutfill_y2.count() >= 2:
            self.cutfill_y2.blockSignals(True)
            self.cutfill_y2.setCurrentIndex(1)
            self.cutfill_y2.blockSignals(False)

    # ------------------------------------------------------------------ triggers

    def _trigger_update(self):
        if self._active_tab == 0 and self.profile_geom is not None:
            self._on_live_update(self.profile_geom)

    def _add_check_level(self):
        v = self.check_level_spin.value()
        self._check_levels.append(v)
        self.check_level_list.addItem(QListWidgetItem(f'{v:.3f} m'))
        self._refresh_plot()

    def _remove_check_level(self):
        row = self.check_level_list.currentRow()
        if row >= 0:
            self.check_level_list.takeItem(row)
            self._check_levels.pop(row)
            self._refresh_plot()

    def _refresh_plot(self):
        """Restyle-only redraw for whichever tab is active. Every Profile
        Windows control (Auto, Y-range, Name, Data checklist, Show
        Difference, its Y1/Y2 combos) is wired straight to this one method
        regardless of which tab is open — so it has to dispatch by tab
        itself, rather than each of those controls needing its own
        tab-aware wiring."""
        if not self._profile_chainages or not MATPLOTLIB_AVAILABLE:
            return
        if self._active_tab == _TAB_EMBANK:
            self._plot(self._profile_chainages, self._profile_data_store,
                       self._collect_plot_meta(raster_rows=self._embank_raster_rows,
                                                vector_rows=[]))
            self._draw_embank_vertical_decorations()
            return
        if self._active_tab == _TAB_CHANNEL:
            self._plot(self._profile_chainages, self._profile_data_store,
                       self._collect_plot_meta(raster_rows=self._terrain_raster_rows,
                                                vector_rows=[]))
            self._draw_terrain_vertical_decorations()
            return
        self._plot(self._profile_chainages, self._profile_data_store,
                   self._collect_plot_meta())

    # ------------------------------------------------------------------ map tool

    def _toggle_digitizing(self, checked):
        if self._active_tab == _TAB_EMBANK:
            self._toggle_embank_digitizing(checked)
            return
        if self._active_tab == _TAB_CHANNEL:
            self._toggle_terrain_digitizing(checked)
            return
        if checked:
            self._prev_tool = self.canvas.mapTool()
            self._map_tool.reset()
            self.canvas.setMapTool(self._map_tool)
            self.lbl_line.setText('Click to add vertices — right-click to finish…')
            self.lbl_line.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._map_tool:
                self.canvas.unsetMapTool(self._map_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)

    def _on_line_captured(self, geom):
        self.profile_geom = geom
        if self._perm_band:
            self.canvas.scene().removeItem(self._perm_band)
        self._perm_band = QgsRubberBand(self.canvas, _LINE_GEOM)
        _c = QColor(self._map_line_color); _c.setAlpha(self._map_line_opacity)
        self._perm_band.setColor(_c)
        self._perm_band.setWidth(int(round(self._map_line_width)))
        try:
            self._perm_band.setLineStyle(Qt.PenStyle.SolidLine)
        except AttributeError:
            self._perm_band.setLineStyle(Qt.SolidLine)  # type: ignore[attr-defined]
        self._perm_band.setToGeometry(geom, None)
        self.lbl_line.setText(f'Profile line: {geom.length():.2f} m — ready.')
        self.lbl_line.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')
        self.btn_draw.setChecked(False)
        self._toggle_digitizing(False)
        self._on_live_update(geom)  # live for both Raster and Vector tabs

    def _use_line_layer(self):
        """Load the first line feature from the selected layer as the profile line."""
        lyr = self._line_layer_combo.currentLayer()
        if lyr is None:
            self.lbl_line.setText('No line layer selected.')
            self.lbl_line.setStyleSheet('color:#E53935;font-style:italic;font-size:11px;')
            return

        feats = list(lyr.getFeatures())
        if not feats:
            self.lbl_line.setText('Selected layer has no features.')
            self.lbl_line.setStyleSheet('color:#E53935;font-style:italic;font-size:11px;')
            return

        if len(feats) > 1:
            self.iface.messageBar().pushMessage(
                'Advanced Profile Tool',
                f'Layer has {len(feats)} features — using the first feature only.',
                level=1, duration=5)

        geom = feats[0].geometry()
        if geom is None or geom.isEmpty():
            self.lbl_line.setText('First feature has no geometry.')
            self.lbl_line.setStyleSheet('color:#E53935;font-style:italic;font-size:11px;')
            return

        # Merge multi-part geometry into a single polyline
        if geom.isMultipart():
            geom = geom.mergeLines()

        self._on_line_captured(geom)

    def _pick_map_line_color(self):
        c = QColorDialog.getColor(self._map_line_color, self)
        if c.isValid():
            self._map_line_color = c
            self._map_line_color_btn.setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;')
            self._apply_map_line_style()

    def _apply_map_line_style(self):
        """Apply width/opacity/colour spinbox values to the profile rubber band."""
        self._map_line_width   = self._map_lw_spin.value()
        self._map_line_opacity = int(self._map_op_spin.value() * 255 / 100)
        if self._perm_band:
            c = QColor(self._map_line_color)
            c.setAlpha(self._map_line_opacity)
            self._perm_band.setColor(c)
            self._perm_band.setWidth(int(round(self._map_line_width)))
            self.canvas.refresh()

    def _clear_line(self):
        if self._active_tab == _TAB_EMBANK:
            self._clear_embank_alignment()
            return
        if self._active_tab == _TAB_CHANNEL:
            self._clear_terrain_alignment()
            return
        self.profile_geom        = None
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
        self._profile_markers        = []
        self._profile_marker_artists = []
        self._clear_marker_map_points()
        self._close_all_xs_dialogs()
        if self._perm_band:
            self.canvas.scene().removeItem(self._perm_band)
            self._perm_band = None
        self._map_tool.reset()
        self.btn_draw.setChecked(False)
        self.lbl_line.setText('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self._hide_hover()
        if MATPLOTLIB_AVAILABLE:
            self._cursor_lines = []
            self._cc_hlines    = []
            self._xsec_hline   = None
            self._reset_axes()
            self.canvas_plot.draw()
        if self.btn_save_png is not None:
            self.btn_save_png.setEnabled(False)
        self.lbl_status.setText('')

    def _on_visibility_changed(self, visible):
        if not visible:
            if self.btn_draw.isChecked():
                self.btn_draw.setChecked(False)
                self._toggle_digitizing(False)
            if hasattr(self, 'btn_terrain_draw') and self.btn_terrain_draw.isChecked():
                self.btn_terrain_draw.setChecked(False)
                self._toggle_terrain_digitizing(False)
            self._hide_hover()

    # ------------------------------------------------------------------ live preview

    def _on_live_update(self, partial_geom):
        if self._active_tab != 0:
            return
        if self._extracting or not MATPLOTLIB_AVAILABLE:
            return
        raster_entries, vector_entries, col_meta = self._collect_entries()
        if not raster_entries and not vector_entries:
            return
        self._extracting = True
        try:
            data, chainages = self._extract(
                partial_geom, raster_entries, vector_entries,
                self.interval_spin.value()
            )
            self._plot(chainages, data, col_meta)
            if self.btn_save_png is not None:
                self.btn_save_png.setEnabled(True)
        except Exception:
            pass
        finally:
            self._extracting = False

    # ------------------------------------------------------------------ mouse / hover

    def _on_mouse_press(self, event):
        if event.button == 2 and event.inaxes is self.ax:
            self._pan_press_px  = (event.x, event.y)
            self._pan_xlim0     = self.ax.get_xlim()
            self._pan_ylim0     = self.ax.get_ylim()
            self._pan_transform = self.ax.transData.inverted().frozen()
            return
        if (event.button == 3 and self._sketch_mode is None
                and self._active_tab == _TAB_EMBANK and event.inaxes is self.ax
                and event.xdata is not None and event.ydata is not None):
            near = self._find_embank_break_near(event.xdata, event.ydata)
            if near is not None:
                self._remove_embank_break(near)
                return
            ctrl = self._find_embank_control_near(event.xdata, event.ydata)
            if ctrl is not None:
                self._remove_embank_control(ctrl)
            return
        if (event.button == 1 and self._sketch_mode is None
                and self._active_tab == _TAB_EMBANK and event.inaxes is self.ax
                and event.xdata is not None and event.ydata is not None):
            if not self._embank_add_break_mode and self._try_edit_embank_text(event):
                return
            end = self._find_embank_endpoint_near(event.xdata, event.ydata)
            if end is not None:
                self._embank_endpoint_drag = end
                self._select_embank_xs_node(end)
                return
            near = self._find_embank_break_near(event.xdata, event.ydata)
            if near is not None:
                self._embank_break_drag_obj = self._embank_invert_breaks[near]
                self._select_embank_xs_node(id(self._embank_break_drag_obj))
                return
            ctrl = self._find_embank_control_near(event.xdata, event.ydata)
            if ctrl is not None:
                self._select_embank_xs_node(id(ctrl))
                return
            if self._embank_add_break_mode:
                self._add_embank_break(event.xdata)
                return
            if self._embank_add_control_mode:
                self._add_embank_control(event.xdata)
                return
        if (event.button == 3 and self._sketch_mode is None
                and self._active_tab == _TAB_CHANNEL and event.inaxes is self.ax
                and event.xdata is not None and event.ydata is not None):
            near = self._find_terrain_break_near(event.xdata, event.ydata)
            if near is not None:
                self._remove_terrain_break(near)
                return
            ctrl = self._find_terrain_control_near(event.xdata, event.ydata)
            if ctrl is not None:
                self._remove_terrain_control(ctrl)
            return
        if (event.button == 1 and self._sketch_mode is None
                and self._active_tab == _TAB_CHANNEL and event.inaxes is self.ax
                and event.xdata is not None and event.ydata is not None):
            if not self._terrain_add_break_mode and self._try_edit_terrain_text(event):
                return
            end = self._find_terrain_endpoint_near(event.xdata, event.ydata)
            if end is not None:
                self._terrain_endpoint_drag = end
                # 'us'/'ds' from the endpoint finder ARE the node keys —
                # pressing an end triangle also selects it as the current
                # cross-section station, same as pressing a break does.
                self._select_terrain_xs_node(end)
                return
            near = self._find_terrain_break_near(event.xdata, event.ydata)
            if near is not None:
                self._terrain_break_drag_obj = self._terrain_invert_breaks[near]
                self._select_terrain_xs_node(id(self._terrain_break_drag_obj))
                return
            ctrl = self._find_terrain_control_near(event.xdata, event.ydata)
            if ctrl is not None:
                # Selecting an existing control section works regardless
                # of whether 'Add Control Section' is currently on — same
                # as break points always being selectable/editable
                # regardless of 'Add Vertical Break'. Draggable along
                # CHAINAGE only (see the drag-motion handler) — it still
                # carries no level of its own, always reading the
                # invert's own level at wherever its chainage currently
                # is, per explicit request.
                self._terrain_control_drag_obj = ctrl
                self._select_terrain_xs_node(id(ctrl))
                return
            if self._terrain_add_break_mode:
                self._add_terrain_break(event.xdata)
                return
            if self._terrain_add_control_mode:
                self._add_terrain_control(event.xdata)
                return
        if (event.button == 1 and self._sketch_mode is not None
                and event.inaxes is not None
                and event.xdata is not None and event.ydata is not None):
            self._sketch_on_press(event)

    def _find_terrain_break_near(self, xdata, ydata, tol_px=8):
        """Nearest break-point index within tol_px SCREEN pixels of a
        click — pixel distance rather than data distance since chainage
        (metres) and elevation (metres, but a much smaller data range) are
        wildly different scales on this chart."""
        if not self._terrain_invert_breaks:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        best_i, best_d = None, tol_px
        for i, b in enumerate(self._terrain_invert_breaks):
            pt_px = self.ax.transData.transform((b['chainage'], b['level']))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_i = d, i
        return best_i

    def _find_terrain_control_near(self, xdata, ydata, tol_px=8):
        """Nearest control section (its own dict, by reference) within
        tol_px SCREEN pixels of a click, or None — same pixel-distance
        technique as _find_terrain_break_near. Control sections are drawn
        at (chainage, invert level there), same y-position as the invert
        line itself since they carry no level of their own — draggable
        along chainage only, see _terrain_control_drag_obj."""
        if not self._terrain_control_sections:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        best_cs, best_d = None, tol_px
        for cs in self._terrain_control_sections:
            level = self._terrain_invert_level_at(cs['chainage'])
            pt_px = self.ax.transData.transform((cs['chainage'], level))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_cs = d, cs
        return best_cs

    def _find_terrain_endpoint_near(self, xdata, ydata, tol_px=10):
        """'us' or 'ds' if the click is near that end's triangle marker,
        else None. Checked separately from break points since the two end
        markers aren't in self._terrain_invert_breaks."""
        verts = self._terrain_invert_vertices()
        if verts is None:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        us_px = self.ax.transData.transform((verts[0][0], verts[0][1]))
        ds_px = self.ax.transData.transform((verts[-1][0], verts[-1][1]))
        d_us = math.hypot(us_px[0] - click_px[0], us_px[1] - click_px[1])
        d_ds = math.hypot(ds_px[0] - click_px[0], ds_px[1] - click_px[1])
        if d_us <= tol_px and d_us <= d_ds:
            return 'us'
        if d_ds <= tol_px:
            return 'ds'
        return None

    def _find_embank_break_near(self, xdata, ydata, tol_px=8):
        """Nearest break-point index within tol_px SCREEN pixels of a
        click — pixel distance rather than data distance since chainage
        (metres) and elevation (metres, but a much smaller data range) are
        wildly different scales on this chart."""
        if not self._embank_invert_breaks:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        best_i, best_d = None, tol_px
        for i, b in enumerate(self._embank_invert_breaks):
            pt_px = self.ax.transData.transform((b['chainage'], b['level']))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_i = d, i
        return best_i

    def _find_embank_control_near(self, xdata, ydata, tol_px=8):
        """Nearest control section (its own dict, by reference) within
        tol_px SCREEN pixels of a click, or None — same pixel-distance
        technique as _find_embank_break_near. Control sections are drawn
        at (chainage, invert level there), same y-position as the invert
        line itself since they carry no level of their own."""
        if not self._embank_control_sections:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        best_cs, best_d = None, tol_px
        for cs in self._embank_control_sections:
            level = self._embank_invert_level_at(cs['chainage'])
            pt_px = self.ax.transData.transform((cs['chainage'], level))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_cs = d, cs
        return best_cs

    def _find_embank_endpoint_near(self, xdata, ydata, tol_px=10):
        """'us' or 'ds' if the click is near that end's triangle marker,
        else None. Checked separately from break points since the two end
        markers aren't in self._embank_invert_breaks."""
        verts = self._embank_invert_vertices()
        if verts is None:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        us_px = self.ax.transData.transform((verts[0][0], verts[0][1]))
        ds_px = self.ax.transData.transform((verts[-1][0], verts[-1][1]))
        d_us = math.hypot(us_px[0] - click_px[0], us_px[1] - click_px[1])
        d_ds = math.hypot(ds_px[0] - click_px[0], ds_px[1] - click_px[1])
        if d_us <= tol_px and d_us <= d_ds:
            return 'us'
        if d_ds <= tol_px:
            return 'ds'
        return None

    def _on_mouse_release(self, event):
        if event.button == 2:
            self._pan_press_px = self._pan_xlim0 = \
                self._pan_ylim0 = self._pan_transform = None
        if event.button == 1 and self._embank_break_drag_obj is not None:
            self._embank_break_drag_obj = None
            self._draw_embank_vertical_decorations()   # drop the 'dragging' highlight colour
        if event.button == 1 and self._embank_endpoint_drag is not None:
            self._embank_commit_endpoint_chainage_drag()
            self._embank_endpoint_drag = None
            self._embank_endpoint_drag_chainage = None
            self._draw_embank_vertical_decorations()
        if event.button == 1 and self._terrain_break_drag_obj is not None:
            self._terrain_break_drag_obj = None
            self._draw_terrain_vertical_decorations()   # drop the 'dragging' highlight colour
        if event.button == 1 and self._terrain_control_drag_obj is not None:
            self._terrain_control_drag_obj = None
            self._draw_terrain_vertical_decorations()
        if event.button == 1 and self._terrain_endpoint_drag is not None:
            self._terrain_endpoint_drag = None
            self._draw_terrain_vertical_decorations()
        if event.button == 1 and self._sketch_pressed:
            self._sketch_pressed    = False
            self._sketch_current    = None
            self._sketch_press_data = None
            self._sketch_pen_pts    = ([], [])
            self._sketch_drag_info  = None
            self.canvas_plot.draw_idle()

    def _on_scroll_zoom(self, event):
        all_p = [self.ax] + self._extra_axes
        if event.inaxes not in all_p or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y    = event.xdata, event.ydata
        ax_hit  = event.inaxes
        xl, xr  = ax_hit.get_xlim()
        yb, yt  = ax_hit.get_ylim()
        ax_hit.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax_hit.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        if self.ax_xsec is not None and ax_hit is self.ax:
            self.ax_xsec.set_ylim(self.ax.get_ylim())
        self.canvas_plot.draw_idle()

    def _on_terrain_xs_scroll_zoom(self, event):
        """Same scroll-to-zoom convention as the main chart's own
        _on_scroll_zoom, scoped to the single embedded Cross-Section axis."""
        if event.inaxes is not self.ax_terrain_xs or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y = event.xdata, event.ydata
        ax = event.inaxes
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        ax.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        self.canvas_terrain_xs.draw_idle()

    def _on_terrain_xs_press(self, event):
        """Middle-mouse-drag pan for the embedded Cross-Section preview —
        no button needed, same 'hold the scroll wheel and drag' gesture as
        the main chart, just with its own state so the two never interfere."""
        if event.button == 2 and event.inaxes is self.ax_terrain_xs:
            self._terrain_xs_pan_press_px  = (event.x, event.y)
            self._terrain_xs_pan_xlim0     = self.ax_terrain_xs.get_xlim()
            self._terrain_xs_pan_ylim0     = self.ax_terrain_xs.get_ylim()
            self._terrain_xs_pan_transform = self.ax_terrain_xs.transData.inverted().frozen()
            return
        if (event.button == 1 and self._terrain_xs_elev_mode
                and event.inaxes is self.ax_terrain_xs
                and event.xdata is not None and event.ydata is not None):
            idx = self._find_terrain_xs_elev_marker_near(event.xdata, event.ydata)
            if idx is not None:
                self._remove_terrain_xs_elev_marker(idx)
            else:
                x, y = self._terrain_xs_snap_point(event.xdata, event.ydata) \
                    or (event.xdata, event.ydata)
                self._add_terrain_xs_elev_marker(x, y)

    def _terrain_xs_snap_point(self, xdata, ydata, tol_px=28):
        """Nearest point to the click on either the channel design line or
        the ground profile line — checking both their VERTICES and any
        point along a SEGMENT (via perpendicular pixel-space projection,
        clamped to the segment), not just vertices. Returns (x, y) in
        data coordinates, or None if nothing is within tol_px screen
        pixels (so a click far from both lines still free-places)."""
        ax = self.ax_terrain_xs
        click_px = ax.transData.transform((xdata, ydata))
        best = None  # (dist_px, x, y)

        def _consider(xs, ys):
            nonlocal best
            n = len(xs)
            for i in range(n):
                if ys[i] is None:
                    continue
                px, py = ax.transData.transform((xs[i], ys[i]))
                d = math.hypot(px - click_px[0], py - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i], ys[i])
            for i in range(n - 1):
                y0, y1 = ys[i], ys[i + 1]
                if y0 is None or y1 is None:
                    continue
                p0x, p0y = ax.transData.transform((xs[i], y0))
                p1x, p1y = ax.transData.transform((xs[i + 1], y1))
                sx, sy = p1x - p0x, p1y - p0y
                seg_len2 = sx * sx + sy * sy
                if seg_len2 < 1e-9:
                    continue
                t = ((click_px[0] - p0x) * sx + (click_px[1] - p0y) * sy) / seg_len2
                t = max(0.0, min(1.0, t))
                proj_x, proj_y = p0x + t * sx, p0y + t * sy
                d = math.hypot(proj_x - click_px[0], proj_y - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i] + t * (xs[i + 1] - xs[i]), y0 + t * (y1 - y0))

        key = self._terrain_selected_xs_key
        if key is not None:
            params = self._terrain_xs_params.get(key, self._terrain_xs_default)
            pos = self._terrain_selected_node_position()
            if pos is not None:
                poly_x, poly_y = self._terrain_xs_build_shape(pos[1], params, self._terrain_xs_ground)
                _consider(poly_x, poly_y)

        ground = self._terrain_xs_ground
        col = self._terrain_ground_col
        if ground is not None and col and col in ground['data']:
            _consider(ground['xs'], ground['data'][col])

        if best is not None and best[0] <= tol_px:
            return best[1], best[2]
        return None

    def _position_terrain_xs_elev_btn(self):
        """Tucks 'Elev'/'Add' inside the axes' own top-RIGHT corner (over
        the plotted data area) rather than in the blank margin above it,
        per explicit request — uses the axes' actual rendered bounding box
        (matplotlib bottom-left-origin pixels, converted to the Qt
        widget's top-left-origin space, same technique as the inline
        label editor's positioning). The 'Z:' factor control sits in the
        top-LEFT corner instead, per explicit request to keep it apart
        from those two."""
        btn = self.btn_terrain_xs_elev
        if btn is None or self.canvas_terrain_xs is None or self.ax_terrain_xs is None:
            return
        add_btn = self.btn_terrain_xs_add_compare
        zf_widget = self.zf_terrain_xs_widget
        try:
            renderer = self.canvas_terrain_xs.get_renderer()
            bbox = self.ax_terrain_xs.get_window_extent(renderer)
        except Exception:
            x = max(0, self.canvas_terrain_xs.width() - btn.width() - 6)
            btn.move(x, 4)
            if add_btn is not None:
                add_btn.move(x, 4 + btn.height() + 4)
            if zf_widget is not None:
                zf_widget.move(6, 4)
            return
        canvas_h = self.canvas_terrain_xs.height()
        x = max(0, int(bbox.x1) - btn.width() - 6)
        y = max(0, int(canvas_h - bbox.y1) + 4)
        btn.move(x, y)
        if add_btn is not None:
            add_btn.move(x, y + btn.height() + 4)
        if zf_widget is not None:
            zf_widget.move(max(0, int(bbox.x0) + 6), y)

    def _on_terrain_xs_zfactor_changed(self):
        """Sets the embedded Cross-Section Preview's OWN vertical-
        exaggeration Z factor and redraws just that preview. Deliberately
        independent of Check Cross-Section's own Z factor (see
        TerrainCheckXSDialog._on_zfactor_changed) — per explicit request,
        after trying a shared value, that changing Z in one of these two
        views should never move the other."""
        self._terrain_xs_z_factor = self.spin_terrain_xs_zfactor.value()
        self._draw_terrain_xs_preview()

    def _on_terrain_xs_canvas_resize(self, event):
        """Re-flow margins on resize too, not just on redraw — otherwise
        resizing the dock narrower/shorter after a plot is already drawn
        can leave the x-axis label clipped until the next full redraw."""
        try:
            self.figure_terrain_xs.tight_layout(pad=1.0)
        except Exception:
            pass
        self._position_terrain_xs_elev_btn()

    def _toggle_terrain_xs_elev_tool(self, checked):
        self._terrain_xs_elev_mode = checked
        if checked:
            self._terrain_deactivate_all_tools(except_btn=self.btn_terrain_xs_elev)
        if not checked:
            self._clear_terrain_xs_elev_markers()

    def _on_terrain_xs_add_compare(self):
        """Picks (or clears) the raster the Cross-Section Preview samples
        and plots as a comparison line (red, solid, 50% opacity, width 1
        — _terrain_draw_xs_axes' 'compare' line) alongside the design and
        ground profile. Defaults to the most recently Create-TIFF'd
        layer, but works with any loaded raster — useful for checking the
        actual generated surface against what it was built from, or just
        comparing against a different DEM."""
        dlg = QDialog(self)
        dlg.setWindowTitle('Add Comparison Raster')
        v = QVBoxLayout(dlg)
        lbl = QLabel(
            'Pick a raster to sample along this same cross-section and\n'
            'plot for comparison (red line).'
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        combo = QgsMapLayerComboBox()
        combo.setFilters(_RASTER_FILTER)
        combo.setAllowEmptyLayer(True)
        combo.wheelEvent = lambda e: e.ignore()
        default_layer = self._terrain_xs_compare_layer or self._terrain_last_tiff_layer
        if default_layer is not None:
            combo.setLayer(default_layer)
        else:
            combo.setCurrentIndex(0)
        v.addWidget(combo)

        btns = QHBoxLayout()
        btn_clear = QPushButton('Remove')
        btn_clear.setEnabled(self._terrain_xs_compare_layer is not None)
        btns.addWidget(btn_clear)
        btns.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_ok = QPushButton('OK')
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _do_clear():
            self._terrain_xs_compare_layer = None
            dlg.accept()
            self._refresh_terrain_xs()

        def _do_ok():
            self._terrain_xs_compare_layer = combo.currentLayer()
            dlg.accept()
            self._refresh_terrain_xs()

        btn_clear.clicked.connect(_do_clear)
        btn_ok.clicked.connect(_do_ok)
        dlg.exec()

    def _find_terrain_xs_elev_marker_near(self, xdata, ydata, tol_px=10):
        """Nearest existing elevation marker within tol_px SCREEN pixels
        of a click, or None — same pixel-distance technique as the
        vertical alignment chart's own break-point hit-testing, so a
        click on an existing marker removes JUST that one rather than
        every marker on the chart."""
        if not self._terrain_xs_elev_markers:
            return None
        ax = self.ax_terrain_xs
        click_px = ax.transData.transform((xdata, ydata))
        best_i, best_d = None, tol_px
        for i, m in enumerate(self._terrain_xs_elev_markers):
            pt_px = ax.transData.transform((m['x'], m['y']))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_i = d, i
        return best_i

    def _remove_terrain_xs_elev_marker(self, idx):
        if not (0 <= idx < len(self._terrain_xs_elev_markers)):
            return
        m = self._terrain_xs_elev_markers.pop(idx)
        for art in (m['marker'], m['text']):
            try: art.remove()
            except Exception: pass
        self.canvas_terrain_xs.draw_idle()

    def _clear_terrain_xs_elev_markers(self):
        for m in self._terrain_xs_elev_markers:
            for art in (m['marker'], m['text']):
                try: art.remove()
                except Exception: pass
        self._terrain_xs_elev_markers = []
        if self.canvas_terrain_xs is not None:
            self.canvas_terrain_xs.draw_idle()

    def _add_terrain_xs_elev_marker(self, x, y):
        """Inverted-triangle spot elevation marker, same convention as
        the Layer Profile tab's own elevation tool — independent of any
        other markers already on the chart, so several can coexist and
        each is only removed by clicking that specific one."""
        ax = self.ax_terrain_xs
        mk, = ax.plot([x], [y], marker='v', markersize=12, markeredgewidth=1.3,
                      markerfacecolor='#D32F2F', markeredgecolor='#7A0000', zorder=30)
        txt = ax.annotate(
            f'{y:.3f} masl', xy=(x, y), xytext=(6, -12), textcoords='offset points',
            fontsize=8, fontweight='bold', color='#D32F2F',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.9, edgecolor='none'),
            zorder=30,
        )
        self._terrain_xs_elev_markers.append({'x': x, 'y': y, 'marker': mk, 'text': txt})
        self.canvas_terrain_xs.draw_idle()

    def _on_terrain_xs_motion(self, event):
        if self._terrain_xs_pan_press_px is None or self._terrain_xs_pan_transform is None:
            return
        if event.x is None or event.y is None:
            return
        x0d, y0d = self._terrain_xs_pan_transform.transform(self._terrain_xs_pan_press_px)
        x1d, y1d = self._terrain_xs_pan_transform.transform((event.x, event.y))
        dx, dy = x0d - x1d, y0d - y1d
        xl0, xr0 = self._terrain_xs_pan_xlim0
        yb0, yt0 = self._terrain_xs_pan_ylim0
        self.ax_terrain_xs.set_xlim([xl0 + dx, xr0 + dx])
        self.ax_terrain_xs.set_ylim([yb0 + dy, yt0 + dy])
        self.canvas_terrain_xs.draw_idle()

    def _on_terrain_xs_release(self, event):
        if event.button == 2:
            self._terrain_xs_pan_press_px = self._terrain_xs_pan_xlim0 = \
                self._terrain_xs_pan_ylim0 = self._terrain_xs_pan_transform = None

    def _on_embank_xs_scroll_zoom(self, event):
        """Same scroll-to-zoom convention as the main chart's own
        _on_scroll_zoom, scoped to the single embedded Cross-Section axis."""
        if event.inaxes is not self.ax_embank_xs or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y = event.xdata, event.ydata
        ax = event.inaxes
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        ax.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        self.canvas_embank_xs.draw_idle()

    def _on_embank_xs_press(self, event):
        """Middle-mouse-drag pan for the embedded Cross-Section preview —
        no button needed, same 'hold the scroll wheel and drag' gesture as
        the main chart, just with its own state so the two never interfere."""
        if event.button == 2 and event.inaxes is self.ax_embank_xs:
            self._embank_xs_pan_press_px  = (event.x, event.y)
            self._embank_xs_pan_xlim0     = self.ax_embank_xs.get_xlim()
            self._embank_xs_pan_ylim0     = self.ax_embank_xs.get_ylim()
            self._embank_xs_pan_transform = self.ax_embank_xs.transData.inverted().frozen()
            return
        if (event.button == 1 and self._embank_xs_elev_mode
                and event.inaxes is self.ax_embank_xs
                and event.xdata is not None and event.ydata is not None):
            idx = self._find_embank_xs_elev_marker_near(event.xdata, event.ydata)
            if idx is not None:
                self._remove_embank_xs_elev_marker(idx)
            else:
                x, y = self._embank_xs_snap_point(event.xdata, event.ydata) \
                    or (event.xdata, event.ydata)
                self._add_embank_xs_elev_marker(x, y)

    def _embank_xs_snap_point(self, xdata, ydata, tol_px=28):
        """Nearest point to the click on either the channel design line or
        the ground profile line — checking both their VERTICES and any
        point along a SEGMENT (via perpendicular pixel-space projection,
        clamped to the segment), not just vertices. Returns (x, y) in
        data coordinates, or None if nothing is within tol_px screen
        pixels (so a click far from both lines still free-places)."""
        ax = self.ax_embank_xs
        click_px = ax.transData.transform((xdata, ydata))
        best = None  # (dist_px, x, y)

        def _consider(xs, ys):
            nonlocal best
            n = len(xs)
            for i in range(n):
                if ys[i] is None:
                    continue
                px, py = ax.transData.transform((xs[i], ys[i]))
                d = math.hypot(px - click_px[0], py - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i], ys[i])
            for i in range(n - 1):
                y0, y1 = ys[i], ys[i + 1]
                if y0 is None or y1 is None:
                    continue
                p0x, p0y = ax.transData.transform((xs[i], y0))
                p1x, p1y = ax.transData.transform((xs[i + 1], y1))
                sx, sy = p1x - p0x, p1y - p0y
                seg_len2 = sx * sx + sy * sy
                if seg_len2 < 1e-9:
                    continue
                t = ((click_px[0] - p0x) * sx + (click_px[1] - p0y) * sy) / seg_len2
                t = max(0.0, min(1.0, t))
                proj_x, proj_y = p0x + t * sx, p0y + t * sy
                d = math.hypot(proj_x - click_px[0], proj_y - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i] + t * (xs[i + 1] - xs[i]), y0 + t * (y1 - y0))

        key = self._embank_selected_xs_key
        if key is not None:
            params = self._embank_xs_params.get(key, self._embank_xs_default)
            pos = self._embank_selected_node_position()
            if pos is not None:
                poly_x, poly_y = self._embank_xs_build_shape(pos[1], params, self._embank_xs_ground)
                _consider(poly_x, poly_y)

        ground = self._embank_xs_ground
        col = self._embank_ground_col
        if ground is not None and col and col in ground['data']:
            _consider(ground['xs'], ground['data'][col])

        if best is not None and best[0] <= tol_px:
            return best[1], best[2]
        return None

    def _position_embank_xs_elev_btn(self):
        """Tucks 'Elev'/'Add' inside the axes' own top-RIGHT corner (over
        the plotted data area) rather than in the blank margin above it,
        per explicit request — uses the axes' actual rendered bounding box
        (matplotlib bottom-left-origin pixels, converted to the Qt
        widget's top-left-origin space, same technique as the inline
        label editor's positioning). The 'Z:' factor control sits in the
        top-LEFT corner instead, per explicit request to keep it apart
        from those two."""
        btn = self.btn_embank_xs_elev
        if btn is None or self.canvas_embank_xs is None or self.ax_embank_xs is None:
            return
        add_btn = self.btn_embank_xs_add_compare
        zf_widget = self.zf_embank_xs_widget
        try:
            renderer = self.canvas_embank_xs.get_renderer()
            bbox = self.ax_embank_xs.get_window_extent(renderer)
        except Exception:
            x = max(0, self.canvas_embank_xs.width() - btn.width() - 6)
            btn.move(x, 4)
            if add_btn is not None:
                add_btn.move(x, 4 + btn.height() + 4)
            if zf_widget is not None:
                zf_widget.move(6, 4)
            return
        canvas_h = self.canvas_embank_xs.height()
        x = max(0, int(bbox.x1) - btn.width() - 6)
        y = max(0, int(canvas_h - bbox.y1) + 4)
        btn.move(x, y)
        if add_btn is not None:
            add_btn.move(x, y + btn.height() + 4)
        if zf_widget is not None:
            zf_widget.move(max(0, int(bbox.x0) + 6), y)

    def _on_embank_xs_zfactor_changed(self):
        """Sets the embedded Cross-Section Preview's OWN vertical-
        exaggeration Z factor and redraws just that preview. Deliberately
        independent of Check Cross-Section's own Z factor (see
        EmbankCheckXSDialog._on_zfactor_changed) — per explicit request,
        after trying a shared value, that changing Z in one of these two
        views should never move the other."""
        self._embank_xs_z_factor = self.spin_embank_xs_zfactor.value()
        self._draw_embank_xs_preview()

    def _on_embank_xs_canvas_resize(self, event):
        """Re-flow margins on resize too, not just on redraw — otherwise
        resizing the dock narrower/shorter after a plot is already drawn
        can leave the x-axis label clipped until the next full redraw."""
        try:
            self.figure_embank_xs.tight_layout(pad=1.0)
        except Exception:
            pass
        self._position_embank_xs_elev_btn()

    def _toggle_embank_xs_elev_tool(self, checked):
        self._embank_xs_elev_mode = checked
        if checked:
            self._embank_deactivate_all_tools(except_btn=self.btn_embank_xs_elev)
        if not checked:
            self._clear_embank_xs_elev_markers()

    def _on_embank_xs_add_compare(self):
        """Picks (or clears) the raster the Cross-Section Preview samples
        and plots as a comparison line (red, solid, 50% opacity, width 1
        — _embank_draw_xs_axes' 'compare' line) alongside the design and
        ground profile. Defaults to the most recently Create-TIFF'd
        layer, but works with any loaded raster — useful for checking the
        actual generated surface against what it was built from, or just
        comparing against a different DEM."""
        dlg = QDialog(self)
        dlg.setWindowTitle('Add Comparison Raster')
        v = QVBoxLayout(dlg)
        lbl = QLabel(
            'Pick a raster to sample along this same cross-section and\n'
            'plot for comparison (red line).'
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        combo = QgsMapLayerComboBox()
        combo.setFilters(_RASTER_FILTER)
        combo.setAllowEmptyLayer(True)
        combo.wheelEvent = lambda e: e.ignore()
        default_layer = self._embank_xs_compare_layer or self._embank_last_tiff_layer
        if default_layer is not None:
            combo.setLayer(default_layer)
        else:
            combo.setCurrentIndex(0)
        v.addWidget(combo)

        btns = QHBoxLayout()
        btn_clear = QPushButton('Remove')
        btn_clear.setEnabled(self._embank_xs_compare_layer is not None)
        btns.addWidget(btn_clear)
        btns.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_ok = QPushButton('OK')
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _do_clear():
            self._embank_xs_compare_layer = None
            dlg.accept()
            self._refresh_embank_xs()

        def _do_ok():
            self._embank_xs_compare_layer = combo.currentLayer()
            dlg.accept()
            self._refresh_embank_xs()

        btn_clear.clicked.connect(_do_clear)
        btn_ok.clicked.connect(_do_ok)
        dlg.exec()

    def _find_embank_xs_elev_marker_near(self, xdata, ydata, tol_px=10):
        """Nearest existing elevation marker within tol_px SCREEN pixels
        of a click, or None — same pixel-distance technique as the
        vertical alignment chart's own break-point hit-testing, so a
        click on an existing marker removes JUST that one rather than
        every marker on the chart."""
        if not self._embank_xs_elev_markers:
            return None
        ax = self.ax_embank_xs
        click_px = ax.transData.transform((xdata, ydata))
        best_i, best_d = None, tol_px
        for i, m in enumerate(self._embank_xs_elev_markers):
            pt_px = ax.transData.transform((m['x'], m['y']))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_i = d, i
        return best_i

    def _remove_embank_xs_elev_marker(self, idx):
        if not (0 <= idx < len(self._embank_xs_elev_markers)):
            return
        m = self._embank_xs_elev_markers.pop(idx)
        for art in (m['marker'], m['text']):
            try: art.remove()
            except Exception: pass
        self.canvas_embank_xs.draw_idle()

    def _clear_embank_xs_elev_markers(self):
        for m in self._embank_xs_elev_markers:
            for art in (m['marker'], m['text']):
                try: art.remove()
                except Exception: pass
        self._embank_xs_elev_markers = []
        if self.canvas_embank_xs is not None:
            self.canvas_embank_xs.draw_idle()

    def _add_embank_xs_elev_marker(self, x, y):
        """Inverted-triangle spot elevation marker, same convention as
        the Layer Profile tab's own elevation tool — independent of any
        other markers already on the chart, so several can coexist and
        each is only removed by clicking that specific one."""
        ax = self.ax_embank_xs
        mk, = ax.plot([x], [y], marker='v', markersize=12, markeredgewidth=1.3,
                      markerfacecolor='#D32F2F', markeredgecolor='#7A0000', zorder=30)
        txt = ax.annotate(
            f'{y:.3f} masl', xy=(x, y), xytext=(6, -12), textcoords='offset points',
            fontsize=8, fontweight='bold', color='#D32F2F',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.9, edgecolor='none'),
            zorder=30,
        )
        self._embank_xs_elev_markers.append({'x': x, 'y': y, 'marker': mk, 'text': txt})
        self.canvas_embank_xs.draw_idle()

    def _on_embank_xs_motion(self, event):
        if self._embank_xs_pan_press_px is None or self._embank_xs_pan_transform is None:
            return
        if event.x is None or event.y is None:
            return
        x0d, y0d = self._embank_xs_pan_transform.transform(self._embank_xs_pan_press_px)
        x1d, y1d = self._embank_xs_pan_transform.transform((event.x, event.y))
        dx, dy = x0d - x1d, y0d - y1d
        xl0, xr0 = self._embank_xs_pan_xlim0
        yb0, yt0 = self._embank_xs_pan_ylim0
        self.ax_embank_xs.set_xlim([xl0 + dx, xr0 + dx])
        self.ax_embank_xs.set_ylim([yb0 + dy, yt0 + dy])
        self.canvas_embank_xs.draw_idle()

    def _on_embank_xs_release(self, event):
        if event.button == 2:
            self._embank_xs_pan_press_px = self._embank_xs_pan_xlim0 = \
                self._embank_xs_pan_ylim0 = self._embank_xs_pan_transform = None

    def _on_chart_hover(self, event):
        if not MATPLOTLIB_AVAILABLE:
            return

        # Same break/endpoint dragging as Channel Surface (see its own
        # comment just below), independently for Embankment Surface.
        if self._embank_break_drag_obj is not None:
            if event.inaxes is self.ax and event.xdata is not None and event.ydata is not None:
                length = self._embank_geom.length() if self._embank_geom is not None else None
                if length:
                    self._embank_break_drag_obj['chainage'] = \
                        max(1e-3, min(length - 1e-3, float(event.xdata)))
                self._embank_break_drag_obj['level'] = float(event.ydata)
                self._embank_invert_breaks.sort(key=lambda b: b['chainage'])
                self._draw_embank_vertical_decorations()
                self._update_embank_diff_annotation(self._embank_break_drag_obj['chainage'])
                self.canvas_plot.draw_idle()
            return

        if self._embank_endpoint_drag is not None:
            if event.inaxes is self.ax and event.ydata is not None:
                level = float(event.ydata)
                ch_for_diff = 0.0
                if self._embank_endpoint_drag == 'us':
                    self._embank_us_level = level
                    self.spin_embank_us_level.blockSignals(True)
                    self.spin_embank_us_level.setValue(level)
                    self.spin_embank_us_level.blockSignals(False)
                else:
                    self._embank_ds_level = level
                    self.spin_embank_ds_level.blockSignals(True)
                    self.spin_embank_ds_level.setValue(level)
                    self.spin_embank_ds_level.blockSignals(False)
                    if self._embank_geom is not None:
                        ch_for_diff = self._embank_geom.length()
                # Horizontal movement is tracked only as a PREVIEW chainage
                # (the marker's own drawn x — see _draw_embank_vertical_
                # decorations) — the real alignment/raw points are only
                # actually changed once the drag is released, in
                # _on_mouse_release, since that needs a full ground
                # re-sample and shouldn't run on every mouse-move tick.
                if event.xdata is not None and self._embank_geom is not None:
                    length = self._embank_geom.length()
                    min_gap = max(1e-3, length * 0.01)
                    if self._embank_endpoint_drag == 'us':
                        # Can trim inward up to min_gap before the DS end,
                        # or extend outward only as far as the current
                        # Extend US budget already plots ground for —
                        # dragging the real start further than that would
                        # shrink the total visible ground-profile extent.
                        lo = -max(0.0, self._embank_extend_us)
                        hi = length - min_gap
                        self._embank_endpoint_drag_chainage = \
                            max(lo, min(float(event.xdata), hi))
                    else:
                        lo = min_gap
                        hi = length + max(0.0, self._embank_extend_ds)
                        self._embank_endpoint_drag_chainage = \
                            max(lo, min(float(event.xdata), hi))
                self._draw_embank_vertical_decorations()
                self._update_embank_diff_annotation(ch_for_diff)
                self.canvas_plot.draw_idle()
            return

        # Dragging a break point — both level (Y) and chainage (X) — so the
        # invert line, slope labels, cut/fill hatch AND the map-canvas tick
        # marker all update live, right up until release. Re-sorting after
        # every move lets a break slide past a neighbour (it just swaps
        # which segment is which); the drag target is tracked by object
        # reference rather than list index specifically so that re-sort
        # never loses track of which one is actually being dragged.
        if self._terrain_break_drag_obj is not None:
            if event.inaxes is self.ax and event.xdata is not None and event.ydata is not None:
                length = self._terrain_geom.length() if self._terrain_geom is not None else None
                if length:
                    self._terrain_break_drag_obj['chainage'] = \
                        max(1e-3, min(length - 1e-3, float(event.xdata)))
                self._terrain_break_drag_obj['level'] = float(event.ydata)
                self._terrain_invert_breaks.sort(key=lambda b: b['chainage'])
                self._draw_terrain_vertical_decorations()   # recreates _terrain_diff_ann fresh
                self._update_terrain_diff_annotation(self._terrain_break_drag_obj['chainage'])
                self.canvas_plot.draw_idle()
            return

        # Dragging a control section — CHAINAGE (X) only, per explicit
        # request; it carries no level of its own to drag, always reading
        # the invert's own level at wherever its chainage currently is
        # (see _draw_terrain_vertical_decorations), so the Y position
        # just follows automatically as the invert line itself redraws.
        if self._terrain_control_drag_obj is not None:
            if event.inaxes is self.ax and event.xdata is not None:
                length = self._terrain_geom.length() if self._terrain_geom is not None else None
                if length:
                    self._terrain_control_drag_obj['chainage'] = \
                        max(1e-3, min(length - 1e-3, float(event.xdata)))
                self._terrain_control_sections.sort(key=lambda c: c['chainage'])
                self._draw_terrain_vertical_decorations()
                self._update_terrain_diff_annotation(self._terrain_control_drag_obj['chainage'])
                self.canvas_plot.draw_idle()
            return

        # Dragging an end-level triangle (US at chainage 0 / DS at the end
        # chainage) — level (Y) only, chainage never moves for these two,
        # per the brief. Keeps the spinbox in sync so either editing method
        # (drag or type a value) always agrees with the other.
        if self._terrain_endpoint_drag is not None:
            if event.inaxes is self.ax and event.ydata is not None:
                level = float(event.ydata)
                ch_for_diff = 0.0
                if self._terrain_endpoint_drag == 'us':
                    self._terrain_us_level = level
                    self.spin_terrain_us_level.blockSignals(True)
                    self.spin_terrain_us_level.setValue(level)
                    self.spin_terrain_us_level.blockSignals(False)
                else:
                    self._terrain_ds_level = level
                    self.spin_terrain_ds_level.blockSignals(True)
                    self.spin_terrain_ds_level.setValue(level)
                    self.spin_terrain_ds_level.blockSignals(False)
                    if self._terrain_geom is not None:
                        ch_for_diff = self._terrain_geom.length()
                self._draw_terrain_vertical_decorations()
                self._update_terrain_diff_annotation(ch_for_diff)
                self.canvas_plot.draw_idle()
            return

        # Ctrl+pen polyline: live rubber band without holding mouse button
        if (self._pen_poly_mode and self._pen_poly_art is not None
                and event.inaxes is not None
                and event.xdata is not None and event.ydata is not None):
            xs = list(self._pen_poly_pts[0]) + [event.xdata]
            ys = list(self._pen_poly_pts[1]) + [event.ydata]
            self._pen_poly_art.set_data(xs, ys)
            self.canvas_plot.draw_idle()
            return

        # Sketch tool motion (suppress hover cursor while drawing)
        if (self._sketch_pressed and self._sketch_mode is not None
                and event.x is not None and event.y is not None):
            self._sketch_on_motion(event)
            return

        # Middle-mouse pan
        if self._pan_press_px is not None and self._pan_transform is not None:
            if event.x is not None and event.y is not None:
                x0d, y0d = self._pan_transform.transform(self._pan_press_px)
                x1d, y1d = self._pan_transform.transform((event.x, event.y))
                dx, dy = x0d - x1d, y0d - y1d
                xl0, xr0 = self._pan_xlim0
                yb0, yt0 = self._pan_ylim0
                self.ax.set_xlim([xl0 + dx, xr0 + dx])
                self.ax.set_ylim([yb0 + dy, yt0 + dy])
                if self.ax_xsec is not None:
                    self.ax_xsec.set_ylim(self.ax.get_ylim())
                self.canvas_plot.draw_idle()
            return

        if self.profile_geom is None:
            return
        if self._nav_toolbar is not None and self._nav_toolbar.mode:
            self._hide_hover(); return
        all_p = [self.ax] + self._extra_axes
        if event.inaxes not in all_p or event.xdata is None:
            self._hide_hover(); return

        # Level tool: update persistent snap indicator (+) in place
        if self._sketch_mode == 'level':
            if self._level_snap_art is not None:
                ax = event.inaxes
                snapped_x, snapped_y, snap_col, snap_type = _snap_level(ax, event.xdata, event.ydata)
                col = snap_col or '#888888'
                self._level_snap_art.set_data([snapped_x], [snapped_y])
                if snap_type == 'vertex':
                    self._level_snap_art.set_marker('s')
                    self._level_snap_art.set_markersize(8)
                    self._level_snap_art.set_markerfacecolor('none')
                else:
                    self._level_snap_art.set_marker('+')
                    self._level_snap_art.set_markersize(13)
                self._level_snap_art.set_markeredgecolor(col)
                self._level_snap_art.set_alpha(1.0 if snap_col else 0.5)
                self._level_snap_art.set_visible(True)
                self.canvas_plot.draw_idle()
            return

        total = self.profile_geom.length()
        ch = max(0.0, min(float(event.xdata), total))

        for cl in self._cursor_lines:
            if cl is not None:
                cl.set_xdata([ch, ch])
                cl.set_visible(True)

        elev = float(event.ydata) if event.ydata is not None else None

        # Horizontal elevation lines in all profile panels
        if elev is not None:
            for hl in self._cc_hlines:
                if hl is not None:
                    try:
                        hl.set_ydata([elev, elev])
                        hl.set_visible(True)
                    except Exception:
                        pass

        # Horizontal elevation line in stage-area panel (existing)
        if self.ax_xsec is not None and self._xsec_hline is not None and elev is not None:
            try:
                self._xsec_hline.set_ydata([elev, elev])
                self._xsec_hline.set_visible(True)
            except Exception:
                pass

        # Dynamic airspace fill — redraws up to current hover elevation
        if (elev is not None and self._xsec_fill_data
                and self._xsec_xs_w is not None and self._xsec_z_top is not None):
            e_cap    = min(elev, self._xsec_z_top)
            cutfill  = True  # always shade in cross-section view
            for fc in self._xsec_fill_cols:
                try:
                    fc.remove()
                except Exception:
                    pass
            self._xsec_fill_cols = []
            for fd in self._xsec_fill_data:
                fc = self._draw_layer_fill(
                    self._xsec_xs_w, fd['z_w'], e_cap,
                    fd['col_str'], fd['hatch'], fd['valid'], cutfill,
                )
                self._xsec_fill_cols.append(fc)

            # Area readout — floating text inside Stage-Area panel
            if self._xsec_curves_store and self._xsec_area_text is not None:
                lines = []
                for col_key, (elevs, areas, _c) in self._xsec_curves_store.items():
                    a = float(np.interp(elev, elevs, areas,
                                        left=0.0, right=float(areas[-1])))
                    short = col_key if len(col_key) <= 14 else col_key[:13] + '…'
                    lines.append(f'[{short}]\n{a:.2f} m²')
                try:
                    self._xsec_area_text.set_position((0.04, elev))
                    self._xsec_area_text.set_text('\n'.join(lines))
                    self._xsec_area_text.set_visible(True)
                except Exception:
                    pass

        # Chainage lookup — used for both legend live values and bottom bar
        _hi = None
        if self._profile_chainages and self._profile_data_store:
            _arr = np.array(self._profile_chainages)
            _hi  = int(np.searchsorted(_arr, ch))
            _hi  = min(max(_hi, 0), len(_arr) - 1)
            if _hi > 0 and abs(_arr[_hi - 1] - ch) < abs(_arr[_hi] - ch):
                _hi -= 1

            def _fv(v):
                if v is None: return None
                try:
                    f = float(v); return f if np.isfinite(f) else None
                except (TypeError, ValueError): return None

            # Update live values in legend texts for every profile window
            _all_p = [self.ax] + self._extra_axes
            for _j, _ax in enumerate(_all_p):
                _lgd = _ax.get_legend()
                if _lgd is None:
                    continue
                _cols  = self._plot_cols_per_ax.get(_j, [])
                _texts = _lgd.get_texts()
                for _k, _col in enumerate(_cols):
                    if _k >= len(_texts):
                        break
                    _vals = self._profile_data_store.get(_col, [])
                    _v    = _fv(_vals[_hi]) if _hi < len(_vals) else None
                    _vs   = f'{_v:.3f}' if _v is not None else '—'
                    _texts[_k].set_text(f'{_prune_mid(_col)} [{_vs}]')

        self.canvas_plot.draw_idle()

        pt_xy = self.profile_geom.interpolate(ch).asPoint()
        self._hover_band.reset(_POINT_GEOM)
        self._hover_band.addPoint(QgsPointXY(pt_xy))

        if _hi is not None and self._active_tab == _TAB_EMBANK:
            _shared_ann = self._cf_ann_texts.get(0)
            if _shared_ann is not None:
                _shared_ann.set_visible(False)
            self._update_embank_diff_annotation(ch)
        elif _hi is not None and self._active_tab == _TAB_CHANNEL:
            # Channel Surface has its own dedicated Y1=invert / Y2=picked-
            # raster difference readout — the shared Profile Windows one
            # below doesn't apply here (that section is hidden on this
            # tab), so make sure IT stays hidden rather than showing
            # whatever the Layer Profile tab last set it to.
            _shared_ann = self._cf_ann_texts.get(0)
            if _shared_ann is not None:
                _shared_ann.set_visible(False)
            self._update_terrain_diff_annotation(ch)
        elif _hi is not None:
            # Per-window ΔY annotation (shown inside the plot axes, not in a bottom bar)
            _all_p2 = [self.ax] + self._extra_axes
            for _j2, _ax2 in enumerate(_all_p2):
                _ann = self._cf_ann_texts.get(_j2)
                _cfg2 = self._win_cfgs[_j2] if _j2 < len(self._win_cfgs) else None
                if _ann is None or _cfg2 is None:
                    continue
                if (_cfg2['cutfill_cb'].isChecked()
                        and _cfg2['cf_y1'].count() > 0 and _cfg2['cf_y2'].count() > 0):
                    _y1k = _cfg2['cf_y1'].currentText()
                    _y2k = _cfg2['cf_y2'].currentText()
                    _y1l = self._profile_data_store.get(_y1k, [])
                    _y2l = self._profile_data_store.get(_y2k, [])
                    _y1v = _fv(_y1l[_hi]) if _hi < len(_y1l) else None
                    _y2v = _fv(_y2l[_hi]) if _hi < len(_y2l) else None
                    if _y1v is not None and _y2v is not None:
                        _diff = _y2v - _y1v
                        _ann.set_text(f'ΔY: {_diff:+.3f} m')
                        _ann.set_visible(True)
                    else:
                        _ann.set_visible(False)
                else:
                    _ann.set_visible(False)

    def _do_tight_layout(self):
        """Reflow the figure margins so titles/labels are never clipped."""
        try:
            self.figure.tight_layout(pad=1.0)
        except Exception:
            pass

    def _on_chart_resize(self, event):
        """Re-run tight_layout whenever the canvas is resized."""
        self._do_tight_layout()
        self.canvas_plot.draw_idle()

    def _on_chart_leave(self, event):
        self._hide_hover()

    def _hide_hover(self):
        self._hover_band.reset(_POINT_GEOM)
        for cl in self._cursor_lines:
            if cl is not None:
                cl.set_visible(False)
        for hl in self._cc_hlines:
            if hl is not None:
                try:
                    hl.set_visible(False)
                except Exception:
                    pass
        if self._xsec_hline is not None:
            try:
                self._xsec_hline.set_visible(False)
            except Exception:
                self._xsec_hline = None

        # Reset dynamic fills back to full z_top (total airspace view)
        if (self._xsec_fill_data and self._xsec_xs_w is not None
                and self._xsec_z_top is not None):
            for fc in self._xsec_fill_cols:
                try:
                    fc.remove()
                except Exception:
                    pass
            self._xsec_fill_cols = []
            cutfill = True  # always shade in cross-section view
            for fd in self._xsec_fill_data:
                fc = self._draw_layer_fill(
                    self._xsec_xs_w, fd['z_w'], self._xsec_z_top,
                    fd['col_str'], fd['hatch'], fd['valid'], cutfill,
                )
                self._xsec_fill_cols.append(fc)

        if self._xsec_area_text is not None:
            try:
                self._xsec_area_text.set_visible(False)
            except Exception:
                pass

        if MATPLOTLIB_AVAILABLE:
            # Reset legend texts to plain names (strip live values)
            try:
                _all_p = [self.ax] + self._extra_axes
                for _j, _ax in enumerate(_all_p):
                    _lgd = _ax.get_legend()
                    if _lgd is None:
                        continue
                    _cols  = self._plot_cols_per_ax.get(_j, [])
                    _texts = _lgd.get_texts()
                    for _k, _col in enumerate(_cols):
                        if _k >= len(_texts):
                            break
                        _texts[_k].set_text(_prune_mid(_col))
            except Exception:
                pass
            self.canvas_plot.draw_idle()
        for _ann in getattr(self, '_cf_ann_texts', {}).values():
            try: _ann.set_visible(False)
            except Exception: pass
        if getattr(self, '_terrain_diff_ann', None) is not None:
            try: self._terrain_diff_ann.set_visible(False)
            except Exception: pass

    # ------------------------------------------------------------------ sketch tool

    def _sketch_activate(self, mode):
        for m, btn in self._sketch_btns.items():
            btn.setChecked(m == mode)
        self._sketch_mode = mode
        if mode == 'level':
            # Pre-create a persistent snap indicator so we never create/destroy
            # it inside the hot motion path (that causes missed draws).
            if self._level_snap_art is not None:
                try: self._level_snap_art.remove()
                except Exception: pass
                self._level_snap_art = None
            if MATPLOTLIB_AVAILABLE and hasattr(self, 'ax'):
                try:
                    self._level_snap_art, = self.ax.plot(
                        [], [], marker='+', markersize=13, markeredgewidth=1.5,
                        markerfacecolor='none', markeredgecolor='#888888',
                        linestyle='none', zorder=30, clip_on=False, visible=False)
                except Exception:
                    pass
        try:
            _cs = Qt.CursorShape
            _cur = {
                'move':   _cs.SizeAllCursor,
                'eraser': _cs.ForbiddenCursor,
                'edit':   _cs.PointingHandCursor,
            }.get(mode, _cs.CrossCursor)
        except AttributeError:
            _cur = {
                'move':   Qt.SizeAllCursor,    # type: ignore
                'eraser': Qt.ForbiddenCursor,  # type: ignore
                'edit':   Qt.PointingHandCursor,  # type: ignore
            }.get(mode, Qt.CrossCursor)  # type: ignore
        self.canvas_plot.setCursor(_cur)

    def _sketch_deactivate(self):
        for btn in self._sketch_btns.values():
            btn.setChecked(False)
        self._sketch_mode    = None
        self._sketch_pressed = False
        self._sketch_current = None
        # Clear Ctrl+pen polyline state if active
        if self._pen_poly_mode:
            self._pen_poly_mode = False
            self._pen_poly_pts  = ([], [])
            self._pen_poly_art  = None
        if self._level_snap_art is not None:
            try:
                self._level_snap_art.remove()
            except Exception:
                pass
            self._level_snap_art = None
            self.canvas_plot.draw_idle()
        self.canvas_plot.unsetCursor()

    def _sketch_set_color(self, hex_color):
        self._sketch_color = hex_color
        if self._sketch_color_btn:
            self._sketch_color_btn.setStyleSheet(
                f'background:{hex_color};border:2px solid #555;border-radius:2px;'
            )

    def _sketch_pick_color(self):
        col = QColorDialog.getColor(QColor(self._sketch_color))
        if col.isValid():
            self._sketch_set_color(col.name())

    def _sketch_clear(self):
        for obj in self._sketch_objects:
            try:
                obj.remove()
            except Exception:
                pass
        self._sketch_objects.clear()
        self._sketch_current  = None
        self._sketch_pressed  = False
        self._profile_markers = []
        self._clear_marker_map_points()
        self._refresh_profile_markers()
        if MATPLOTLIB_AVAILABLE:
            self.canvas_plot.draw_idle()

    def _sketch_serialise(self):
        """Return a list of plain-dict specs that can recreate all current sketch objects."""
        if not MATPLOTLIB_AVAILABLE or not self._sketch_objects:
            return []
        specs = []
        for obj in self._sketch_objects:
            try:
                if isinstance(obj, _MplRect):
                    specs.append({
                        't': 'rect', 'coord': 'axfrac',
                        'xy': tuple(obj.get_xy()),
                        'w': obj.get_width(),
                        'h': obj.get_height(),
                        'ec': obj.get_edgecolor(),
                        'lw': obj.get_linewidth(),
                        'ls': obj.get_linestyle(),
                    })
                elif isinstance(obj, _MplEllipse):
                    specs.append({
                        't': 'ellipse', 'coord': 'axfrac',
                        'center': tuple(obj.center),
                        'w': obj.width,
                        'h': obj.height,
                        'ec': obj.get_edgecolor(),
                        'lw': obj.get_linewidth(),
                        'ls': obj.get_linestyle(),
                    })
                elif hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None:
                    ap = obj.arrow_patch
                    specs.append({
                        't': 'arrow', 'coord': 'axfrac',
                        'head': (float(obj.xy[0]), float(obj.xy[1])),
                        'tail': (float(obj.get_position()[0]), float(obj.get_position()[1])),
                        'ec': ap.get_edgecolor(),
                        'lw': ap.get_linewidth(),
                        'ls': ap.get_linestyle(),
                    })
                elif hasattr(obj, 'get_text'):
                    specs.append({
                        't': 'text', 'coord': 'axfrac',
                        'x': float(obj.get_position()[0]),
                        'y': float(obj.get_position()[1]),
                        'text': obj.get_text(),
                        'color': _to_hex_color(obj.get_color()),
                        'fontsize': float(obj.get_fontsize()),
                        'fontweight': obj.get_fontweight(),
                        'fontstyle': obj.get_fontstyle(),
                        'fontfamily': (obj.get_fontfamily() or ['sans-serif'])[0],
                    })
                elif hasattr(obj, 'get_xdata'):
                    specs.append({
                        't': 'line2d', 'coord': 'axfrac',
                        'x': list(obj.get_xdata()),
                        'y': list(obj.get_ydata()),
                        'color': obj.get_color(),
                        'lw': obj.get_linewidth(),
                        'ls': obj.get_linestyle(),
                        'cap': obj.get_solid_capstyle(),
                        'join': obj.get_solid_joinstyle(),
                    })
            except Exception:
                pass
        return specs

    def _sketch_restore(self, specs):
        """Re-create sketch artists from serialised specs onto the current axes."""
        if not specs or not MATPLOTLIB_AVAILABLE:
            return
        ax = self.ax
        for s in specs:
            try:
                t = s['t']
                use_axfrac = s.get('coord') == 'axfrac'
                tf = ax.transAxes if use_axfrac else None
                if t == 'rect':
                    kw = dict(linewidth=s['lw'], linestyle=s['ls'],
                              edgecolor=s['ec'], facecolor='none', zorder=10)
                    if tf is not None:
                        kw['transform'] = tf
                    p = _MplRect(s['xy'], s['w'], s['h'], **kw)
                    ax.add_patch(p)
                    self._sketch_objects.append(p)
                elif t == 'ellipse':
                    kw = dict(linewidth=s['lw'], linestyle=s['ls'],
                              edgecolor=s['ec'], facecolor='none', zorder=10)
                    if tf is not None:
                        kw['transform'] = tf
                    p = _MplEllipse(s['center'], s['w'], s['h'], **kw)
                    ax.add_patch(p)
                    self._sketch_objects.append(p)
                elif t == 'arrow':
                    kw = dict(arrowprops=dict(arrowstyle='->', color=s['ec'],
                                              lw=s['lw'], linestyle=s['ls']),
                              zorder=10)
                    if use_axfrac:
                        kw['xycoords'] = 'axes fraction'
                        kw['textcoords'] = 'axes fraction'
                    ann = ax.annotate('', xy=s['head'], xytext=s['tail'], **kw)
                    self._sketch_objects.append(ann)
                elif t == 'text':
                    kw = dict(color=s['color'],
                              fontsize=s.get('fontsize', 9),
                              fontweight=s.get('fontweight', 'bold'),
                              fontstyle=s.get('fontstyle', 'normal'),
                              fontfamily=s.get('fontfamily', 'sans-serif'),
                              zorder=10,
                              bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                                        edgecolor=s['color'], alpha=0.85))
                    if tf is not None:
                        kw['transform'] = tf
                    ann = ax.text(s['x'], s['y'], s['text'], **kw)
                    self._sketch_objects.append(ann)
                elif t == 'line2d':
                    kw = dict(color=s['color'], linewidth=s['lw'], linestyle=s['ls'],
                              solid_capstyle=s.get('cap', 'round'),
                              solid_joinstyle=s.get('join', 'round'),
                              zorder=10)
                    if tf is not None:
                        kw['transform'] = tf
                    line, = ax.plot(s['x'], s['y'], **kw)
                    self._sketch_objects.append(line)
            except Exception:
                pass

    def _sketch_erase_at(self, event):
        """Remove the topmost sketch object or chainage cursor under the cursor."""
        # Check chainage cursors first (proximity in x)
        ax = event.inaxes
        if ax is not None and event.xdata is not None:
            all_p = [self.ax] + self._extra_axes
            ax_idx = all_p.index(ax) if ax in all_p else -1
            xlim = ax.get_xlim()
            tol = abs(xlim[1] - xlim[0]) * 0.015
            for i, cursor in enumerate(self._ch_cursors):
                if cursor['ax_idx'] == ax_idx and abs(cursor['chainage'] - event.xdata) < tol:
                    if i < len(self._ch_cursor_artists):
                        for _a in self._ch_cursor_artists[i]:
                            try: _a.remove()
                            except Exception: pass
                        self._ch_cursor_artists.pop(i)
                    erased_cursor = self._ch_cursors.pop(i)
                    self._remove_cursor_map_point(i)
                    # Close any open XS dialog that belongs to this cursor
                    for _dlg in list(self._xs_dialogs):
                        if _dlg.cursor is erased_cursor:
                            try: _dlg.close()
                            except Exception: pass
                            break
                    self.canvas_plot.draw_idle()
                    return

        # Sketch objects
        for obj in reversed(self._sketch_objects):
            try:
                hit, _ = obj.contains(event)
            except Exception:
                hit = False
            if hit:
                try:
                    obj.remove()
                except Exception:
                    pass
                self._sketch_objects.remove(obj)
                self.canvas_plot.draw_idle()
                break

    def _sketch_edit_object(self, obj):
        """Open a property-editor dialog for a sketch object and apply changes."""
        try:
            self._sketch_edit_object_impl(obj)
        except Exception as exc:
            QMessageBox.critical(self, 'Sketch Edit Error', str(exc))

    def _sketch_edit_object_impl(self, obj):
        is_text  = (hasattr(obj, 'get_text') and bool(obj.get_text())
                    and not (hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None))
        is_arrow = hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None
        is_line  = hasattr(obj, 'get_xdata')
        is_patch = isinstance(obj, (_MplRect, _MplEllipse))

        # --- read current colour ------------------------------------------
        if is_text:
            cur_color = _to_hex_color(obj.get_color())
        elif is_arrow:
            cur_color = _to_hex_color(obj.arrow_patch.get_edgecolor())
        elif is_line:
            cur_color = _to_hex_color(obj.get_color())
        elif is_patch:
            cur_color = _to_hex_color(obj.get_edgecolor())
        else:
            cur_color = '#000000'

        # --- read current lw / ls -----------------------------------------
        def _cur_lw():
            if is_arrow: return obj.arrow_patch.get_linewidth()
            if is_line:  return obj.get_linewidth()
            if is_patch: return obj.get_linewidth()
            return 2.0

        def _cur_ls():
            _norm = {'solid': '-', 'dashed': '--', 'dotted': ':', 'dashdot': '-.'}
            if is_arrow: raw = obj.arrow_patch.get_linestyle()
            elif is_line:  raw = obj.get_linestyle()
            elif is_patch: raw = obj.get_linestyle()
            else: return '-'
            return _norm.get(raw, raw)

        # === build dialog ==================================================
        dlg = QDialog(self)
        dlg.setWindowTitle('Edit Annotation')
        dlg.setMinimumWidth(260)
        try:
            dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        except AttributeError:
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint)  # type: ignore
        root = QVBoxLayout(dlg)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        _lbl_style = 'font-size:10px; color:#546E7A;'

        # ── Colour ────────────────────────────────────────────────────────
        color_val = [cur_color]
        cr = QHBoxLayout()
        _clbl = QLabel('Colour:'); _clbl.setStyleSheet(_lbl_style)
        cr.addWidget(_clbl)
        color_swatch = QPushButton()
        color_swatch.setFixedSize(50, 22)
        color_swatch.setStyleSheet(
            f'background:{cur_color};border:1px solid #888;border-radius:3px;'
        )
        def _pick():
            c = QColorDialog.getColor(QColor(color_val[0]), dlg)
            if c.isValid():
                color_val[0] = c.name()
                color_swatch.setStyleSheet(
                    f'background:{c.name()};border:1px solid #888;border-radius:3px;'
                )
        color_swatch.clicked.connect(_pick)
        cr.addWidget(color_swatch); cr.addStretch()
        root.addLayout(cr)

        # ── Line width + style (non-text objects) ──────────────────────────
        lw_spin  = None
        ls_combo = None
        if not is_text:
            lwr = QHBoxLayout()
            _wlbl = QLabel('Width:'); _wlbl.setStyleSheet(_lbl_style)
            lwr.addWidget(_wlbl)
            lw_spin = QDoubleSpinBox()
            lw_spin.setRange(0.5, 10.0); lw_spin.setSingleStep(0.5)
            lw_spin.setDecimals(1); lw_spin.setFixedWidth(65)
            lw_spin.setValue(_cur_lw())
            lwr.addWidget(lw_spin); lwr.addStretch()
            root.addLayout(lwr)

            lsr = QHBoxLayout()
            _slbl = QLabel('Style:'); _slbl.setStyleSheet(_lbl_style)
            lsr.addWidget(_slbl)
            ls_combo = QComboBox(); ls_combo.setFixedWidth(130)
            for _v, _n in [('-',  '─── Solid'), ('--', '-- Dashed'),
                           (':',  '··· Dotted'), ('-.', '-·- Dash-dot')]:
                ls_combo.addItem(_n, _v)
            _cur = _cur_ls()
            for _i in range(ls_combo.count()):
                if ls_combo.itemData(_i) == _cur:
                    ls_combo.setCurrentIndex(_i); break
            lsr.addWidget(ls_combo); lsr.addStretch()
            root.addLayout(lsr)

        # ── Text style (text objects only) ─────────────────────────────────
        font_combo = size_spin = bold_cb = italic_cb = None
        if is_text:
            # Font family
            fr = QHBoxLayout()
            _flbl = QLabel('Font:'); _flbl.setStyleSheet(_lbl_style)
            fr.addWidget(_flbl)
            font_combo = QComboBox(); font_combo.setFixedWidth(140)
            for _fn in ['sans-serif', 'serif', 'monospace',
                        'DejaVu Sans', 'Arial', 'Courier New']:
                font_combo.addItem(_fn)
            try:
                _ff = (obj.get_fontfamily() or ['sans-serif'])[0]
                _fi = font_combo.findText(_ff)
                if _fi >= 0: font_combo.setCurrentIndex(_fi)
            except Exception:
                pass
            fr.addWidget(font_combo); fr.addStretch()
            root.addLayout(fr)

            # Size
            sr = QHBoxLayout()
            _szlbl = QLabel('Size:'); _szlbl.setStyleSheet(_lbl_style)
            sr.addWidget(_szlbl)
            size_spin = QSpinBox()
            size_spin.setRange(6, 72); size_spin.setFixedWidth(60)
            size_spin.setValue(int(obj.get_fontsize()))
            sr.addWidget(size_spin); sr.addStretch()
            root.addLayout(sr)

            # Bold + Italic
            bir = QHBoxLayout()
            bold_cb   = QCheckBox('Bold')
            italic_cb = QCheckBox('Italic')
            bold_cb.setChecked(str(obj.get_fontweight()) in ('bold', '700', '800', '900'))
            italic_cb.setChecked(obj.get_fontstyle() == 'italic')
            bir.addWidget(bold_cb); bir.addWidget(italic_cb); bir.addStretch()
            root.addLayout(bir)

        # ── OK / Cancel ───────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(_HLINE); sep.setFrameShadow(_SUNKEN)
        root.addWidget(sep)
        btn_row = QHBoxLayout()
        btn_ok     = QPushButton('OK');     btn_ok.setFixedWidth(70)
        btn_cancel = QPushButton('Cancel'); btn_cancel.setFixedWidth(70)
        btn_row.addStretch(); btn_row.addWidget(btn_ok); btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)
        btn_cancel.clicked.connect(dlg.reject)

        def _apply():
            nc = color_val[0]
            # Apply colour
            if is_text:
                obj.set_color(nc)
                bp = obj.get_bbox_patch()
                if bp: bp.set_edgecolor(nc)
            elif is_arrow:
                obj.arrow_patch.set_edgecolor(nc)
                obj.arrow_patch.set_facecolor(nc)
            elif is_line:
                obj.set_color(nc)
            elif is_patch:
                obj.set_edgecolor(nc)
            # Apply width / style
            if lw_spin is not None:
                nlw = lw_spin.value()
                if is_arrow: obj.arrow_patch.set_linewidth(nlw)
                elif is_line: obj.set_linewidth(nlw)
                elif is_patch: obj.set_linewidth(nlw)
            if ls_combo is not None:
                nls = ls_combo.currentData()
                if is_arrow: obj.arrow_patch.set_linestyle(nls)
                elif is_line: obj.set_linestyle(nls)
                elif is_patch: obj.set_linestyle(nls)
            # Apply text style
            if is_text:
                if font_combo: obj.set_fontfamily(font_combo.currentText())
                if size_spin:  obj.set_fontsize(size_spin.value())
                if bold_cb:    obj.set_fontweight('bold' if bold_cb.isChecked() else 'normal')
                if italic_cb:  obj.set_fontstyle('italic' if italic_cb.isChecked() else 'normal')
                bp = obj.get_bbox_patch()
                if bp: bp.set_edgecolor(nc)
            self.canvas_plot.draw_idle()
            dlg.accept()

        btn_ok.clicked.connect(_apply)
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    def _open_xs_menu(self):
        """Show a dropdown listing all placed cursors; selecting one opens XSectionDialog."""
        if not self._ch_cursors:
            QMessageBox.information(
                self, 'Cross-Section',
                'No chainage cursors placed yet.\nUse the ⌇ cursor tool to place cursors first.')
            return
        menu = QMenu(self)
        for i, cursor in enumerate(self._ch_cursors):
            name = cursor.get('name', f'XS-{i+1:03d}')
            ch   = cursor['chainage']
            act  = menu.addAction(f'{name}  —  Ch: {ch:.1f} m')
            act.setData(i)
        chosen = menu.exec(QCursor.pos())
        if chosen is not None:
            self._open_xs_dialog(chosen.data())

    def _open_xs_dialog(self, cursor_idx):
        """Open (or re-raise) the cross-section dialog for the given cursor index."""
        if cursor_idx >= len(self._ch_cursors):
            return
        cursor = self._ch_cursors[cursor_idx]
        dlg = XSectionDialog(self, cursor)
        self._xs_dialogs.append(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._on_xs_dialog_closed(d))
        dlg.show()
        dlg.raise_()
        # Highlights this marker on the main chart (see
        # _refresh_chainage_cursors' active_ids) so it's obvious which one
        # a just-opened window is looking at.
        self._refresh_main_chart_xs_highlight()

    def _on_xs_dialog_closed(self, dlg):
        if dlg in self._xs_dialogs:
            self._xs_dialogs.remove(dlg)
        self._refresh_main_chart_xs_highlight()

    def _refresh_main_chart_xs_highlight(self):
        """Redraws just the chainage-cursor markers on the main chart so
        the highlighted one(s) (see _refresh_chainage_cursors' active_ids)
        stay in sync with whichever marker each open Cross-Section window
        is currently showing — called whenever that can have changed: a
        window opening, closing, or paging via its own US/DS buttons."""
        self._refresh_chainage_cursors()
        if MATPLOTLIB_AVAILABLE:
            self.canvas_plot.draw_idle()

    def _close_all_xs_dialogs(self):
        """Close every open cross-section window. Each XSectionDialog owns
        its own map markers (a perpendicular-line rubber band and a hover
        dot, both on this same canvas — see XSectionDialog._map_band /
        _xs_hover_band) which it only cleans up in its own closeEvent. If
        the profile/pipe chain those cross-sections were built from gets
        cleared or replaced while a dialog is still open, its markers were
        never told to go away and were left behind as a 'ghost' on the map
        — this is what actually fixes that, by closing the dialogs (which
        triggers their own cleanup) whenever the base profile goes away:
        Clear on either tab, and switching tabs."""
        for dlg in list(self._xs_dialogs):
            try:
                dlg.close()
            except Exception:
                pass
        self._xs_dialogs = []

    def _make_perp_line_geom(self, chainage, left_m, right_m, direction=None, align_geom=None):
        """Return a QgsGeometry line perpendicular to the profile at the given chainage.

        'Left' is the CCW direction from the tangent (left when walking along profile).
        The returned line runs from the left end to the right end.

        direction, if given, is a unit (tx, ty) tangent overriding the
        normal finite-difference estimate entirely — used for a MITER
        station at a sharp Modify Terrain corner (see
        _terrain_vertex_miter_bisector / _terrain_xs_sample_ground),
        where the sampling line needs to follow the two legs' bisector
        rather than whatever this function would otherwise estimate from
        a window straddling the corner.

        align_geom, if given, is used INSTEAD of self.profile_geom —
        every other caller leaves this at its default (None ->
        self.profile_geom, unchanged behaviour); Embankment Surface's
        own End Adjustment closure (see _embank_build_channel_corridor_
        data) passes its own EXTENDED alignment geometry here so a
        chainage past the real alignment's own start/end can still be
        sampled, rather than being clamped back onto it.
        """
        geom = align_geom if align_geom is not None else self.profile_geom
        if geom is None:
            return None
        total  = geom.length()
        # Clamp first — a caller can pass a chainage slightly (or wildly)
        # outside [0, total], e.g. a chainage list left over from before
        # the alignment was redrawn/shortened. QgsGeometry.interpolate()
        # returns a NULL geometry beyond the line's own length rather than
        # clamping itself, and .asPoint() on that raises ValueError — this
        # used to only get clamped for ch1/ch2 below, not for `chainage`
        # itself (used further down for the perpendicular's centre point).
        chainage = max(0.0, min(chainage, total))
        if direction is not None:
            tx, ty = direction
        else:
            delta  = min(0.5, chainage, total - chainage)
            delta  = max(delta, 0.001)
            ch1    = max(0.0, chainage - delta)
            ch2    = min(total, chainage + delta)
            pt1    = geom.interpolate(ch1).asPoint()
            pt2    = geom.interpolate(ch2).asPoint()
            dx, dy = pt2.x() - pt1.x(), pt2.y() - pt1.y()
            dist   = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-10:
                return None
            tx, ty = dx / dist, dy / dist   # tangent unit vector
        px, py = -ty, tx                # perpendicular CCW = "left"
        ctr    = geom.interpolate(chainage).asPoint()
        cx, cy = ctr.x(), ctr.y()
        l_pt   = QgsPointXY(cx + px * left_m,  cy + py * left_m)
        r_pt   = QgsPointXY(cx - px * right_m, cy - py * right_m)
        return QgsGeometry.fromPolylineXY([l_pt, r_pt])

    def _refresh_chainage_cursors(self):
        """Rebuild all permanent chainage cursor artists from self._ch_cursors."""
        import matplotlib.transforms as _mt
        # Remove stale artists (axes may have been cleared)
        for artist_list in self._ch_cursor_artists:
            for _a in artist_list:
                try: _a.remove()
                except Exception: pass
        self._ch_cursor_artists = []

        if not self._ch_cursors or not MATPLOTLIB_AVAILABLE:
            return
        all_p = [self.ax] + self._extra_axes
        xs = (np.array(self._profile_chainages, dtype=float)
              if self._profile_chainages else None)

        _bbox_kw = dict(boxstyle='round,pad=0.2', facecolor='white',
                        edgecolor='#212121', alpha=0.85, linewidth=0.5)
        _bbox_dy = dict(boxstyle='round,pad=0.2', facecolor='white',
                        edgecolor='#D32F2F', alpha=0.85, linewidth=0.5)

        # Whichever marker(s) an open Cross-Section window is CURRENTLY
        # showing — identity-matched (id()), not by value, since stepping
        # via the US/DS buttons (_step_to_marker) re-points a dialog's
        # own .cursor at a DIFFERENT dict from this same list rather than
        # mutating it in place. Drawn with a highlighted style below so
        # it's obvious at a glance which marker a given popped-out window
        # is looking at, especially useful once several are open at once.
        active_ids = {id(dlg.cursor) for dlg in self._xs_dialogs if dlg.cursor is not None}

        for cursor in self._ch_cursors:
            is_active = id(cursor) in active_ids
            ch      = cursor['chainage']
            ax_idx  = cursor['ax_idx']
            if ax_idx >= len(all_p):
                ax_idx = 0
            ax_c = all_p[ax_idx]

            # Cut/fill ΔY at this chainage
            cf_val = None
            if xs is not None and len(xs) > 1:
                cfg_j = self._win_cfgs[ax_idx] if ax_idx < len(self._win_cfgs) else None
                if (cfg_j is not None
                        and cfg_j['cutfill_cb'].isChecked()
                        and cfg_j['cf_y1'].count() > 0
                        and cfg_j['cf_y2'].count() > 0):
                    y1k = cfg_j['cf_y1'].currentText()
                    y2k = cfg_j['cf_y2'].currentText()
                    pd  = self._profile_data_store
                    if y1k in pd and y2k in pd and y1k != y2k:
                        y1a = np.array([v if v is not None else np.nan
                                        for v in pd[y1k]], dtype=float)
                        y2a = np.array([v if v is not None else np.nan
                                        for v in pd[y2k]], dtype=float)
                        _v = float(np.interp(ch, xs, y2a - y1a))
                        if np.isfinite(_v):
                            cf_val = _v

            blend = _mt.blended_transform_factory(ax_c.transData, ax_c.transAxes)

            # Active (currently shown in an open Cross-Section window) —
            # a brighter orange, thicker line, sitting above everything
            # else — vs. the normal subdued grey dash-dot for every other
            # placed marker.
            if is_active:
                vline = ax_c.axvline(x=ch, color='#FF6F00', linewidth=1.8,
                                     linestyle='-.', zorder=12, alpha=0.95)
            else:
                vline = ax_c.axvline(x=ch, color='#212121', linewidth=0.5,
                                     linestyle='-.', zorder=8, alpha=0.85)

            # Top-left: X = chainage [+ ΔY on next line when cut/fill active]
            # Both in one text box so they sit side-by-side along the cursor line
            # and never crowd each other or the opposite side.
            if cf_val is not None:
                top_text  = f'X = {ch:.1f} m\nΔY: {cf_val:+.3f} m'
                top_bbox  = dict(boxstyle='round,pad=0.2', facecolor='white',
                                 edgecolor='#D32F2F', alpha=0.85, linewidth=0.5)
                top_color = '#212121'
            else:
                top_text  = f'X = {ch:.1f} m'
                top_bbox  = _bbox_kw
                top_color = '#212121'
            if is_active:
                top_bbox = dict(top_bbox, facecolor='#FFE0B2', edgecolor='#FF6F00', linewidth=1.2)

            lbl_top = ax_c.text(
                ch, 0.97, top_text,
                transform=blend, rotation=90, rotation_mode='anchor',
                fontsize=7, color=top_color, fontweight=('bold' if is_active else 'normal'),
                ha='right', va='top', zorder=13,
                bbox=top_bbox)

            # Bottom-right: XS name
            name_bbox = (dict(_bbox_kw, facecolor='#FFE0B2', edgecolor='#FF6F00', linewidth=1.2)
                         if is_active else _bbox_kw)
            lbl_name = ax_c.text(
                ch, 0.03, cursor.get('name', ''),
                transform=blend, rotation=90, rotation_mode='anchor',
                fontsize=7, color='#212121', fontweight=('bold' if is_active else 'normal'),
                ha='left', va='bottom', zorder=13,
                bbox=name_bbox)

            self._ch_cursor_artists.append([vline, lbl_top, lbl_name])

    def _add_cursor_map_point(self, chainage, name=''):
        """Place a short dashed perpendicular line and name at its right end."""
        if self.profile_geom is None:
            self._ch_cursor_map_bands.append(None)
            self._ch_cursor_annotations.append(None)
            return

        # Build perpendicular geometry — reuse right endpoint for the label
        geom = self._make_perp_line_geom(chainage, 10.0, 10.0)
        r_pt = None
        try:
            if geom is None:
                raise ValueError('no perp geom')
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(21, 101, 192, 230))
            band.setWidth(2)
            try:
                band.setLineStyle(Qt.PenStyle.DashLine)
            except AttributeError:
                band.setLineStyle(Qt.DashLine)  # type: ignore[attr-defined]
            band.setToGeometry(geom, None)
            self._ch_cursor_map_bands.append(band)
            pts = geom.asPolyline()          # [l_pt, r_pt]
            r_pt = pts[-1] if pts else None  # right end of the perp line
        except Exception:
            self._ch_cursor_map_bands.append(None)

        # Text label at the right end of the perpendicular line
        ann = None
        if name and r_pt is not None:
            try:
                from qgis.core import (QgsAnnotationPointTextItem,
                                        QgsTextFormat, QgsTextBufferSettings)
                from qgis.PyQt.QtGui import QFont, QColor as _QColor
                item = QgsAnnotationPointTextItem(name, r_pt)
                fmt = QgsTextFormat()
                font = QFont('Sans Serif', 7)
                font.setBold(True)
                fmt.setFont(font)
                fmt.setColor(_QColor('#1565C0'))
                buf = QgsTextBufferSettings()
                buf.setEnabled(True)
                buf.setSize(1.5)
                buf.setColor(_QColor(255, 255, 255))
                fmt.setBuffer(buf)
                item.setFormat(fmt)
                ann_layer = QgsProject.instance().mainAnnotationLayer()
                item_id = ann_layer.addItem(item)
                ann = (ann_layer, item_id)
            except Exception:
                ann = None
        self._ch_cursor_annotations.append(ann)

    @staticmethod
    def _remove_ann(ann):
        if ann is None:
            return
        try:
            if isinstance(ann, tuple):        # (QgsAnnotationLayer, item_id)
                layer, item_id = ann
                layer.removeItem(item_id)
            else:                              # legacy QgsTextAnnotation
                QgsProject.instance().annotationManager().removeAnnotation(ann)
        except Exception:
            pass

    def _remove_cursor_map_point(self, idx):
        """Remove the map marker and annotation at the given cursor index."""
        if idx < len(self._ch_cursor_map_bands):
            band = self._ch_cursor_map_bands.pop(idx)
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
        if idx < len(self._ch_cursor_annotations):
            self._remove_ann(self._ch_cursor_annotations.pop(idx))

    def _clear_cursor_map_points(self):
        """Remove all permanent cursor map markers and annotations."""
        for band in self._ch_cursor_map_bands:
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
        self._ch_cursor_map_bands = []
        for ann in self._ch_cursor_annotations:
            self._remove_ann(ann)
        self._ch_cursor_annotations = []

    # ------------------------------------------------------------------ profile markers
    # Point markers dropped by clicking the 'marker' sketch tool on the
    # profile chart — unlike a chainage cursor (a reference LINE across
    # the chart), these are a single POINT on both the chart and the map
    # canvas (interpolated along self.profile_geom at the clicked
    # chainage), persisted independently of the sketch Clear button, and
    # exported as their own 'marker' shapefile when the plot is saved
    # (see _write_markers_shp).

    def _add_marker_map_point(self, pt):
        """Place a small X marker on the map canvas at pt (QgsPointXY)."""
        try:
            vm = QgsVertexMarker(self.canvas)
            try:
                vm.setIconType(QgsVertexMarker.IconType.ICON_X)
            except AttributeError:
                vm.setIconType(QgsVertexMarker.ICON_X)  # type: ignore[attr-defined]
            vm.setColor(QColor(211, 47, 47))
            vm.setIconSize(12)
            vm.setPenWidth(2)
            vm.setCenter(QgsPointXY(pt))
            self._profile_marker_bands.append(vm)
        except Exception:
            self._profile_marker_bands.append(None)

    def _remove_marker_map_point(self, idx):
        """Remove the map marker at the given profile-marker index."""
        if idx < len(self._profile_marker_bands):
            band = self._profile_marker_bands.pop(idx)
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass

    def _clear_marker_map_points(self):
        """Remove all placed profile-marker map markers."""
        for band in self._profile_marker_bands:
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
        self._profile_marker_bands = []

    def _refresh_profile_markers(self):
        """Rebuild all placed profile-marker chart artists from
        self._profile_markers — called after every _plot() (the axes may
        have just been cleared) and after placing a new marker."""
        for art in self._profile_marker_artists:
            try: art.remove()
            except Exception: pass
        self._profile_marker_artists = []

        if not self._profile_markers or not MATPLOTLIB_AVAILABLE:
            return
        all_p = [self.ax] + self._extra_axes
        for m in self._profile_markers:
            ax_idx = m.get('ax_idx', 0)
            ax_m = all_p[ax_idx] if ax_idx < len(all_p) else self.ax
            art, = ax_m.plot(
                [m['chainage']], [m['y']],
                marker='o', markersize=8, linestyle='none',
                markerfacecolor='#D32F2F', markeredgecolor='white',
                markeredgewidth=1.2, zorder=12, clip_on=False)
            self._profile_marker_artists.append(art)

    def _sketch_on_press(self, event):
        self._sketch_pressed = True
        ax = event.inaxes
        fx, fy = event.xdata, event.ydata
        self._sketch_press_data = (fx, fy)

        if self._sketch_mode in ('pen', 'line'):
            ctrl = self._ctrl_tracker.ctrl_held
            if not ctrl:
                try:
                    ctrl = bool(QApplication.queryKeyboardModifiers() & _CTRL_MOD)
                except Exception:
                    ctrl = False

            if self._pen_poly_mode:
                # Already building a click-polyline (Pen or Line, same
                # mechanism) — a double-click OR a plain click without Ctrl
                # both finish it now; Ctrl+click keeps extending it.
                if event.dblclick or not ctrl:
                    self._pen_poly_mode  = False
                    self._pen_poly_pts   = ([], [])
                    self._pen_poly_art   = None
                    self._sketch_pressed = False
                    self.canvas_plot.draw_idle()
                    return
                self._pen_poly_pts[0].append(fx)
                self._pen_poly_pts[1].append(fy)
                self._pen_poly_art.set_data(*self._pen_poly_pts)
                self._sketch_pressed = False
                self.canvas_plot.draw_idle()
                return

            if ctrl:
                # Start a new click-polyline — same for Pen and Line.
                self._sketch_pressed = False
                self._pen_poly_mode  = True
                self._pen_poly_pts   = ([fx], [fy])
                line, = ax.plot(
                    [fx, fx], [fy, fy],
                    color=self._sketch_color, linewidth=self._sketch_lw,
                    linestyle=self._sketch_ls,
                    solid_capstyle='round', solid_joinstyle='round', zorder=10
                )
                self._pen_poly_art = line
                self._sketch_objects.append(line)
                self.canvas_plot.draw_idle()
                return

            # No Ctrl, not mid-polyline — normal click-and-drag behaviour,
            # tool-specific (freehand for Pen, one straight segment for Line).
            if self._sketch_mode == 'pen':
                self._sketch_pen_pts = ([fx], [fy])
                line, = ax.plot(
                    self._sketch_pen_pts[0], self._sketch_pen_pts[1],
                    color=self._sketch_color, linewidth=self._sketch_lw,
                    linestyle=self._sketch_ls,
                    solid_capstyle='round', solid_joinstyle='round', zorder=10
                )
            else:
                line, = ax.plot(
                    [fx, fx], [fy, fy],
                    color=self._sketch_color, linewidth=self._sketch_lw,
                    linestyle=self._sketch_ls,
                    solid_capstyle='round', zorder=10
                )
            self._sketch_current = (line, ax)
            self._sketch_objects.append(line)

        elif self._sketch_mode == 'arrow':
            ann = ax.annotate(
                '', xy=(fx, fy), xytext=(fx, fy),
                xycoords='data', textcoords='data',
                arrowprops=dict(arrowstyle='->', color=self._sketch_color,
                                lw=self._sketch_lw, linestyle=self._sketch_ls),
                zorder=10
            )
            self._sketch_current = (ann, ax)
            self._sketch_objects.append(ann)

        elif self._sketch_mode == 'rect':
            rect = _MplRect(
                (fx, fy), 0, 0,
                linewidth=self._sketch_lw, linestyle=self._sketch_ls,
                edgecolor=self._sketch_color,
                facecolor='none', zorder=10
            )
            ax.add_patch(rect)
            self._sketch_current = (rect, ax, fx, fy)
            self._sketch_objects.append(rect)

        elif self._sketch_mode == 'text':
            self._sketch_pressed = False
            text, ok = QInputDialog.getText(
                self.canvas_plot, 'Add Annotation', 'Text:')
            if ok and text.strip():
                ann = ax.text(
                    fx, fy, text.strip(),
                    color=self._sketch_color, fontsize=9, fontweight='bold',
                    zorder=10,
                    bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                              edgecolor=self._sketch_color, alpha=0.85)
                )
                self._sketch_objects.append(ann)
                self.canvas_plot.draw_idle()
            return

        elif self._sketch_mode == 'level':
            self._sketch_pressed = False
            # Hide snap indicator while placing (keep the persistent artist alive)
            if self._level_snap_art is not None:
                self._level_snap_art.set_visible(False)
            snapped_x, snapped_y, snap_col, _ = _snap_level(ax, event.xdata, event.ydata)
            col = snap_col or self._sketch_color
            # Custom path: tip at (0,0) so the point touches the data coordinate
            lvl_tri, = ax.plot(
                [snapped_x], [snapped_y],
                marker=_get_tri_tip_path(), markersize=14, linestyle='none',
                markerfacecolor=col, markeredgecolor=col,
                markeredgewidth=1.0, zorder=11)
            lvl_ann = ax.annotate(
                f'{snapped_y:.3f}',
                xy=(snapped_x, snapped_y), xycoords='data',
                xytext=(7, 2), textcoords='offset points',
                fontsize=7, color=col, va='bottom', ha='left',
                zorder=11, annotation_clip=False,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          edgecolor='none', alpha=0.85))
            self._sketch_objects.append(lvl_tri)
            self._sketch_objects.append(lvl_ann)
            self.canvas_plot.draw_idle()
            return

        elif self._sketch_mode == 'circle':
            if not MATPLOTLIB_AVAILABLE:
                return
            ellipse = _MplEllipse(
                (fx, fy), 0, 0,
                linewidth=self._sketch_lw, linestyle=self._sketch_ls,
                edgecolor=self._sketch_color,
                facecolor='none', zorder=10
            )
            ax.add_patch(ellipse)
            self._sketch_current = (ellipse, ax, fx, fy)
            self._sketch_objects.append(ellipse)

        elif self._sketch_mode == 'cursor':
            self._sketch_pressed = False
            all_p = [self.ax] + self._extra_axes
            ax_idx = all_p.index(ax) if ax in all_p else 0
            name = f'XS-{len(self._ch_cursors) + 1:03d}'
            self._ch_cursors.append({'chainage': event.xdata, 'ax_idx': ax_idx, 'name': name})
            self._add_cursor_map_point(event.xdata, name)
            self._refresh_chainage_cursors()
            self.canvas_plot.draw_idle()
            return

        elif self._sketch_mode == 'marker':
            self._sketch_pressed = False
            if self.profile_geom is None:
                return
            all_p = [self.ax] + self._extra_axes
            ax_idx = all_p.index(ax) if ax in all_p else 0
            name = f'M-{len(self._profile_markers) + 1:03d}'
            pt = self.profile_geom.interpolate(fx).asPoint()
            # Snap the dot onto whichever plotted line on this axis is
            # nearest the click, rather than using the raw click height —
            # otherwise it floats at wherever the mouse happened to land.
            snap_y = fy
            if self._profile_chainages:
                best = None
                for col in self._plot_cols_per_ax.get(ax_idx, []):
                    vals = self._profile_data_store.get(col)
                    if not vals:
                        continue
                    clean = [v if v is not None else float('nan') for v in vals]
                    yv = float(np.interp(fx, self._profile_chainages, clean))
                    if np.isfinite(yv) and (best is None or abs(yv - fy) < abs(best - fy)):
                        best = yv
                if best is not None:
                    snap_y = best
            self._profile_markers.append({
                'chainage': fx, 'y': snap_y, 'ax_idx': ax_idx,
                'point': pt, 'name': name,
            })
            self._add_marker_map_point(pt)
            self._refresh_profile_markers()
            self.canvas_plot.draw_idle()
            return

        elif self._sketch_mode == 'eraser':
            self._sketch_erase_at(event)
            self._sketch_pressed = True  # keep pressed so drag-erase works
            return

        elif self._sketch_mode == 'move':
            self._sketch_drag_info = None
            for obj in reversed(self._sketch_objects):
                try:
                    hit, _ = obj.contains(event)
                except Exception:
                    hit = False
                if not hit:
                    continue
                # Build drag info depending on object type
                if isinstance(obj, _MplEllipse):
                    cx, cy = obj.center
                    self._sketch_drag_info = {
                        'obj': obj, 'type': 'ellipse',
                        'ox': cx - fx, 'oy': cy - fy,
                    }
                elif isinstance(obj, _MplRect):
                    bx, by = obj.get_xy()
                    self._sketch_drag_info = {
                        'obj': obj, 'type': 'rect',
                        'ox': bx - fx, 'oy': by - fy,
                    }
                elif hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None:
                    hx, hy = float(obj.xy[0]), float(obj.xy[1])
                    tx, ty = obj.get_position()
                    self._sketch_drag_info = {
                        'obj': obj, 'type': 'arrow',
                        'hox': hx - fx, 'hoy': hy - fy,
                        'tox': tx - fx, 'toy': ty - fy,
                    }
                elif hasattr(obj, 'get_text') and obj.get_text():
                    px, py = obj.get_position()
                    self._sketch_drag_info = {
                        'obj': obj, 'type': 'text',
                        'ox': px - fx, 'oy': py - fy,
                    }
                elif hasattr(obj, 'get_xdata'):
                    self._sketch_drag_info = {
                        'obj': obj, 'type': 'line2d',
                        'x0': list(obj.get_xdata()),
                        'y0': list(obj.get_ydata()),
                        'px': fx, 'py': fy,
                    }
                if self._sketch_drag_info:
                    break
            self._sketch_pressed = self._sketch_drag_info is not None
            return

        elif self._sketch_mode == 'edit':
            self._sketch_pressed = False
            _hit_obj = None
            for obj in reversed(self._sketch_objects):
                hit = False
                try:
                    hit, _ = obj.contains(event)
                except Exception:
                    pass
                if not hit:
                    # Fallback: display-coord bbox check (reliable for Text)
                    try:
                        renderer = self.figure.canvas.renderer
                        if hasattr(obj, 'get_window_extent'):
                            bb = obj.get_window_extent(renderer)
                            hit = bb.contains(event.x, event.y)
                    except Exception:
                        pass
                if hit:
                    _hit_obj = obj
                    break
            if _hit_obj is not None:
                # Defer past matplotlib's callback so Qt can handle the dialog normally
                QTimer.singleShot(0, lambda o=_hit_obj: self._sketch_edit_object(o))
            return

        self.canvas_plot.draw_idle()

    def _sketch_on_motion(self, event):
        _no_current_ok = self._sketch_mode in ('eraser', 'move', 'edit')
        if not self._sketch_current and not _no_current_ok:
            return
        ax = event.inaxes
        if ax is None or event.xdata is None or event.ydata is None:
            return
        fx, fy = event.xdata, event.ydata

        if self._sketch_mode == 'pen':
            line, _ax = self._sketch_current
            self._sketch_pen_pts[0].append(fx)
            self._sketch_pen_pts[1].append(fy)
            line.set_data(self._sketch_pen_pts[0], self._sketch_pen_pts[1])

        elif self._sketch_mode == 'line':
            line, _ax = self._sketch_current
            x0, y0 = self._sketch_press_data  # already ax-frac
            line.set_data([x0, fx], [y0, fy])

        elif self._sketch_mode == 'arrow':
            ann, _ax = self._sketch_current
            ann.xy = (fx, fy)

        elif self._sketch_mode == 'rect':
            rect, _ax, x0, y0 = self._sketch_current  # x0, y0 already ax-frac
            rect.set_xy((min(x0, fx), min(y0, fy)))
            rect.set_width(abs(fx - x0))
            rect.set_height(abs(fy - y0))

        elif self._sketch_mode == 'circle':
            ellipse, _ax, x0, y0 = self._sketch_current  # x0, y0 already ax-frac
            dx = abs(fx - x0)
            dy = abs(fy - y0)
            if event.key == 'shift':
                dx = dy = max(dx, dy)
            ellipse.set_width(2 * dx)
            ellipse.set_height(2 * dy)

        elif self._sketch_mode == 'eraser':
            self._sketch_erase_at(event)
            return

        elif self._sketch_mode == 'move':
            d = self._sketch_drag_info
            if d is None:
                return
            obj = d['obj']
            if d['type'] == 'ellipse':
                obj.set_center((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'rect':
                obj.set_xy((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'arrow':
                obj.xy = (fx + d['hox'], fy + d['hoy'])
                obj.set_position((fx + d['tox'], fy + d['toy']))
            elif d['type'] == 'text':
                obj.set_position((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'line2d':
                ddx = fx - d['px']
                ddy = fy - d['py']
                obj.set_data([v + ddx for v in d['x0']],
                             [v + ddy for v in d['y0']])

        self.canvas_plot.draw_idle()

    # ------------------------------------------------------------------ vector rows

    def _add_vector_row(self, rows_list=None, layout=None,
                         on_data_change=None, on_style_change=None,
                         on_field_changed=None, refresh_previews=None):
        """See _add_raster_row's docstring — same reasoning: defaults
        reproduce the original Profile-tab behaviour exactly; the 1D
        Profile tab's ground-overlay rows pass their own callbacks."""
        rows_list        = self._vector_rows if rows_list is None else rows_list
        layout           = self._vector_layout if layout is None else layout
        on_data_change   = self._trigger_update if on_data_change is None else on_data_change
        on_style_change  = self._refresh_plot if on_style_change is None else on_style_change
        on_field_changed = self._on_zfield_changed if on_field_changed is None else on_field_changed
        refresh_previews = self._refresh_col_previews if refresh_previews is None else refresh_previews

        vec = {'z_fields': []}
        frame = QFrame()
        frame.setStyleSheet(
            'QFrame{border:1px solid #BDBDBD;border-radius:3px;margin-top:2px;}'
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4); fl.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel('Layer:'))
        lc = QgsMapLayerComboBox()
        lc.setFilters(_VECTOR_FILTER)
        lc.setAllowEmptyLayer(True)
        lc.setCurrentIndex(0)
        lc.layerChanged.connect(lambda _: (refresh_previews(), on_data_change()))
        lc.wheelEvent = lambda e: e.ignore()
        rm = _rm_btn('Remove vector layer')
        rm.clicked.connect(lambda: self._remove_vector_row(vec, rows_list, on_data_change))
        hdr.addWidget(lc, 1); hdr.addWidget(rm)
        fl.addLayout(hdr)

        zf_widget = QWidget()
        zf_layout = QVBoxLayout(zf_widget)
        zf_layout.setContentsMargins(0, 0, 0, 0); zf_layout.setSpacing(2)
        fl.addWidget(zf_widget)

        add_z = QPushButton('+ Add Z-field')
        add_z.setStyleSheet('color:#43A047;font-size:10px;border:none;')
        add_z.clicked.connect(
            lambda: self._add_zfield_row(vec, on_style_change, on_field_changed,
                                          refresh_previews, on_data_change)
        )
        zf_layout.addWidget(add_z)

        vec.update({'widget': frame, 'layer_combo': lc, 'zf_layout': zf_layout})
        layout.insertWidget(layout.count() - 1, frame)
        rows_list.append(vec)
        self._add_zfield_row(vec, on_style_change, on_field_changed, refresh_previews, on_data_change)

    def _remove_vector_row(self, vec, rows_list=None, on_change=None):
        rows_list = self._vector_rows if rows_list is None else rows_list
        on_change = self._trigger_update if on_change is None else on_change
        rows_list.remove(vec)
        vec['widget'].setParent(None); vec['widget'].deleteLater()
        on_change()

    def _add_zfield_row(self, vec, on_style_change=None, on_field_changed=None,
                         refresh_previews=None, on_data_change=None):
        on_style_change  = self._refresh_plot if on_style_change is None else on_style_change
        on_field_changed = self._on_zfield_changed if on_field_changed is None else on_field_changed
        refresh_previews = self._refresh_col_previews if refresh_previews is None else refresh_previews
        on_data_change   = self._trigger_update if on_data_change is None else on_data_change

        _auto_ls = ['-', '--', ':', '-.']
        _pos  = len(vec['z_fields'])
        hex_c = vec['z_fields'][0]['color'].name() if _pos > 0 else self._next_color()
        zf = {'color': QColor(hex_c), '_col': None}
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(3)
        tog = QCheckBox(); tog.setChecked(True)
        tog.stateChanged.connect(on_style_change)
        badge = QLabel()
        badge.setPixmap(_vector_type_pix(14))
        badge.setFixedSize(16, 16)
        badge.setToolTip('Vector layer')
        fc = QgsFieldComboBox()
        try: fc.setFilters(_NUMERIC_FILTER)
        except Exception: pass
        fc.setLayer(vec['layer_combo'].currentLayer())
        vec['layer_combo'].layerChanged.connect(fc.setLayer)
        fc.fieldChanged.connect(lambda _: on_field_changed())
        fc.wheelEvent = lambda e: e.ignore()
        ls_combo = QComboBox()
        ls_combo.setFixedWidth(88)
        for code, label in _LINESTYLES:
            ls_combo.addItem(label, code)
        _default_ls = _auto_ls[_pos] if _pos < len(_auto_ls) else '-'
        _ls_idx = next((i for i, (c, _) in enumerate(_LINESTYLES) if c == _default_ls), 0)
        ls_combo.setCurrentIndex(_ls_idx)
        ls_combo.currentIndexChanged.connect(on_style_change)
        ls_combo.wheelEvent = lambda e: e.ignore()
        c_btn = _color_btn(hex_c)
        c_btn.clicked.connect(lambda: self._pick_zfield_color(vec, zf, on_style_change))

        _lw = QDoubleSpinBox()
        _lw.setRange(0.5, 5.0); _lw.setValue(1.5); _lw.setSingleStep(0.5)
        _lw.setDecimals(1); _lw.setFixedWidth(48)
        _lw.setToolTip('Line width')
        _lw.valueChanged.connect(on_style_change)

        _al = QSpinBox()
        _al.setRange(10, 100); _al.setValue(100); _al.setSuffix('%')
        _al.setFixedWidth(52)
        _al.setToolTip('Opacity')
        _al.valueChanged.connect(on_style_change)

        r_btn = QPushButton('−'); r_btn.setFixedSize(22, 22)
        r_btn.setStyleSheet('color:#E53935;font-weight:bold;font-size:16px;')
        r_btn.clicked.connect(lambda: self._remove_zfield_row(vec, zf, on_data_change, refresh_previews))
        h.addWidget(tog); h.addWidget(badge)
        h.addWidget(QLabel('Z:')); h.addWidget(fc, 1)
        h.addWidget(ls_combo); h.addWidget(_lw); h.addWidget(_al)
        h.addWidget(c_btn); h.addWidget(r_btn)
        zf.update({'widget': w, 'toggle': tog, 'combo': fc,
                   'ls_combo': ls_combo, 'color_btn': c_btn,
                   'lw_spin': _lw, 'al_spin': _al})
        layout = vec['zf_layout']
        layout.insertWidget(layout.count() - 1, w)
        vec['z_fields'].append(zf)
        refresh_previews()

    def _on_zfield_changed(self):
        self._refresh_col_previews()
        if self._active_tab == 0 and self.profile_geom is not None:
            self._run()
        else:
            self._trigger_update()

    def _refresh_col_previews(self):
        """Update Profile Window dropdowns from current field config without running extraction."""
        raster_entries, vector_entries, _ = self._collect_entries()
        col_names = [col for _, col in raster_entries] + [col for _, _, col in vector_entries]
        if col_names:
            self._update_win_col_combos(col_names)

    def _remove_zfield_row(self, vec, zf, on_change=None, refresh_previews=None):
        on_change        = self._trigger_update if on_change is None else on_change
        refresh_previews = self._refresh_col_previews if refresh_previews is None else refresh_previews
        if len(vec['z_fields']) <= 1: return
        vec['z_fields'].remove(zf)
        zf['widget'].setParent(None); zf['widget'].deleteLater()
        refresh_previews()
        on_change()

    def _pick_zfield_color(self, vec, zf, on_change=None):
        on_change = self._refresh_plot if on_change is None else on_change
        c = QColorDialog.getColor(zf['color'], self)
        if c.isValid():
            zf['color'] = c
            zf['color_btn'].setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;')
            on_change()

    # ------------------------------------------------------------------ placeholder tabs
    #
    # Reserves a tab slot with a short description of the planned workflow
    # so it's visible/positioned correctly ahead of the actual tool being
    # built — same idea as the old 'Add Network' stub.

    def _build_placeholder_tab(self, title, detail=''):
        tab = QWidget()
        l = QVBoxLayout(tab)
        l.addStretch()
        lbl = QLabel(f'{title} — coming soon')
        lbl.setStyleSheet('color:gray;font-style:italic;font-size:12px;font-weight:bold;')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter
                         if hasattr(Qt, 'AlignmentFlag') else Qt.AlignCenter)
        l.addWidget(lbl)
        if detail:
            dlbl = QLabel(detail)
            dlbl.setWordWrap(True)
            dlbl.setStyleSheet('color:gray;font-size:10px;padding:0 24px;')
            dlbl.setAlignment(Qt.AlignmentFlag.AlignCenter
                              if hasattr(Qt, 'AlignmentFlag') else Qt.AlignCenter)
            l.addWidget(dlbl)
        l.addStretch()
        return tab

    # ------------------------------------------------------------------ Shapes and Polygon Surface
    #
    # First-pass build, per explicit request ('give the first look, we
    # will try, keep improving later'): draw ONE regular shape (circle,
    # ellipse, rectangle, square) or a freehand closed polygon (own map
    # tool — ShapeDrawTool, in profile_line_tool.py), give it a flat
    # target elevation (relative to ground at its own centre, or an
    # absolute value) and an edge slope (H:V) that runs out to meet real
    # ground — 0 = vertical edge — then Create TIFF builds a standalone
    # surface from it via the SAME constrained-TIN engine Embankment/
    # Channel Surface use (QgsTinInterpolator/QgsGridFileWriter), just
    # with the shape's own outline as ONE ring instead of a station-by-
    # station corridor. The edge offset direction is approximated as
    # straight out from the shape's own centroid through each vertex
    # (correct for a circle/ellipse/centred rectangle; a reasonable but
    # not perfectly perpendicular approximation for an irregular drawn
    # polygon) — a real per-edge offset is a good candidate for a later
    # pass.
    def _build_shapes_tab(self):
        tab = QWidget()
        outer_l = QVBoxLayout(tab)
        outer_l.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        sc.setFrameShape(_NO_FRAME)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(4, 6, 4, 4); l.setSpacing(3)

        l.addWidget(_section_label('Draw Shape'))
        shape_row = FlowLayout(h_spacing=6, v_spacing=4)
        self._shapes_tool_btns = {}
        for mode, label, tip in (
            ('circle', 'Circle',
             'Freehand: click the centre, drag to set the radius, release to finish.\n'
             'Or set a Diameter below, then just click once to place it there.'),
            ('ellipse', 'Ellipse',
             'Freehand: click the centre, drag to set each radius, release to finish.\n'
             'Or set Width/Height below, then just click once to place it there.'),
            ('rectangle', 'Rectangle',
             'Freehand: click one corner, drag to the opposite corner, release to finish.\n'
             'Or set Width/Length below, then click the ORIGIN corner to place it\n'
             '(+X = Width, +Y = Length from that point).'),
            ('square', 'Square',
             'Freehand: click one corner, drag to set the side length, release to finish.\n'
             'Or set Side below, then click the ORIGIN corner to place it\n'
             '(+X/+Y = Side from that point).'),
            ('polygon', 'Polygon',
             'Click to add vertices.\nRight-click or double-click to finish.'),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda checked, m=mode: self._toggle_shapes_tool(m, checked))
            shape_row.addWidget(btn)
            self._shapes_tool_btns[mode] = btn

        self.btn_shapes_rotate = QPushButton('Rotate')
        self.btn_shapes_rotate.setCheckable(True)
        self.btn_shapes_rotate.setEnabled(False)
        self.btn_shapes_rotate.setToolTip(
            'Drag anywhere on the map to rotate the current shape around\n'
            "its own centre (only the angle matters, not where you click)\n"
            '— or just type an exact Angle below instead.')
        self.btn_shapes_rotate.clicked.connect(lambda checked: self._toggle_shapes_tool('rotate', checked))
        shape_row.addWidget(self.btn_shapes_rotate)
        self._shapes_tool_btns['rotate'] = self.btn_shapes_rotate

        self.btn_shapes_clear = QPushButton('Clear Shape')
        self.btn_shapes_clear.setToolTip('Removes the currently drawn shape.')
        self.btn_shapes_clear.clicked.connect(self._on_shapes_clear)
        shape_row.addWidget(self.btn_shapes_clear)
        l.addLayout(shape_row)

        self.lbl_shapes_status = QLabel('Draw a shape on the map to begin.')
        self.lbl_shapes_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_shapes_status.setWordWrap(True)
        l.addWidget(self.lbl_shapes_status)

        l.addSpacing(6)
        l.addWidget(_section_label('Shape Size (optional)'))
        size_hint = QLabel('Leave at 0 to draw freehand — set a size to place by a single click instead.')
        size_hint.setStyleSheet('color:gray;font-size:9px;')
        size_hint.setWordWrap(True)
        l.addWidget(size_hint)

        def _size_spin():
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 9999.0)
            spin.setDecimals(2)
            spin.setSuffix(' m')
            spin.setFixedWidth(90)
            spin.setSpecialValueText('Freehand')
            spin.valueChanged.connect(self._on_shapes_param_changed)
            return spin

        def _size_page(*fields):
            """One shape's own size field(s) in a row — just this one
            shape's, rather than all six fields shown at once, per
            explicit request. No tab strip of its own (see
            self.stack_shapes_size below) — the Draw Shape buttons above
            are the ONLY place Circle/Ellipse/Rectangle/Square/Polygon
            appear as clickable labels, rather than that same set of
            names appearing twice; activating a shape's own DRAW tool
            (see _toggle_shapes_tool) is what switches which page shows
            here, one-way only (there's nothing to click ON this stack
            itself)."""
            page = QWidget()
            row = QHBoxLayout(page)
            row.setContentsMargins(6, 4, 6, 4)
            row.setSpacing(10)
            for label, spin in fields:
                row.addWidget(self._embank_flow_pair(label, spin))
            row.addStretch()
            return page

        # QStackedWidget, not QTabWidget — no tab strip of its own (see
        # _size_page's own docstring for why), just whichever one shape's
        # fields are currently relevant. Keyed by the same mode strings
        # _toggle_shapes_tool already uses, so activating a draw tool can
        # look its own page index up directly — see _shapes_size_tab_index.
        # Height capped outright: QStackedWidget/QTabWidget both default
        # to sizing themselves to their TALLEST page even while showing a
        # shorter one, which otherwise left a large empty gap below
        # whichever short page (e.g. Rectangle) happened to be showing.
        self.stack_shapes_size = QStackedWidget()
        self.stack_shapes_size.setMaximumHeight(50)
        self._shapes_size_tab_index = {}

        self.spin_shapes_circle_diameter = _size_spin()
        self.stack_shapes_size.addWidget(_size_page(('Diameter:', self.spin_shapes_circle_diameter)))
        self._shapes_size_tab_index['circle'] = 0

        self.spin_shapes_ellipse_width = _size_spin()
        self.spin_shapes_ellipse_height = _size_spin()
        self.stack_shapes_size.addWidget(_size_page(
            ('Width:', self.spin_shapes_ellipse_width),
            ('Height:', self.spin_shapes_ellipse_height)))
        self._shapes_size_tab_index['ellipse'] = 1

        self.spin_shapes_rect_width = _size_spin()
        self.spin_shapes_rect_length = _size_spin()
        self.stack_shapes_size.addWidget(_size_page(
            ('Width:', self.spin_shapes_rect_width),
            ('Length:', self.spin_shapes_rect_length)))
        self._shapes_size_tab_index['rectangle'] = 2

        self.spin_shapes_square_side = _size_spin()
        self.stack_shapes_size.addWidget(_size_page(('Side:', self.spin_shapes_square_side)))
        self._shapes_size_tab_index['square'] = 3
        # Polygon has no size field (always freehand) so it isn't a page
        # here at all — see the Polygon Type section below instead,
        # which replaces what used to be a one-line placeholder here.

        l.addWidget(self.stack_shapes_size)

        l.addSpacing(6)
        l.addWidget(_section_label('Polygon Type'))
        self.tabs_shapes_polygon_type = QTabWidget()
        self.tabs_shapes_polygon_type.setStyleSheet(_style.SUBTAB_STYLE)
        self.tabs_shapes_polygon_type.addTab(self._build_shapes_normal_polygon_tab(), 'Normal Polygon')
        self.tabs_shapes_polygon_type.addTab(self._build_wetland_tab(), 'Wetland Pond')
        l.addWidget(self.tabs_shapes_polygon_type)

        l.addSpacing(6)
        l.addWidget(_section_label('Rotation'))
        rot_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.spin_shapes_rotation = QDoubleSpinBox()
        self.spin_shapes_rotation.setRange(-360.0, 360.0)
        self.spin_shapes_rotation.setDecimals(1)
        self.spin_shapes_rotation.setSuffix(' °')
        self.spin_shapes_rotation.setValue(0.0)
        self.spin_shapes_rotation.setToolTip(
            "The shape's current rotation from how it was originally\n"
            'drawn, around its own centre. Type a value to rotate to it\n'
            'directly — or use the Rotate tool above to drag it instead.')
        self.spin_shapes_rotation.valueChanged.connect(self._on_shapes_rotation_field_changed)
        rot_row.addWidget(self._embank_flow_pair('Angle:', self.spin_shapes_rotation))
        l.addLayout(rot_row)

        l.addSpacing(6)
        l.addWidget(_section_label('Raster Layers (DEM)', _raster_type_pix(12)))
        dem_row = QHBoxLayout()
        dem_row.setSpacing(4)
        dem_row.addWidget(QLabel('Ground DEM:'))
        self.combo_shapes_raster = QgsMapLayerComboBox()
        self.combo_shapes_raster.setFilters(_RASTER_FILTER)
        self.combo_shapes_raster.setAllowEmptyLayer(True)
        self.combo_shapes_raster.setCurrentIndex(0)
        self.combo_shapes_raster.wheelEvent = lambda e: e.ignore()
        dem_row.addWidget(self.combo_shapes_raster, 1)
        l.addLayout(dem_row)

        l.addSpacing(6)
        l.addWidget(_section_label('Elevation'))
        elev_row = FlowLayout(h_spacing=10, v_spacing=6)
        elev_row.addWidget(QLabel('Mode:'))
        self.combo_shapes_elev_mode = QComboBox()
        self.combo_shapes_elev_mode.addItem('Relative to ground', 'relative')
        self.combo_shapes_elev_mode.addItem('Absolute elevation', 'absolute')
        self.combo_shapes_elev_mode.wheelEvent = lambda e: e.ignore()
        self.combo_shapes_elev_mode.currentIndexChanged.connect(self._on_shapes_elev_mode_changed)
        elev_row.addWidget(self.combo_shapes_elev_mode)

        self.spin_shapes_elev = QDoubleSpinBox()
        self.spin_shapes_elev.setRange(-9999.0, 9999.0)
        self.spin_shapes_elev.setDecimals(3)
        self.spin_shapes_elev.setValue(self._shapes_elevation_value)
        self.spin_shapes_elev.setSuffix(' m')
        self.spin_shapes_elev.setFixedWidth(100)
        self.spin_shapes_elev.valueChanged.connect(self._on_shapes_elev_value_changed)
        elev_row.addWidget(self._embank_flow_pair('Height:', self.spin_shapes_elev))

        self.spin_shapes_slope = QDoubleSpinBox()
        self.spin_shapes_slope.setRange(0.0, 20.0)
        self.spin_shapes_slope.setDecimals(2)
        self.spin_shapes_slope.setSuffix(' :1')
        self.spin_shapes_slope.setValue(self._shapes_slope)
        self.spin_shapes_slope.setToolTip(
            'Edge slope (H:V) running out from the shape to existing\n'
            'ground. 0 = vertical edge.')
        self.spin_shapes_slope.valueChanged.connect(self._on_shapes_slope_changed)
        elev_row.addWidget(self._embank_flow_pair('Edge slope:', self.spin_shapes_slope))
        l.addLayout(elev_row)
        self._on_shapes_elev_mode_changed()   # seed the tooltip for the default mode

        l.addSpacing(6)
        res_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.spin_shapes_resolution = QDoubleSpinBox()
        self.spin_shapes_resolution.setRange(0.01, 1000.0)
        self.spin_shapes_resolution.setDecimals(2)
        self.spin_shapes_resolution.setValue(1.0)
        self.spin_shapes_resolution.setSuffix(' m')
        self.spin_shapes_resolution.setToolTip('Output raster cell size.')
        res_row.addWidget(self._embank_flow_pair('Resolution:', self.spin_shapes_resolution))
        l.addLayout(res_row)
        # Create TIFF itself lives in the shared bottom action row (built
        # in _build_ui, right before Refresh), same as Embankment/Channel
        # Surface's own Create TIFF — see self.btn_shapes_create_tiff.

        l.addStretch()
        sc.setWidget(inner)
        outer_l.addWidget(sc)
        return tab

    def _build_shapes_normal_polygon_tab(self):
        """Normal Polygon — unchanged from the plain freehand behaviour
        Polygon always had before Wetland Pond existed: no fillet, no
        sections, drawn with the Polygon button up in Draw Shape, feeding
        into the same shared Elevation/Edge slope/Create TIFF as every
        other shape here."""
        tab = QWidget()
        row = QHBoxLayout(tab)
        row.setContentsMargins(6, 4, 6, 4)
        lbl = QLabel('Always drawn freehand — click to add vertices, right-click or double-click to finish.')
        lbl.setStyleSheet('color:gray;font-size:9px;')
        lbl.setWordWrap(True)
        row.addWidget(lbl)
        return tab

    # ------------------------------------------------------------------ Wetland Pond
    #
    # Nested under Polygon — a CLOSED-ring boundary workflow, same overall
    # architecture as Channel/Embankment Surface (Plan ~ their Horizontal/
    # Vertical Alignment; Section ~ their per-station cross-section) but
    # for a closed ring instead of a linear alignment, and its own
    # separate draw/vertex-edit tools (self._wetland_draw_tool/
    # _wetland_vertex_tool) independent of the Shapes tab's own
    # self._shapes_map_tool — the two sweep each other off (see
    # _wetland_deactivate_all_tools / _toggle_shapes_tool) so only ever
    # one tool across the WHOLE Shapes and Polygon Surface tab is active
    # at a time. First structural pass, per explicit request — Section's
    # own fields (currently Berm/Slope/Height, copied from Embankment's)
    # are an intentional placeholder ('we will change what to add in each
    # section later on'); there's no ground sampling, interpolation
    # between vertices, or surface generation wired up yet.

    def _build_wetland_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        self.tabs_wetland_workflow = QTabWidget()
        self.tabs_wetland_workflow.setStyleSheet(_style.SUBTAB_STYLE)
        self.tabs_wetland_workflow.addTab(self._build_wetland_plan_tab(), 'Plan')
        self.tabs_wetland_workflow.addTab(self._build_wetland_section_tab(), 'Section')
        v.addWidget(self.tabs_wetland_workflow)
        return tab

    def _build_wetland_plan_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)

        fillet_row = FlowLayout(h_spacing=6, v_spacing=4)
        self.btn_wetland_draw = QPushButton('Draw Polygon')
        self.btn_wetland_draw.setCheckable(True)
        self.btn_wetland_draw.setToolTip(
            'Draw the wetland/pond boundary.\nLeft-click to add vertices.\n'
            'Right-click or double-click to finish.'
        )
        self.btn_wetland_draw.clicked.connect(self._toggle_wetland_draw)
        fillet_row.addWidget(self.btn_wetland_draw)

        fillet_row.addWidget(QLabel('Fillet radius:'))
        self.spin_wetland_fillet = QDoubleSpinBox()
        self.spin_wetland_fillet.setRange(0.0, 500.0)
        self.spin_wetland_fillet.setValue(self._wetland_fillet_radius)
        self.spin_wetland_fillet.setSingleStep(1.0)
        self.spin_wetland_fillet.setSuffix(' m')
        self.spin_wetland_fillet.setFixedWidth(75)
        self.spin_wetland_fillet.setToolTip(
            'Rounds a sharp corner with an arc of this radius. 0 = keep\n'
            'the corner sharp. Applies to every corner unless a vertex is\n'
            'selected (Edit Vertices), in which case it sets that corner\n'
            'only.'
        )
        self.spin_wetland_fillet.valueChanged.connect(self._on_wetland_fillet_changed)
        fillet_row.addWidget(self.spin_wetland_fillet)

        self.lbl_wetland_fillet_tag = QLabel('(default)')
        self.lbl_wetland_fillet_tag.setStyleSheet('color:gray;font-size:10px;font-style:italic;')
        fillet_row.addWidget(self.lbl_wetland_fillet_tag)

        self.btn_wetland_set_global_radius = QPushButton('Set Global Radius')
        self.btn_wetland_set_global_radius.setToolTip(
            "Sets every corner's fillet radius to the Fillet radius value\n"
            'above, uniformly — replacing any individual corner radius\n'
            'already set via Edit Vertices (asks to confirm first). Same\n'
            "behaviour as Channel Surface's own Set Global Radius."
        )
        self.btn_wetland_set_global_radius.clicked.connect(self._on_wetland_set_global_radius)
        fillet_row.addWidget(self.btn_wetland_set_global_radius)

        self.btn_wetland_vertex_edit = QPushButton('Edit Vertices')
        self.btn_wetland_vertex_edit.setCheckable(True)
        self.btn_wetland_vertex_edit.setToolTip(
            'Click a vertex to select it (set its own fillet radius above,\n'
            'or edit its own section in the Section tab) — click the same\n'
            'vertex again to deselect. Drag a vertex to move it. Right-\n'
            'click a vertex to remove it.'
        )
        self.btn_wetland_vertex_edit.clicked.connect(self._toggle_wetland_vertex_edit)
        fillet_row.addWidget(self.btn_wetland_vertex_edit)

        self.btn_wetland_vertex_reset = QPushButton('Reset')
        self.btn_wetland_vertex_reset.setFixedWidth(48)
        self.btn_wetland_vertex_reset.setToolTip("Clear this vertex's override — use the default radius again")
        self.btn_wetland_vertex_reset.clicked.connect(self._on_wetland_vertex_reset)
        self.btn_wetland_vertex_reset.setVisible(False)
        fillet_row.addWidget(self.btn_wetland_vertex_reset)

        self.btn_wetland_clear = QPushButton('Clear')
        self.btn_wetland_clear.setToolTip('Removes the current wetland/pond boundary.')
        self.btn_wetland_clear.clicked.connect(self._on_wetland_clear)
        fillet_row.addWidget(self.btn_wetland_clear)
        v.addLayout(fillet_row)

        self.lbl_wetland_status = QLabel('Draw a wetland/pond boundary on the map to begin.')
        self.lbl_wetland_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_wetland_status.setWordWrap(True)
        v.addWidget(self.lbl_wetland_status)
        v.addStretch()
        return tab

    def _build_wetland_section_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        self.lbl_wetland_xs_status = QLabel(
            'Select a vertex (Edit Vertices, in the Plan tab) to edit its own section.')
        self.lbl_wetland_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_wetland_xs_status.setWordWrap(True)
        v.addWidget(self.lbl_wetland_xs_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(280)
        scroll.setFrameShape(_NO_FRAME)
        rows_container = QWidget()
        self._wetland_xs_rows_vbox = QVBoxLayout(rows_container)
        self._wetland_xs_rows_vbox.setContentsMargins(0, 0, 0, 0)
        self._wetland_xs_rows_vbox.setSpacing(5)
        self._wetland_xs_rows_vbox.addStretch()
        scroll.setWidget(rows_container)
        v.addWidget(scroll)

        action_row = FlowLayout(h_spacing=6, v_spacing=4)
        self.btn_wetland_section_flush = QPushButton('Flush with Ground')
        self.btn_wetland_section_flush.setToolTip(
            "Selected (last) section runs at its own slope until it\n"
            'meets existing ground — nothing below it.')
        self.btn_wetland_section_flush.clicked.connect(self._on_wetland_section_flush)
        action_row.addWidget(self.btn_wetland_section_flush)

        self.btn_wetland_section_add = QPushButton('Add Section')
        self.btn_wetland_section_add.setToolTip('Adds another section below the selected (last) one.')
        self.btn_wetland_section_add.clicked.connect(self._on_wetland_section_add)
        action_row.addWidget(self.btn_wetland_section_add)

        self.btn_wetland_section_remove = QPushButton('Remove')
        self.btn_wetland_section_remove.setToolTip(
            "Removes the selected (last) section, reverting the one\n"
            'before it back to Flush with Ground.')
        self.btn_wetland_section_remove.clicked.connect(self._on_wetland_section_remove)
        action_row.addWidget(self.btn_wetland_section_remove)
        v.addLayout(action_row)

        self._update_wetland_section_action_area()
        return tab

    def _wetland_tool_buttons(self):
        return [(self.btn_wetland_draw, self._toggle_wetland_draw),
                (self.btn_wetland_vertex_edit, self._toggle_wetland_vertex_edit)]

    def _wetland_deactivate_all_tools(self, except_btn=None):
        for btn, toggle_fn in self._wetland_tool_buttons():
            if btn is except_btn or not btn.isChecked():
                continue
            btn.setChecked(False)
            toggle_fn(False)

    def _toggle_wetland_draw(self, checked):
        if checked:
            self._wetland_deactivate_all_tools(except_btn=self.btn_wetland_draw)
            self._shapes_deactivate_all_tools()   # only one tool across the whole Shapes tab at a time
            self._wetland_draw_tool.set_mode('polygon')
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._wetland_draw_tool)
            self.lbl_wetland_status.setText('Click to add vertices — right-click or double-click to finish…')
            self.lbl_wetland_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._wetland_draw_tool:
                self.canvas.unsetMapTool(self._wetland_draw_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)

    def _toggle_wetland_vertex_edit(self, checked):
        if checked:
            self._wetland_deactivate_all_tools(except_btn=self.btn_wetland_vertex_edit)
            self._shapes_deactivate_all_tools()
            if len(self._wetland_raw_pts) < 3:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    'Draw a wetland/pond boundary first.')
                self.btn_wetland_vertex_edit.setChecked(False)
                return
            self._wetland_vertex_tool.set_vertices(self._wetland_raw_pts)
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._wetland_vertex_tool)
            self.lbl_wetland_status.setText(
                'Click a vertex to select it, drag to move it, or right-click to remove it.')
            self.lbl_wetland_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._wetland_vertex_tool:
                self.canvas.unsetMapTool(self._wetland_vertex_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)

    def _rebuild_wetland_polygon(self):
        if not self._wetland_raw_pts or len(self._wetland_raw_pts) < 3:
            return
        filleted = self._fillet_ring(self._wetland_raw_pts, self._wetland_fillet_radius,
                                      self._wetland_vertex_radii)
        ring = filleted + [filleted[0]]
        self._wetland_geom = QgsGeometry.fromPolygonXY([ring])
        if self._wetland_band:
            try: self.canvas.scene().removeItem(self._wetland_band)
            except Exception: pass
        self._wetland_band = QgsRubberBand(self.canvas, _POLYGON_GEOM)
        self._wetland_band.setColor(QColor(0, 150, 136, 60))
        self._wetland_band.setStrokeColor(QColor(0, 150, 136, 220))
        self._wetland_band.setWidth(2)
        self._wetland_band.setToGeometry(self._wetland_geom, None)
        self.lbl_wetland_status.setText(
            f'Wetland/Pond boundary: {len(self._wetland_raw_pts)} vertices — ready.')
        self.lbl_wetland_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')
        self._draw_wetland_vertex_markers()

    def _draw_wetland_vertex_markers(self):
        """One small marker per RAW (pre-fillet) vertex, always shown
        once a boundary exists — not just while Edit Vertices is active
        — so it's clear where every vertex actually is and which ones
        click targets. The selected vertex (if any) gets its own
        distinct yellow highlight on top, per explicit request ('hard to
        know if selection is working'). Same convention as Embankment/
        Channel Surface's own vertex markers."""
        self._clear_wetland_vertex_markers()
        pts = self._wetland_raw_pts
        if len(pts) < 3:
            return
        for i, pt in enumerate(pts):
            band = QgsRubberBand(self.canvas, _POINT_GEOM)
            has_override = i in self._wetland_vertex_radii
            band.setColor(QColor(255, 152, 0, 200) if has_override else QColor(0, 150, 136, 150))
            band.setIcon(_ICON_CIRCLE)
            band.setIconSize(12)
            band.setToGeometry(QgsGeometry.fromPointXY(pt), None)
            self._wetland_vertex_bands.append(band)

        idx = self._wetland_selected_vertex
        if idx is not None and 0 <= idx < len(pts):
            sel = QgsRubberBand(self.canvas, _POINT_GEOM)
            sel.setColor(QColor(255, 235, 59, 230))   # yellow — matches VertexSelectTool's own drag highlight
            sel.setIcon(_ICON_BOX)
            sel.setIconSize(18)
            sel.setToGeometry(QgsGeometry.fromPointXY(pts[idx]), None)
            self._wetland_vertex_selected_band = sel

    def _clear_wetland_vertex_markers(self):
        for band in self._wetland_vertex_bands:
            try: self.canvas.scene().removeItem(band)
            except Exception: pass
        self._wetland_vertex_bands = []
        if self._wetland_vertex_selected_band is not None:
            try: self.canvas.scene().removeItem(self._wetland_vertex_selected_band)
            except Exception: pass
            self._wetland_vertex_selected_band = None

    def _on_wetland_polygon_completed(self, geom):
        parts = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        ring = list(parts[0][0]) if parts and parts[0] else []
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            return
        self._wetland_raw_pts = [QgsPointXY(p) for p in ring]
        self._wetland_vertex_radii = {}
        self._wetland_xs_params = {}
        self._wetland_selected_vertex = None
        self._wetland_xs_sel_idx = None
        self._rebuild_wetland_polygon()
        self._wetland_vertex_tool.set_vertices(self._wetland_raw_pts)
        self._rebuild_wetland_section_rows()
        self._update_wetland_fillet_ui()
        self.btn_wetland_draw.setChecked(False)
        self._toggle_wetland_draw(False)

    def _on_wetland_clear(self):
        self._wetland_deactivate_all_tools()
        self._wetland_raw_pts = []
        self._wetland_geom = None
        if self._wetland_band:
            try: self.canvas.scene().removeItem(self._wetland_band)
            except Exception: pass
            self._wetland_band = None
        self._wetland_vertex_radii = {}
        self._wetland_xs_params = {}
        self._wetland_selected_vertex = None
        self._wetland_xs_sel_idx = None
        self._wetland_draw_tool.reset()
        self._wetland_vertex_tool.set_vertices([])
        self._clear_wetland_vertex_markers()
        self._update_wetland_fillet_ui()
        self._rebuild_wetland_section_rows()
        self.lbl_wetland_status.setText('Draw a wetland/pond boundary on the map to begin.')
        self.lbl_wetland_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')

    def _on_wetland_fillet_changed(self, value):
        self._wetland_fillet_radius = value
        if self._wetland_selected_vertex is not None:
            self._wetland_vertex_radii[self._wetland_selected_vertex] = value
            self.lbl_wetland_fillet_tag.setText(f'(vertex {self._wetland_selected_vertex + 1})')
        if self._wetland_raw_pts:
            self._rebuild_wetland_polygon()

    def _on_wetland_set_global_radius(self):
        """Sets EVERY vertex's fillet radius to the current Fillet radius
        field's value, uniformly — same literal behaviour as Channel
        Surface's own Set Global Radius, per explicit request."""
        if self._wetland_geom is None or len(self._wetland_raw_pts) < 3:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Draw a wetland/pond boundary first.')
            return
        if not self._wetland_vertex_radii:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Every vertex already uses the global fillet radius — nothing to change.')
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f"Set every vertex's fillet radius to {self._wetland_fillet_radius:.2f} m "
            '(the current Fillet radius value)?\n'
            'This replaces any individual vertex radius already set via Edit Vertices.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        self._wetland_vertex_radii = {}
        self._rebuild_wetland_polygon()
        self.lbl_wetland_status.setText(
            f'Set every vertex to the global fillet radius ({self._wetland_fillet_radius:.2f} m).')
        self.lbl_wetland_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')

    def _update_wetland_fillet_ui(self):
        if self._wetland_selected_vertex is None:
            self.lbl_wetland_fillet_tag.setText('(default)')
            self.spin_wetland_fillet.blockSignals(True)
            self.spin_wetland_fillet.setValue(self._wetland_fillet_radius)
            self.spin_wetland_fillet.blockSignals(False)
            self.btn_wetland_vertex_reset.setVisible(False)
            return
        r = self._wetland_vertex_radii.get(self._wetland_selected_vertex, self._wetland_fillet_radius)
        self.spin_wetland_fillet.blockSignals(True)
        self.spin_wetland_fillet.setValue(r)
        self.spin_wetland_fillet.blockSignals(False)
        self.lbl_wetland_fillet_tag.setText(f'(vertex {self._wetland_selected_vertex + 1})')
        self.btn_wetland_vertex_reset.setVisible(True)

    def _on_wetland_vertex_reset(self):
        if self._wetland_selected_vertex is not None:
            self._wetland_vertex_radii.pop(self._wetland_selected_vertex, None)
            self.lbl_wetland_fillet_tag.setText('(default)')
            self._rebuild_wetland_polygon()

    def _set_wetland_selected_vertex(self, idx):
        self._wetland_selected_vertex = idx
        self._wetland_xs_sel_idx = None
        self._update_wetland_fillet_ui()
        self._rebuild_wetland_section_rows()
        self._draw_wetland_vertex_markers()

    def _on_wetland_vertex_clicked(self, idx):
        if idx is None:
            self._set_wetland_selected_vertex(None)
            return
        if self._wetland_selected_vertex == idx:
            self._set_wetland_selected_vertex(None)   # click same vertex again -> deselect
        else:
            self._set_wetland_selected_vertex(idx)

    def _on_wetland_vertex_moved(self, idx, new_pt):
        if not (0 <= idx < len(self._wetland_raw_pts)):
            return
        self._wetland_raw_pts[idx] = new_pt
        self._rebuild_wetland_polygon()
        self._wetland_vertex_tool.set_vertices(self._wetland_raw_pts)

    def _on_wetland_vertex_deleted(self, idx):
        if len(self._wetland_raw_pts) <= 3:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'A boundary needs at least 3 points — this vertex can\'t be removed.')
            return
        if not (0 <= idx < len(self._wetland_raw_pts)):
            return
        self._wetland_raw_pts.pop(idx)
        self._wetland_vertex_radii = {
            (k if k < idx else k - 1): v
            for k, v in self._wetland_vertex_radii.items() if k != idx
        }
        self._wetland_xs_params = {
            (k if k < idx else k - 1): v
            for k, v in self._wetland_xs_params.items() if k != idx
        }
        if self._wetland_selected_vertex == idx:
            self._wetland_selected_vertex = None
        elif self._wetland_selected_vertex is not None and self._wetland_selected_vertex > idx:
            self._wetland_selected_vertex -= 1
        self._rebuild_wetland_polygon()
        self._wetland_vertex_tool.set_vertices(self._wetland_raw_pts)
        self._update_wetland_fillet_ui()
        self._rebuild_wetland_section_rows()

    # ---- Section (per-vertex section-stack, same mechanics as
    # Embankment's own — see _build_embank_section_row) -------------------

    def _wetland_xs_default_sections(self):
        return [{'slope': 3.0, 'berm_width': 0.0, 'height': None}]

    def _wetland_section_action_target(self):
        key = self._wetland_selected_vertex
        if key is None:
            return None, None
        sections = self._wetland_xs_params[key]['sections']
        return sections, self._wetland_xs_sel_idx

    def _on_wetland_section_field_changed(self, sec, field, value):
        sec[field] = value

    def _on_wetland_section_row_clicked(self, idx):
        if self._wetland_xs_sel_idx == idx:
            return
        self._wetland_xs_sel_idx = idx
        self._rebuild_wetland_section_rows()

    def _on_wetland_section_flush(self):
        sections, idx = self._wetland_section_action_target()
        if sections is None or idx is None or idx != len(sections) - 1:
            return
        sections[idx]['height'] = None
        self._rebuild_wetland_section_rows()

    def _on_wetland_section_add(self):
        sections, idx = self._wetland_section_action_target()
        if sections is None or idx is None or idx != len(sections) - 1:
            return
        if sections[idx].get('height') is None:
            sections[idx]['height'] = 2.0
        sections.append({'slope': 3.0, 'berm_width': 0.0, 'height': None})
        self._wetland_xs_sel_idx = len(sections) - 1
        self._rebuild_wetland_section_rows()

    def _on_wetland_section_remove(self):
        sections, idx = self._wetland_section_action_target()
        if sections is None or idx is None or idx != len(sections) - 1 or len(sections) <= 1:
            return
        sections.pop()
        sections[-1]['height'] = None
        self._wetland_xs_sel_idx = len(sections) - 1
        self._rebuild_wetland_section_rows()

    def _rebuild_wetland_section_rows(self):
        vbox = self._wetland_xs_rows_vbox
        while vbox.count() > 1:   # leave the trailing stretch in place
            item = vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        key = self._wetland_selected_vertex
        if key is None:
            self._wetland_xs_sel_idx = None
            self._update_wetland_section_action_area()
            self.lbl_wetland_xs_status.setText(
                'Select a vertex (Edit Vertices, in the Plan tab) to edit its own section.')
            self.lbl_wetland_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            return
        params = self._wetland_xs_params.setdefault(key, {'sections': [dict(s) for s in self._wetland_xs_default_sections()]})
        sections = params['sections']

        sel_idx = self._wetland_xs_sel_idx
        if sel_idx is None or sel_idx >= len(sections):
            sel_idx = len(sections) - 1
        self._wetland_xs_sel_idx = sel_idx

        for idx in range(len(sections)):
            row = self._build_wetland_section_row(sections, idx, selected=(idx == sel_idx))
            vbox.insertWidget(idx, row)
        self._update_wetland_section_action_area()
        self.lbl_wetland_xs_status.setText(f'Vertex {key + 1} selected.')
        self.lbl_wetland_xs_status.setStyleSheet('color:#6A1B9A;font-style:italic;font-size:11px;')

    def _update_wetland_section_action_area(self):
        key = self._wetland_selected_vertex
        if key is None:
            self.btn_wetland_section_flush.setEnabled(False)
            self.btn_wetland_section_add.setEnabled(False)
            self.btn_wetland_section_remove.setEnabled(False)
            return
        sections = self._wetland_xs_params[key]['sections']
        sel_idx = self._wetland_xs_sel_idx
        is_last = sel_idx is not None and sel_idx == len(sections) - 1
        self.btn_wetland_section_flush.setEnabled(is_last)
        self.btn_wetland_section_add.setEnabled(is_last)
        self.btn_wetland_section_remove.setEnabled(is_last and len(sections) > 1)

    def _build_wetland_section_row(self, sections, idx, selected):
        """One section's own single-line editable row — same mechanics
        as Embankment's _build_embank_section_row (Berm on every section
        after the first, Slope, Height or '→ ground' while flush), minus
        its Crest Width special-case on Section 1 (nothing analogous here
        yet). Placeholder fields — 'we will change what to add in each
        section later on'."""
        sec = sections[idx]
        row = QWidget()
        row.setStyleSheet(
            'QWidget{background:%s;border-radius:5px;}' % ('#C8E0F9' if selected else '#EFEFEF'))
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(10)

        hdr = QLabel(f'Section {idx + 1}')
        hdr.setStyleSheet('font-weight:bold;font-size:11px;color:#455A64;')
        hdr.setFixedWidth(58)
        h.addWidget(hdr)

        def _mk_spin(minimum, maximum, value, suffix, slot):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(2)
            spin.setSuffix(suffix)
            spin.setFixedWidth(88)
            spin.setMinimumHeight(28)
            spin.setValue(value)
            spin.wheelEvent = lambda e: e.ignore()
            spin.valueChanged.connect(slot)
            return spin

        if idx > 0:
            berm_spin = _mk_spin(
                0.0, 200.0, sec.get('berm_width', 0.0), ' m',
                lambda v: self._on_wetland_section_field_changed(sec, 'berm_width', v))
            h.addWidget(self._embank_flow_pair('Berm:', berm_spin))

        slope_spin = _mk_spin(
            0.0, 20.0, sec.get('slope', 3.0), ' :1',
            lambda v: self._on_wetland_section_field_changed(sec, 'slope', v))
        h.addWidget(self._embank_flow_pair('Slope:', slope_spin))

        height = sec.get('height')
        if height is None:
            flush_lbl = QLabel('→ ground')
            flush_lbl.setStyleSheet('color:gray;font-style:italic;font-size:10px;')
            h.addWidget(flush_lbl)
        else:
            height_spin = _mk_spin(
                0.01, 200.0, height, ' m',
                lambda v: self._on_wetland_section_field_changed(sec, 'height', v))
            h.addWidget(self._embank_flow_pair('Height:', height_spin))

        h.addStretch()
        row.mousePressEvent = lambda e, i=idx: self._on_wetland_section_row_clicked(i)
        return row

    def _shapes_tool_buttons(self):
        """[(mode, button), ...] for every Shapes and Polygon Surface
        tool, INCLUDING Rotate — _shapes_deactivate_all_tools and
        _shapes_escape_deactivate_tool both walk this, same pattern as
        _embank_tool_buttons/_terrain_tool_buttons, so only ever one of
        these is active at a time."""
        return list(self._shapes_tool_btns.items())

    def _shapes_deactivate_all_tools(self, except_mode=None):
        for mode, btn in self._shapes_tool_buttons():
            if mode == except_mode or not btn.isChecked():
                continue
            btn.setChecked(False)
            self._toggle_shapes_tool(mode, False)

    def _shapes_escape_deactivate_tool(self):
        if self._active_tab == _TAB_SHAPES:
            self._shapes_deactivate_all_tools()
            self._wetland_deactivate_all_tools()

    def _shapes_fixed_size_for(self, mode):
        """The fixed-size params dict for ShapeDrawTool.set_fixed_size()
        — read from this mode's own size field(s), None (freehand) while
        any of them is still at its default 0."""
        if mode == 'circle':
            d = self.spin_shapes_circle_diameter.value()
            return {'diameter': d} if d > 0 else None
        if mode == 'ellipse':
            w, h = self.spin_shapes_ellipse_width.value(), self.spin_shapes_ellipse_height.value()
            return {'width': w, 'height': h} if w > 0 and h > 0 else None
        if mode == 'rectangle':
            w, length = self.spin_shapes_rect_width.value(), self.spin_shapes_rect_length.value()
            return {'width': w, 'length': length} if w > 0 and length > 0 else None
        if mode == 'square':
            s = self.spin_shapes_square_side.value()
            return {'side': s} if s > 0 else None
        return None

    def _on_shapes_param_changed(self, _value=None):
        """Any shape-size field changed — re-applies it to the drawing
        tool live IF that same shape's tool is the one currently active,
        so adjusting a value while its 'follows the cursor' preview is
        already showing updates immediately rather than only on the next
        click. A no-op otherwise (e.g. typing into Rectangle's fields
        while Circle is active doesn't touch anything yet)."""
        if self._shapes_current_mode in ('circle', 'ellipse', 'rectangle', 'square'):
            self._shapes_map_tool.set_fixed_size(self._shapes_fixed_size_for(self._shapes_current_mode))

    def _toggle_shapes_tool(self, mode, checked):
        if checked:
            self._wetland_deactivate_all_tools()   # only one tool across the whole Shapes tab at a time
            if mode == 'rotate':
                if self._shapes_geom is None:
                    self.btn_shapes_rotate.setChecked(False)
                    return
                self._shapes_deactivate_all_tools(except_mode=mode)
                self._shapes_rotate_base_geom = QgsGeometry(self._shapes_geom)
                self._shapes_rotate_centroid = self._shapes_rotate_base_geom.centroid().asPoint()
                self._shapes_rotate_tool.set_center(self._shapes_rotate_centroid)
                self._prev_tool = self.canvas.mapTool()
                self.canvas.setMapTool(self._shapes_rotate_tool)
                self.lbl_shapes_status.setText(
                    "Drag anywhere to rotate the shape around its own centre…")
                self.lbl_shapes_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
                self._shapes_current_mode = mode
                return
            self._shapes_deactivate_all_tools(except_mode=mode)
            # Starting a new draw replaces whatever was there before —
            # keeps this a 'one shape at a time' tool and fixes the old
            # shape otherwise lingering once you're done with it / move
            # to another shape tool, per explicit request.
            if self._shapes_geom is not None:
                self._shapes_clear_current()
            self._shapes_map_tool.set_mode(mode)
            self._shapes_map_tool.set_fixed_size(self._shapes_fixed_size_for(mode))
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._shapes_map_tool)
            self.lbl_shapes_status.setText(f'Drawing a {mode} — click on the map…')
            self.lbl_shapes_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
            self._shapes_current_mode = mode
            # Switches the Shape Size stack to this mode's own page — see
            # _size_page's own docstring for why this has no tab strip
            # of its own to browse independently; the Draw Shape buttons
            # above are the only control for this.
            if mode in self._shapes_size_tab_index:
                self.stack_shapes_size.setCurrentIndex(self._shapes_size_tab_index[mode])
        else:
            tool = self._shapes_rotate_tool if mode == 'rotate' else self._shapes_map_tool
            if self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)
            if mode == 'rotate':
                self._shapes_rotate_base_geom = None
                self._shapes_rotate_centroid = None
            self._shapes_current_mode = None
            self._update_shapes_status()

    def _on_shapes_shape_completed(self, geom):
        """A shape tool just finished a shape — replaces whatever was
        drawn before (one shape at a time for this first pass) and turns
        the tool back off so an accidental extra click doesn't
        immediately start overwriting it with another one."""
        self._shapes_geom = geom
        if self._shapes_band:
            try: self.canvas.scene().removeItem(self._shapes_band)
            except Exception: pass
        self._shapes_band = QgsRubberBand(self.canvas, _POLYGON_GEOM)
        self._shapes_band.setColor(QColor(233, 30, 99, 60))
        self._shapes_band.setStrokeColor(QColor(233, 30, 99, 220))
        self._shapes_band.setWidth(2)
        self._shapes_band.setToGeometry(geom, None)
        self._shapes_reset_rotation_field()
        self._shapes_deactivate_all_tools()
        self._update_shapes_status()

    def _shapes_reset_rotation_field(self):
        self._shapes_rotation_deg = 0.0
        self.spin_shapes_rotation.blockSignals(True)
        self.spin_shapes_rotation.setValue(0.0)
        self.spin_shapes_rotation.blockSignals(False)

    def _update_shapes_status(self):
        if hasattr(self, 'btn_shapes_rotate'):
            self.btn_shapes_rotate.setEnabled(self._shapes_geom is not None)
        if self._shapes_geom is None:
            self.lbl_shapes_status.setText('Draw a shape on the map to begin.')
            self.lbl_shapes_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            return
        area = self._shapes_geom.area()
        self.lbl_shapes_status.setText(f'Shape drawn — area {area:.1f} m². Ready to create a surface.')
        self.lbl_shapes_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

    def _shapes_clear_current(self):
        """Drops the current shape without touching which tool is
        active — shared by _on_shapes_clear and _toggle_shapes_tool
        (starting a fresh draw implicitly clears the old one)."""
        self._shapes_geom = None
        if self._shapes_band:
            try: self.canvas.scene().removeItem(self._shapes_band)
            except Exception: pass
            self._shapes_band = None
        self._shapes_reset_rotation_field()
        self._update_shapes_status()

    def _on_shapes_clear(self):
        self._shapes_deactivate_all_tools()
        self._shapes_clear_current()
        self._shapes_map_tool.reset()

    def _on_shapes_rotate_preview(self, delta_rad):
        if self._shapes_rotate_base_geom is None or self._shapes_band is None:
            return
        g = QgsGeometry(self._shapes_rotate_base_geom)
        # QgsGeometry.rotate()'s own positive direction is CLOCKWISE,
        # opposite to ShapeRotateTool's atan2-based delta (standard
        # math convention, counter-clockwise-positive) — negated here so
        # the point under the cursor actually follows the cursor while
        # dragging, verified empirically before writing this.
        g.rotate(-math.degrees(delta_rad), self._shapes_rotate_centroid)
        self._shapes_band.setToGeometry(g, None)

    def _on_shapes_rotate_committed(self, delta_rad):
        if self._shapes_rotate_base_geom is None:
            return
        g = QgsGeometry(self._shapes_rotate_base_geom)
        g.rotate(-math.degrees(delta_rad), self._shapes_rotate_centroid)
        self._shapes_geom = g
        self._shapes_rotation_deg = (self._shapes_rotation_deg - math.degrees(delta_rad)) % 360.0
        self.spin_shapes_rotation.blockSignals(True)
        self.spin_shapes_rotation.setValue(self._shapes_rotation_deg)
        self.spin_shapes_rotation.blockSignals(False)
        if self._shapes_band:
            self._shapes_band.setToGeometry(g, None)
        # Ready for another drag without leaving/re-entering Rotate mode.
        self._shapes_rotate_base_geom = QgsGeometry(g)
        self._shapes_rotate_centroid = g.centroid().asPoint()
        self._update_shapes_status()

    def _on_shapes_rotation_field_changed(self, value):
        """Typing an exact angle rotates the shape directly to it (from
        its as-drawn orientation), around its own centre — independent
        of the Rotate (drag) tool, same underlying geometry either way."""
        if self._shapes_geom is None:
            return
        delta = value - self._shapes_rotation_deg
        if abs(delta) < 1e-9:
            return
        centroid = self._shapes_geom.centroid().asPoint()
        g = QgsGeometry(self._shapes_geom)
        g.rotate(delta, centroid)
        self._shapes_geom = g
        self._shapes_rotation_deg = value
        if self._shapes_band:
            self._shapes_band.setToGeometry(g, None)

    def _on_shapes_elev_mode_changed(self, *_args):
        self._shapes_elevation_mode = self.combo_shapes_elev_mode.currentData()
        if self._shapes_elevation_mode == 'relative':
            self.spin_shapes_elev.setToolTip(
                'Height relative to existing ground at the shape\'s own\n'
                'centre — positive raises a flat pad that much above it,\n'
                'negative cuts a flat pit that much below it.')
        else:
            self.spin_shapes_elev.setToolTip(
                'Exact target elevation for the whole shape, flat,\n'
                'regardless of what existing ground is there.')

    def _on_shapes_elev_value_changed(self, value):
        self._shapes_elevation_value = value

    def _on_shapes_slope_changed(self, value):
        self._shapes_slope = value

    def _shapes_sample_z(self, layer, pt):
        """Raster value at a single map-canvas-CRS point, reprojected
        into the layer's own CRS first if they differ. None if the layer
        is missing, the point falls outside it, or the cell is NoData."""
        if layer is None:
            return None
        p = QgsPointXY(pt)
        try:
            canvas_crs = self.canvas.mapSettings().destinationCrs()
            if layer.crs() != canvas_crs:
                xform = QgsCoordinateTransform(canvas_crs, layer.crs(), QgsProject.instance())
                p = xform.transform(p)
        except Exception:
            pass
        try:
            val, ok = layer.dataProvider().sample(p, 1)
        except Exception:
            return None
        if not ok or val is None:
            return None
        try:
            if math.isnan(val):
                return None
        except TypeError:
            pass
        return float(val)

    def _generate_shapes_surface_tiff(self, resolution):
        """Builds the shape's surface as a constrained-Delaunay TIN —
        same engine as _generate_embank_channel_tiff, just with the
        shape's own outline as ONE ring (target elevation) and an offset
        outer ring (real ground, via each vertex's own edge-slope run)
        instead of a station-by-station corridor. Raises RuntimeError
        with a user-facing message on failure. Returns the new
        QgsRasterLayer (already added to the project)."""
        if self._shapes_geom is None:
            raise RuntimeError('Draw a shape first.')
        layer = self.combo_shapes_raster.currentLayer()
        if layer is None:
            raise RuntimeError('Pick a ground DEM first.')

        geom = self._shapes_geom
        parts = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        if not parts or not parts[0]:
            raise RuntimeError('Shape is not a valid polygon.')
        ring = list(parts[0][0])
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            raise RuntimeError('Shape is not a valid polygon.')

        centroid = geom.centroid().asPoint()
        ref_z = self._shapes_sample_z(layer, centroid)
        if ref_z is None:
            raise RuntimeError(
                "Could not sample the ground DEM at the shape's own centre "
                '— check the DEM actually covers this area.')

        if self._shapes_elevation_mode == 'absolute':
            target_z = self._shapes_elevation_value
        else:
            target_z = ref_z + self._shapes_elevation_value

        slope = max(0.0, self._shapes_slope)
        inner_pts, outer_pts = [], []
        for v in ring:
            local_z = self._shapes_sample_z(layer, v)
            if local_z is None:
                local_z = ref_z
            inner_pts.append((v.x(), v.y(), target_z))
            dx, dy = v.x() - centroid.x(), v.y() - centroid.y()
            d = math.hypot(dx, dy)
            run = slope * abs(target_z - local_z)
            if d < 1e-9 or run <= 1e-9:
                outer_pts.append((v.x(), v.y(), local_z))
                continue
            ux, uy = dx / d, dy / d
            ox, oy = v.x() + ux * run, v.y() + uy * run
            outer_z = self._shapes_sample_z(layer, QgsPointXY(ox, oy))
            if outer_z is None:
                outer_z = local_z
            outer_pts.append((ox, oy, outer_z))

        n = len(inner_pts)
        inner_ring = inner_pts + [inner_pts[0]]
        outer_ring = outer_pts + [outer_pts[0]]
        breaklines = [inner_ring, outer_ring] + [[inner_pts[i], outer_pts[i]] for i in range(n)]
        points = inner_pts + outer_pts
        footprint = [(x, y) for x, y, _z in outer_ring]

        crs = self.canvas.mapSettings().destinationCrs()
        pts_layer = QgsVectorLayer(f'PointZ?crs={crs.authid()}', 'shapes_points', 'memory')
        pts_layer.startEditing()
        for x, y, z in points:
            feat = QgsFeature(pts_layer.fields())
            feat.setGeometry(QgsGeometry(QgsPoint(x, y, z)))
            pts_layer.addFeature(feat)
        pts_layer.commitChanges()

        lines_layer = QgsVectorLayer(f'LineStringZ?crs={crs.authid()}', 'shapes_breaklines', 'memory')
        lines_layer.startEditing()
        for pts in breaklines:
            if len(pts) < 2:
                continue
            feat = QgsFeature(lines_layer.fields())
            feat.setGeometry(QgsGeometry(QgsLineString([QgsPoint(x, y, z) for x, y, z in pts])))
            lines_layer.addFeature(feat)
        lines_layer.commitChanges()

        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        margin = resolution
        extent = QgsRectangle(min(xs) - margin, min(ys) - margin,
                               max(xs) + margin, max(ys) + margin)
        if extent.width() <= 0 or extent.height() <= 0:
            raise RuntimeError('Computed output extent is empty or invalid.')
        columns = max(1, math.ceil(extent.width() / resolution))
        rows = max(1, math.ceil(extent.height() / resolution))
        if columns * rows > 40_000_000:
            raise RuntimeError(
                f'Requested grid is {columns} x {rows} = {columns * rows:,} cells '
                '— too fine for this extent. Use a coarser Resolution.')

        feedback = QgsFeedback()
        layer_data = []
        for src, source_type in ((pts_layer, QgsInterpolator.SourcePoints),
                                   (lines_layer, QgsInterpolator.SourceBreakLines)):
            d = QgsInterpolator.LayerData()
            d.source = src
            d.transformContext = QgsProject.instance().transformContext()
            d.valueSource = QgsInterpolator.ValueZ
            d.interpolationAttribute = -1
            d.sourceType = source_type
            layer_data.append(d)

        interpolator = QgsTinInterpolator(layer_data, QgsTinInterpolator.Linear, feedback)
        raw_path = QgsProcessingUtils.generateTempFilename('shapes_surface_raw.tif')
        writer = QgsGridFileWriter(interpolator, raw_path, extent, columns, rows)
        writer.writeFile(feedback)

        raw_layer = QgsRasterLayer(raw_path, 'Shapes Surface (raw)')
        if not raw_layer.isValid():
            raise RuntimeError('The raw TIN raster could not be written/loaded.')

        out_path = raw_path
        clip_note = ''
        mask_geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in footprint]])
        if not mask_geom.isGeosValid():
            mask_geom = mask_geom.makeValid()
            if mask_geom.type() != _POLYGON_GEOM:
                poly_parts = [g for g in mask_geom.asGeometryCollection()
                               if g.type() == _POLYGON_GEOM and not g.isEmpty()]
                mask_geom = QgsGeometry.unaryUnion(poly_parts) if poly_parts else QgsGeometry()
        mask_geom.convertToMultiType()
        if not mask_geom.isEmpty():
            mask_layer = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', 'shapes_footprint', 'memory')
            mask_layer.startEditing()
            feat = QgsFeature(mask_layer.fields())
            feat.setGeometry(mask_geom)
            mask_added = mask_layer.addFeature(feat)
            mask_layer.commitChanges()
            if mask_added:
                try:
                    import processing
                    clip_path = QgsProcessingUtils.generateTempFilename('shapes_surface.tif')
                    processing.run('gdal:cliprasterbymasklayer', {
                        'INPUT': raw_path, 'MASK': mask_layer,
                        'SOURCE_CRS': crs, 'TARGET_CRS': crs,
                        'NODATA': -9999.0, 'ALPHA_BAND': False,
                        'CROP_TO_CUTLINE': False, 'KEEP_RESOLUTION': True,
                        'MULTITHREADING': False, 'OUTPUT': clip_path,
                    })
                    clip_layer = QgsRasterLayer(clip_path, 'Shapes Surface (clip check)')
                    if clip_layer.isValid():
                        out_path = clip_path
                    else:
                        clip_note = ('\n\n(Clip step produced no valid output — '
                                      'showing the unclipped rectangle instead.)')
                except Exception as exc:
                    clip_note = f'\n\n(Clip step failed ({exc}) — showing the unclipped rectangle instead.)'

        final_layer = QgsRasterLayer(out_path, 'Shapes Surface (temp)')
        if not final_layer.isValid():
            raise RuntimeError('The generated TIFF could not be loaded as a raster layer.' + clip_note)
        if clip_note:
            QMessageBox.warning(self, 'Advanced Profile Tool', clip_note.strip())
        QgsProject.instance().addMapLayer(final_layer)
        return final_layer

    def _on_shapes_create_tiff(self):
        self.btn_shapes_create_tiff.setEnabled(False)
        QApplication.setOverrideCursor(_WAIT_CURSOR)
        try:
            layer = self._generate_shapes_surface_tiff(self.spin_shapes_resolution.value())
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.btn_shapes_create_tiff.setEnabled(True)
            QMessageBox.critical(self, 'Advanced Profile Tool', f'Create TIFF failed:\n{exc}')
            return
        QApplication.restoreOverrideCursor()
        self.btn_shapes_create_tiff.setEnabled(True)
        self._shapes_last_tiff_layer = layer
        QMessageBox.information(
            self, 'Advanced Profile Tool',
            f"Shapes surface TIFF created: '{layer.name()}'.")

    # ------------------------------------------------------------------ Modify Terrain (channel design)
    #
    # The user draws the channel's centreline on the map (own
    # ProfileLineTool instance, own geometry — never touches
    # self.profile_geom/self._map_tool, which belong to the Layer Profile
    # tab); sharp bends can optionally be rounded with a fillet radius.
    # Draw Line and Add Vertical Break are OWN dedicated buttons embedded
    # in their own sections here (Horizontal/Vertical Alignment) rather
    # than the shared bottom action row — only Refresh/Chart/Clear stay
    # shared with Layer Profile there, dispatched via _active_tab as
    # before. Reuses the generic _extract/_plot engine to show the
    # existing ground profile along the (filleted) alignment.

    def _build_terrain_tab(self):
        tab = QWidget()
        outer_l = QVBoxLayout(tab)
        outer_l.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        sc.setFrameShape(_NO_FRAME)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(4, 6, 4, 4); l.setSpacing(3)

        # Two mini-tabs — Alignment (horizontal AND vertical together —
        # per explicit request, since both still belong to one 'lay the
        # geometry out' phase before any cross-section work starts) and
        # Cross-Section, so that step gets its own space without the
        # alignment controls crowding it, and editing one can't
        # accidentally touch a control that belongs to the other.
        # Switching between them is entirely manual — nothing in this
        # dock jumps tabs on its own, same convention as Embankment
        # Surface's own Alignment/Cross-Section split.
        self.tabs_terrain_workflow = QTabWidget()
        self.tabs_terrain_workflow.setStyleSheet(_style.SUBTAB_STYLE)
        tab_a = QWidget(); la = QVBoxLayout(tab_a)
        la.setContentsMargins(4, 6, 4, 4); la.setSpacing(3)
        lh = la   # Horizontal Alignment content below goes into the shared tab
        lv = la   # Vertical Alignment content below goes into the same shared tab
        tab_x = QWidget(); lx = QVBoxLayout(tab_x)
        lx.setContentsMargins(4, 6, 4, 4); lx.setSpacing(3)

        # One merged section (alignment fillet + chainage marks) rather
        # than two — keeps this compact since later phases (cross-section
        # shape, vertical design) will need the vertical space more.
        lh.addWidget(_section_label('Horizontal Alignment'))

        # FlowLayout, not QHBoxLayout — this row picked up enough fixed-
        # width controls (label+spin, tag, three buttons) that it can
        # overflow a narrowed dock; wrapping onto a second line keeps
        # every control reachable instead of clipping some of them, same
        # convention as the other Modify Terrain rows below.
        fillet_row = FlowLayout(h_spacing=6, v_spacing=4)
        fillet_row.addWidget(QLabel('Fillet radius:'))
        self.spin_terrain_fillet = QDoubleSpinBox()
        self.spin_terrain_fillet.setRange(0.0, 500.0)
        self.spin_terrain_fillet.setValue(self._terrain_fillet_radius)
        self.spin_terrain_fillet.setSingleStep(1.0)
        self.spin_terrain_fillet.setSuffix(' m')
        self.spin_terrain_fillet.setFixedWidth(75)
        self.spin_terrain_fillet.setToolTip(
            'Rounds a sharp bend with an arc of this radius. 0 = keep the\n'
            'corner sharp. Applies to all bends unless a vertex is selected\n'
            '(Edit Vertices) below, in which case it sets that bend only.'
        )
        self.spin_terrain_fillet.valueChanged.connect(self._on_terrain_fillet_changed)
        fillet_row.addWidget(self.spin_terrain_fillet)

        self.lbl_terrain_fillet_tag = QLabel('(default)')
        self.lbl_terrain_fillet_tag.setStyleSheet('color:gray;font-size:10px;font-style:italic;')
        fillet_row.addWidget(self.lbl_terrain_fillet_tag)

        # Own dedicated Draw Line button (not the shared Layer Profile
        # one) — lives here so it's right next to the tools it actually
        # controls, per explicit request to stop it taking bottom-row
        # space that's shared with an unrelated tab. Sits before Set
        # Half-width Radius per explicit request — drawing the line is
        # the first thing a user does, so it reads left-to-right as the
        # natural order of operations — same convention as Embankment
        # Surface's own Horizontal Alignment row.
        self.btn_terrain_draw = QPushButton('Draw Line')
        self.btn_terrain_draw.setCheckable(True)
        self.btn_terrain_draw.setToolTip(
            'Draw Horizontal Alignment.\nLeft-click to add vertices.\n'
            'Right-click or double-click to finish.\n'
            'Ctrl+Z to undo the last vertex.'
        )
        self.btn_terrain_draw.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_draw.clicked.connect(self._toggle_terrain_digitizing)
        fillet_row.addWidget(self.btn_terrain_draw)

        self.btn_terrain_import = QPushButton('Import Line')
        self.btn_terrain_import.setToolTip(
            'Import an existing line (shapefile or GeoPackage) as the\n'
            'horizontal alignment, instead of drawing one by hand.\n'
            'Only the first feature in the file is used. Vertex editing,\n'
            'fillets, etc. all work exactly the same afterwards as a\n'
            'hand-drawn line.'
        )
        self.btn_terrain_import.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_import.clicked.connect(self._on_terrain_import_line)
        fillet_row.addWidget(self.btn_terrain_import)

        # Applies the Fillet radius field's value to EVERY vertex at
        # once, uniformly — replaces the old 'raise to at least the
        # channel's own half-width' behaviour per explicit request; that
        # narrower half-width check still runs (see
        # _terrain_check_tight_bends, surfaced as a warning in Create
        # TIFF), it just no longer has its own dedicated one-click fix
        # button — widening the Fillet radius field and clicking this
        # works as the replacement for that case too.
        self.btn_terrain_set_global_radius = QPushButton('Set Global Radius')
        self.btn_terrain_set_global_radius.setToolTip(
            "Sets every bend's fillet radius to the Fillet radius value\n"
            'above, uniformly — replacing any individual bend radius\n'
            'already set via Edit Vertices (asks to confirm first).\n'
            'Individual bends can still be overridden again afterwards\n'
            'the normal way: select one with Edit Vertices, then adjust\n'
            'the radius above.'
        )
        self.btn_terrain_set_global_radius.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_set_global_radius.clicked.connect(self._on_terrain_set_global_radius)
        fillet_row.addWidget(self.btn_terrain_set_global_radius)

        self.btn_terrain_vertex_edit = QPushButton('Edit Vertices')
        self.btn_terrain_vertex_edit.setCheckable(True)
        self.btn_terrain_vertex_edit.setToolTip(
            'Click a vertex on the map to select it, then set its own\n'
            'fillet radius above (click the same vertex again to deselect).\n'
            'Drag a vertex to move it — including the two ends, which\n'
            'extends or shortens the alignment. Right-click a vertex to\n'
            'remove it. Break points and control sections stay attached\n'
            'to the same real-world spot as the alignment changes shape.'
        )
        self.btn_terrain_vertex_edit.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_vertex_edit.clicked.connect(self._toggle_terrain_vertex_edit)
        fillet_row.addWidget(self.btn_terrain_vertex_edit)

        self.btn_terrain_vertex_reset = QPushButton('Reset')
        self.btn_terrain_vertex_reset.setFixedWidth(48)
        self.btn_terrain_vertex_reset.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_terrain_vertex_reset.setToolTip("Clear this vertex's override — use the default radius again")
        self.btn_terrain_vertex_reset.clicked.connect(self._on_terrain_vertex_reset)
        self.btn_terrain_vertex_reset.setVisible(False)
        fillet_row.addWidget(self.btn_terrain_vertex_reset)
        lh.addLayout(fillet_row)

        chainage_row = QHBoxLayout()
        chainage_row.setSpacing(4)
        chainage_row.addWidget(QLabel('Chainage:'))
        self.chk_terrain_major_ch = QCheckBox('Major')
        self.chk_terrain_major_ch.setChecked(self._terrain_show_major_ch)
        self.chk_terrain_major_ch.stateChanged.connect(self._on_terrain_chainage_settings_changed)
        chainage_row.addWidget(self.chk_terrain_major_ch)
        self.spin_terrain_major_ch = QDoubleSpinBox()
        self.spin_terrain_major_ch.setRange(1.0, 100000.0)
        self.spin_terrain_major_ch.setValue(self._terrain_major_interval)
        self.spin_terrain_major_ch.setSuffix(' m')
        self.spin_terrain_major_ch.setFixedWidth(80)
        self.spin_terrain_major_ch.valueChanged.connect(self._on_terrain_chainage_settings_changed)
        chainage_row.addWidget(self.spin_terrain_major_ch)

        self.chk_terrain_minor_ch = QCheckBox('Minor')
        self.chk_terrain_minor_ch.setChecked(self._terrain_show_minor_ch)
        self.chk_terrain_minor_ch.stateChanged.connect(self._on_terrain_chainage_settings_changed)
        chainage_row.addWidget(self.chk_terrain_minor_ch)
        self.spin_terrain_minor_ch = QDoubleSpinBox()
        self.spin_terrain_minor_ch.setRange(0.5, 100000.0)
        self.spin_terrain_minor_ch.setValue(self._terrain_minor_interval)
        self.spin_terrain_minor_ch.setSuffix(' m')
        self.spin_terrain_minor_ch.setFixedWidth(80)
        self.spin_terrain_minor_ch.valueChanged.connect(self._on_terrain_chainage_settings_changed)
        chainage_row.addWidget(self.spin_terrain_minor_ch)
        chainage_row.addStretch()
        lh.addLayout(chainage_row)

        lv.addSpacing(8)
        lv.addWidget(_section_label('Vertical Alignment'))

        lv.addWidget(_section_label('Raster Layers (DEM)', _raster_type_pix(12)))
        # Only ever ONE ground DEM here (unlike Layer Profile's multi-layer
        # list) — a single fixed row, no add/remove — but the same style
        # controls (visibility/linestyle/width/opacity/colour) as Layer
        # Profile's own raster rows, in the same row, since those are
        # still genuinely useful for a single line too.
        raster_row = QHBoxLayout()
        raster_row.setSpacing(3)

        _terrain_raster_tog = QCheckBox()
        _terrain_raster_tog.setChecked(True)
        _terrain_raster_tog.setToolTip('Show/hide this layer on the chart')
        _terrain_raster_tog.stateChanged.connect(self._refresh_plot)
        raster_row.addWidget(_terrain_raster_tog)

        self.combo_terrain_raster = QgsMapLayerComboBox()
        self.combo_terrain_raster.setFilters(_RASTER_FILTER)
        self.combo_terrain_raster.setAllowEmptyLayer(True)
        self.combo_terrain_raster.setCurrentIndex(0)
        self.combo_terrain_raster.wheelEvent = lambda e: e.ignore()
        self.combo_terrain_raster.layerChanged.connect(lambda _: self._on_terrain_raster_changed())
        raster_row.addWidget(self.combo_terrain_raster, 1)

        _terrain_raster_ls = QComboBox()
        _terrain_raster_ls.setFixedWidth(80)
        for _code, _label in _LINESTYLES:
            _terrain_raster_ls.addItem(_label, _code)
        # Default to Dash-dot / 1.0 / 75% / black — the DEM ground line
        # reads clearest against the design/invert lines at this style,
        # per explicit request, rather than the generic "first option"/
        # auto-assigned-colour defaults every other raster row gets.
        _terrain_raster_ls.setCurrentIndex(max(0, _terrain_raster_ls.findData('-.')))
        _terrain_raster_ls.currentIndexChanged.connect(self._refresh_plot)
        _terrain_raster_ls.wheelEvent = lambda e: e.ignore()
        raster_row.addWidget(_terrain_raster_ls)

        _terrain_raster_lw = QDoubleSpinBox()
        _terrain_raster_lw.setRange(0.5, 5.0)
        _terrain_raster_lw.setValue(1.0)
        _terrain_raster_lw.setSingleStep(0.5)
        _terrain_raster_lw.setDecimals(1)
        _terrain_raster_lw.setFixedWidth(48)
        _terrain_raster_lw.setToolTip('Line width')
        _terrain_raster_lw.valueChanged.connect(self._refresh_plot)
        raster_row.addWidget(_terrain_raster_lw)

        _terrain_raster_al = QSpinBox()
        _terrain_raster_al.setRange(10, 100)
        _terrain_raster_al.setValue(75)
        _terrain_raster_al.setSuffix('%')
        _terrain_raster_al.setFixedWidth(52)
        _terrain_raster_al.setToolTip('Opacity')
        _terrain_raster_al.valueChanged.connect(self._refresh_plot)
        raster_row.addWidget(_terrain_raster_al)

        _terrain_raster_row_dict = {
            '_col': None, 'layer_combo': self.combo_terrain_raster,
            'toggle': _terrain_raster_tog, 'ls_combo': _terrain_raster_ls,
            'lw_spin': _terrain_raster_lw, 'al_spin': _terrain_raster_al,
            'color': QColor('#000000'),
        }
        _terrain_raster_c_btn = _color_btn(
            _terrain_raster_row_dict['color'].name(), 'Change colour')
        _terrain_raster_c_btn.clicked.connect(
            lambda: self._pick_raster_color(_terrain_raster_row_dict, self._refresh_plot))
        _terrain_raster_row_dict['color_btn'] = _terrain_raster_c_btn
        raster_row.addWidget(_terrain_raster_c_btn)

        self._terrain_raster_rows = [_terrain_raster_row_dict]
        lv.addLayout(raster_row)
        lv.addSpacing(4)

        def _flow_pair(label_text, widget):
            """A label glued tightly to its own field, as ONE widget — so
            a FlowLayout wrapping this row never separates a label from
            the field it describes onto different lines."""
            pair = QWidget()
            hb = QHBoxLayout(pair)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.setSpacing(4)
            hb.addWidget(QLabel(label_text))
            hb.addWidget(widget)
            return pair

        # FlowLayout, not QHBoxLayout — this row has 5 fairly wide
        # controls (Chart, two level fields, two mode-toggle buttons) that
        # don't all fit once the dock is narrowed to give the map canvas
        # more room; wrapping onto a second line keeps every control
        # visible and at its own real size, rather than clipping some of
        # them or requiring horizontal scrolling to reach them.
        vert_row = FlowLayout(h_spacing=10, v_spacing=6)
        # Own dedicated Chart button — fits the Vertical Alignment
        # workflow better here (levels -> breaks/control sections -> view
        # the chart) than the shared bottom row's Refresh/Chart/Clear,
        # which hides its own Chart button on this tab in favour of this
        # one (see _on_tab_changed).
        self.btn_terrain_chart = QPushButton('Profile')
        self.btn_terrain_chart.setToolTip('Show / Hide Chart')
        self.btn_terrain_chart.clicked.connect(self._on_open_chart)
        vert_row.addWidget(self.btn_terrain_chart)

        self.spin_terrain_us_level = QDoubleSpinBox()
        self.spin_terrain_us_level.setRange(-9999.0, 9999.0)
        self.spin_terrain_us_level.setDecimals(3)
        self.spin_terrain_us_level.setSuffix(' m')
        self.spin_terrain_us_level.setFixedWidth(90)
        self.spin_terrain_us_level.setToolTip(
            'Channel invert level at chainage 0 (upstream end).\n'
            'Auto-filled from the ground profile once a DEM is added —\n'
            'change it, or drag the purple triangle on the chart, to set\n'
            'the design invert instead.'
        )
        self.spin_terrain_us_level.valueChanged.connect(self._on_terrain_invert_levels_changed)
        vert_row.addWidget(_flow_pair('US Invert:', self.spin_terrain_us_level))

        self.spin_terrain_ds_level = QDoubleSpinBox()
        self.spin_terrain_ds_level.setRange(-9999.0, 9999.0)
        self.spin_terrain_ds_level.setDecimals(3)
        self.spin_terrain_ds_level.setSuffix(' m')
        self.spin_terrain_ds_level.setFixedWidth(90)
        self.spin_terrain_ds_level.setToolTip(
            'Channel invert level at the downstream end (end chainage).\n'
            'Auto-filled from the ground profile once a DEM is added —\n'
            'change it, or drag the purple triangle on the chart, to set\n'
            'the design invert instead.'
        )
        self.spin_terrain_ds_level.valueChanged.connect(self._on_terrain_invert_levels_changed)
        vert_row.addWidget(_flow_pair('DS Invert:', self.spin_terrain_ds_level))

        # Own dedicated button, moved here from the shared bottom row —
        # deliberately a separate tool from the generic chart 'cursor'
        # sketch tool (which drops a named reference line) so the two
        # never get confused.
        self.btn_terrain_break = QPushButton('Vertical Break')
        self.btn_terrain_break.setCheckable(True)
        self.btn_terrain_break.setToolTip(
            'Click along the channel invert line in the chart to add a\n'
            'vertical break point (a change of slope).\n'
            'Existing break points can always be dragged (left) or\n'
            'removed (right-click) regardless of this tool\'s state.\n'
            'With this OFF, click a level or slope % label instead to\n'
            'type an exact value.'
        )
        self.btn_terrain_break.clicked.connect(self._toggle_terrain_break_tool)
        vert_row.addWidget(self.btn_terrain_break)

        # Places a CONTROL SECTION — a lightweight station with its own
        # full cross-section (edited in the SAME grid below as a break
        # point's), used to steer the Check Cross-Section reach-
        # interpolation blend between real stations, for both numeric
        # params AND Topo Adjustment (see _terrain_control_sections /
        # _terrain_xs_all_anchor_list). Unlike a break point: no level of
        # its own — always reads the invert's own level at wherever its
        # chainage currently is — but IS draggable along chainage, per
        # explicit request.
        self.btn_terrain_control = QPushButton('Control Section')
        self.btn_terrain_control.setCheckable(True)
        self.btn_terrain_control.setToolTip(
            'Click along the channel invert line to add a control section\n'
            '— a station with its own full cross-section (edit it in the\n'
            'grid below, same as a break point) used to steer the\n'
            'interpolated blend between two real stations, for both\n'
            'widths/slopes AND Topo Adjustment. Unlike a break point this\n'
            'has no level of its own — only affects the interpolated\n'
            'preview / eventual TIFF, not the real alignment geometry.\n'
            'Existing control sections can always be clicked (select),\n'
            'dragged along the channel (chainage only — its level always\n'
            'just follows the invert) or removed (right-click), regardless\n'
            'of this tool\'s state.'
        )
        self.btn_terrain_control.clicked.connect(self._toggle_terrain_control_tool)
        vert_row.addWidget(self.btn_terrain_control)
        lv.addLayout(vert_row)

        # Bank Line, Cut Depth and Interval share ONE row — Bank Line
        # (a planning aid, works before any break points/design details
        # are set) comes first, then Cut Depth once the design invert is
        # being refined, then the shared sampling Interval.
        bank_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.chk_terrain_bank_analysis = QCheckBox('Bank Line')
        self.chk_terrain_bank_analysis.setToolTip(
            'Adds two lines to the chart above showing the REAL existing\n'
            'ground at the point each side\'s inner slope would\n'
            'reach at that height. Where the real ground already sits near\n'
            'or above the design line, Fixed Channel Height fits naturally;\n'
            'well below it, Flush with Ground fits better — the crossover\n'
            'points are good places to add a Control Section.'
        )
        self.chk_terrain_bank_analysis.stateChanged.connect(self._on_terrain_bank_analysis_changed)
        bank_row.addWidget(self.chk_terrain_bank_analysis)

        self.chk_terrain_diff = QCheckBox('Cut Depth [m]')
        self.chk_terrain_diff.setToolTip(
            'Shows Ground minus Invert (Y2 - Y1) at the cursor on hover.\n'
            'Y1 is always the channel invert; Y2 is the raster picked here.\n'
            'Positive = dig this much; negative = invert is above ground (fill).'
        )
        self.chk_terrain_diff.stateChanged.connect(self._on_terrain_diff_changed)
        bank_row.addWidget(self.chk_terrain_diff)

        self.combo_terrain_diff_col = QComboBox()
        self.combo_terrain_diff_col.setFixedWidth(120)
        self.combo_terrain_diff_col.wheelEvent = lambda e: e.ignore()
        self.combo_terrain_diff_col.currentIndexChanged.connect(self._on_terrain_diff_changed)
        bank_row.addWidget(_flow_pair('vs:', self.combo_terrain_diff_col))

        self.spin_terrain_interval = QDoubleSpinBox()
        self.spin_terrain_interval.setRange(0.001, 100000.0)
        self.spin_terrain_interval.setValue(1.0)
        self.spin_terrain_interval.setDecimals(3)
        self.spin_terrain_interval.setSuffix(' m')
        self.spin_terrain_interval.setFixedWidth(80)
        self.spin_terrain_interval.setToolTip(
            'Sampling interval along the alignment and cross-sections\n'
            'on this tab (own setting — independent of Layer Profile\'s).'
        )
        bank_row.addWidget(_flow_pair('Interval:', self.spin_terrain_interval))
        lv.addLayout(bank_row)

        lv.addSpacing(8)
        la.addStretch()

        lx.addWidget(_section_label('Cross-Section'))

        self.lbl_terrain_xs_status = QLabel(
            'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
        self.lbl_terrain_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_terrain_xs_status.setWordWrap(True)
        lx.addWidget(self.lbl_terrain_xs_status)

        # Three columns (Left half / Both halves / Right half) so each
        # side of the channel can have its own slopes, berm and ground-
        # fitting option — only Bottom width and Channel height stay
        # shared between the two sides.
        def _xs_cell(label_text, widget):
            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0); v.setSpacing(1)
            lbl = QLabel(label_text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet('font-size:9px;color:#555;')
            v.addWidget(lbl)
            v.addWidget(widget)
            return cell

        # One shared width for every field in this grid (slope/width spins
        # AND the option combos) so all three columns line up edge-to-edge
        # instead of the combos (sized to fit 'Fixed Channel Height') and
        # the spinboxes ending up visibly different widths.
        _XS_FIELD_WIDTH = 132

        def _xs_slope_spin():
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 20.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setSuffix(' :1')
            spin.setFixedWidth(_XS_FIELD_WIDTH)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._on_terrain_xs_params_changed)
            return spin

        def _xs_width_spin(minimum=0.0):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, 500.0)
            spin.setDecimals(2)
            spin.setSuffix(' m')
            spin.setFixedWidth(_XS_FIELD_WIDTH)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._on_terrain_xs_params_changed)
            return spin

        def _xs_option_combo():
            combo = QComboBox()
            combo.addItem('Flush with Ground', 'flush')
            combo.addItem('Fixed Channel Height', 'fixed')
            combo.setFixedWidth(_XS_FIELD_WIDTH)
            combo.setEnabled(False)
            combo.wheelEvent = lambda e: e.ignore()
            combo.currentIndexChanged.connect(self._on_terrain_xs_params_changed)
            return combo

        # Four independent, FIXED-WIDTH column widgets in a plain row,
        # rather than a QGridLayout with column-stretch/minimum-width —
        # a grid's leftover space gets split by Qt's own stretch
        # heuristics, which (even with every column's stretch and minimum
        # width set equal) still ended up giving the Earthwork column a
        # visibly different width/gap in practice. Fixing each column's
        # width outright and giving the row ONE fixed inter-column spacing
        # removes that ambiguity entirely — there's no leftover space left
        # for Qt to distribute unevenly, any extra room just becomes
        # trailing blank space after the last column instead of
        # stretching a gap somewhere in the middle.
        def _xs_column(header_text, header_color):
            col = QWidget()
            col.setFixedWidth(_XS_FIELD_WIDTH)
            vb = QVBoxLayout(col)
            vb.setContentsMargins(0, 0, 0, 0)
            vb.setSpacing(4)
            hdr = QLabel(header_text)
            hdr.setStyleSheet(f'font-weight:bold;font-size:10px;color:{header_color};')
            vb.addWidget(hdr)
            return col, vb

        col_left,  vb_left  = _xs_column('Left half', '#6A1B9A')
        col_both,  vb_both  = _xs_column('Both', '#455A64')
        col_right, vb_right = _xs_column('Right half', '#6A1B9A')
        col_earth, vb_earth = _xs_column('Earthwork', '#6D4C41')

        self.spin_terrain_xs_left_inner = _xs_slope_spin()
        self.spin_terrain_xs_right_inner = _xs_slope_spin()
        vb_left.addWidget(_xs_cell('Channel side slope (H:V)', self.spin_terrain_xs_left_inner))
        vb_right.addWidget(_xs_cell('Channel side slope (H:V)', self.spin_terrain_xs_right_inner))

        self.spin_terrain_xs_bottom = _xs_width_spin()
        self.spin_terrain_xs_bottom.setToolTip('Flat channel bottom width — shared by both halves.')
        vb_both.addWidget(_xs_cell('Bottom width', self.spin_terrain_xs_bottom))

        self.spin_terrain_xs_min_embank = _xs_width_spin()
        self.spin_terrain_xs_min_embank.setToolTip(
            'On a side set to Fixed Channel Height: if the actual fill\n'
            "needed (the berm's own level minus real ground at the\n"
            "berm's own midpoint) is SHALLOWER than this, building a\n"
            'separate berm + batter slope for it is not practical —\n'
            'instead the design backfills flat to existing ground\n'
            '(see Backfill Threshold), rather than shaping a proper\n'
            'embankment for a marginal, shallow difference.'
        )
        vb_earth.addWidget(_xs_cell('Min Embankment Height', self.spin_terrain_xs_min_embank))

        self.spin_terrain_xs_left_outer = _xs_slope_spin()
        self.spin_terrain_xs_right_outer = _xs_slope_spin()
        vb_left.addWidget(_xs_cell('Batter side slope (H:V)', self.spin_terrain_xs_left_outer))
        vb_right.addWidget(_xs_cell('Batter side slope (H:V)', self.spin_terrain_xs_right_outer))

        self.spin_terrain_xs_height = _xs_width_spin(minimum=0.01)
        self.spin_terrain_xs_height.setToolTip(
            'Depth of the main channel, invert to top of the channel\n'
            'side slope (i.e. to the berm, or straight to the top if\n'
            'there is no berm) — shared by both halves. Only used on a\n'
            'side set to Fixed Channel Height.'
        )
        vb_both.addWidget(_xs_cell('Channel height', self.spin_terrain_xs_height))

        self.spin_terrain_xs_backfill_thresh = _xs_width_spin()
        self.spin_terrain_xs_backfill_thresh.setToolTip(
            'How far sideways (from where the berm would start) to look\n'
            'for existing ground to backfill flat against, when the\n'
            'required fill is shallower than Min Embankment Height. If\n'
            'ground is not found within this distance, the normal berm +\n'
            'batter slope is built instead.'
        )
        vb_earth.addWidget(_xs_cell('Backfill Threshold', self.spin_terrain_xs_backfill_thresh))

        self.spin_terrain_xs_left_berm = _xs_width_spin()
        self.spin_terrain_xs_right_berm = _xs_width_spin()
        vb_left.addWidget(_xs_cell('Berm width', self.spin_terrain_xs_left_berm))
        vb_right.addWidget(_xs_cell('Berm width', self.spin_terrain_xs_right_berm))

        # Sits right below Backfill Threshold in the Earthwork column —
        # same fixed width and two-line label convention as Copy US
        # Section/Copy Section to All in the middle column.
        self.btn_terrain_check_xs = QPushButton('Check\nCross-Section…')
        self.btn_terrain_check_xs.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_check_xs.setToolTip(
            'Preview how the cross-section is interpolated between two\n'
            'break points along a reach, stepping through it at a chosen\n'
            'spacing — opens in its own window.'
        )
        self.btn_terrain_check_xs.clicked.connect(self._open_terrain_check_xs)
        vb_earth.addWidget(self.btn_terrain_check_xs)
        vb_earth.addStretch()   # Earthwork has one fewer row than the others

        # Copy shortcuts — sit in the otherwise-empty middle column below
        # Channel height, since Berm width/Topo adjustment don't apply to
        # 'Both' halves. Fixed to the same width as every other field in
        # this grid (_XS_FIELD_WIDTH); short single-line labels now that
        # there are four of these instead of two.
        self.btn_terrain_xs_copy_ds = QPushButton('Copy to DS')
        self.btn_terrain_xs_copy_ds.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_copy_ds.setToolTip(
            "Copy the currently selected section's cross-section to every\n"
            'station DOWNSTREAM of it (including control sections) —\n'
            'everything upstream, including this section itself, is left\n'
            'unchanged (asks to confirm first).'
        )
        self.btn_terrain_xs_copy_ds.setEnabled(False)
        self.btn_terrain_xs_copy_ds.clicked.connect(self._on_terrain_xs_copy_ds_all)
        vb_both.addWidget(self.btn_terrain_xs_copy_ds)

        self.btn_terrain_xs_copy_all = QPushButton('Copy to All')
        self.btn_terrain_xs_copy_all.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_copy_all.setToolTip(
            "Copy the currently selected section's cross-section to every\n"
            'other section on this alignment (asks to confirm first).'
        )
        self.btn_terrain_xs_copy_all.setEnabled(False)
        self.btn_terrain_xs_copy_all.clicked.connect(self._on_terrain_xs_copy_all)
        vb_both.addWidget(self.btn_terrain_xs_copy_all)

        # Copy Section / Paste Section — a clipboard pair, distinct from
        # Copy to DS/All (which push THIS station onto others): Copy
        # Section remembers this station's whole cross-section, Paste
        # Section applies it onto whichever station is selected when
        # clicked — so a section can be grabbed and dropped onto one
        # specific, non-adjacent station elsewhere, per explicit request.
        self.btn_terrain_xs_copy_section = QPushButton('Copy Section')
        self.btn_terrain_xs_copy_section.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_copy_section.setToolTip(
            "Remembers this station's whole cross-section (left + both +\n"
            'right) so it can be pasted onto a different station — select\n'
            'that station, then click Paste Section.'
        )
        self.btn_terrain_xs_copy_section.setEnabled(False)
        self.btn_terrain_xs_copy_section.clicked.connect(self._on_terrain_xs_copy_section)
        vb_both.addWidget(self.btn_terrain_xs_copy_section)

        self.btn_terrain_xs_paste_section = QPushButton('Paste Section')
        self.btn_terrain_xs_paste_section.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_paste_section.setToolTip(
            'Applies the section last captured with Copy Section onto the\n'
            'currently selected station, overwriting its current settings.'
        )
        self.btn_terrain_xs_paste_section.setEnabled(False)
        self.btn_terrain_xs_paste_section.clicked.connect(self._on_terrain_xs_paste_section)
        vb_both.addWidget(self.btn_terrain_xs_paste_section)

        self.combo_terrain_xs_left = _xs_option_combo()
        self.combo_terrain_xs_right = _xs_option_combo()
        vb_left.addWidget(_xs_cell('Topo adjustment', self.combo_terrain_xs_left))
        vb_right.addWidget(_xs_cell('Topo adjustment', self.combo_terrain_xs_right))

        # Copy to [other] Half — sits at the bottom of each side's own
        # column, per explicit request so one side's whole shape (both
        # slopes, berm width, Topo adjustment) can be copied onto the
        # other with one click rather than matching every field by hand.
        self.btn_terrain_xs_copy_right = QPushButton('Copy to\nRight Half')
        self.btn_terrain_xs_copy_right.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_copy_right.setToolTip(
            "Copies this station's left half (both slopes, berm width,\n"
            "Topo adjustment) onto the right half, overwriting it\n"
            '(asks to confirm first).'
        )
        self.btn_terrain_xs_copy_right.setEnabled(False)
        self.btn_terrain_xs_copy_right.clicked.connect(self._on_terrain_xs_copy_to_right_half)
        vb_left.addWidget(self.btn_terrain_xs_copy_right)

        self.btn_terrain_xs_copy_left = QPushButton('Copy to\nLeft Half')
        self.btn_terrain_xs_copy_left.setFixedWidth(_XS_FIELD_WIDTH)
        self.btn_terrain_xs_copy_left.setToolTip(
            "Copies this station's right half (both slopes, berm width,\n"
            "Topo adjustment) onto the left half, overwriting it\n"
            '(asks to confirm first).'
        )
        self.btn_terrain_xs_copy_left.setEnabled(False)
        self.btn_terrain_xs_copy_left.clicked.connect(self._on_terrain_xs_copy_to_left_half)
        vb_right.addWidget(self.btn_terrain_xs_copy_left)

        # FlowLayout, not a plain row — each column is a complete, fixed-
        # width block, so when the dock is narrower than all 4 side by
        # side, they wrap to a second line (e.g. 2-and-2) as whole blocks
        # instead of needing horizontal scrolling to reach Earthwork.
        xs_row = FlowLayout(h_spacing=10, v_spacing=8)
        xs_row.addWidget(col_left)
        xs_row.addWidget(col_both)
        xs_row.addWidget(col_right)
        xs_row.addWidget(col_earth)
        lx.addLayout(xs_row)

        # Embedded preview — deliberately INSIDE this dock (unlike the
        # Layer Profile tab's cross-section, which pops out into its own
        # XSectionDialog window) since the brief specifically asked for
        # the section plot to live in the palette itself. The exact shape
        # this draws is a placeholder until the four parameters' precise
        # geometric meaning is confirmed — see _draw_terrain_xs_preview.
        if MATPLOTLIB_AVAILABLE:
            self.figure_terrain_xs = Figure(figsize=(4, 2.0), dpi=100)
            self.ax_terrain_xs = self.figure_terrain_xs.add_subplot(111)
            self.canvas_terrain_xs = FigureCanvas(self.figure_terrain_xs)
            self.canvas_terrain_xs.setMinimumHeight(170)
            # Expanding + a stretch factor when added below — otherwise
            # this widget just sits at its minimum height and any extra
            # room in the dock goes unused as blank space beneath it.
            self.canvas_terrain_xs.setSizePolicy(_EXPAND, _EXPAND)
            self.canvas_terrain_xs.mpl_connect('scroll_event', self._on_terrain_xs_scroll_zoom)
            self.canvas_terrain_xs.mpl_connect('button_press_event', self._on_terrain_xs_press)
            self.canvas_terrain_xs.mpl_connect('motion_notify_event', self._on_terrain_xs_motion)
            self.canvas_terrain_xs.mpl_connect('button_release_event', self._on_terrain_xs_release)
            self.canvas_terrain_xs.mpl_connect('resize_event', self._on_terrain_xs_canvas_resize)
            lx.addWidget(self.canvas_terrain_xs, 1)

            # Floating 'Elev' button OVER the chart itself (top-right
            # corner), not in the surrounding palette — click it on, then
            # click an empty spot on the chart to read its elevation;
            # click again to remove that reading. Same idea as Layer
            # Profile's own elevation tool, kept inside the chart here per
            # explicit request.
            self.btn_terrain_xs_elev = QPushButton('▽', self.canvas_terrain_xs)
            self.btn_terrain_xs_elev.setCheckable(True)
            self.btn_terrain_xs_elev.setFixedSize(26, 26)
            self.btn_terrain_xs_elev.setToolTip(
                'Click, then click a point on the chart to read its\n'
                'elevation — snaps onto the design line or the ground\n'
                'profile, whichever is nearer. Click the chart again to\n'
                'remove it.'
            )
            self.btn_terrain_xs_elev.setStyleSheet(
                'QPushButton{background:rgba(255,255,255,210);border:1px solid #888;'
                'border-radius:13px;font-size:13px;font-weight:bold;color:#7A0000;}'
                'QPushButton:checked{background:#FFB74D;border:1px solid #E65100;}'
            )
            self.btn_terrain_xs_elev.clicked.connect(self._toggle_terrain_xs_elev_tool)
            self.btn_terrain_xs_elev.raise_()

            # Floating 'Add' button right below 'Elev' — picks a raster
            # (defaulting to the last Create-TIFF'd layer) to sample and
            # plot alongside the design/ground lines, for checking the
            # actual generated surface against what it was built from.
            self.btn_terrain_xs_add_compare = QPushButton('+', self.canvas_terrain_xs)
            self.btn_terrain_xs_add_compare.setFixedSize(26, 26)
            self.btn_terrain_xs_add_compare.setToolTip(
                'Pick a raster layer (e.g. a Channel Design TIFF) to\n'
                'sample and plot alongside this preview, to compare it\n'
                'against the design/ground profile.'
            )
            self.btn_terrain_xs_add_compare.setStyleSheet(
                'QPushButton{background:rgba(255,255,255,210);border:1px solid #888;'
                'border-radius:13px;font-size:15px;font-weight:bold;color:#2E7D32;}'
                'QPushButton:hover{background:rgba(255,255,255,255);}'
            )
            self.btn_terrain_xs_add_compare.clicked.connect(self._on_terrain_xs_add_compare)
            self.btn_terrain_xs_add_compare.raise_()

            # Floating 'Z:' factor control in the TOP-LEFT corner (the
            # Elev/Add buttons stay top-right) — vertical exaggeration,
            # same convention as QGIS's own terrain Z factor (1 = true
            # scale). This preview's OWN value, independent of Check
            # Cross-Section's; see _on_terrain_xs_zfactor_changed.
            self.zf_terrain_xs_widget = QWidget(self.canvas_terrain_xs)
            self.zf_terrain_xs_widget.setFixedSize(60, 22)
            self.zf_terrain_xs_widget.setStyleSheet(
                'QWidget{background:rgba(255,255,255,200);border:1px solid #888;border-radius:3px;}'
            )
            _zf_l = QHBoxLayout(self.zf_terrain_xs_widget)
            _zf_l.setContentsMargins(3, 0, 2, 0)
            _zf_l.setSpacing(2)
            _zf_lbl = QLabel('Z:')
            _zf_lbl.setStyleSheet('font-size:9px;border:none;background:transparent;')
            _zf_l.addWidget(_zf_lbl)
            self.spin_terrain_xs_zfactor = QDoubleSpinBox()
            self.spin_terrain_xs_zfactor.setRange(0.1, 100.0)
            self.spin_terrain_xs_zfactor.setValue(self._terrain_xs_z_factor)
            self.spin_terrain_xs_zfactor.setSingleStep(0.5)
            self.spin_terrain_xs_zfactor.setDecimals(1)
            self.spin_terrain_xs_zfactor.setFixedWidth(42)
            self.spin_terrain_xs_zfactor.setStyleSheet(
                'QDoubleSpinBox{font-size:9px;border:none;background:transparent;}')
            self.spin_terrain_xs_zfactor.wheelEvent = lambda e: e.ignore()
            self.spin_terrain_xs_zfactor.setToolTip(
                "Vertical exaggeration — same idea as QGIS's own terrain\n"
                'Z factor. 1 = true scale (1 m vertical = 1 m horizontal\n'
                'on screen); 2 = vertical scale doubled, and so on.\n'
                "This preview's own setting — independent of Check\n"
                "Cross-Section's own Z factor."
            )
            self.spin_terrain_xs_zfactor.valueChanged.connect(self._on_terrain_xs_zfactor_changed)
            _zf_l.addWidget(self.spin_terrain_xs_zfactor)
            self.zf_terrain_xs_widget.raise_()

            self._position_terrain_xs_elev_btn()

            self._draw_terrain_xs_preview()
        else:
            self.canvas_terrain_xs = None

        lx.addStretch()
        self.tabs_terrain_workflow.addTab(tab_a, 'Alignment')
        self.tabs_terrain_workflow.addTab(tab_x, 'Cross-Section')
        l.addWidget(self.tabs_terrain_workflow)

        l.addSpacing(6)
        self.lbl_terrain_status = QLabel('Horizontal alignment: not drawn')
        self.lbl_terrain_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_terrain_status.setWordWrap(True)
        l.addWidget(self.lbl_terrain_status)

        sc.setWidget(inner)
        outer_l.addWidget(sc)
        return tab

    def _terrain_tool_buttons(self):
        """Every one of Channel Surface's own checkable 'mode' tool
        buttons (Draw Line, Edit Vertices, Vertical Break, Control
        Section, the Cross-Section Preview's own spot-elevation tool),
        paired with its own toggle handler — the single list
        _terrain_deactivate_all_tools and _terrain_escape_deactivate_tool
        both walk, so only ever one of these is active at a time. Same
        pattern as _embank_tool_buttons."""
        pairs = [
            (self.btn_terrain_draw, self._toggle_terrain_digitizing),
            (self.btn_terrain_vertex_edit, self._toggle_terrain_vertex_edit),
            (self.btn_terrain_break, self._toggle_terrain_break_tool),
            (self.btn_terrain_control, self._toggle_terrain_control_tool),
        ]
        if getattr(self, 'btn_terrain_xs_elev', None) is not None:
            pairs.append((self.btn_terrain_xs_elev, self._toggle_terrain_xs_elev_tool))
        return pairs

    def _terrain_deactivate_all_tools(self, except_btn=None):
        """Turns off whichever Channel Surface tool button(s) are
        currently checked, other than `except_btn` (the one just turned
        ON, when this is called from inside that button's own toggle
        handler to clear everyone else first). Also the target of
        Escape (see _terrain_escape_deactivate_tool) and of switching
        away to a different top-level tab (see _on_tab_changed) —
        calling this with no argument turns everything off."""
        for btn, toggle_fn in self._terrain_tool_buttons():
            if btn is except_btn or not btn.isChecked():
                continue
            btn.setChecked(False)
            toggle_fn(False)

    def _terrain_escape_deactivate_tool(self):
        """Escape cancels whichever Channel Surface tool is currently
        active — same effect as clicking that tool's own button again.
        A no-op when nothing's active, so this can be bound broadly
        without worrying about stealing Escape from something
        unrelated."""
        if self._active_tab == _TAB_CHANNEL:
            self._terrain_deactivate_all_tools()

    def _toggle_terrain_digitizing(self, checked):
        if checked:
            self._terrain_deactivate_all_tools(except_btn=self.btn_terrain_draw)
            self._prev_tool = self.canvas.mapTool()
            self._terrain_draw_start_extent = self.canvas.extent()
            self._terrain_map_tool.reset()
            self.canvas.setMapTool(self._terrain_map_tool)
            self.lbl_terrain_status.setText('Click to add vertices — right-click to finish…')
            self.lbl_terrain_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._terrain_map_tool:
                self.canvas.unsetMapTool(self._terrain_map_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)

    def _on_terrain_import_line(self):
        self._import_alignment_line('Channel', self._on_terrain_line_captured)

    def _import_alignment_line(self, surface_label, captured_callback):
        """Shared 'Import Line' handler for Channel Surface and Embankment
        Surface — lets the user pick an existing line shapefile or
        GeoPackage layer instead of hand-drawing the horizontal alignment.
        Only the first feature is used; the resulting geometry is fed into
        captured_callback (_on_terrain_line_captured / _on_embank_line_
        captured) — the exact same pipeline a hand-drawn line goes
        through, so vertex editing/fillets/etc. all work identically
        afterwards."""
        path, _ = QFileDialog.getOpenFileName(
            self, f'Import {surface_label} Horizontal Alignment', '',
            'Vector Files (*.shp *.gpkg);;All Files (*)')
        if not path:
            return

        try:
            sublayers = QgsProviderRegistry.instance().querySublayers(path)
        except Exception as exc:
            QMessageBox.warning(self, 'Advanced Profile Tool', f'Could not read file:\n{exc}')
            return

        line_subs = [
            s for s in sublayers
            if s.type() == Qgis.LayerType.Vector
            and QgsWkbTypes.geometryType(s.wkbType()) == QgsWkbTypes.LineGeometry
        ]
        if not line_subs:
            QMessageBox.warning(self, 'Advanced Profile Tool', f'No line layers found in:\n{path}')
            return

        if len(line_subs) == 1:
            chosen = line_subs[0]
        else:
            names = [s.name() for s in line_subs]
            name, ok = QInputDialog.getItem(
                self, 'Select Layer',
                'This file has multiple line layers — pick one:',
                names, 0, False)
            if not ok:
                return
            chosen = line_subs[names.index(name)]

        layer = QgsVectorLayer(chosen.uri(), chosen.name(), chosen.providerKey())
        if not layer.isValid():
            QMessageBox.warning(self, 'Advanced Profile Tool', f'Could not open layer from:\n{path}')
            return

        feats = list(layer.getFeatures())
        if not feats:
            QMessageBox.warning(self, 'Advanced Profile Tool', 'The selected layer has no features.')
            return
        if len(feats) > 1:
            reply = QMessageBox.question(
                self, 'Advanced Profile Tool',
                f'This layer has {len(feats)} features — only the first one will be\n'
                'used as the horizontal alignment. Continue?',
                _MB_YES | _MB_NO)
            if reply != _MB_YES:
                return

        geom = feats[0].geometry()
        if geom is None or geom.isEmpty():
            QMessageBox.warning(self, 'Advanced Profile Tool', 'The first feature has no geometry.')
            return

        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and layer.crs() != canvas_crs:
            try:
                xform = QgsCoordinateTransform(layer.crs(), canvas_crs, QgsProject.instance())
                geom.transform(xform)
            except Exception as exc:
                QMessageBox.warning(self, 'Advanced Profile Tool',
                                     f'Could not reproject the imported line:\n{exc}')
                return

        captured_callback(geom)

    def _on_terrain_line_captured(self, geom):
        if geom.isMultipart():
            parts = geom.asMultiPolyline()
            pts = parts[0] if parts else []
        else:
            pts = geom.asPolyline()
        self._terrain_raw_pts = [QgsPointXY(p) for p in pts]
        # A freshly-drawn alignment has all-new vertex indices and a new
        # chainage range — any previous per-vertex fillet overrides,
        # vertical break points, and end levels (tied to the OLD line's
        # geometry) no longer mean anything. Levels reset to None so they
        # re-seed from the new ground profile on the next refresh.
        self._terrain_vertex_radii = {}
        self._terrain_selected_vertex = None
        self._terrain_invert_breaks = []
        self._terrain_us_level = None
        self._terrain_ds_level = None
        self._terrain_break_drag_obj = None
        self._terrain_control_drag_obj = None
        self._terrain_endpoint_drag = None
        self._terrain_xs_params = {}
        self._terrain_xs_clipboard = None
        self._terrain_control_sections = []
        self._terrain_selected_xs_key = None
        self._terrain_xs_ground = None
        if hasattr(self, 'lbl_terrain_xs_status'):
            for spin in (self.spin_terrain_xs_bottom, self.spin_terrain_xs_height,
                         self.spin_terrain_xs_left_inner, self.spin_terrain_xs_left_outer,
                         self.spin_terrain_xs_left_berm, self.combo_terrain_xs_left,
                         self.spin_terrain_xs_right_inner, self.spin_terrain_xs_right_outer,
                         self.spin_terrain_xs_right_berm, self.combo_terrain_xs_right,
                         self.spin_terrain_xs_min_embank, self.spin_terrain_xs_backfill_thresh,
                         self.btn_terrain_xs_copy_ds, self.btn_terrain_xs_copy_all,
                         self.btn_terrain_xs_copy_section, self.btn_terrain_xs_paste_section,
                         self.btn_terrain_xs_copy_right, self.btn_terrain_xs_copy_left):
                spin.setEnabled(False)
            self.lbl_terrain_xs_status.setText(
                'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
            self.lbl_terrain_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            self._draw_terrain_xs_preview()
        if hasattr(self, 'spin_terrain_us_level'):
            self.spin_terrain_us_level.blockSignals(True)
            self.spin_terrain_us_level.setValue(0.0)
            self.spin_terrain_us_level.blockSignals(False)
            self.spin_terrain_ds_level.blockSignals(True)
            self.spin_terrain_ds_level.setValue(0.0)
            self.spin_terrain_ds_level.blockSignals(False)
        if self.btn_terrain_break.isChecked():
            self.btn_terrain_break.setChecked(False)
            self._toggle_terrain_break_tool(False)
        if self.btn_terrain_control.isChecked():
            self.btn_terrain_control.setChecked(False)
            self._toggle_terrain_control_tool(False)
        self.btn_terrain_draw.setChecked(False)
        self._toggle_terrain_digitizing(False)
        # Deferred (QTimer.singleShot) rather than called directly: this
        # handler runs synchronously INSIDE the map canvas's own
        # double-click dispatch (ProfileLineTool.canvasDoubleClickEvent ->
        # _finalise -> line_completed, a direct same-thread connection),
        # and _rebuild_terrain_alignment() flows into
        # _refresh_terrain_profile(), which calls
        # QApplication.processEvents() before a potentially slow raster
        # extract — doing that while still inside the canvas's own event
        # handler risks Qt processing queued events re-entrantly mid-
        # gesture. Deferring lets the double-click event finish cleanly
        # first.
        #
        # Snapshot-and-restore the canvas extent around it as a defensive
        # belt-and-braces measure on top of that: nothing in this plugin
        # ever calls setExtent/zoomTo/setCenter anywhere (checked), so
        # whatever nudges the extent right at this moment is an indirect
        # QGIS-side effect (most likely the dock's own layout changing
        # size — new status text, spinboxes enabling — causing the canvas
        # WIDGET to resize, which QGIS then reads as 'show more/less area
        # at the new aspect ratio', i.e. a zoom nobody asked for) rather
        # than anything this code explicitly requests. Restored extent is
        # the one from BEFORE 'Draw Line' was even pressed (captured in
        # _toggle_terrain_digitizing), per explicit request to end up
        # exactly where the user started rather than wherever it happened
        # to be at the moment of the final double-click — any drift that
        # crept in gradually while adding vertices gets undone too, not
        # just drift from the finishing click itself.
        restore_extent = self._terrain_draw_start_extent

        def _rebuild_and_restore_extent():
            self._rebuild_terrain_alignment()
            if restore_extent is not None:
                self.canvas.setExtent(restore_extent)
                self.canvas.refresh()

        QTimer.singleShot(0, _rebuild_and_restore_extent)
        self._update_terrain_fillet_ui()

    @staticmethod
    def _fillet_alignment(pts, default_radius, overrides=None):
        """Round sharp interior bends of a polyline with a circular arc.
        Each interior vertex (index i, 1..len(pts)-2) uses overrides.get(i,
        default_radius) as its own radius — so bends can be rounded by
        different amounts instead of one setting for the whole alignment.
        A per-vertex radius <= 0 keeps that corner sharp. Each arc is sized
        to the shorter of its two adjacent segments, so fillets on
        tightly-spaced vertices never overlap or overshoot."""
        overrides = overrides or {}
        if len(pts) < 3:
            return list(pts)
        out = [pts[0]]
        n = len(pts)
        for i in range(1, n - 1):
            radius = overrides.get(i, default_radius)
            prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
            if radius is None or radius <= 1e-6:
                out.append(cur)
                continue
            v1x, v1y = prev.x() - cur.x(), prev.y() - cur.y()
            v2x, v2y = nxt.x() - cur.x(), nxt.y() - cur.y()
            len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if len1 < 1e-9 or len2 < 1e-9:
                out.append(cur)
                continue
            u1x, u1y = v1x / len1, v1y / len1
            u2x, u2y = v2x / len2, v2y / len2
            dot = max(-1.0, min(1.0, u1x * u2x + u1y * u2y))
            theta = math.acos(dot)
            if theta > math.radians(179.0) or theta < 1e-6:
                out.append(cur)  # near-straight — no corner to round
                continue
            half = theta / 2.0
            tan_half = math.tan(half)
            t = radius / tan_half if tan_half > 1e-9 else 0.0
            t = min(t, 0.999 * min(len1, len2))
            r_eff = t * tan_half
            if t < 1e-6 or r_eff < 1e-6:
                out.append(cur)
                continue
            A = QgsPointXY(cur.x() + u1x * t, cur.y() + u1y * t)
            B = QgsPointXY(cur.x() + u2x * t, cur.y() + u2y * t)
            bx, by = u1x + u2x, u1y + u2y
            blen = math.hypot(bx, by)
            if blen < 1e-9:
                out.append(cur)
                continue
            bx, by = bx / blen, by / blen
            d = r_eff / math.sin(half)
            cx, cy = cur.x() + bx * d, cur.y() + by * d
            ang_a = math.atan2(A.y() - cy, A.x() - cx)
            ang_b = math.atan2(B.y() - cy, B.x() - cx)
            da = (ang_b - ang_a) % (2 * math.pi)
            if da > math.pi:
                da -= 2 * math.pi
            n_seg = max(2, int(abs(math.degrees(da)) / 10) + 1)
            out.append(A)
            for s in range(1, n_seg):
                a = ang_a + da * (s / n_seg)
                out.append(QgsPointXY(cx + r_eff * math.cos(a), cy + r_eff * math.sin(a)))
            out.append(B)
        out.append(pts[-1])
        return out

    def _rebuild_terrain_alignment(self):
        if not self._terrain_raw_pts or len(self._terrain_raw_pts) < 2:
            return
        filleted = self._fillet_alignment(self._terrain_raw_pts, self._terrain_fillet_radius,
                                           self._terrain_vertex_radii)
        self._terrain_geom = QgsGeometry.fromPolylineXY(filleted)
        # Mirrors self.profile_geom exactly like the Network Profile tab's
        # concatenated pipe chain does — from here on this behaves like a
        # drawn/selected Layer Profile line, so the existing chart hover
        # cursor (_on_chart_hover) moves the map marker along it without
        # any tab-specific hover logic needing to exist.
        self.profile_geom = self._terrain_geom

        if self._terrain_align_band:
            try: self.canvas.scene().removeItem(self._terrain_align_band)
            except Exception: pass
        self._terrain_align_band = QgsRubberBand(self.canvas, _LINE_GEOM)
        self._terrain_align_band.setColor(QColor(255, 111, 0, 220))
        self._terrain_align_band.setWidth(3)
        self._terrain_align_band.setToGeometry(self._terrain_geom, None)

        length = self._terrain_geom.length()
        self.lbl_terrain_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
        self.lbl_terrain_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

        self._draw_terrain_vertex_markers()
        self._draw_terrain_chainage_marks()
        self._refresh_terrain_profile()

    def _clear_terrain_alignment(self):
        self._close_terrain_inline_editor(commit=False)
        self._terrain_raw_pts = []
        self._terrain_geom = None
        self.profile_geom = None
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
        self._profile_markers        = []
        self._profile_marker_artists = []
        self._clear_marker_map_points()
        self._hide_hover()
        self._terrain_vertex_radii = {}
        self._terrain_selected_vertex = None
        if self.btn_terrain_vertex_edit.isChecked():
            self.btn_terrain_vertex_edit.setChecked(False)
            self._toggle_terrain_vertex_edit(False)
        if self._terrain_align_band:
            try: self.canvas.scene().removeItem(self._terrain_align_band)
            except Exception: pass
            self._terrain_align_band = None
        self._clear_terrain_chainage_marks()
        self._clear_terrain_vertex_markers()
        self._terrain_map_tool.reset()

        # Vertical alignment resets together with the horizontal one — a
        # break point or end level tied to a line that no longer exists
        # has nothing left to mean.
        self._terrain_us_level = None
        self._terrain_ds_level = None
        self._terrain_invert_breaks = []
        self._terrain_ground_col = None
        self._terrain_break_drag_obj = None
        self._terrain_control_drag_obj = None
        self._terrain_endpoint_drag = None
        if hasattr(self, 'btn_terrain_break') and self.btn_terrain_break.isChecked():
            self.btn_terrain_break.setChecked(False)
            self._toggle_terrain_break_tool(False)
        if hasattr(self, 'btn_terrain_control') and self.btn_terrain_control.isChecked():
            self.btn_terrain_control.setChecked(False)
            self._toggle_terrain_control_tool(False)
        if hasattr(self, 'spin_terrain_us_level'):
            self.spin_terrain_us_level.blockSignals(True)
            self.spin_terrain_us_level.setValue(0.0)
            self.spin_terrain_us_level.blockSignals(False)
            self.spin_terrain_ds_level.blockSignals(True)
            self.spin_terrain_ds_level.setValue(0.0)
            self.spin_terrain_ds_level.blockSignals(False)
        self._clear_terrain_break_map_bands()
        self._clear_terrain_xs_map_marker()
        for art in self._terrain_invert_artists:
            try: art.remove()
            except Exception: pass
        self._terrain_invert_artists = []
        self._terrain_diff_ann = None

        self._terrain_xs_params = {}
        self._terrain_xs_clipboard = None
        self._terrain_control_sections = []
        self._terrain_selected_xs_key = None
        self._terrain_xs_ground = None
        if hasattr(self, 'lbl_terrain_xs_status'):
            for spin in (self.spin_terrain_xs_bottom, self.spin_terrain_xs_height,
                         self.spin_terrain_xs_left_inner, self.spin_terrain_xs_left_outer,
                         self.spin_terrain_xs_left_berm, self.combo_terrain_xs_left,
                         self.spin_terrain_xs_right_inner, self.spin_terrain_xs_right_outer,
                         self.spin_terrain_xs_right_berm, self.combo_terrain_xs_right,
                         self.spin_terrain_xs_min_embank, self.spin_terrain_xs_backfill_thresh,
                         self.btn_terrain_xs_copy_ds, self.btn_terrain_xs_copy_all,
                         self.btn_terrain_xs_copy_section, self.btn_terrain_xs_paste_section,
                         self.btn_terrain_xs_copy_right, self.btn_terrain_xs_copy_left):
                spin.setEnabled(False)
            self.lbl_terrain_xs_status.setText(
                'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
            self.lbl_terrain_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            self._draw_terrain_xs_preview()

        if hasattr(self, 'btn_terrain_draw'):
            self.btn_terrain_draw.setChecked(False)
        if hasattr(self, 'lbl_terrain_status'):
            self.lbl_terrain_status.setText('Horizontal alignment: not drawn')
            self.lbl_terrain_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        if hasattr(self, 'spin_terrain_fillet'):
            self._update_terrain_fillet_ui()
        if MATPLOTLIB_AVAILABLE and self._active_tab == _TAB_CHANNEL:
            self._reset_axes()
            self.canvas_plot.draw()
        if self.btn_save_png is not None:
            self.btn_save_png.setEnabled(False)

    def _clear_terrain_chainage_marks(self):
        for _attr in ('_terrain_major_band', '_terrain_minor_band'):
            band = getattr(self, _attr, None)
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
                setattr(self, _attr, None)
        for ann in self._terrain_chainage_annotations:
            self._remove_ann(ann)
        self._terrain_chainage_annotations = []

    def _add_terrain_chainage_label(self, pt, chainage, angle_deg=0.0, color='#B71C1C', target=None, text=None):
        target = self._terrain_chainage_annotations if target is None else target
        label_text = f'{chainage:.0f}' if text is None else text
        try:
            from qgis.core import QgsAnnotationPointTextItem, QgsTextFormat, QgsTextBufferSettings
            from qgis.PyQt.QtGui import QFont, QColor as _QColor
            item = QgsAnnotationPointTextItem(label_text, QgsPointXY(pt))
            try:
                item.setAngle(angle_deg)   # aligns the text with the tick mark's own direction
            except Exception:
                pass
            fmt = QgsTextFormat()
            font = QFont('Sans Serif', 7); font.setBold(True)
            fmt.setFont(font)
            fmt.setColor(_QColor(color))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True); buf.setSize(1.5)
            buf.setColor(_QColor(255, 255, 255))
            fmt.setBuffer(buf)
            item.setFormat(fmt)
            ann_layer = QgsProject.instance().mainAnnotationLayer()
            item_id = ann_layer.addItem(item)
            target.append((ann_layer, item_id))
        except Exception:
            pass

    def _cleanup_stray_terrain_annotations(self):
        """One-time sweep, run once when this dock is created, for stray
        chainage/break/'X-S' labels left behind in QGIS's own
        mainAnnotationLayer() from a PREVIOUS session. These labels are
        meant to be purely transient UI (they get cleared and redrawn
        constantly as the alignment is edited), but the layer they live
        in is a real, PROJECT-PERSISTED layer — if the project was saved
        while one was on screen, reopening that project brings it back
        even though this freshly-created dock has no memory of it (the
        Python object that could have removed it belonged to a previous
        session). Restarting QGIS does NOT clear these, since they're
        baked into the saved project, not just runtime canvas state.

        Removing them safely (without risking a user's own, unrelated,
        manually-placed QGIS annotations) means never matching on text
        content alone — instead this requires BOTH a matching text
        pattern (purely numeric, or exactly 'X-S') AND this exact
        style/formatting fingerprint (_add_terrain_chainage_label always
        uses the same font/buffer settings for every label it creates),
        which a manually-placed annotation — created through QGIS's own
        Annotation tool, with whatever default styling that applies —
        essentially can't coincidentally match."""
        try:
            ann_layer = QgsProject.instance().mainAnnotationLayer()
            if ann_layer is None:
                return
            items = dict(ann_layer.items())
        except Exception:
            return
        for item_id, item in items.items():
            try:
                text = item.text()
            except Exception:
                continue
            if not (text == 'X-S' or text.strip().lstrip('-').isdigit()):
                continue
            try:
                fmt = item.format()
                font = fmt.font()
                buf = fmt.buffer()
                looks_like_ours = (
                    font.family() == 'Sans Serif' and font.pointSize() == 7
                    and font.bold() and buf.enabled() and abs(buf.size() - 1.5) < 1e-6
                )
            except Exception:
                looks_like_ours = False
            if looks_like_ours:
                try:
                    ann_layer.removeItem(item_id)
                except Exception:
                    pass

    def _terrain_tick_geom(self, d, half_len):
        """Perpendicular tick line + label anchor/angle at chainage d along
        self._terrain_geom — shared by the major/minor chainage marks and
        the vertical-break map markers, so both read the exact same
        alignment direction and label placement/rotation convention."""
        geom = self._terrain_geom
        if geom is None or geom.isEmpty():
            return None
        pt_g = geom.interpolate(d)
        if pt_g is None or pt_g.isEmpty():
            return None
        pt = pt_g.asPoint()
        try:
            ang = geom.interpolateAngle(d)
        except Exception:
            ang = 0.0
        fx, fy = math.sin(ang), math.cos(ang)   # forward dir (bearing, clockwise from N)
        px, py = fy, -fx                        # perpendicular to it — the tick's own direction
        p1 = QgsPointXY(pt.x() + px * half_len, pt.y() + py * half_len)
        p2 = QgsPointXY(pt.x() - px * half_len, pt.y() - py * half_len)
        # Label anchor sits just past the tick's outer end (p1 side,
        # always — consistently the same side of the alignment rather
        # than alternating) instead of on the alignment line itself,
        # and is rotated to run along the tick instead of floating
        # nearby at a fixed horizontal angle.
        label_pt = QgsPointXY(pt.x() + px * (half_len + 2.0), pt.y() + py * (half_len + 2.0))
        tick_angle = math.degrees(math.atan2(py, px)) % 360
        if 90 < tick_angle < 270:
            tick_angle -= 180   # keep the text reading left-to-right, never upside-down
        return QgsGeometry.fromPolylineXY([p1, p2]), label_pt, tick_angle

    def _draw_terrain_chainage_marks(self):
        self._clear_terrain_chainage_marks()
        geom = self._terrain_geom
        if geom is None or geom.isEmpty():
            return
        length = geom.length()
        if length <= 0:
            return

        def _ticks(interval):
            if not interval or interval <= 0:
                return []
            out, d = [], 0.0
            while d <= length + 1e-6:
                out.append(min(d, length))
                d += interval
            return out

        major_list = _ticks(self._terrain_major_interval) if self._terrain_show_major_ch else []
        minor_list = _ticks(self._terrain_minor_interval) if self._terrain_show_minor_ch else []
        # The end chainage always gets a labelled mark, regardless of the
        # Major setting/interval — it rarely lands on a round interval
        # (e.g. a 96.94 m alignment with 20 m majors), but it's the one
        # chainage a modeller always needs to see.
        if not any(abs(d - length) < 1e-6 for d in major_list):
            major_list = major_list + [length]
        major_set  = {round(d, 3) for d in major_list}

        if minor_list:
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(120, 120, 120, 200))
            band.setWidth(1)
            drawn = False
            for d in minor_list:
                if round(d, 3) in major_set:
                    continue
                tg = self._terrain_tick_geom(d, 3.0)
                if tg is None:
                    continue
                band.addGeometry(tg[0], None)
                drawn = True
            if drawn:
                self._terrain_minor_band = band
            else:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass

        if major_list:
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(213, 0, 0, 230))
            band.setWidth(2)
            drawn = False
            for d in major_list:
                tg = self._terrain_tick_geom(d, 6.0)
                if tg is None:
                    continue
                line_geom, label_pt, angle_deg = tg
                band.addGeometry(line_geom, None)
                self._add_terrain_chainage_label(label_pt, d, angle_deg)
                drawn = True
            if drawn:
                self._terrain_major_band = band
            else:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass

    def _on_terrain_fillet_changed(self, value):
        idx = self._terrain_selected_vertex
        if idx is not None:
            self._terrain_vertex_radii[idx] = value
            self.btn_terrain_vertex_reset.setVisible(True)
        else:
            self._terrain_fillet_radius = value
        if self._terrain_raw_pts:
            self._rebuild_terrain_alignment()

    def _on_terrain_vertex_reset(self):
        idx = self._terrain_selected_vertex
        if idx is None:
            return
        self._terrain_vertex_radii.pop(idx, None)
        self._update_terrain_fillet_ui()
        if self._terrain_raw_pts:
            self._rebuild_terrain_alignment()

    def _on_terrain_set_global_radius(self):
        """Sets EVERY vertex's fillet radius to the current Fillet
        radius field's value, uniformly — a literal 'make every bend
        the same' apply, replacing whatever per-vertex overrides Edit
        Vertices set, per explicit request (previously this raised each
        bend to AT LEAST its own channel half-width, which is now just
        what widening the Fillet radius field and clicking this does
        for a tight bend too). Clears every per-vertex override outright
        (self._terrain_vertex_radii = {}) rather than setting each one
        individually, so they all cleanly fall back to the one global
        value. Individual bends can still be overridden again
        afterwards the normal way: select one with Edit Vertices and
        adjust the radius spinbox above."""
        pts = self._terrain_raw_pts
        if self._terrain_geom is None or len(pts) <= 2:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Draw a horizontal alignment with at least one bend first.')
            return
        if not self._terrain_vertex_radii:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Every bend already uses the global fillet radius — nothing to change.')
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f"Set every bend's fillet radius to {self._terrain_fillet_radius:.2f} m "
            '(the current Fillet radius value)?\n'
            'This replaces any individual bend radius already set via Edit Vertices.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        self._terrain_vertex_radii = {}
        self._rebuild_terrain_alignment()
        self._update_terrain_fillet_ui()
        self.lbl_terrain_status.setText(
            f'Set every bend to the global fillet radius ({self._terrain_fillet_radius:.2f} m).')
        self.lbl_terrain_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')

    def _terrain_raw_chainage(self, idx):
        """Distance along the un-filleted alignment up to raw vertex idx —
        an approximate but cheap-to-compute chainage for labelling which
        vertex is currently selected (the actual filleted geometry shifts
        slightly near a rounded corner, but not enough to matter here)."""
        d = 0.0
        pts = self._terrain_raw_pts
        for i in range(1, idx + 1):
            d += math.hypot(pts[i].x() - pts[i - 1].x(), pts[i].y() - pts[i - 1].y())
        return d

    def _update_terrain_fillet_ui(self):
        idx = self._terrain_selected_vertex
        pts = self._terrain_raw_pts
        is_endpoint = idx is not None and (idx == 0 or idx == len(pts) - 1)
        self.spin_terrain_fillet.blockSignals(True)
        if idx is None:
            self.spin_terrain_fillet.setValue(self._terrain_fillet_radius)
            self.lbl_terrain_fillet_tag.setText('(default)')
            self.btn_terrain_vertex_reset.setVisible(False)
            self.spin_terrain_fillet.setEnabled(True)
        elif is_endpoint:
            # The two ends have no corner to round — fillet radius doesn't
            # apply, but they're still a selected vertex you can drag to
            # extend/shorten the alignment or right-click to remove.
            self.lbl_terrain_fillet_tag.setText('(end point — drag to move, right-click to remove)')
            self.btn_terrain_vertex_reset.setVisible(False)
            self.spin_terrain_fillet.setEnabled(False)
        else:
            val = self._terrain_vertex_radii.get(idx, self._terrain_fillet_radius)
            self.spin_terrain_fillet.setValue(val)
            ch = self._terrain_raw_chainage(idx)
            self.lbl_terrain_fillet_tag.setText(f'(vertex @ ~{ch:.1f} m)')
            self.btn_terrain_vertex_reset.setVisible(idx in self._terrain_vertex_radii)
            self.spin_terrain_fillet.setEnabled(True)
        self.spin_terrain_fillet.blockSignals(False)

    def _set_terrain_selected_vertex(self, idx):
        self._terrain_selected_vertex = idx
        self._update_terrain_fillet_ui()
        self._draw_terrain_vertex_markers()

    def _on_terrain_vertex_clicked(self, raw_idx):
        if raw_idx is None:
            self._set_terrain_selected_vertex(None)
            return
        if self._terrain_selected_vertex == raw_idx:
            self._set_terrain_selected_vertex(None)  # click same vertex again -> deselect
        else:
            self._set_terrain_selected_vertex(raw_idx)

    def _terrain_capture_chainage_anchors(self):
        """[(dict, old_world_QgsPointXY), ...] for every break point and
        control section, read off the CURRENT (pre-edit) self._terrain_geom
        — must be called before that geometry is rebuilt. Paired with
        _terrain_reproject_chainage_anchors so a vertex move/delete keeps
        break points/control sections 'attached' to the ground they were
        actually placed over, rather than silently drifting to a
        different physical spot (or past the end of a shortened
        alignment) just because the alignment's own vertices changed."""
        if self._terrain_geom is None:
            return []
        anchors = []
        for b in self._terrain_invert_breaks:
            anchors.append((b, self._terrain_geom.interpolate(b['chainage']).asPoint()))
        for cs in self._terrain_control_sections:
            anchors.append((cs, self._terrain_geom.interpolate(cs['chainage']).asPoint()))
        return anchors

    def _terrain_reproject_chainage_anchors(self, anchors, new_geom):
        """Updates each anchor dict's 'chainage' in place to wherever its
        OLD real-world point now falls on new_geom (nearest point along
        the edited line), clamped to the new length. See
        _terrain_capture_chainage_anchors for why."""
        if not anchors or new_geom is None:
            return
        length = new_geom.length()
        for d, pt in anchors:
            try:
                ch = new_geom.lineLocatePoint(QgsGeometry.fromPointXY(pt))
            except Exception:
                ch = d['chainage']
            d['chainage'] = max(0.0, min(ch, length))
        self._terrain_invert_breaks.sort(key=lambda b: b['chainage'])

    def _terrain_refillet_geom(self):
        """The filleted QgsGeometry self._terrain_raw_pts/_vertex_radii
        would currently produce, WITHOUT touching self._terrain_geom —
        used to re-project chainage anchors against the about-to-be-
        current geometry before _rebuild_terrain_alignment() commits it
        (that method recomputes the identical geometry itself right
        after; the small duplicate fillet cost is cheap and keeps this
        self-contained rather than threading a 'new_geom' through it)."""
        if len(self._terrain_raw_pts) < 2:
            return None
        filleted = self._fillet_alignment(self._terrain_raw_pts, self._terrain_fillet_radius,
                                           self._terrain_vertex_radii)
        return QgsGeometry.fromPolylineXY(filleted)

    def _on_terrain_vertex_moved(self, raw_idx, new_pt):
        if not (0 <= raw_idx < len(self._terrain_raw_pts)):
            return
        anchors = self._terrain_capture_chainage_anchors()
        self._terrain_raw_pts[raw_idx] = new_pt
        self._terrain_reproject_chainage_anchors(anchors, self._terrain_refillet_geom())
        self._rebuild_terrain_alignment()
        self._update_terrain_fillet_ui()
        self._terrain_vertex_tool.set_vertices(self._terrain_raw_pts)

    def _on_terrain_vertex_deleted(self, raw_idx):
        if len(self._terrain_raw_pts) <= 2:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'An alignment needs at least 2 points — this vertex can\'t be removed.')
            return
        if not (0 <= raw_idx < len(self._terrain_raw_pts)):
            return
        anchors = self._terrain_capture_chainage_anchors()
        self._terrain_raw_pts.pop(raw_idx)
        self._terrain_vertex_radii = {
            (k if k < raw_idx else k - 1): v
            for k, v in self._terrain_vertex_radii.items() if k != raw_idx
        }
        if self._terrain_selected_vertex is not None:
            if self._terrain_selected_vertex == raw_idx:
                self._terrain_selected_vertex = None
            elif self._terrain_selected_vertex > raw_idx:
                self._terrain_selected_vertex -= 1
        self._terrain_reproject_chainage_anchors(anchors, self._terrain_refillet_geom())
        self._rebuild_terrain_alignment()
        self._update_terrain_fillet_ui()
        self._terrain_vertex_tool.set_vertices(self._terrain_raw_pts)

    def _toggle_terrain_vertex_edit(self, checked):
        if checked:
            self._terrain_deactivate_all_tools(except_btn=self.btn_terrain_vertex_edit)
            if len(self._terrain_raw_pts) < 2:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    'Draw a horizontal alignment first.')
                self.btn_terrain_vertex_edit.setChecked(False)
                return
            self._terrain_vertex_tool.set_vertices(self._terrain_raw_pts)
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._terrain_vertex_tool)
            self.lbl_terrain_status.setText(
                'Click a vertex to select it (set fillet radius above), drag to move it '
                '(including the two ends — extends/shortens the alignment), or '
                'right-click to remove it.')
            self.lbl_terrain_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._terrain_vertex_tool:
                self.canvas.unsetMapTool(self._terrain_vertex_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)
            self._set_terrain_selected_vertex(None)

    def _clear_terrain_vertex_markers(self):
        for band in self._terrain_vertex_bands:
            try: self.canvas.scene().removeItem(band)
            except Exception: pass
        self._terrain_vertex_bands = []
        if self._terrain_vertex_selected_band is not None:
            try: self.canvas.scene().removeItem(self._terrain_vertex_selected_band)
            except Exception: pass
            self._terrain_vertex_selected_band = None

    def _draw_terrain_vertex_markers(self):
        self._clear_terrain_vertex_markers()
        pts = self._terrain_raw_pts
        if len(pts) < 2:
            return
        for i, pt in enumerate(pts):
            band = QgsRubberBand(self.canvas, _POINT_GEOM)
            has_override = i in self._terrain_vertex_radii
            band.setColor(QColor(255, 152, 0, 200) if has_override else QColor(3, 169, 244, 130))
            band.setIcon(_ICON_CIRCLE)
            band.setIconSize(12)
            band.setToGeometry(QgsGeometry.fromPointXY(pt), None)
            self._terrain_vertex_bands.append(band)

        idx = self._terrain_selected_vertex
        if idx is not None and 0 <= idx < len(pts):
            sel = QgsRubberBand(self.canvas, _POINT_GEOM)
            sel.setColor(QColor(255, 87, 34, 230))
            sel.setIcon(_ICON_BOX)
            sel.setIconSize(18)
            sel.setToGeometry(QgsGeometry.fromPointXY(pts[idx]), None)
            self._terrain_vertex_selected_band = sel

    def _on_terrain_chainage_settings_changed(self):
        self._terrain_major_interval = self.spin_terrain_major_ch.value()
        self._terrain_minor_interval = self.spin_terrain_minor_ch.value()
        self._terrain_show_major_ch  = self.chk_terrain_major_ch.isChecked()
        self._terrain_show_minor_ch  = self.chk_terrain_minor_ch.isChecked()
        if self._terrain_geom is not None:
            self._draw_terrain_chainage_marks()

    def _on_terrain_raster_changed(self):
        if self._terrain_geom is not None:
            self._refresh_terrain_profile()

    def _refresh_terrain_profile(self):
        if self._terrain_geom is None:
            QMessageBox.warning(self, 'Advanced Profile Tool', 'Draw a channel alignment first.')
            return
        raster_entries, _ve, col_meta = self._collect_entries(
            raster_rows=self._terrain_raster_rows, vector_rows=[])
        if not raster_entries:
            self._terrain_ground_col = None
            self._refresh_terrain_diff_combo()
            if MATPLOTLIB_AVAILABLE:
                self._reset_axes()
                self._draw_terrain_vertical_decorations()
            return
        if QgsProject.instance().crs().isGeographic():
            QMessageBox.critical(self, 'Advanced Profile Tool — CRS Error',
                'Project CRS is Geographic (degrees).\n'
                'Switch to a Projected Metric CRS first.')
            return
        self.btn_run.setEnabled(False)
        self.progress.setRange(0, 0)
        self.lbl_status.setText('Refreshing…')
        QApplication.processEvents()
        try:
            profile_data, chainages = self._extract(
                self._terrain_geom, raster_entries, [], self.spin_terrain_interval.value())
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool — Error', str(exc))
            self.btn_run.setEnabled(True)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.lbl_status.setText('Failed — see error dialog.')
            return
        self.lbl_status.setText(f'Done — {len(chainages)} pts, {len(profile_data)} layer(s).')
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

        # First raster added is the ground reference for cut/fill shading.
        # Upstream/downstream levels auto-seed from it (chainage 0 / end)
        # the first time it's available, so the invert line starts
        # somewhere sane instead of both ends defaulting to 0 m — the user
        # can freely override either afterwards.
        self._terrain_ground_col = raster_entries[0][1]
        self._refresh_terrain_diff_combo()
        gvals = [v for v in profile_data.get(self._terrain_ground_col, []) if v is not None]
        if gvals:
            if self._terrain_us_level is None:
                self._terrain_us_level = round(gvals[0], 3)
                self.spin_terrain_us_level.blockSignals(True)
                self.spin_terrain_us_level.setValue(self._terrain_us_level)
                self.spin_terrain_us_level.blockSignals(False)
            if self._terrain_ds_level is None:
                self._terrain_ds_level = round(gvals[-1], 3)
                self.spin_terrain_ds_level.blockSignals(True)
                self.spin_terrain_ds_level.setValue(self._terrain_ds_level)
                self.spin_terrain_ds_level.blockSignals(False)

        if MATPLOTLIB_AVAILABLE:
            self._plot(chainages, profile_data, col_meta)
            self._draw_terrain_vertical_decorations()
            if self.btn_save_png is not None:
                self.btn_save_png.setEnabled(True)
        self.btn_run.setEnabled(True)

    # ---- vertical alignment (channel invert) -----------------------------

    def _terrain_invert_vertex_handles(self):
        """Like _terrain_invert_vertices() but keeps a handle to WHAT each
        vertex actually is — ('us', None), ('break', that break's dict) or
        ('ds', None) — so a typed-in edit (slope or level text click) or a
        drag can write the new value back to the right place. None until
        both end levels are set."""
        if (self._terrain_geom is None or self._terrain_us_level is None
                or self._terrain_ds_level is None):
            return None
        length = self._terrain_geom.length()
        handles = [(0.0, self._terrain_us_level, 'us', None)]
        for b in self._terrain_invert_breaks:
            if 0.0 < b['chainage'] < length:
                handles.append((b['chainage'], b['level'], 'break', b))
        handles.append((length, self._terrain_ds_level, 'ds', None))
        return handles

    def _terrain_invert_vertices(self):
        """Sorted [(chainage, level), ...] for the whole invert polyline —
        chainage 0 at the upstream level, the end chainage at the
        downstream level, with any interior break points (PVIs) in
        between. None until both end levels are set."""
        handles = self._terrain_invert_vertex_handles()
        return None if handles is None else [(h[0], h[1]) for h in handles]

    def _terrain_invert_level_at(self, ch):
        """Plain linear interpolation (no numpy) so this stays usable even
        if matplotlib/numpy aren't available — in practice the break-point
        click handler that calls this can't fire without a chart anyway,
        but there's no reason to depend on numpy for a 2-point lerp."""
        verts = self._terrain_invert_vertices()
        if not verts:
            return 0.0
        if ch <= verts[0][0]:
            return verts[0][1]
        for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
            if x0 <= ch <= x1:
                if x1 == x0:
                    return y1
                return y0 + (ch - x0) / (x1 - x0) * (y1 - y0)
        return verts[-1][1]

    # ---- Cross-Section (per vertical-alignment NODE) -----------------------
    #
    # Cross-sections are defined at each STATION — the US end, every break
    # point, and the DS end — not per reach. A reach between two stations
    # doesn't own a shape of its own; it's whatever eventually gets
    # interpolated between its two bounding node sections (a job for the
    # future raster-generation step, not this preview). This deliberately
    # replaced an earlier per-reach-midpoint design: with one hard-edged
    # shape per reach, two adjacent reaches met at a break point with a
    # visible discontinuity — exactly the "confusing at the junction"
    # problem the user flagged. Node keys are simple and already stable:
    # 'us', a break's own id() (mutated in place, never replaced), or 'ds'.

    @staticmethod
    def _terrain_node_key(handle):
        kind, ref = handle[2], handle[3]
        return id(ref) if kind in ('break', 'control') else kind   # 'us' / <id> / 'ds'

    def _terrain_node_handle(self, key):
        """The (chainage, level, kind, ref) handle for a given REAL station
        key ('us' / a break's id() / 'ds'), or None if it no longer exists
        (e.g. its break was removed). Does NOT resolve control sections —
        see _terrain_any_node_handle for that."""
        if key is None:
            return None
        handles = self._terrain_invert_vertex_handles()
        if handles is None:
            return None
        return next((h for h in handles if self._terrain_node_key(h) == key), None)

    def _terrain_control_handle(self, key):
        """Like _terrain_node_handle but for a CONTROL SECTION's own id()
        — control sections aren't part of the invert's real vertex handles
        (no level/geometry of their own), so they need their own lookup,
        deriving 'level' live from the invert rather than storing one."""
        for cs in self._terrain_control_sections:
            if id(cs) == key:
                ch = cs['chainage']
                return (ch, self._terrain_invert_level_at(ch), 'control', cs)
        return None

    def _terrain_any_node_handle(self, key):
        """A REAL station's handle, or (if not one) a control section's —
        the combined lookup used everywhere the Cross-Section editor needs
        to resolve 'whatever is currently selected', since a control
        section shares the exact same selection/editing machinery as a
        real station despite not being one."""
        handle = self._terrain_node_handle(key)
        return handle if handle is not None else self._terrain_control_handle(key)

    def _terrain_selected_node_position(self):
        """(chainage, level) for whichever node self._terrain_selected_xs_key
        currently refers to, or None."""
        handle = self._terrain_any_node_handle(self._terrain_selected_xs_key)
        return None if handle is None else (handle[0], handle[1])

    @staticmethod
    def _terrain_xs_half_width(params):
        """Wide enough to show the whole design shape plus a bit of
        ground context either side — recomputed from the station's own
        parameters rather than a fixed/user-set extent, since there's no
        separate extent control on this embedded (dialog-free) preview.
        A 'flush' side searches outward until it meets the ground, which
        can run wider than the fixed-height shape alone would suggest, so
        it gets extra headroom to search within. A Fixed side's Earthwork
        backfill check also searches sideways up to Backfill Threshold
        past the berm's own inner edge, so that needs to be within the
        sampled extent too, or the backfill search could hit the edge of
        the sampled ground and give up prematurely even where real
        (unsampled) ground further out would have worked."""
        half_bw = params['bottom_width'] / 2.0
        height  = params['channel_height']
        backfill_thresh = params.get('backfill_threshold', 0.0)
        left_run  = half_bw + params['left_inner_slope'] * height \
            + max(params['left_berm_width'] + params['left_outer_slope'] * _TERRAIN_XS_OUTER_RISE,
                  backfill_thresh)
        right_run = half_bw + params['right_inner_slope'] * height \
            + max(params['right_berm_width'] + params['right_outer_slope'] * _TERRAIN_XS_OUTER_RISE,
                  backfill_thresh)
        run = max(left_run, right_run)
        if params.get('left_option') == 'flush' or params.get('right_option') == 'flush':
            run += 15.0
        return max(10.0, run + 5.0)

    def _sync_terrain_xs_widgets_from_params(self, params):
        """Push a params dict into the input widgets — shared by selecting
        a new section and by the Copy US Section/Copy to All actions,
        which mutate an already-selected section's own stored params and
        need the on-screen fields to reflect the new values too."""
        for spin, pname in ((self.spin_terrain_xs_bottom, 'bottom_width'),
                             (self.spin_terrain_xs_height, 'channel_height'),
                             (self.spin_terrain_xs_left_inner, 'left_inner_slope'),
                             (self.spin_terrain_xs_left_outer, 'left_outer_slope'),
                             (self.spin_terrain_xs_left_berm, 'left_berm_width'),
                             (self.spin_terrain_xs_right_inner, 'right_inner_slope'),
                             (self.spin_terrain_xs_right_outer, 'right_outer_slope'),
                             (self.spin_terrain_xs_right_berm, 'right_berm_width'),
                             (self.spin_terrain_xs_min_embank, 'min_embankment_height'),
                             (self.spin_terrain_xs_backfill_thresh, 'backfill_threshold')):
            spin.blockSignals(True)
            spin.setValue(params.get(pname, self._terrain_xs_default[pname]))
            spin.blockSignals(False)
            spin.setEnabled(True)
        for combo, pname in ((self.combo_terrain_xs_left, 'left_option'),
                              (self.combo_terrain_xs_right, 'right_option')):
            combo.blockSignals(True)
            idx = combo.findData(params[pname])
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
            combo.setEnabled(True)
        self.btn_terrain_xs_copy_ds.setEnabled(True)
        self.btn_terrain_xs_copy_all.setEnabled(True)
        self.btn_terrain_xs_copy_section.setEnabled(True)
        self.btn_terrain_xs_paste_section.setEnabled(self._terrain_xs_clipboard is not None)
        self.btn_terrain_xs_copy_right.setEnabled(True)
        self.btn_terrain_xs_copy_left.setEnabled(True)

    def _terrain_xs_node_list(self):
        """[(key, chainage), ...] for every station (US end, each break,
        DS end), in upstream-to-downstream order — the ordering Copy US
        Section walks to find 'the previous station'."""
        handles = self._terrain_invert_vertex_handles()
        if handles is None:
            return []
        return [(self._terrain_node_key(h), h[0]) for h in handles]

    def _terrain_xs_all_anchor_list(self):
        """[(key, chainage), ...] combining every REAL station AND every
        CONTROL SECTION, sorted by chainage — the anchor set used to
        interpolate both numeric cross-section params
        (_terrain_xs_interp_params) and Topo Adjustment options
        (_terrain_xs_option_anchors), so a control section can steer
        either, not just the option. Kept separate from
        _terrain_xs_node_list, which stays REAL-station-only since that
        one still defines the actual reaches the Check Cross-Section
        window picks between."""
        anchors = list(self._terrain_xs_node_list())
        anchors.extend((id(cs), cs['chainage']) for cs in self._terrain_control_sections)
        anchors.sort(key=lambda a: a[1])
        return anchors

    def _terrain_xs_reach_list(self):
        """[(label, ch0, ch1), ...] for each reach between two CONSECUTIVE
        stations (US->break, break->break, ..., break->DS) — what the
        Check Cross-Section window's reach picker offers, since a reach
        (not a single station) is what gets interpolated along."""
        nodes = self._terrain_xs_node_list()
        reaches = []
        for i in range(len(nodes) - 1):
            (_k0, ch0), (_k1, ch1) = nodes[i], nodes[i + 1]
            reaches.append((f'Reach {i + 1}:  {ch0:.2f} m → {ch1:.2f} m', ch0, ch1))
        return reaches

    def _terrain_xs_interp_params(self, ch):
        """Cross-section params at an arbitrary chainage ch, linearly
        interpolated between the two anchors (real stations AND control
        sections — see _terrain_xs_all_anchor_list) bounding it — the same
        _terrain_xs_build_shape() is then reused as-is on the result, so
        an interpolated chainage's shape is built exactly like a real
        station's. Numeric params (widths, slopes) blend continuously; the
        categorical Topo Adjustment option can't blend the same way here
        (nearest-anchor-wins) — the real per-side option handling used for
        BUILDING a reach's shape lives in _terrain_xs_build_interpolated_
        shape instead; this nearest-wins value is only used incidentally
        (e.g. to size the ground-sampling extent)."""
        nodes = self._terrain_xs_all_anchor_list()
        if not nodes:
            return dict(self._terrain_xs_default)
        if ch <= nodes[0][1]:
            return dict(self._terrain_xs_params.get(nodes[0][0], self._terrain_xs_default))
        if ch >= nodes[-1][1]:
            return dict(self._terrain_xs_params.get(nodes[-1][0], self._terrain_xs_default))
        for i in range(len(nodes) - 1):
            k0, ch0 = nodes[i]
            k1, ch1 = nodes[i + 1]
            if ch0 <= ch <= ch1:
                p0 = self._terrain_xs_params.get(k0, self._terrain_xs_default)
                p1 = self._terrain_xs_params.get(k1, self._terrain_xs_default)
                t = 0.0 if ch1 == ch0 else (ch - ch0) / (ch1 - ch0)
                out = {}
                for pname in ('bottom_width', 'channel_height',
                              'left_inner_slope', 'left_outer_slope', 'left_berm_width',
                              'right_inner_slope', 'right_outer_slope', 'right_berm_width',
                              'min_embankment_height', 'backfill_threshold'):
                    default = self._terrain_xs_default[pname]
                    out[pname] = p0.get(pname, default) + t * (p1.get(pname, default) - p0.get(pname, default))
                out['left_option']  = p0['left_option']  if t < 0.5 else p1['left_option']
                out['right_option'] = p0['right_option'] if t < 0.5 else p1['right_option']
                return out
        return dict(self._terrain_xs_default)

    def _on_terrain_xs_copy_ds_all(self):
        """Pushes the currently selected station's cross-section onto
        every station DOWNSTREAM of it (real stations AND control
        sections, via the same chainage-sorted anchor list Copy to All
        uses) — everything upstream, including the selected section
        itself, is left untouched. Replaces the old 'Copy US Section'
        (which pulled FROM the upstream neighbour) per explicit request —
        this one pushes forward instead of pulling backward."""
        key = self._terrain_selected_xs_key
        if key is None:
            return
        nodes = self._terrain_xs_all_anchor_list()
        idx = next((i for i, (k, _ch) in enumerate(nodes) if k == key), None)
        if idx is None:
            return
        ds_keys = [k for k, _ch in nodes[idx + 1:]]
        if not ds_keys:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'This is already the most downstream station — there is\n'
                'nothing further downstream to copy to.')
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f'Copy this station\'s cross-section to all {len(ds_keys)} '
            f'station(s)\ndownstream of it (including control sections)?\n'
            'Everything upstream, including this section itself, is left\n'
            'unchanged. This overwrites their current settings.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = dict(self._terrain_xs_params.get(key, self._terrain_xs_default))
        for k in ds_keys:
            self._terrain_xs_params[k] = dict(params)
        self.lbl_terrain_xs_status.setText(
            f'Copied this station to {len(ds_keys)} downstream station(s).')
        self.lbl_terrain_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')
        self._refresh_terrain_xs()

    def _on_terrain_xs_copy_all(self):
        key = self._terrain_selected_xs_key
        if key is None:
            return
        nodes = self._terrain_xs_all_anchor_list()
        other_keys = [k for k, _ch in nodes if k != key]
        if not other_keys:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f'Copy this station\'s cross-section to all {len(other_keys)} other '
            f'station(s) on this alignment?\nThis overwrites their current settings.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = dict(self._terrain_xs_params.get(key, self._terrain_xs_default))
        for k in other_keys:
            self._terrain_xs_params[k] = dict(params)
        self.lbl_terrain_xs_status.setText(f'Copied this station to {len(other_keys)} other station(s).')
        self.lbl_terrain_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')
        self._refresh_terrain_xs()

    def _on_terrain_xs_copy_section(self):
        """Remembers the currently selected station's whole cross-section
        — paired with _on_terrain_xs_paste_section, a clipboard distinct
        from Copy to DS/All (which push THIS station onto OTHERS): this
        pair instead lets one specific station's section be grabbed and
        dropped onto a different, non-adjacent one."""
        key = self._terrain_selected_xs_key
        if key is None:
            return
        self._terrain_xs_clipboard = dict(self._terrain_xs_params.get(key, self._terrain_xs_default))
        self.btn_terrain_xs_paste_section.setEnabled(True)
        self.lbl_terrain_xs_status.setText(
            'Section copied — select another station, then click Paste Section.')
        self.lbl_terrain_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')

    def _on_terrain_xs_paste_section(self):
        key = self._terrain_selected_xs_key
        if key is None or self._terrain_xs_clipboard is None:
            return
        self._terrain_xs_params[key] = dict(self._terrain_xs_clipboard)
        self._sync_terrain_xs_widgets_from_params(self._terrain_xs_params[key])
        self.lbl_terrain_xs_status.setText('Pasted the copied section onto this station.')
        self.lbl_terrain_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')
        self._refresh_terrain_xs()
        self._draw_terrain_vertical_decorations()

    def _on_terrain_xs_copy_to_right_half(self):
        """Copies this station's own left-half fields (both slopes, berm
        width, Topo adjustment) onto its right half — same idea as
        Embankment Surface's Copy to Right/Left Section, adapted to this
        tab's fixed Left/Both/Right/Earthwork grid rather than a
        section-stack."""
        key = self._terrain_selected_xs_key
        if key is None:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            "Copy this station's left half to the right half?\n"
            "This overwrites the right half's current settings.",
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = self._terrain_xs_params.setdefault(key, dict(self._terrain_xs_default))
        params['right_inner_slope'] = params['left_inner_slope']
        params['right_outer_slope'] = params['left_outer_slope']
        params['right_berm_width']  = params['left_berm_width']
        params['right_option']      = params['left_option']
        self._sync_terrain_xs_widgets_from_params(params)
        self._refresh_terrain_xs()
        self._draw_terrain_vertical_decorations()

    def _on_terrain_xs_copy_to_left_half(self):
        key = self._terrain_selected_xs_key
        if key is None:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            "Copy this station's right half to the left half?\n"
            "This overwrites the left half's current settings.",
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = self._terrain_xs_params.setdefault(key, dict(self._terrain_xs_default))
        params['left_inner_slope'] = params['right_inner_slope']
        params['left_outer_slope'] = params['right_outer_slope']
        params['left_berm_width']  = params['right_berm_width']
        params['left_option']      = params['right_option']
        self._sync_terrain_xs_widgets_from_params(params)
        self._refresh_terrain_xs()
        self._draw_terrain_vertical_decorations()

    def _select_terrain_xs_node(self, key):
        handle = self._terrain_any_node_handle(key)
        if handle is None:
            return
        ch, level, kind, _ref = handle
        self._terrain_selected_xs_key = key
        params = self._terrain_xs_params.setdefault(key, dict(self._terrain_xs_default))
        self._sync_terrain_xs_widgets_from_params(params)
        label = {'us': 'Upstream end', 'ds': 'Downstream end',
                 'control': 'Control section'}.get(kind, 'Break point')
        self.lbl_terrain_xs_status.setText(
            f'{label} at chainage {ch:.2f} m (level {level:.3f} m) selected.')
        self.lbl_terrain_xs_status.setStyleSheet('color:#6A1B9A;font-style:italic;font-size:11px;')
        # _refresh_terrain_xs() runs automatically as part of this, now
        # that self._terrain_selected_xs_key is set — see the end of
        # _draw_terrain_vertical_decorations.
        self._draw_terrain_vertical_decorations()

    def _on_terrain_xs_params_changed(self):
        key = self._terrain_selected_xs_key
        if key is None:
            return
        params = self._terrain_xs_params.setdefault(key, dict(self._terrain_xs_default))
        params['bottom_width']       = self.spin_terrain_xs_bottom.value()
        params['channel_height']     = self.spin_terrain_xs_height.value()
        params['left_inner_slope']   = self.spin_terrain_xs_left_inner.value()
        params['left_outer_slope']   = self.spin_terrain_xs_left_outer.value()
        params['left_berm_width']    = self.spin_terrain_xs_left_berm.value()
        params['right_inner_slope']  = self.spin_terrain_xs_right_inner.value()
        params['right_outer_slope']  = self.spin_terrain_xs_right_outer.value()
        params['right_berm_width']   = self.spin_terrain_xs_right_berm.value()
        params['left_option']        = self.combo_terrain_xs_left.currentData()
        params['right_option']       = self.combo_terrain_xs_right.currentData()
        params['min_embankment_height'] = self.spin_terrain_xs_min_embank.value()
        params['backfill_threshold']    = self.spin_terrain_xs_backfill_thresh.value()
        self._refresh_terrain_xs()
        # The main chart's 'Show Bank Elevation Analysis' lines (and a
        # control section's own marker/label) depend on this station's
        # params too (e.g. Channel Height feeds the design-height line
        # directly) — without this they'd only refresh on the NEXT
        # unrelated redraw, which reads as 'my edit didn't do anything'.
        self._draw_terrain_vertical_decorations()

    def _terrain_xs_sample_ground(self, ch, params, direction=None, scale=1.0):
        """Ground raster sample at an arbitrary chainage (not necessarily
        a station) — same perpendicular-line-then-extract pipeline as
        below, generalised so the Check Cross-Section reach-interpolation
        window can resample at every step chainage, not just a selected
        station's. None if there's no raster data or the sample fails.

        direction/scale, if given, are for a MITER station at a sharp
        Modify Terrain corner (see _terrain_vertex_miter_bisector):
        direction overrides the sampling line's own orientation (the
        corner's bisector instead of the normal per-leg tangent), and
        scale is the miter amplification factor applied to that
        station's own design offsets. The sampling line itself is
        extended by `scale` so it reaches as far out as the FINAL
        (scaled) design edges will — but the returned 'xs' values are
        then divided back down by `scale`, staying in the same nominal/
        un-scaled coordinate space _terrain_xs_build_interpolated_shape
        already builds the (not-yet-scaled) shape in. Without this
        rescale, a ground value read at nominal offset x would actually
        describe the ground at x (unscaled), not at the design's own
        FINAL position x * scale once the whole shape is scaled up
        afterward — i.e. the 'Flush with Ground' search would be
        comparing the design against the wrong point on the ground."""
        half_extent = self._terrain_xs_half_width(params)
        raster_entries, _ve, col_meta = self._collect_entries(
            raster_rows=self._terrain_raster_rows, vector_rows=[])
        if not raster_entries:
            return None
        scaled_extent = half_extent * scale
        geom = self._make_perp_line_geom(ch, scaled_extent, scaled_extent, direction=direction)
        if geom is None:
            return None
        try:
            data, chainages = self._extract(
                geom, raster_entries, [], self.spin_terrain_interval.value())
        except Exception:
            return None
        xs = [(c - scaled_extent) / scale for c in chainages]
        return {'xs': xs, 'data': data, 'col_meta': col_meta}

    def _terrain_xs_sample_compare_layer(self, ch, half_extent, layer):
        """Ground-style sample of an ARBITRARY raster layer (e.g. a just-
        generated channel-design TIFF) along the perpendicular line at
        chainage ch — same perpendicular-line-then-extract pipeline as
        _terrain_xs_sample_ground, generalised to any single layer rather
        than the configured ground raster rows, for the Cross-Section
        Preview's 'Add' comparison overlay. None if there's no layer, no
        alignment, or the sample fails."""
        if layer is None:
            return None
        geom = self._make_perp_line_geom(ch, half_extent, half_extent)
        if geom is None:
            return None
        try:
            data, chainages = self._extract(
                geom, [(layer, 'compare')], [], self.spin_terrain_interval.value())
        except Exception:
            return None
        vals = data.get('compare')
        if vals is None:
            return None
        xs = [c - half_extent for c in chainages]
        return {'xs': xs, 'values': vals}

    def _terrain_bank_ground_profiles(self):
        """Three extra lines for the main long-section chart's 'Show Bank
        Elevation Analysis' toggle — NOT design quantities, all driven by
        the REAL existing ground and the already-interpolated numeric
        params (bottom width / inner slopes / channel height), so they
        work continuously along the whole alignment regardless of Topo
        Adjustment choices:
        - height_vals: invert + Channel Height at every chainage — 'what
          elevation would the top of the channel be if built to its full
          user-entered height everywhere'.
        - left_vals / right_vals: the REAL ground elevation sampled at the
          exact point each side's inner slope would reach at that height
          (offset = half bottom width + that side's inner slope x channel
          height) — i.e. what's actually there today, not a calculated
          design value.
        Comparing these along the whole alignment is what the user's own
        idea was for: where real ground already sits near/above the
        height line, Fixed suits that stretch; well below it, Flush suits
        it better; the crossover points are where a Control Section is
        worth placing. Returns (chainages, left_vals, right_vals,
        height_vals) or None if there's no alignment/ground data yet."""
        if self._terrain_geom is None or not self._profile_chainages:
            return None
        raster_entries, _ve, _cm = self._collect_entries(
            raster_rows=self._terrain_raster_rows, vector_rows=[])
        if not raster_entries:
            return None
        layer = raster_entries[0][0]
        provider = layer.dataProvider()
        nodata = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else None

        def _sample(pt):
            try:
                v, ok = provider.sample(pt, 1)
            except Exception:
                return None
            if not ok or (nodata is not None and abs(v - nodata) <= 1e-6):
                return None
            return v

        chainages, left_vals, right_vals, height_vals = [], [], [], []
        for i, ch in enumerate(self._profile_chainages):
            if i % 500 == 0:
                QApplication.processEvents()
            params = self._terrain_xs_interp_params(ch)
            half_bw = params['bottom_width'] / 2.0
            left_off  = half_bw + params['left_inner_slope'] * params['channel_height']
            right_off = half_bw + params['right_inner_slope'] * params['channel_height']
            geom = self._make_perp_line_geom(ch, left_off, right_off)
            if geom is None:
                left_vals.append(None)
                right_vals.append(None)
            else:
                pts = geom.asPolyline()
                left_vals.append(_sample(QgsPointXY(pts[0])))
                right_vals.append(_sample(QgsPointXY(pts[1])))
            chainages.append(ch)
            height_vals.append(self._terrain_invert_level_at(ch) + params['channel_height'])
        return chainages, left_vals, right_vals, height_vals

    def _terrain_design_height_profile(self):
        """Cheap height-only companion to _terrain_bank_ground_profiles —
        just invert + Channel Height at every chainage, no raster
        sampling at all. This is what draws the design-height reference
        line on the profile chart UNCONDITIONALLY (whenever a profile
        exists), per explicit request that this line isn't tied to the
        'Show Banks' toggle — only the two REAL-ground bank lines are;
        those still come from the (pricier) _terrain_bank_ground_profiles
        above, only called when that toggle is actually on. Returns
        (chainages, height_vals) or None if there's no profile yet."""
        if self._terrain_geom is None or not self._profile_chainages:
            return None
        chainages, height_vals = [], []
        for ch in self._profile_chainages:
            params = self._terrain_xs_interp_params(ch)
            chainages.append(ch)
            height_vals.append(self._terrain_invert_level_at(ch) + params['channel_height'])
        return chainages, height_vals

    def _refresh_terrain_xs(self):
        """Re-samples the ground raster at whichever STATION (node) is
        selected — same perpendicular-line-then-extract pipeline as the
        Layer Profile tab's own Cross-Section (XSectionDialog): build a
        perpendicular line at the node's own chainage via the already-
        generic _make_perp_line_geom (it reads self.profile_geom, which
        already aliases self._terrain_geom), then run it through the same
        _extract() every other tab uses. Extent is sized to bracket the
        current design shape rather than a fixed/user-set value, since
        this embedded preview has no separate extent control. Called on
        both selection change AND every shape-parameter edit, since either
        can change how much ground context is needed either side."""
        self._terrain_xs_ground = None
        self._terrain_xs_compare = None
        pos = self._terrain_selected_node_position()
        if pos is not None:
            ch, _level = pos
            key = self._terrain_selected_xs_key
            params = self._terrain_xs_params.get(key, self._terrain_xs_default)
            self._terrain_xs_ground = self._terrain_xs_sample_ground(ch, params)
            if self._terrain_xs_compare_layer is not None:
                half_extent = self._terrain_xs_half_width(params)
                self._terrain_xs_compare = self._terrain_xs_sample_compare_layer(
                    ch, half_extent, self._terrain_xs_compare_layer)
        self._draw_terrain_xs_preview()

    def _terrain_xs_ground_value_at(self, x, ground):
        """Ground elevation at offset x from centreline, linear-
        interpolated over the given sampled cross-section raster data
        dict (an explicit param rather than always self._terrain_xs_ground
        so this also works for a chainage sampled on the fly, e.g. by the
        Check Cross-Section reach-interpolation window). Uses the FIRST/
        primary raster column — same one the vertical alignment's own
        cut/fill hatch uses. None if there's no ground data, x falls
        outside what was sampled, or the bracketing samples are nodata."""
        col = self._terrain_ground_col
        if ground is None or col is None or col not in ground['data']:
            return None
        xs = ground['xs']
        ys = ground['data'][col]
        if not xs:
            return None
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                y0, y1 = ys[i], ys[i + 1]
                if y0 is None or y1 is None:
                    return None
                if xs[i + 1] == xs[i]:
                    return y0
                t = (x - xs[i]) / (xs[i + 1] - xs[i])
                return y0 + t * (y1 - y0)
        return None

    def _terrain_xs_find_ground_intersection(self, x_start, y_start, run_per_rise,
                                              direction, ground, max_search_height=30.0):
        """Walk a slope line starting at (x_start, y_start), moving outward
        (direction=-1 left / +1 right) at run_per_rise:1 (H:V), until it
        meets the existing ground profile — going UP if the start point is
        below ground (the normal 'wall rising up to meet ground' case) or
        DOWN if the start point is already at/above ground (e.g. a berm at
        a fixed channel height that ended up higher than the surrounding
        land — its outer slope has to come back DOWN to meet it, not keep
        rising). Returns (x, y). Falls back to a fixed nominal rise if
        there's no ground data at all; stops at whatever it last checked
        if the slope runs off the sampled ground extent, or hits
        max_search_height, without ever crossing it. `ground` is an
        explicit param (not always self._terrain_xs_ground) so this also
        works for a chainage sampled on the fly by the Check Cross-Section
        reach-interpolation window."""
        if ground is None or not ground['xs']:
            h = min(max_search_height, _TERRAIN_XS_OUTER_RISE)
            return x_start + direction * run_per_rise * h, y_start + h

        xs = ground['xs']
        x_lo, x_hi = xs[0], xs[-1]
        if x_start < x_lo or x_start > x_hi:
            return x_start, y_start
        gy0 = self._terrain_xs_ground_value_at(x_start, ground)
        if gy0 is None:
            return x_start, y_start
        # +1 = rise to meet ground from below; -1 = fall to meet it from above
        sign = 1.0 if y_start < gy0 else -1.0

        x_step = max(0.05, (x_hi - x_lo) / 500.0)
        dh = (x_step / run_per_rise) if run_per_rise > 1e-6 else 0.05

        h = 0.0
        prev_x, prev_y, prev_diff = x_start, y_start, y_start - gy0
        while h <= max_search_height:
            h += dh
            x = x_start + direction * run_per_rise * h
            y = y_start + sign * h
            if x < x_lo or x > x_hi:
                return prev_x, prev_y
            gy = self._terrain_xs_ground_value_at(x, ground)
            if gy is None:
                return prev_x, prev_y
            diff = y - gy
            crossed = (prev_diff < 0.0 <= diff) if sign > 0 else (prev_diff > 0.0 >= diff)
            if crossed:
                span = diff - prev_diff
                t = (-prev_diff / span) if abs(span) > 1e-9 else 0.0
                return prev_x + t * (x - prev_x), prev_y + t * (y - prev_y)
            prev_x, prev_y, prev_diff = x, y, diff
        return prev_x, prev_y

    def _terrain_xs_find_backfill_point(self, x_start, y_level, direction, max_dist, ground):
        """Walks a FLAT line at y_level outward from x_start (direction=-1
        left / +1 right) up to max_dist, looking for where the real
        ground rises to meet it. Used by the Fixed Channel Height
        Earthwork check: when only a shallow embankment would be needed,
        it's more practical to backfill flat to existing ground over a
        short run than build a separate berm+batter-slope structure for
        it. Returns (x, y_level) at the crossing, or None if the ground
        doesn't reach y_level within max_dist (or there's no ground data,
        or it's already at/above y_level at x_start — that's the caller's
        'no embankment needed at all' case, not this one)."""
        if ground is None or not ground['xs'] or max_dist <= 0.0:
            return None
        xs = ground['xs']
        x_lo, x_hi = xs[0], xs[-1]
        if x_start < x_lo or x_start > x_hi:
            return None
        gy0 = self._terrain_xs_ground_value_at(x_start, ground)
        if gy0 is None or gy0 >= y_level:
            return None
        step = max(0.02, (x_hi - x_lo) / 1000.0)
        prev_x, prev_gy = x_start, gy0
        dist = 0.0
        while dist < max_dist:
            dist = min(dist + step, max_dist)
            x = x_start + direction * dist
            if x < x_lo or x > x_hi:
                return None
            gy = self._terrain_xs_ground_value_at(x, ground)
            if gy is None:
                return None
            if gy >= y_level:
                span = gy - prev_gy
                t = (y_level - prev_gy) / span if abs(span) > 1e-9 else 0.0
                return prev_x + t * (x - prev_x), y_level
            prev_x, prev_gy = x, gy
        return None

    def _terrain_xs_build_side(self, direction, invert_level, half_bw, option,
                                ins, outs, berm, height, ground,
                                min_embank=0.0, backfill_thresh=0.0):
        """One side (direction=-1 left / +1 right) of the design cross-
        section, from the bottom-of-channel vertex out to where it meets
        existing ground, per its OWN fitting option:
        - 'flush': the inner slope keeps rising at its own H:V until it
          meets existing ground — Channel height is ignored on that side.
        - 'fixed': the inner slope rises exactly to Channel height, then
          (usually) a flat berm, then the outer slope runs from the
          berm's outer edge until it meets existing ground. A berm only
          makes physical sense as an EMBANKMENT — built specifically
          because the natural ground there is LOWER than the design
          Channel Height and needs raising up to it — so this only
          actually builds one when that's true, checked two ways:
          1. If the ground right where the berm would start is already
             AT or ABOVE the design height, there's no low ground to
             build up to at all — falls back to the SAME construction as
             'flush' (continue the inner slope straight to ground).
          2. Otherwise a fill genuinely is needed, but if it's SHALLOW
             (the gap between the berm's own level and the ground at the
             berm's own midpoint is less than Earthwork's Min Embankment
             Height), building a separate berm+batter-slope structure
             for it isn't practical — instead this searches sideways at
             the flat berm elevation, outward, up to Backfill Threshold
             metres (_terrain_xs_find_backfill_point) for where real
             ground reaches that level, and if found, just backfills
             flat to there (no berm width, no separate outer slope).
             Falls back to the normal berm construction if that flat
             search doesn't reach ground within the threshold.
        Returned in natural build order (near-centre first, outward last)
        — NOT the final left-to-right polyline order; callers that need
        that (_terrain_xs_build_shape) reverse the left side themselves.
        Pulled out of _terrain_xs_build_shape as its own method so the
        Check Cross-Section reach-interpolation window can build BOTH
        candidate half-shapes for a side whose option differs between the
        reach's two end stations (see _terrain_xs_build_interpolated_shape)."""
        x_bot, y_bot = direction * half_bw, invert_level
        if option == 'fixed':
            x_berm_i = x_bot + direction * ins * height
            y_berm   = invert_level + height
            ground_at_berm = self._terrain_xs_ground_value_at(x_berm_i, ground)
            if ground_at_berm is None or ground_at_berm < y_berm:
                x_berm_o = x_berm_i + direction * berm
                x_berm_mid = (x_berm_i + x_berm_o) / 2.0
                ground_at_mid = self._terrain_xs_ground_value_at(x_berm_mid, ground)
                embank_height = (y_berm - ground_at_mid) if ground_at_mid is not None else None
                if embank_height is not None and embank_height < min_embank:
                    backfill = self._terrain_xs_find_backfill_point(
                        x_berm_i, y_berm, direction, backfill_thresh, ground)
                    if backfill is not None:
                        # Still rises via the inner slope to design height
                        # first (x_berm_i, y_berm) — backfill only replaces
                        # the berm-width-then-outer-slope part beyond that
                        # with a flat run to ground, it doesn't skip the
                        # inner slope itself. Missing this point was the
                        # bug: it drew one straight line from the channel
                        # bottom all the way to the ground crossing,
                        # changing the whole slope's angle instead of just
                        # flattening what comes after the design height.
                        return [(x_bot, y_bot), (x_berm_i, y_berm), backfill]
                x_top, y_top = self._terrain_xs_find_ground_intersection(
                    x_berm_o, y_berm, outs, direction, ground)
                return [(x_bot, y_bot), (x_berm_i, y_berm), (x_berm_o, y_berm), (x_top, y_top)]
            # Ground is already at/above the design height right where the
            # berm would start — no embankment needed, so no berm either.
        # 'flush' (also the 'fixed' fallbacks above)
        x_top, y_top = self._terrain_xs_find_ground_intersection(
            x_bot, y_bot, ins, direction, ground)
        return [(x_bot, y_bot), (x_top, y_top)]

    def _terrain_xs_build_shape(self, invert_level, params, ground):
        """Design cross-section polyline (left-top -> ... -> right-top,
        x monotonically increasing) for a single, definite set of params
        (always the case for a real station — see
        _terrain_xs_build_interpolated_shape for the reach-interpolation
        case, where the two sides' options can differ between the US and
        DS ends and need blending instead). `ground` is an explicit param
        (not always self._terrain_xs_ground) so the same builder works for
        an arbitrary chainage sampled on the fly."""
        bw      = params['bottom_width']
        height  = params['channel_height']
        half_bw = bw / 2.0
        min_embank      = params.get('min_embankment_height', self._terrain_xs_default['min_embankment_height'])
        backfill_thresh = params.get('backfill_threshold', self._terrain_xs_default['backfill_threshold'])
        left_pts  = self._terrain_xs_build_side(
            -1, invert_level, half_bw, params.get('left_option', 'flush'),
            params['left_inner_slope'], params['left_outer_slope'],
            params['left_berm_width'], height, ground, min_embank, backfill_thresh)
        right_pts = self._terrain_xs_build_side(
            1, invert_level, half_bw, params.get('right_option', 'flush'),
            params['right_inner_slope'], params['right_outer_slope'],
            params['right_berm_width'], height, ground, min_embank, backfill_thresh)
        poly = list(reversed(left_pts)) + list(right_pts)
        return [p[0] for p in poly], [p[1] for p in poly]

    @staticmethod
    def _terrain_xs_arclen_param(pts):
        """Cumulative normalised arc-length (0 at pts[0], 1 at pts[-1])
        for a point sequence — the common parametrisation that lets two
        DIFFERENTLY-SHAPED polylines (e.g. a 2-point Flush edge and a
        4-point Fixed-height edge, at different widths) be morphed into
        each other point-for-point, instead of blending by a shared x
        (which breaks down whenever the two candidates don't cover the
        same x-range — see _terrain_xs_build_interpolated_shape)."""
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if len(pts) == 1:
            return [0.0], xs, ys
        seg = [0.0]
        for i in range(1, len(pts)):
            seg.append(seg[-1] + math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]))
        total = seg[-1] if seg[-1] > 1e-9 else 1.0
        return [v / total for v in seg], xs, ys

    def _terrain_xs_option_anchors(self, side):
        """Sorted [(chainage, option), ...] for one side ('left'/'right')
        — every anchor's (real station OR control section) own Topo
        Adjustment, from the same combined list _terrain_xs_interp_params
        uses for numeric values. This is what
        _terrain_xs_build_interpolated_shape brackets an arbitrary
        chainage against, instead of always using the reach's two formal
        end stations — a control section effectively splits a long reach
        into shorter sub-spans to blend across, without touching either
        real station's own recorded cross-section."""
        key = f'{side}_option'
        return [(ch, self._terrain_xs_params.get(k, self._terrain_xs_default).get(key, 'flush'))
                for k, ch in self._terrain_xs_all_anchor_list()]

    @staticmethod
    def _terrain_xs_top_search_inputs(direction, half_bw, invert_level, option, ins, outs, berm, height):
        """(x_start, y_start, run) that _terrain_xs_find_ground_intersection
        is called with to find ONE side's top-of-slope vertex under its own
        option — the exact values _terrain_xs_build_side derives internally
        for that same call, pulled out here so a blend between two
        DIFFERENT options can blend these SEARCH INPUTS (see
        _terrain_xs_build_interpolated_shape) instead of blending two
        already ground-solved OUTPUTS, which is what let the blended top
        vertex drift off the real ground between them."""
        x_bot = direction * half_bw
        if option == 'fixed':
            x_berm_i = x_bot + direction * ins * height
            y_berm = invert_level + height
            x_berm_o = x_berm_i + direction * berm
            return x_berm_o, y_berm, outs
        return x_bot, invert_level, ins

    def _terrain_xs_build_interpolated_shape(self, ch, invert_level, ground):
        """Design cross-section at an arbitrary chainage along a reach,
        for the Check Cross-Section window. Numeric parameters (widths,
        slopes, height) interpolate linearly between the two anchors
        (real stations AND control sections — see _terrain_xs_interp_
        params) bounding it.

        The Topo Adjustment option is categorical and can't blend the
        same way — each side is bracketed independently against its own
        _terrain_xs_option_anchors. Where the bracketing anchors agree,
        that side is built directly with no extra cost. Where they
        DISAGREE, this builds BOTH candidate half-shapes for that side
        (one per option, using the same interpolated numeric params —
        each candidate still runs its own proper construction: Flush does
        its own ground search, Fixed reaches its own analytic height) and
        MORPHS one into the other by normalised arc-length position (see
        _terrain_xs_arclen_param) for the interior/geometric vertices,
        blending both the top-of-slope's transverse position AND its
        elevation together by the LOCAL fraction between those two
        anchors. The one exception is the very last (ground-meeting)
        vertex: rather than blending the two candidates' already-solved
        ground intersections (which generally does NOT land back on the
        real ground, since it curves between their two different x's —
        this was a real, live-tested artifact, a thin visible sliver),
        this blends the ground SEARCH's own inputs (start point + slope,
        see _terrain_xs_top_search_inputs) from each candidate and runs
        the search ONCE — the result sits exactly on the real ground, by
        construction, at every blend fraction.

        A flood-mitigation channel needs to represent something actually
        constructible, not just a smooth-looking drafting shortcut, but an
        abrupt jump is a worse and less physically real result than a
        gradual taper. Where a genuine engineered transition is needed at
        a specific spot, a CONTROL SECTION (a lightweight station with its
        own full cross-section, placed via the main chart's 'Add Control
        Section' tool) lets the user plant a real, directly-built anchor
        exactly there, splitting the reach into shorter sub-spans either
        side of it — this automatic blend is a safety net for whatever
        anchors currently exist, not a substitute for that judgement
        call."""
        nodes = self._terrain_xs_node_list()
        params = self._terrain_xs_interp_params(ch)
        if len(nodes) < 2 or ch <= nodes[0][1] or ch >= nodes[-1][1]:
            return self._terrain_xs_build_shape(invert_level, params, ground)

        bw      = params['bottom_width']
        height  = params['channel_height']
        half_bw = bw / 2.0
        min_embank      = params.get('min_embankment_height', self._terrain_xs_default['min_embankment_height'])
        backfill_thresh = params.get('backfill_threshold', self._terrain_xs_default['backfill_threshold'])

        def _side_blended(direction, side_name, ins, outs, berm):
            anchors = self._terrain_xs_option_anchors(side_name)
            opt0 = opt1 = 'flush'
            t = 0.0
            if anchors:
                if ch <= anchors[0][0]:
                    opt0 = opt1 = anchors[0][1]
                elif ch >= anchors[-1][0]:
                    opt0 = opt1 = anchors[-1][1]
                else:
                    for i in range(len(anchors) - 1):
                        a_ch, a_opt = anchors[i]
                        b_ch, b_opt = anchors[i + 1]
                        if a_ch <= ch <= b_ch:
                            opt0, opt1 = a_opt, b_opt
                            t = 0.0 if b_ch == a_ch else (ch - a_ch) / (b_ch - a_ch)
                            break

            pts0 = self._terrain_xs_build_side(
                direction, invert_level, half_bw, opt0, ins, outs, berm, height, ground,
                min_embank, backfill_thresh)
            if opt0 == opt1:
                # _terrain_xs_build_side returns near-centre-first order;
                # normalise to ascending-x here so both sides can be
                # concatenated directly below with no further reversal.
                return sorted(pts0) if direction < 0 else pts0
            pts1 = self._terrain_xs_build_side(
                direction, invert_level, half_bw, opt1, ins, outs, berm, height, ground,
                min_embank, backfill_thresh)
            s0, x0, y0 = self._terrain_xs_arclen_param(pts0)
            s1, x1, y1 = self._terrain_xs_arclen_param(pts1)
            s_all = sorted(set(s0) | set(s1))
            x_blend = (1.0 - t) * np.interp(s_all, s0, x0) + t * np.interp(s_all, s1, x1)
            y_blend = (1.0 - t) * np.interp(s_all, s0, y0) + t * np.interp(s_all, s1, y1)
            pts = list(zip(x_blend.tolist(), y_blend.tolist()))
            # The LAST point (s=1 for both candidates, however many
            # interior vertices each has) is where the batter/inner slope
            # meets existing ground — blending it like every other point
            # would just average two points that each independently sit
            # exactly ON the ground at their own x, and since real ground
            # generally curves between those two x's, the average doesn't
            # land back on it (the thin-sliver artifact). Re-solved here
            # instead: blend the ground SEARCH's own inputs (start point +
            # slope) from each candidate, then run the search once — the
            # result is guaranteed to sit on the real ground, by
            # construction, rather than approximating it.
            xa, ya, ra = self._terrain_xs_top_search_inputs(
                direction, half_bw, invert_level, opt0, ins, outs, berm, height)
            xb, yb, rb = self._terrain_xs_top_search_inputs(
                direction, half_bw, invert_level, opt1, ins, outs, berm, height)
            x_start = (1.0 - t) * xa + t * xb
            y_start = (1.0 - t) * ya + t * yb
            run     = (1.0 - t) * ra + t * rb
            pts[-1] = self._terrain_xs_find_ground_intersection(
                x_start, y_start, run, direction, ground)
            # x is monotonic in s for each candidate individually (each
            # candidate's own build always moves consistently outward
            # from the bottom vertex), so a weighted average of the two
            # is monotonic too — this just normalises direction/ordering,
            # not fixing a real ambiguity.
            return sorted(pts) if direction < 0 else pts

        left_pts  = _side_blended(-1, 'left', params['left_inner_slope'],
                                   params['left_outer_slope'], params['left_berm_width'])
        right_pts = _side_blended(1, 'right', params['right_inner_slope'],
                                   params['right_outer_slope'], params['right_berm_width'])
        poly = left_pts + right_pts
        return [p[0] for p in poly], [p[1] for p in poly]

    def _terrain_draw_xs_axes(self, ax, poly_x, poly_y, ground, title='Cross-Section Preview',
                                compare=None, z_factor=None):
        """Shared cross-section drawing — ground profile + cut/fill hatch
        + channel design line, in real elevation so the design shape and
        the ground raster overlay sit in the same frame (same centred-at-0
        offset convention as Layer Profile's own XSectionDialog._draw_xs).
        Cut/fill hatching matches the vertical alignment chart's own
        convention: design below ground = cut (blue), design above ground
        = fill (red). Used by both the embedded per-station preview
        (_draw_terrain_xs_preview) and the popped-out Check Cross-Section
        reach-interpolation window — it takes an already-built (poly_x,
        poly_y) rather than building the shape itself, since the two
        callers build it differently (a real station's single definite
        shape vs. the reach window's US/DS-blended interpolated one).

        `compare`, if given, is an extra {'xs': [...], 'values': [...]}
        sample (see _terrain_xs_sample_compare_layer) — typically a just-
        generated channel-design TIFF, sampled along this SAME
        perpendicular line — plotted as its own line so the actual
        rasterised surface can be checked directly against the design/
        ground profile it was generated from, per the 'Add' button in the
        Cross-Section Preview.

        `z_factor`, if given, overrides self._terrain_xs_z_factor (the
        embedded preview's own Z factor) for just this draw — used by
        Check Cross-Section, which keeps its OWN independent Z factor per
        explicit request that the two views' vertical exaggeration not
        affect each other."""
        ax.clear()
        ax.set_title(title, fontsize=9, fontweight='bold')
        ax.grid(True, alpha=0.3)

        # Ground profile (raster) — same overlay technique as the Layer
        # Profile tab's own Cross-Section dialog, just embedded here.
        if ground is not None:
            for col, vals in ground['data'].items():
                meta = ground['col_meta'].get(col, {})
                if not meta.get('visible', True):
                    continue
                color = meta.get('color', QColor('#2196F3'))
                color_str = color.name() if hasattr(color, 'name') else color
                ys = [v if v is not None else float('nan') for v in vals]
                ax.plot(ground['xs'], ys, label=_prune_mid(col), color=color_str,
                         linewidth=meta.get('linewidth', 1.5),
                         linestyle=meta.get('linestyle', '-'),
                         alpha=meta.get('alpha', 1.0), zorder=5)

            # Cut (blue) / Fill (red) hatch between the design line and
            # ground — same colours/convention as the vertical alignment
            # chart's own cut/fill shading. Evaluated on the GROUND's own
            # dense sample grid (not just the design polygon's few
            # vertices) so the hatch boundary actually tracks the real
            # (wiggly) ground shape between vertices, not a crude
            # straight-line guess between them — but the design polygon's
            # OWN vertex x-positions (poly_x) are merged into that grid
            # too. Without this, a narrow pinch region (e.g. a small
            # berm's outer/batter slope closing in on the ground over a
            # short run) can fall entirely between two raster sample
            # points and never get a fill_between segment at all, leaving
            # a visible unfilled gap right where the hatch should taper
            # to a point. Restricted to the design shape's OWN x-range
            # (its left-top to right-top vertices) — np.interp clamps to
            # the edge value outside that range, which would otherwise
            # paint a hatch clear across the rest of the sampled ground
            # that has nothing to do with the channel at all.
            col = self._terrain_ground_col
            if col and col in ground['data'] and poly_x:
                ground_vals = [v if v is not None else float('nan') for v in ground['data'][col]]
                xs_ground = np.array(ground['xs'], dtype=float)
                ga_ground = np.array(ground_vals, dtype=float)
                if xs_ground.size:
                    poly_xa = np.array(poly_x, dtype=float)
                    x_lo, x_hi = poly_xa[0], poly_xa[-1]
                    extra = poly_xa[(poly_xa >= xs_ground[0]) & (poly_xa <= xs_ground[-1])]
                    xa = np.union1d(xs_ground, extra)
                    ga = np.interp(xa, xs_ground, ga_ground)
                    da = np.interp(xa, poly_xa, poly_y)
                    in_shape = (xa >= x_lo) & (xa <= x_hi)
                    valid = in_shape & np.isfinite(ga) & np.isfinite(da)
                    ax.fill_between(xa, ga, da, where=valid & (da < ga), interpolate=True,
                                     facecolor='#4FC3F7', alpha=0.30, hatch='///',
                                     edgecolor='#4FC3F7', linewidth=0.0, zorder=2)
                    ax.fill_between(xa, ga, da, where=valid & (da > ga), interpolate=True,
                                     facecolor='#F44336', alpha=0.30, hatch='///',
                                     edgecolor='#F44336', linewidth=0.0, zorder=2)

        ax.plot(poly_x, poly_y, color='#6D4C41', linewidth=2, marker='o', markersize=3,
                 label='Channel Design', zorder=6)

        if compare is not None and compare.get('xs') and compare.get('values') is not None:
            cvals = [v if v is not None else float('nan') for v in compare['values']]
            ax.plot(compare['xs'], cvals, color='#E53935', linewidth=1,
                     linestyle='-', alpha=0.5, label='Channel Design TIFF', zorder=7)

        ax.axvline(x=0, color='#212121', linewidth=0.8, linestyle='-.', alpha=0.7, zorder=4)
        ax.set_xlabel('Distance from centre  [ − Left | + Right ]  [m]', fontsize=8)
        ax.set_ylabel('Elevation [masl]', fontsize=8)
        ax.tick_params(labelsize=7)
        try:
            ax.legend(fontsize=7, loc='best')
        except Exception:
            pass
        # Vertical exaggeration (Z factor) — same convention as QGIS's own
        # terrain Z factor: 1.0 means TRUE 1:1 scale (1 m on X takes the
        # same screen distance as 1 m on Y, matplotlib's own 'equal'
        # aspect), 2.0 doubles the vertical scale relative to that, etc.
        # Without this, matplotlib's independent auto-scaling of X/Y picks
        # whatever ratio fills the plot area, which for a wide-shallow
        # channel stretches the Y range out until it looks like a deep
        # narrow slot. Each cross-section VIEW keeps its own independent
        # factor (the z_factor param, when given — e.g. Check Cross-
        # Section's own self._z_factor — falls back to this embedded
        # preview's own self._terrain_xs_z_factor otherwise), per explicit
        # request that changing one view's Z doesn't affect the other.
        # 'datalim' keeps this subplot's own box filling its normal space
        # — it extends the shorter axis's DATA range instead of shrinking
        # the box. Only applied to actual cross-sections (transverse to
        # the channel) — never the longitudinal/long-reach charts, which
        # are conventionally shown with vertical exaggeration since they
        # cover distances far longer than their elevation range.
        z = self._terrain_xs_z_factor if z_factor is None else z_factor
        try:
            ax.set_aspect(max(0.01, z), adjustable='datalim')
        except Exception:
            pass
        # Without this, the default matplotlib margins leave too little
        # room at a small canvas height and the x-axis label/tick labels
        # get clipped at the bottom of the chart.
        try:
            ax.figure.tight_layout(pad=1.0)
        except Exception:
            pass

    def _draw_terrain_xs_preview(self):
        """Embedded (not popped-out) cross-section preview, drawn at the
        selected station's own chainage — see _terrain_draw_xs_axes for
        the actual drawing, shared with the Check Cross-Section window."""
        if not MATPLOTLIB_AVAILABLE or self.canvas_terrain_xs is None:
            return
        ax = self.ax_terrain_xs
        self._terrain_xs_elev_markers = []   # wiped by ax.clear() below; drop the stale references
        key = self._terrain_selected_xs_key
        if key is None:
            ax.clear()
            ax.set_title('Cross-Section Preview', fontsize=9, fontweight='bold')
            ax.grid(True, alpha=0.3)
            ax.text(0.5, 0.5, 'Click a break point (or the US/DS end) in the\n'
                              'chart above to preview its cross-section.',
                    ha='center', va='center', fontsize=9, color='gray', transform=ax.transAxes)
            ax.set_xticks([]); ax.set_yticks([])
            self.canvas_terrain_xs.draw_idle()
            return

        pos = self._terrain_selected_node_position()
        invert_level = pos[1] if pos is not None else 0.0
        params = self._terrain_xs_params.get(key, self._terrain_xs_default)
        poly_x, poly_y = self._terrain_xs_build_shape(invert_level, params, self._terrain_xs_ground)
        self._terrain_draw_xs_axes(ax, poly_x, poly_y, self._terrain_xs_ground,
                                    compare=self._terrain_xs_compare)
        self.canvas_terrain_xs.draw_idle()
        self._position_terrain_xs_elev_btn()

    def _open_terrain_check_xs(self):
        """Launch the (non-modal, popped-out) Check Cross-Section window —
        lets the user step through the interpolated shape along a reach
        between two break points at a chosen spacing, ahead of eventually
        using the same interpolation for raster/TIFF generation."""
        if self._terrain_geom is None or self._terrain_us_level is None or self._terrain_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set up the horizontal alignment and both end levels first\n'
                '— there needs to be at least one reach to step through.')
            return
        if not self._terrain_xs_reach_list():
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Need at least two stations (e.g. the US and DS ends) to\n'
                'define a reach to interpolate along.')
            return
        dlg = TerrainCheckXSDialog(self)
        self._terrain_check_xs_dialogs.append(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._on_terrain_check_xs_closed(d))
        dlg.show()
        dlg.raise_()
        # Shows this window's ghost marker on the main ground-profile
        # chart right away — see _draw_terrain_vertical_decorations.
        self._draw_terrain_vertical_decorations()

    def _on_terrain_check_xs_closed(self, dlg):
        if dlg in self._terrain_check_xs_dialogs:
            self._terrain_check_xs_dialogs.remove(dlg)
        self._draw_terrain_vertical_decorations()

    def _terrain_find_or_load_raster(self, info):
        """Resolves a saved DEM reference back to a real QgsRasterLayer:
        first by matching `source` against every raster already loaded in
        the project (the common case — the same DEM is still open), else
        by loading it fresh from that same source path if it still exists
        on disk. Returns None if neither works (caller surfaces this as a
        'pick it manually' note rather than failing the whole load)."""
        source = info.get('source')
        if not source:
            return None
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer) and lyr.source() == source:
                return lyr
        if os.path.exists(source):
            name = info.get('name') or os.path.basename(source)
            lyr = QgsRasterLayer(source, name, info.get('provider') or 'gdal')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
                return lyr
        return None

    def _terrain_collect_save_state(self):
        """Serialises every multi-step input the Modify Terrain tab
        carries — horizontal alignment, vertical alignment/breaks,
        per-station cross-section params, control sections, DEM pick —
        into one plain-dict/JSON-safe structure. Works perfectly well on a
        still-BLANK tab too (no alignment drawn yet) — every field below
        just comes out empty/None in that case — per explicit request
        that a project folder can be created and saved right at the start
        of a work session, before anything's been drawn, not only once a
        full design exists.

        Cross-section params (self._terrain_xs_params) are keyed by
        id(break-or-control dict) in memory, which is only valid for this
        running process — re-keyed here to a stable 'us'/'ds'/'break:<i>'/
        'control:<i>' scheme (i = that break's/control's own index in
        self._terrain_invert_breaks / self._terrain_control_sections,
        lists this method and _terrain_apply_save_state both walk in the
        same order) so it can be restored correctly after a reload, when
        every dict gets a brand new id()."""
        break_ids = {id(b): i for i, b in enumerate(self._terrain_invert_breaks)}
        control_ids = {id(c): i for i, c in enumerate(self._terrain_control_sections)}

        def _stable_key(key):
            if key == 'us' or key == 'ds':
                return key
            if key in break_ids:
                return f'break:{break_ids[key]}'
            if key in control_ids:
                return f'control:{control_ids[key]}'
            return None   # stale entry from an already-removed break/control

        xs_params_out = {}
        for key, params in self._terrain_xs_params.items():
            save_key = _stable_key(key)
            if save_key is None:
                continue
            xs_params_out[save_key] = dict(params)

        dem_info = None
        if self._terrain_raster_rows:
            row = self._terrain_raster_rows[0]
            layer = row['layer_combo'].currentLayer()
            if layer is not None:
                dem_info = {
                    'source': layer.source(), 'provider': layer.providerType(),
                    'name': layer.name(), 'enabled': row['toggle'].isChecked(),
                    'linestyle': row['ls_combo'].currentData(),
                    'line_width': row['lw_spin'].value(),
                    'opacity': row['al_spin'].value(),
                    'color': row['color'].name(),
                }

        return {
            'version': 1,
            'crs': QgsProject.instance().crs().authid(),
            'raw_points': [[p.x(), p.y()] for p in self._terrain_raw_pts],
            'vertex_radii': {str(k): v for k, v in self._terrain_vertex_radii.items()},
            'default_fillet_radius': self._terrain_fillet_radius,
            'major_interval': self._terrain_major_interval,
            'minor_interval': self._terrain_minor_interval,
            'show_major_ch': self._terrain_show_major_ch,
            'show_minor_ch': self._terrain_show_minor_ch,
            'sampling_interval': self.spin_terrain_interval.value(),
            'us_level': self._terrain_us_level,
            'ds_level': self._terrain_ds_level,
            'invert_breaks': [dict(b) for b in self._terrain_invert_breaks],
            'control_sections': [dict(c) for c in self._terrain_control_sections],
            'xs_params': xs_params_out,
            'selected_station': _stable_key(self._terrain_selected_xs_key),
            'dem_layer': dem_info,
            'show_bank_analysis': self._terrain_show_bank_analysis,
            'show_diff': self._terrain_show_diff,
            'diff_col': self.combo_terrain_diff_col.currentText()
                        if hasattr(self, 'combo_terrain_diff_col') else None,
        }

    def _terrain_apply_save_state(self, data):
        """Restores everything _terrain_collect_save_state captured,
        resetting the tab first (same full reset 'Clear' already uses) so
        nothing from whatever was open before bleeds into the loaded
        design. Returns the DEM layer's own name/source if it couldn't be
        found/loaded automatically (caller surfaces this as a note — the
        rest of the design still loads fine, it just needs that layer
        picked manually), or None if the DEM resolved cleanly or none was
        saved."""
        self._clear_terrain_alignment()

        missing_dem = None
        dem_info = data.get('dem_layer')
        if dem_info and self._terrain_raster_rows:
            row = self._terrain_raster_rows[0]
            layer = self._terrain_find_or_load_raster(dem_info)
            if layer is not None:
                row['layer_combo'].setLayer(layer)
            else:
                missing_dem = dem_info.get('name') or dem_info.get('source')
            row['toggle'].setChecked(dem_info.get('enabled', True))
            idx = row['ls_combo'].findData(dem_info.get('linestyle', '-.'))
            if idx >= 0:
                row['ls_combo'].setCurrentIndex(idx)
            row['lw_spin'].setValue(dem_info.get('line_width', 1.0))
            row['al_spin'].setValue(dem_info.get('opacity', 75))
            color = QColor(dem_info.get('color', '#000000'))
            row['color'] = color
            row['color_btn'].setStyleSheet(
                f'background-color:{color.name()};border:1px solid #888;border-radius:2px;')

        self._terrain_raw_pts = [QgsPointXY(x, y) for x, y in data.get('raw_points', [])]
        self._terrain_vertex_radii = {int(k): v for k, v in data.get('vertex_radii', {}).items()}
        self._terrain_fillet_radius = data.get('default_fillet_radius', 0.0)

        self._terrain_major_interval = data.get('major_interval', 20.0)
        self._terrain_minor_interval = data.get('minor_interval', 5.0)
        self._terrain_show_major_ch = data.get('show_major_ch', True)
        self._terrain_show_minor_ch = data.get('show_minor_ch', True)
        if hasattr(self, 'spin_terrain_major_ch'):
            self.spin_terrain_major_ch.blockSignals(True)
            self.spin_terrain_major_ch.setValue(self._terrain_major_interval)
            self.spin_terrain_major_ch.blockSignals(False)
            self.spin_terrain_minor_ch.blockSignals(True)
            self.spin_terrain_minor_ch.setValue(self._terrain_minor_interval)
            self.spin_terrain_minor_ch.blockSignals(False)
            self.chk_terrain_major_ch.blockSignals(True)
            self.chk_terrain_major_ch.setChecked(self._terrain_show_major_ch)
            self.chk_terrain_major_ch.blockSignals(False)
            self.chk_terrain_minor_ch.blockSignals(True)
            self.chk_terrain_minor_ch.setChecked(self._terrain_show_minor_ch)
            self.chk_terrain_minor_ch.blockSignals(False)
        if hasattr(self, 'spin_terrain_interval'):
            self.spin_terrain_interval.setValue(data.get('sampling_interval', 1.0))

        self._terrain_us_level = data.get('us_level')
        self._terrain_ds_level = data.get('ds_level')
        if hasattr(self, 'spin_terrain_us_level'):
            if self._terrain_us_level is not None:
                self.spin_terrain_us_level.blockSignals(True)
                self.spin_terrain_us_level.setValue(self._terrain_us_level)
                self.spin_terrain_us_level.blockSignals(False)
            if self._terrain_ds_level is not None:
                self.spin_terrain_ds_level.blockSignals(True)
                self.spin_terrain_ds_level.setValue(self._terrain_ds_level)
                self.spin_terrain_ds_level.blockSignals(False)

        self._terrain_invert_breaks = [dict(b) for b in data.get('invert_breaks', [])]
        self._terrain_control_sections = [dict(c) for c in data.get('control_sections', [])]

        def _resolve_key(save_key):
            """Inverse of _terrain_collect_save_state's _stable_key — maps
            a saved 'us'/'ds'/'break:<i>'/'control:<i>' string back to a
            REAL (new-this-load) id()-based key, now that
            self._terrain_invert_breaks/_terrain_control_sections above
            have been rebuilt in the exact same order they were saved in.
            None if save_key is unset or no longer resolves (e.g. the
            saved file is malformed, or references an index that no
            longer exists)."""
            if save_key is None:
                return None
            if save_key in ('us', 'ds'):
                return save_key
            if save_key.startswith('break:'):
                i = int(save_key.split(':', 1)[1])
                return id(self._terrain_invert_breaks[i]) if i < len(self._terrain_invert_breaks) else None
            if save_key.startswith('control:'):
                i = int(save_key.split(':', 1)[1])
                return id(self._terrain_control_sections[i]) if i < len(self._terrain_control_sections) else None
            return None

        self._terrain_xs_params = {}
        self._terrain_xs_clipboard = None
        for save_key, params in data.get('xs_params', {}).items():
            key = _resolve_key(save_key)
            if key is None:
                continue
            self._terrain_xs_params[key] = dict(params)

        self._terrain_show_bank_analysis = data.get('show_bank_analysis', False)
        if hasattr(self, 'chk_terrain_bank_analysis'):
            self.chk_terrain_bank_analysis.blockSignals(True)
            self.chk_terrain_bank_analysis.setChecked(self._terrain_show_bank_analysis)
            self.chk_terrain_bank_analysis.blockSignals(False)
        self._terrain_show_diff = data.get('show_diff', False)
        if hasattr(self, 'chk_terrain_diff'):
            self.chk_terrain_diff.blockSignals(True)
            self.chk_terrain_diff.setChecked(self._terrain_show_diff)
            self.chk_terrain_diff.blockSignals(False)

        self._update_terrain_fillet_ui()
        self._rebuild_terrain_alignment()
        self._refresh_terrain_diff_combo()
        diff_col = data.get('diff_col')
        if diff_col and hasattr(self, 'combo_terrain_diff_col'):
            idx = self.combo_terrain_diff_col.findText(diff_col)
            if idx >= 0:
                self.combo_terrain_diff_col.setCurrentIndex(idx)

        # Re-select whichever station was selected at Save time — without
        # this the Cross-Section grid/preview stays on its empty 'click a
        # break point…' placeholder even though every station's params
        # loaded correctly, since nothing else sets
        # self._terrain_selected_xs_key back. Must run AFTER
        # _rebuild_terrain_alignment() above, which is what makes the
        # invert/handles resolvable to select against in the first place.
        selected_key = _resolve_key(data.get('selected_station'))
        if selected_key is not None and self._terrain_any_node_handle(selected_key) is not None:
            self._select_terrain_xs_node(selected_key)

        return missing_dem

    def _on_terrain_project_save(self):
        """Saves the current channel design. If a project folder is
        already open (self._terrain_project_folder — set by a previous
        Save or by Open), re-saves straight into it with no prompt, so
        repeated Saves during testing are a single click. Otherwise (the
        very first Save, or any Save after Close) asks for a folder name
        under the 'Result Folder' path already used for CSV/PNG/SHP
        export (self.csv_edit), then remembers it as the open project.
        Works on a still-blank tab too, per explicit request — a project
        folder can be created and saved into right at the start of a work
        session, before anything's been drawn yet."""
        folder = self._terrain_project_folder
        if not folder:
            base = self.csv_edit.text().strip() or self._default_terrain_output_folder()
            default_name = 'Channel_' + datetime.now().strftime('%Y-%m-%d_%H%M')
            name, ok = QInputDialog.getText(
                self, 'Save Channel Design',
                f'Save under:\n{base}\n\nFolder name for this design:',
                text=default_name)
            if not ok or not name.strip():
                return
            folder = os.path.join(base, name.strip())
        try:
            os.makedirs(folder, exist_ok=True)
            state = self._terrain_collect_save_state()
            with open(os.path.join(folder, 'channel_design.json'), 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool', f'Could not save design:\n{exc}')
            return
        self._terrain_project_folder = folder
        QMessageBox.information(
            self, 'Advanced Profile Tool', f'Channel design saved to:\n{folder}')

    def _on_terrain_project_open(self):
        """Opens a previously saved channel design folder (picked under
        the same 'Result Folder' base as Save), replacing whatever is
        currently on this tab — the counterpart to _on_terrain_project_
        save, meant to avoid redrawing the alignment and retyping every
        cross-section by hand on each test run. Remembers the opened
        folder so the next Save re-saves straight into it."""
        base = self.csv_edit.text().strip() or self._default_terrain_output_folder()
        folder = QFileDialog.getExistingDirectory(self, 'Open Channel Design Folder', base)
        if not folder:
            return
        path = os.path.join(folder, 'channel_design.json')
        if not os.path.isfile(path):
            QMessageBox.critical(
                self, 'Advanced Profile Tool',
                f"No 'channel_design.json' found in:\n{folder}")
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool', f'Could not read design:\n{exc}')
            return
        cur_crs = QgsProject.instance().crs().authid()
        saved_crs = state.get('crs')
        if saved_crs and saved_crs != cur_crs:
            reply = QMessageBox.question(
                self, 'Advanced Profile Tool',
                f'This design was saved under CRS {saved_crs}, but the\n'
                f'current project CRS is {cur_crs}. Coordinates will be\n'
                'used as-is, NOT reprojected — continue anyway?',
                _MB_YES | _MB_NO)
            if reply != _MB_YES:
                return
        missing_dem = self._terrain_apply_save_state(state)
        self._terrain_project_folder = folder
        msg = f'Channel design loaded from:\n{folder}'
        if missing_dem:
            msg += (f"\n\nNote: DEM layer '{missing_dem}' could not be found or\n"
                    'reloaded automatically — pick it manually in Raster\n'
                    'Layers (DEM).')
        QMessageBox.information(self, 'Advanced Profile Tool', msg)

    def _on_terrain_project_close(self):
        """Closes the current channel design — clears the Modify Terrain
        tab back to blank (same full reset as Clear) and forgets which
        folder was open, so the next Save asks for a new name instead of
        silently overwriting the just-closed one. Confirms first since
        this discards any changes made since the last Save."""
        if self._terrain_geom is None and self._terrain_project_folder is None:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            'Close the current channel design?\n'
            'Any changes made since the last Save will be lost.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        self._clear_terrain_alignment()
        self._terrain_project_folder = None

    def _on_terrain_create_tiff(self):
        """Opens the Create TIFF dialog — generates a standalone GeoTIFF of
        ONLY the channel design (NOT merged into any existing DEM — that's
        Merge TIFF's job, deliberately separate so this one can be checked
        on its own first). Built the way 12d/Civil 3D build a corridor
        design surface: per-station cross-sections + longitudinal
        breaklines -> constrained-Delaunay TIN -> rasterise (see
        _generate_terrain_channel_tiff), not a plain scattered-point
        interpolation — researched specifically for this feature."""
        if self._terrain_geom is None or self._terrain_us_level is None or self._terrain_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set up the horizontal alignment and both end levels first\n'
                '— there needs to be a channel design to generate a TIFF from.')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle('Create TIFF')
        v = QVBoxLayout(dlg)
        crs_name = self.canvas.mapSettings().destinationCrs().authid()
        lbl = QLabel(
            'Generates a GeoTIFF of the CHANNEL DESIGN ONLY — not merged\n'
            'into any existing DEM yet (check it first, then use Merge\n'
            f'TIFF). Output projection: {crs_name} (the current map canvas CRS).'
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Resolution (grid size):'))
        spin_res = QDoubleSpinBox()
        spin_res.setRange(0.01, 1000.0)
        spin_res.setDecimals(2)
        spin_res.setSuffix(' m')
        spin_res.setValue(self.spin_terrain_interval.value())
        spin_res.setToolTip(
            'Output raster cell size, and also the spacing between\n'
            'cross-sections generated along the alignment to build the\n'
            'design surface.'
        )
        row1.addWidget(spin_res)
        row1.addStretch()
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Reference DEM (optional):'))
        combo_dem = QgsMapLayerComboBox()
        combo_dem.setFilters(_RASTER_FILTER)
        combo_dem.setAllowEmptyLayer(True)
        combo_dem.setCurrentIndex(0)
        combo_dem.wheelEvent = lambda e: e.ignore()
        row2.addWidget(combo_dem, 1)
        v.addLayout(row2)
        lbl2 = QLabel(
            "If given, the output raster's grid origin is snapped onto\n"
            "this DEM's own pixel grid (whole Resolution steps from its\n"
            'corner) so the two line up pixel-for-pixel when merged later.\n'
            "Only its grid alignment is used — CRS is always the canvas's."
        )
        lbl2.setWordWrap(True)
        lbl2.setStyleSheet('font-size:9px;color:#666;')
        v.addWidget(lbl2)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_run = QPushButton('Run')
        btn_run.setStyleSheet('font-weight:bold;')
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_run)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _do_run():
            resolution = spin_res.value()
            dem_layer = combo_dem.currentLayer()
            # A bend whose fillet radius is smaller than the channel's OWN
            # half-width there can't be represented as a simple swept
            # corridor at all — the inside edge would have to fold over
            # itself to exist at that radius. No amount of station
            # density or orientation fixing (what the miter join and bend
            # densification above actually solve) can resolve THIS one;
            # it's a real geometric constraint, not a sampling artifact.
            # Warn with the exact chainage(s) rather than let it surface
            # as an unexplained bad patch in the generated TIFF. A true
            # ZERO-radius corner is NOT flagged — that's the miter join's
            # own, already-handled case.
            tight = self._terrain_check_tight_bends()
            if tight:
                lines = '\n'.join(
                    f'  • Chainage {ch:.2f} m — radius {r:.2f} m, '
                    f'channel half-width ~{hw:.2f} m there'
                    for ch, r, hw in tight[:8])
                more = f'\n  …and {len(tight) - 8} more' if len(tight) > 8 else ''
                reply = QMessageBox.question(
                    self, 'Advanced Profile Tool',
                    'The fillet radius is tighter than the channel\'s own '
                    'half-width at:\n\n' + lines + more +
                    '\n\nThe inside edge of the channel can\'t be represented '
                    'cleanly at these — a surface artifact there is likely. '
                    'Loosening the fillet radius (or narrowing the channel) '
                    'at these chainages is the real fix.\n\nGenerate anyway?',
                    _MB_YES | _MB_NO)
                if reply != _MB_YES:
                    return
            btn_run.setEnabled(False)
            QApplication.setOverrideCursor(_WAIT_CURSOR)
            try:
                layer = self._generate_terrain_channel_tiff(resolution, dem_layer)
            except Exception as exc:
                QApplication.restoreOverrideCursor()
                btn_run.setEnabled(True)
                QMessageBox.critical(self, 'Advanced Profile Tool', f'Create TIFF failed:\n{exc}')
                return
            QApplication.restoreOverrideCursor()
            self._terrain_last_tiff_layer = layer
            dlg.accept()
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                f"Channel design TIFF created: '{layer.name()}'.\n"
                "Use the 'Add' button in the Cross-Section Preview to\n"
                'compare it against the design/ground profile.')

        btn_run.clicked.connect(_do_run)
        dlg.exec()

    def _terrain_xs_local_points_to_world(self, chainage, poly_x, poly_y, geom_cache=None, direction=None):
        """Converts a local cross-section (poly_x = offset from
        centreline, negative=left/positive=right; poly_y = elevation) at
        the given chainage into real-world (x, y, z) points.

        The perpendicular direction is taken from the ACTUAL straight
        segment of self.profile_geom's own polyline that this chainage
        falls on — NOT a finite-difference blend over a fixed window
        either side (what _make_perp_line_geom still uses, which is fine
        there since it's only ever called at a handful of user-picked
        chainages, never swept densely across a sharp corner). A window-
        based tangent straddles a SHARP, un-filleted corner for every
        station within the window's own distance of it, giving those
        stations an increasingly skewed/averaged direction that belongs
        to neither leg — which is what was producing a twisted, pinched
        TIN right at sharp (near-90°) bends in Create TIFF. Reading the
        direction off the real segment instead is exact and has no such
        ambiguity except exactly AT a vertex, where either adjacent
        segment is an equally reasonable choice for a single station's
        own orientation — UNLESS `direction` is given explicitly (a unit
        (tx, ty) tuple), which overrides the segment lookup entirely; see
        _terrain_vertex_miter_bisector — the one sharp-vertex MITER
        station built at each un-filleted corner uses the bisector of its
        two legs instead of either one's own direction, which is what
        actually needs this override.

        geom_cache, if given, is (pts, cumulative_lengths) — see
        _terrain_corridor_geom_cache — so repeated calls across many
        stations (the normal case, from _terrain_build_channel_corridor_
        data) don't re-extract the polyline's vertices and re-walk its
        cumulative length on every single call. Built fresh from
        self.profile_geom when omitted.

        Returns [] if there's no alignment or the geometry is
        degenerate (fewer than 2 vertices, or a zero-length segment)."""
        if self.profile_geom is None:
            return []
        if direction is not None:
            tx, ty = direction
        else:
            if geom_cache is None:
                geom_cache = self._terrain_corridor_geom_cache(self.profile_geom)
            pts, cum = geom_cache
            if len(pts) < 2:
                return []
            total = cum[-1]
            chainage = max(0.0, min(chainage, total))
            seg_i = bisect.bisect_right(cum, chainage) - 1
            seg_i = max(0, min(seg_i, len(pts) - 2))
            p1, p2 = pts[seg_i], pts[seg_i + 1]
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            if dist < 1e-10:
                return []
            tx, ty = dx / dist, dy / dist
        total = self.profile_geom.length()
        chainage = max(0.0, min(chainage, total))
        px, py = -ty, tx
        ctr = self.profile_geom.interpolate(chainage).asPoint()
        cx, cy = ctr.x(), ctr.y()
        return [(cx - px * xo, cy - py * xo, zo) for xo, zo in zip(poly_x, poly_y)]

    @staticmethod
    def _terrain_corridor_geom_cache(geom):
        """(pts, cumulative_lengths) for geom's own polyline vertices —
        built once per Create TIFF run (see
        _terrain_build_channel_corridor_data) and passed into every
        _terrain_xs_local_points_to_world call via geom_cache, rather
        than re-extracting/re-walking the same polyline on every one of
        what can be thousands of stations."""
        pts = geom.asPolyline()
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + math.hypot(pts[i].x() - pts[i - 1].x(),
                                              pts[i].y() - pts[i - 1].y()))
        return pts, cum

    def _terrain_vertex_miter_bisector(self, idx):
        """(bisector_tx, bisector_ty, miter_factor) for raw vertex idx —
        the 'forward' direction the ONE cross-section built exactly at a
        sharp, un-filleted corner (see _terrain_build_channel_corridor_
        data's sharp_chs) should be oriented perpendicular to, and the
        factor every one of its offsets (poly_x — both left AND right,
        the same factor applies to both by symmetry) needs scaling by so
        its edges land exactly where the two legs' OWN offset edges would
        actually intersect — same technique a buffer/offset 'miter' join
        uses. Without the reorientation, the two legs' edges either gap
        apart (convex/outside) or overlap (concave/inside) right at the
        corner; without the scaling, reorienting alone still falls short
        of where they actually meet.

        Geometry: with u1/u2 the legs' own unit tangents and b their
        normalised bisector, u1 and u2 both make the same angle (half the
        total turn) with b by construction, so u1 . b = cos(half-angle)
        directly — and the distance from the vertex to where either leg's
        offset edge crosses the bisector line is EXACTLY half_width /
        cos(half-angle) (same relation for left and right, by symmetry).

        factor is capped at _TERRAIN_MITER_LIMIT, same 'miter limit'
        concept CAD/vector-graphics stroke joins use — past that the turn
        is sharp enough that a true miter point would shoot out an
        unreasonable distance from the corner.

        None if idx isn't an interior vertex, either adjoining segment is
        degenerate (zero length), or the two legs point directly opposite
        (a dead-end reversal has no well-defined bisector) — the caller
        falls back to the normal per-leg tangent (no override) in any of
        these cases."""
        pts = self._terrain_raw_pts
        if not (0 < idx < len(pts) - 1):
            return None
        prev, cur, nxt = pts[idx - 1], pts[idx], pts[idx + 1]
        v1x, v1y = cur.x() - prev.x(), cur.y() - prev.y()
        v2x, v2y = nxt.x() - cur.x(), nxt.y() - cur.y()
        len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
        if len1 < 1e-9 or len2 < 1e-9:
            return None
        u1x, u1y = v1x / len1, v1y / len1
        u2x, u2y = v2x / len2, v2y / len2
        bx, by = u1x + u2x, u1y + u2y
        blen = math.hypot(bx, by)
        if blen < 1e-9:
            return None   # legs point directly opposite — no well-defined bisector
        bx, by = bx / blen, by / blen
        cos_half = max(-1.0, min(1.0, u1x * bx + u1y * by))
        if cos_half < 1e-3:
            return None
        factor = min(1.0 / cos_half, _TERRAIN_MITER_LIMIT)
        return bx, by, factor

    @staticmethod
    def _terrain_params_half_width(params):
        """Conservative channel half-width from interpolated cross-
        section params — bottom half-width plus however far the OUTER
        (batter) slope runs to reach full Channel Height, plus berm
        width, taking whichever side (left/right) is wider. Used by
        _terrain_check_tight_bends (surfaced as a Create TIFF warning)
        to flag a bend whose fillet radius is tighter than the channel
        can geometrically fit there — Set Global Radius no longer has
        its own dedicated fix for this specifically (see its own
        docstring), widening the Fillet radius field and clicking it
        is the replacement."""
        return (params['bottom_width'] / 2.0
                + max(params['left_outer_slope'], params['right_outer_slope'])
                  * params['channel_height']
                + max(params['left_berm_width'], params['right_berm_width']))

    def _terrain_check_tight_bends(self):
        """Scans every interior vertex and flags any whose fillet radius
        (its own override, or the default) is SMALLER than the channel's
        own half-width there. That's a genuine geometric impossibility
        for a simple swept corridor, not a sampling/orientation issue —
        the inside edge would have to fold over itself to exist at that
        radius — so neither the bend densification nor the miter join
        can fix it; the only real fix is a larger radius (or a narrower
        channel) at that chainage. A true ZERO-radius vertex is NOT
        flagged here — that's the miter join's own, already-handled
        case, not this one.

        Returns [(chainage, configured_radius, half_width), ...] sorted
        by chainage; empty if nothing's tight enough to flag, or there's
        no alignment yet."""
        flagged = []
        pts = self._terrain_raw_pts
        if self._terrain_geom is None or len(pts) <= 2:
            return flagged
        for i in range(1, len(pts) - 1):
            r = self._terrain_vertex_radii.get(i, self._terrain_fillet_radius)
            if r is None or r <= 1e-6:
                continue   # a true sharp corner — the miter join's own case
            try:
                ch_v = self._terrain_geom.lineLocatePoint(QgsGeometry.fromPointXY(pts[i]))
            except Exception:
                continue
            params = self._terrain_xs_interp_params(ch_v)
            half_w = self._terrain_params_half_width(params)
            if r < half_w:
                flagged.append((ch_v, r, half_w))
        flagged.sort(key=lambda t: t[0])
        return flagged

    def _terrain_tangent_angle(self, ch):
        """Local tangent direction (radians) of self._terrain_geom at
        chainage ch, via a small centred finite difference. Cheap and
        only needs to be roughly right — used solely to estimate local
        curvature for bend-adaptive station densification (see
        _terrain_densify_bend_stations), not for anything needing high
        precision."""
        total = self._terrain_geom.length()
        delta = min(0.25, max(total * 0.001, 0.01))
        ch1 = max(0.0, ch - delta)
        ch2 = min(total, ch + delta)
        if ch2 - ch1 < 1e-9:
            return 0.0
        p1 = self._terrain_geom.interpolate(ch1).asPoint()
        p2 = self._terrain_geom.interpolate(ch2).asPoint()
        return math.atan2(p2.y() - p1.y(), p2.x() - p1.x())

    def _terrain_densify_bend_stations(self, chs, spacing):
        """Inserts EXTRA chainages within tight bends so the OUTSIDE edge
        of the channel — which, at a bend, covers more real distance than
        the centreline does over that same chainage range — still gets
        sampled at roughly `spacing` m intervals, not just the centreline
        itself. For each consecutive pair of stations, estimates the
        local radius of curvature from how much the tangent direction
        rotates between them (angle / chainage-length), gets a rough
        half-width for that stretch from the interpolated cross-section
        params, and if the OUTSIDE edge's own arc length over that
        stretch (~ seg_len * (1 + half_width / radius)) exceeds spacing,
        subdivides it into enough equal steps to bring it back under.
        Straight runs (negligible tangent rotation) are left untouched —
        this only adds stations where the geometry actually needs them.
        Per-interval subdivision is capped (50 extra steps) so a
        near-zero-radius corner can't blow this up into an unbounded
        number of stations."""
        if len(chs) < 2:
            return chs
        out = [chs[0]]
        for ch_a, ch_b in zip(chs[:-1], chs[1:]):
            seg_len = ch_b - ch_a
            if seg_len <= 1e-9:
                out.append(ch_b)
                continue
            ang_a = self._terrain_tangent_angle(ch_a)
            ang_b = self._terrain_tangent_angle(ch_b)
            dtheta = abs(math.atan2(math.sin(ang_b - ang_a), math.cos(ang_b - ang_a)))
            if dtheta < 1e-6:
                out.append(ch_b)
                continue
            radius = seg_len / dtheta
            mid_params = self._terrain_xs_interp_params((ch_a + ch_b) / 2.0)
            half_w = (mid_params['bottom_width'] / 2.0
                      + max(mid_params['left_outer_slope'], mid_params['right_outer_slope'])
                        * mid_params['channel_height']
                      + max(mid_params['left_berm_width'], mid_params['right_berm_width']))
            outer_len = seg_len * (1.0 + half_w / max(radius, 0.01))
            n = min(50, max(1, math.ceil(outer_len / spacing)))
            if n > 1:
                for i in range(1, n):
                    out.append(ch_a + seg_len * i / n)
            out.append(ch_b)
        return out

    def _terrain_build_channel_corridor_data(self, station_spacing):
        """Builds the point/breakline data used to interpolate the
        channel design as a constrained TIN (see
        _generate_terrain_channel_tiff) — the same idea 12d/Civil 3D use
        for a corridor design surface:
        - one TRANSVERSE breakline per station — its own design cross-
          section, built exactly like the Check Cross-Section window
          builds one (_terrain_xs_build_interpolated_shape, so Topo
          Adjustment blending, the ground re-solve fix, and Earthwork
          backfill are all already correctly reflected).
        - the invert itself as ONE longitudinal breakline — already
          exactly known from the vertical alignment, no need to derive it
          from the cross-sections.
        - the two OUTERMOST left/right edges (each station's own first/
          last vertex — 'top of bank' either side) as two more
          longitudinal breaklines. These stay well-defined regardless of
          how many INTERIOR vertices (berm corners etc.) a given
          station's own shape happens to have, which can vary station to
          station (e.g. Flush = 2 points a side, Fixed = 4) — tracking
          every interior 'role' consistently across that varying vertex
          count isn't attempted; the dense per-station cross-sections
          (one every `station_spacing`) let the TIN interpolate between
          neighbouring stations' interior corners reasonably smoothly on
          their own.
        Real stations AND control sections' own exact chainages are
        always included as their own station, on top of the regular
        spacing grid, so a carefully-placed control section's shape is
        captured exactly rather than approximated by whichever regular
        grid point happens to be nearby.

        Extra stations are also inserted within tight BENDS (see
        _terrain_densify_bend_stations) — spacing stations evenly along
        the CENTRELINE under-samples the OUTSIDE edge of a bend, which
        covers more real distance than the centreline over the same
        chainage range (the tighter the bend relative to the channel's
        own half-width, the worse this gets) — left as-is, this produced
        a visibly faceted/irregular TIN surface specifically on the
        outside of sharp bends.

        A SHARP (un-filleted) corner's own forced-in station is built as
        a MITER JOIN rather than a normal perpendicular cross-section —
        oriented along the bisector of its two legs and scaled so its
        edges land exactly where the two legs' own offset edges would
        actually meet (see _terrain_vertex_miter_bisector) — the same
        technique a buffer/offset 'miter' join uses. Without this, a
        sharp corner's edges would either gap apart (outside) or overlap
        (inside) right at the point, which is what produced a twisted,
        pinched look specifically at hard (near-90°+) bends even once
        every station's own orientation was otherwise correct.

        Every vertex of every cross-section also becomes a POINT
        (redundant with the breaklines' own vertices — expected; the
        breaklines constrain the triangulation's edges, the points supply
        the actual elevation data).

        Returns (points, breaklines, bbox, footprint) — points:
        [(x,y,z), ...]; breaklines: [[(x,y,z), ...], ...]; bbox:
        QgsRectangle covering every generated point; footprint: a closed
        ring of (x, y) pairs tracing the corridor's own true outline
        (every station's left edge, down to the last station, back along
        every station's right edge) — this is what lets the raster get
        CLIPPED to the real swept channel shape afterwards instead of
        just its bounding rectangle, which otherwise triangulates/fills
        the rectangle's corners too (visible as extra fill poking out
        past the channel at a bend, since the rectangle doesn't taper the
        way the real corridor does). None (whole return) if there's no
        alignment/levels set up yet, or nothing could be built from
        them; footprint alone is None if there weren't enough edge
        points to close a ring."""
        if self._terrain_geom is None or self._terrain_us_level is None or self._terrain_ds_level is None:
            return None
        length = self._terrain_geom.length()
        spacing = max(0.01, station_spacing)
        chs = []
        ch = 0.0
        while ch < length - 1e-9:
            chs.append(ch)
            ch += spacing
        if chs and (length - chs[-1]) <= 1e-6:
            chs[-1] = length
        else:
            chs.append(length)
        anchor_chs = [ch for _k, ch in self._terrain_xs_all_anchor_list()]
        # A SHARP (un-filleted, or near-zero-radius) corner is kept as an
        # exact vertex in self._terrain_geom's own polyline (see
        # _fillet_alignment) — its own chainage is forced in as a station
        # here so the design shape is evaluated precisely AT the corner,
        # from both legs, rather than only ever approximated by whichever
        # regular-spacing grid point happens to land nearby. miter_at maps
        # that station's own (rounded) chainage back to its raw vertex
        # index, so the station-building loop below knows to orient and
        # scale its cross-section as a MITER join there (see
        # _terrain_vertex_miter_bisector) instead of using the normal
        # per-leg tangent.
        sharp_chs = []
        miter_at = {}
        if len(self._terrain_raw_pts) > 2:
            for i in range(1, len(self._terrain_raw_pts) - 1):
                r = self._terrain_vertex_radii.get(i, self._terrain_fillet_radius)
                if r is None or r <= 1e-6:
                    try:
                        ch_v = self._terrain_geom.lineLocatePoint(
                            QgsGeometry.fromPointXY(self._terrain_raw_pts[i]))
                    except Exception:
                        continue
                    sharp_chs.append(ch_v)
                    miter_at[round(ch_v, 6)] = i
        chs = sorted(set(round(c, 6) for c in chs) | set(round(c, 6) for c in anchor_chs)
                     | set(round(c, 6) for c in sharp_chs))
        chs = sorted(set(round(c, 6) for c in self._terrain_densify_bend_stations(chs, spacing)))

        points = []
        breaklines = []
        left_edge = []
        right_edge = []
        invert_line = []

        geom_cache = self._terrain_corridor_geom_cache(self.profile_geom)
        for i, ch in enumerate(chs):
            if i % 200 == 0:
                QApplication.processEvents()
            invert_level = self._terrain_invert_level_at(ch)
            params = self._terrain_xs_interp_params(ch)
            # Resolve the miter direction/factor BEFORE sampling ground —
            # the ground sample itself needs to follow the corner's
            # bisector and reach as far as the final (scaled) edges will,
            # not the normal per-leg direction/extent (see
            # _terrain_xs_sample_ground's own docstring for why this
            # ordering, not just the final point conversion, matters).
            direction = None
            miter_factor = 1.0
            vtx = miter_at.get(round(ch, 6))
            if vtx is not None:
                miter = self._terrain_vertex_miter_bisector(vtx)
                if miter is not None:
                    direction = (miter[0], miter[1])
                    miter_factor = miter[2]
            ground = self._terrain_xs_sample_ground(ch, params, direction=direction, scale=miter_factor)
            poly_x, poly_y = self._terrain_xs_build_interpolated_shape(ch, invert_level, ground)
            if not poly_x:
                continue
            if direction is not None:
                poly_x = [xo * miter_factor for xo in poly_x]
            world_pts = self._terrain_xs_local_points_to_world(
                ch, poly_x, poly_y, geom_cache, direction=direction)
            if not world_pts:
                continue
            breaklines.append(world_pts)
            points.extend(world_pts)
            left_edge.append(world_pts[0])
            right_edge.append(world_pts[-1])
            centre = self._terrain_xs_local_points_to_world(ch, [0.0], [invert_level], geom_cache)
            if centre:
                invert_line.append(centre[0])

        if not points:
            return None
        if len(left_edge) >= 2:
            breaklines.append(left_edge)
        if len(right_edge) >= 2:
            breaklines.append(right_edge)
        if len(invert_line) >= 2:
            breaklines.append(invert_line)

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox = QgsRectangle(min(xs), min(ys), max(xs), max(ys))

        footprint = None
        if len(left_edge) >= 2 and len(right_edge) >= 2:
            ring = [(x, y) for x, y, _z in left_edge] + [(x, y) for x, y, _z in reversed(right_edge)]
            ring.append(ring[0])
            footprint = ring

        return points, breaklines, bbox, footprint

    def _generate_terrain_channel_tiff(self, resolution, dem_layer):
        """Builds the channel design surface as a constrained-Delaunay TIN
        (breaklines = every station's own cross-section, the invert, and
        both top-of-bank edges — see _terrain_build_channel_corridor_data)
        and rasterises it to a GeoTIFF, using QGIS's OWN native TIN
        interpolation engine (qgis.analysis.QgsTinInterpolator /
        QgsGridFileWriter — the exact classes the 'TIN interpolation'
        Processing algorithm itself calls internally) rather than a
        third-party triangulation library or a plain scattered-point
        interpolator — no new dependency, and it's the same engine that
        already ships with every QGIS install. This produces the channel
        design ONLY; merging it into a copy of the existing DEM is Merge
        TIFF's separate job, so the result can be checked on its own
        first.

        dem_layer, if given, affects ONLY the output grid's ORIGIN
        (snapped onto its own pixel grid using the resolution given here)
        so a later merge lines up pixel-for-pixel — it never changes the
        output CRS, which is always the map canvas's own.

        The raw TIN raster is then CLIPPED to the corridor's own true
        footprint polygon (every station's left edge out, right edge
        back — see _terrain_build_channel_corridor_data) rather than
        left at its bounding rectangle: pixels outside the real swept
        channel shape are set to NoData via GDAL's own cutline clip
        (gdal:cliprasterbymasklayer, CROP_TO_CUTLINE=False so the extent/
        resolution stay exactly as generated — only pixels outside the
        polygon change). Without this, a bend or taper in the alignment
        left triangulated fill sitting in the rectangle's corners, well
        outside the actual channel.

        Returns the new QgsRasterLayer (already added to the project).
        Raises RuntimeError with a user-facing message on failure."""
        data = self._terrain_build_channel_corridor_data(resolution)
        if data is None:
            raise RuntimeError(
                'No channel design to generate from — set up the horizontal\n'
                'alignment and both end levels first.')
        points, breaklines, bbox, footprint = data

        crs = self.canvas.mapSettings().destinationCrs()

        pts_layer = QgsVectorLayer(f'PointZ?crs={crs.authid()}', 'channel_points', 'memory')
        pts_layer.startEditing()
        for x, y, z in points:
            feat = QgsFeature(pts_layer.fields())
            feat.setGeometry(QgsGeometry(QgsPoint(x, y, z)))
            pts_layer.addFeature(feat)
        pts_layer.commitChanges()

        lines_layer = QgsVectorLayer(f'LineStringZ?crs={crs.authid()}', 'channel_breaklines', 'memory')
        lines_layer.startEditing()
        for pts in breaklines:
            if len(pts) < 2:
                continue
            feat = QgsFeature(lines_layer.fields())
            feat.setGeometry(QgsGeometry(QgsLineString([QgsPoint(x, y, z) for x, y, z in pts])))
            lines_layer.addFeature(feat)
        lines_layer.commitChanges()

        margin = resolution
        x_min, x_max = bbox.xMinimum() - margin, bbox.xMaximum() + margin
        y_min, y_max = bbox.yMinimum() - margin, bbox.yMaximum() + margin

        if dem_layer is not None:
            dem_extent = dem_layer.extent()
            if dem_layer.crs() != crs:
                try:
                    xform = QgsCoordinateTransform(dem_layer.crs(), crs, QgsProject.instance())
                    dem_extent = xform.transformBoundingBox(dem_extent)
                except Exception:
                    pass
            dem_x0, dem_y0 = dem_extent.xMinimum(), dem_extent.yMinimum()
            x_min = dem_x0 + math.floor((x_min - dem_x0) / resolution) * resolution
            y_min = dem_y0 + math.floor((y_min - dem_y0) / resolution) * resolution
            x_max = dem_x0 + math.ceil((x_max - dem_x0) / resolution) * resolution
            y_max = dem_y0 + math.ceil((y_max - dem_y0) / resolution) * resolution

        extent = QgsRectangle(x_min, y_min, x_max, y_max)
        if extent.width() <= 0 or extent.height() <= 0 or not math.isfinite(extent.width()) \
                or not math.isfinite(extent.height()):
            raise RuntimeError(
                'Computed output extent is empty or invalid '
                f'({extent.width():.3f} x {extent.height():.3f} {crs.authid()} units).\n'
                'This usually means the Reference DEM is in a very different\n'
                "location/CRS than the channel alignment, or doesn't actually\n"
                'overlap it — try running Create TIFF again without a\n'
                'Reference DEM to check.')
        columns = max(1, math.ceil(extent.width() / resolution))
        rows = max(1, math.ceil(extent.height() / resolution))
        if columns * rows > 40_000_000:
            raise RuntimeError(
                f'Requested grid is {columns} x {rows} = {columns * rows:,} cells\n'
                f'for a {extent.width():.1f} x {extent.height():.1f} m extent at\n'
                f'{resolution} m resolution — too fine for this extent. Use a\n'
                'coarser Resolution, or check the Reference DEM actually\n'
                'overlaps the channel (a mismatched one can inflate the\n'
                'extent enormously).')

        feedback = QgsFeedback()
        layer_data = []
        for src, source_type in ((pts_layer, QgsInterpolator.SourcePoints),
                                   (lines_layer, QgsInterpolator.SourceBreakLines)):
            d = QgsInterpolator.LayerData()
            d.source = src
            d.transformContext = QgsProject.instance().transformContext()
            d.valueSource = QgsInterpolator.ValueZ
            d.interpolationAttribute = -1
            d.sourceType = source_type
            layer_data.append(d)

        interpolator = QgsTinInterpolator(layer_data, QgsTinInterpolator.Linear, feedback)
        raw_path = QgsProcessingUtils.generateTempFilename('channel_design_raw.tif')
        writer = QgsGridFileWriter(interpolator, raw_path, extent, columns, rows)
        writer.writeFile(feedback)

        raw_layer = QgsRasterLayer(raw_path, 'Channel Design (raw)')
        if not raw_layer.isValid():
            raise RuntimeError(
                'The raw TIN raster could not be written/loaded — this is\n'
                'before the clip step, so it points at the triangulation\n'
                'itself rather than the clip.\n'
                f'Points: {len(points)}, breaklines: {len(breaklines)}, '
                f'grid: {columns} x {rows} @ {resolution} m\n'
                f'({extent.xMinimum():.1f}, {extent.yMinimum():.1f}) - '
                f'({extent.xMaximum():.1f}, {extent.yMaximum():.1f}) {crs.authid()}.\n'
                'Check the main ground DEM (Raster Layers (DEM), in the\n'
                "Vertical Alignment section) is still actually selected —\n"
                "it can be lost after Open if that layer couldn't be\n"
                're-found automatically.')

        out_path = raw_path
        clip_note = ''
        if footprint is not None and len(footprint) >= 4:
            # A tight bend (the inside radius of the curve tighter than
            # the channel's own half-width) can make this ring self-
            # intersect — the left/right edges cross over each other
            # instead of staying a simple outline, i.e. a 'bowtie'
            # polygon. GDAL's cutline clip can quietly fail on that (no
            # exception — just an empty/invalid output, which is the
            # 'Clip step produced no valid output' fallback below).
            # makeValid() repairs a self-intersecting ring into a valid
            # (Multi)Polygon GDAL can actually use as a cutline — it can
            # lose a sliver right at the self-intersection, but stays a
            # true-to-shape mask everywhere else, which is a solid
            # trade-off against just not clipping that bend at all.
            mask_geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in footprint]])
            if not mask_geom.isGeosValid():
                mask_geom = mask_geom.makeValid()
                # A genuinely self-intersecting ring's repair can come
                # back as a GeometryCollection mixing polygon pieces with
                # degenerate line/point slivers at the crossing — a
                # strictly-typed 'Polygon' memory layer below silently
                # REJECTS anything that isn't a clean (Multi)Polygon,
                # which is what was leaving the mask layer with zero
                # features (and the clip with nothing to cut against,
                # hence 'no valid output') even though makeValid() itself
                # hadn't failed. Keep only the real polygon part(s) and
                # re-union them into one clean (Multi)Polygon geometry.
                if mask_geom.type() != _POLYGON_GEOM:
                    parts = [g for g in mask_geom.asGeometryCollection()
                              if g.type() == _POLYGON_GEOM and not g.isEmpty()]
                    mask_geom = QgsGeometry.unaryUnion(parts) if parts else QgsGeometry()
            mask_geom.convertToMultiType()
            mask_layer = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', 'channel_footprint', 'memory')
            mask_layer.startEditing()
            feat = QgsFeature(mask_layer.fields())
            feat.setGeometry(mask_geom)
            mask_added = (not mask_geom.isEmpty()) and mask_layer.addFeature(feat)
            mask_layer.commitChanges()
            if not mask_added or mask_layer.featureCount() == 0:
                clip_note = ('\n\n(Could not build a valid clip shape from this alignment '
                              '— likely an extremely tight bend even the repair step '
                              "couldn't resolve — showing the unclipped rectangle instead. "
                              'Try a larger fillet radius on the sharpest bend.)')
            else:
                try:
                    import processing
                    clip_path = QgsProcessingUtils.generateTempFilename('channel_design.tif')
                    processing.run('gdal:cliprasterbymasklayer', {
                        'INPUT': raw_path, 'MASK': mask_layer,
                        'SOURCE_CRS': crs, 'TARGET_CRS': crs,
                        'NODATA': -9999.0, 'ALPHA_BAND': False,
                        'CROP_TO_CUTLINE': False, 'KEEP_RESOLUTION': True,
                        'MULTITHREADING': False, 'OUTPUT': clip_path,
                    })
                    # processing.run() can return normally without actually
                    # having written a valid file (e.g. an OUTPUT path quirk,
                    # or the GDAL step itself failing quietly) — checked
                    # explicitly here rather than trusting the absence of an
                    # exception, so a bad clip falls back to the known-good
                    # raw raster instead of taking the WHOLE Create TIFF down
                    # with it.
                    clip_layer = QgsRasterLayer(clip_path, 'Channel Design (clip check)')
                    if clip_layer.isValid():
                        out_path = clip_path
                    else:
                        clip_note = ('\n\n(Clip step produced no valid output — showing the '
                                      'unclipped rectangle instead. Most likely cause: a tight '
                                      "bend in the alignment made the channel's own outline "
                                      'self-intersect, which the repair above could not fully '
                                      'resolve — try a larger fillet radius on that bend.)')
                except Exception as exc:
                    # Falls back to the unclipped rectangle rather than
                    # failing the whole Create TIFF — e.g. the 'gdal'
                    # Processing provider not being available for some
                    # reason. The rest of the design is still correct, it
                    # just has the rectangle-corner fill this clip exists to
                    # remove.
                    clip_note = f'\n\n(Clip step failed ({exc}) — showing the unclipped rectangle instead.)'

        layer = QgsRasterLayer(out_path, 'Channel Design (temp)')
        if not layer.isValid():
            raise RuntimeError(
                'The generated TIFF could not be loaded as a raster layer.\n'
                f'Points: {len(points)}, breaklines: {len(breaklines)}, '
                f'grid: {columns} x {rows} @ {resolution} m, '
                f'output: {out_path}{clip_note}')
        if clip_note:
            QMessageBox.warning(self, 'Advanced Profile Tool', clip_note.strip())
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _on_terrain_merge_tiff(self):
        """Pastes a previously created channel-design TIFF into a copy of
        an existing LiDAR/ground raster, replacing only the corridor's own
        footprint (same idea as a GIS/CAD 'paste surface'). See
        _run_merge_tiff_dialog / _merge_lidar_with_design."""
        self._run_merge_tiff_dialog('Channel', self._terrain_last_tiff_layer)

    # ------------------------------------------------------------------ Merge TIFF
    # Shared by both Channel Surface and Embankment Surface tabs — ported
    # from the standalone MergeLiDARWithDesignSurface Processing algorithm
    # (Flood Pre-Modelling library) so Merge TIFF can run it directly
    # without going through the Processing Toolbox. The merged output is
    # pixel-perfect identical to the LiDAR everywhere outside the design
    # surface's own footprint; a difference raster (merged minus LiDAR) is
    # also produced for a quick visual QA of the ground change.

    _MERGE_RESAMPLE_OPTIONS = ['Bilinear', 'Nearest Neighbour', 'Cubic', 'Cubic Spline', 'Lanczos']

    @staticmethod
    def _merge_gdal_resample_alg(gdal_mod, index):
        algs = [
            gdal_mod.GRA_Bilinear,
            gdal_mod.GRA_NearestNeighbour,
            gdal_mod.GRA_Cubic,
            gdal_mod.GRA_CubicSpline,
            gdal_mod.GRA_Lanczos,
        ]
        return algs[index]

    @staticmethod
    def _merge_gdal_to_numpy_dtype(gdal_mod, gdal_dtype):
        mapping = {
            gdal_mod.GDT_Byte:    np.uint8,
            gdal_mod.GDT_UInt16:  np.uint16,
            gdal_mod.GDT_Int16:   np.int16,
            gdal_mod.GDT_UInt32:  np.uint32,
            gdal_mod.GDT_Int32:   np.int32,
            gdal_mod.GDT_Float32: np.float32,
            gdal_mod.GDT_Float64: np.float64,
        }
        return mapping.get(gdal_dtype, np.float32)

    def _merge_lidar_with_design(self, lidar_layer, design_layer, resample_idx, output_path, diff_path):
        """Overlays design_layer onto lidar_layer, replacing only the
        pixels the design surface covers, and writes:
          - output_path: merged raster — same grid/CRS/nodata/dtype as the
            LiDAR, identical to it everywhere outside the design footprint
          - diff_path: difference raster (merged minus LiDAR), non-zero
            only within the design footprint, nodata (-9999) elsewhere
        Raises RuntimeError with a user-facing message on failure. Returns
        (n_valid, n_total, diff_min, diff_max)."""
        try:
            from osgeo import gdal
        except ImportError:
            raise RuntimeError('GDAL Python bindings are not available in this QGIS install.')
        if not MATPLOTLIB_AVAILABLE:
            raise RuntimeError('NumPy is not available in this QGIS install.')

        lidar_path   = lidar_layer.source()
        design_path  = design_layer.source()
        resample_alg = self._merge_gdal_resample_alg(gdal, resample_idx)

        lidar_ds = gdal.Open(lidar_path, gdal.GA_ReadOnly)
        if not lidar_ds:
            raise RuntimeError(f'Could not open LiDAR raster: {lidar_path}')

        lidar_band   = lidar_ds.GetRasterBand(1)
        lidar_gt     = lidar_ds.GetGeoTransform()
        lidar_proj   = lidar_ds.GetProjection()
        lidar_cols   = lidar_ds.RasterXSize
        lidar_rows   = lidar_ds.RasterYSize
        lidar_nodata = lidar_band.GetNoDataValue()
        lidar_dtype  = lidar_band.DataType
        numpy_dtype  = self._merge_gdal_to_numpy_dtype(gdal, lidar_dtype)

        x_min = lidar_gt[0]
        x_res = lidar_gt[1]
        y_max = lidar_gt[3]
        y_res = abs(lidar_gt[5])
        x_max = x_min + x_res * lidar_cols
        y_min = y_max - y_res * lidar_rows

        tmp_dir     = tempfile.mkdtemp(prefix='advprofiletool_merge_')
        warped_path = os.path.join(tmp_dir, 'warped_design.tif')

        warp_opts = gdal.WarpOptions(
            outputBounds=(x_min, y_min, x_max, y_max),
            xRes=x_res, yRes=y_res,
            dstSRS=lidar_proj,
            resampleAlg=resample_alg,
            outputType=gdal.GDT_Float64,
            creationOptions=['COMPRESS=LZW'],
        )
        warped_ds = gdal.Warp(warped_path, design_path, options=warp_opts)
        if not warped_ds:
            raise RuntimeError(
                'Failed to warp the design surface onto the LiDAR grid. '
                'Check that the design surface raster is valid and readable.')
        warped_ds = None

        lidar_array = lidar_band.ReadAsArray().astype(np.float64)
        lidar_ds = None

        warped_ds = gdal.Open(warped_path, gdal.GA_ReadOnly)
        if not warped_ds:
            raise RuntimeError(f'Could not re-open warped design surface at: {warped_path}')
        warped_band        = warped_ds.GetRasterBand(1)
        design_array       = warped_band.ReadAsArray().astype(np.float64)
        design_nodata_warp = warped_band.GetNoDataValue()
        warped_ds = None

        if design_nodata_warp is not None:
            valid_mask = ~np.isnan(design_array) & (design_array != design_nodata_warp)
        else:
            valid_mask = ~np.isnan(design_array)

        n_valid = int(valid_mask.sum())
        n_total = lidar_rows * lidar_cols

        output_array = lidar_array.copy()
        output_array[valid_mask] = design_array[valid_mask]

        DIFF_NODATA = -9999.0
        diff_array = np.full((lidar_rows, lidar_cols), DIFF_NODATA, dtype=np.float32)
        diff_array[valid_mask] = (output_array[valid_mask] - lidar_array[valid_mask]).astype(np.float32)

        diff_min = float(diff_array[valid_mask].min()) if n_valid > 0 else 0.0
        diff_max = float(diff_array[valid_mask].max()) if n_valid > 0 else 0.0

        driver = gdal.GetDriverByName('GTiff')

        out_ds = driver.Create(
            output_path, lidar_cols, lidar_rows, 1, lidar_dtype,
            options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
        )
        if not out_ds:
            raise RuntimeError(f'Failed to create merged output raster at: {output_path}')
        out_ds.SetGeoTransform(lidar_gt)
        out_ds.SetProjection(lidar_proj)
        out_band = out_ds.GetRasterBand(1)
        if lidar_nodata is not None:
            out_band.SetNoDataValue(lidar_nodata)
        out_band.WriteArray(output_array.astype(numpy_dtype))
        out_band.FlushCache()
        out_ds = None

        diff_ds = driver.Create(
            diff_path, lidar_cols, lidar_rows, 1, gdal.GDT_Float32,
            options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'],
        )
        if not diff_ds:
            raise RuntimeError(f'Failed to create difference raster at: {diff_path}')
        diff_ds.SetGeoTransform(lidar_gt)
        diff_ds.SetProjection(lidar_proj)
        diff_band = diff_ds.GetRasterBand(1)
        diff_band.SetNoDataValue(DIFF_NODATA)
        diff_band.WriteArray(diff_array)
        diff_band.FlushCache()
        diff_ds = None

        try:
            os.remove(warped_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass

        return n_valid, n_total, diff_min, diff_max

    _MERGE_TIFF_HELP_HTML = (
        '<h2>Merge TIFF</h2>'
        '<p><b>Author:</b> Dipendra Magaju</p>'

        '<h3>Purpose</h3>'
        '<p>Creates a merged elevation raster for flood displacement analysis. Overlays the '
        'design surface (from this tab\'s own Create TIFF) onto an existing LiDAR DEM, '
        'modifying only the area the design covers. The output is otherwise pixel-perfect '
        'identical to the input LiDAR, enabling a true like-for-like flood model comparison. '
        'A difference raster (merged minus LiDAR) is also produced for quick visual QA of the '
        'ground change.</p>'

        '<h3>Methodology</h3>'
        '<ol>'
        '<li>Reads the LiDAR extent, resolution, CRS, nodata value, and data type</li>'
        '<li>Reprojects and resamples the design surface to exactly match the LiDAR pixel grid</li>'
        '<li>Copies the LiDAR array as the starting output</li>'
        '<li>Identifies valid (non-nodata) pixels in the resampled design surface — its own '
        'footprint, not just its bounding rectangle</li>'
        '<li>Replaces LiDAR values with design surface values at those pixels only</li>'
        '<li>Computes difference = merged minus LiDAR (non-zero only within the design footprint)</li>'
        '<li>Writes the merged raster (permanent) and difference raster (temporary) to output</li>'
        '</ol>'

        '<h3>Inputs</h3>'
        '<ul>'
        '<li><b>Base Surface:</b> Single-band raster DEM (existing LiDAR). Defines the output grid</li>'
        '<li><b>Design Surface:</b> Single-band raster of the proposed ground modification (this '
        'tab\'s own Create TIFF output, pre-selected automatically). Any extent, resolution, or '
        'CRS — reprojection and resampling handled automatically. Nodata pixels are treated as '
        '"no change"</li>'
        '<li><b>Resampling Method:</b> Algorithm for resampling the design surface onto the LiDAR grid</li>'
        '</ul>'

        '<h3>Resampling Methods — Rule of Thumb</h3>'
        '<table border="1" cellpadding="4" cellspacing="0">'
        '<tr><th>Situation</th><th>Recommended Method</th><th>Reason</th></tr>'
        '<tr><td>Design same resolution as LiDAR</td><td><b>Nearest Neighbour</b></td>'
        '<td>Grids may still be offset — NN snaps without blending sharp edges</td></tr>'
        '<tr><td>Design finer than LiDAR (e.g. 0.5 m design, 1 m LiDAR)</td><td><b>Bilinear</b></td>'
        '<td>Smooth weighted average — best for continuous elevation downsampling</td></tr>'
        '<tr><td>Design coarser than LiDAR (e.g. 5 m design, 1 m LiDAR)</td><td><b>Bilinear / Cubic</b></td>'
        '<td>Bilinear is safe; Cubic gives slightly smoother results</td></tr>'
        '<tr><td>Significant upsampling (design much coarser than LiDAR)</td><td><b>Lanczos</b></td>'
        '<td>Highest quality sinc-based filter, best detail preservation</td></tr>'
        '</table>'
        '<p><i>Default (Bilinear) is a safe choice in most cases.</i></p>'

        '<h3>Outputs</h3>'
        '<ul>'
        '<li><b>Merged Raster:</b> GeoTIFF with LZW compression. Identical grid to LiDAR. '
        'Pixels outside the design footprint are unchanged from the LiDAR</li>'
        '<li><b>Difference Raster (temporary):</b> Float32 GeoTIFF. Values = merged minus LiDAR, '
        'non-zero only within the design footprint. Positive = ground raised, Negative = ground '
        'lowered. Nodata (-9999) everywhere outside the design footprint</li>'
        '</ul>'

        '<h3>Important Notes</h3>'
        '<ul>'
        '<li>Both inputs must be single-band elevation rasters</li>'
        '<li>The entire LiDAR raster is loaded into memory — ensure sufficient RAM for large datasets</li>'
        '<li>CRS differences between inputs are handled automatically via reprojection</li>'
        '<li>Requires GDAL and NumPy (both included with QGIS)</li>'
        '</ul>'

        '<h3>Typical flood modelling workflow</h3>'
        '<ol>'
        '<li>Run flood model with existing LiDAR → Base Case</li>'
        '<li>Run flood model with merged raster → Design Case</li>'
        '<li>Use the ICM Difference Maps tool to compare Base vs Design flood extents/depths</li>'
        '</ol>'
    )

    def _show_merge_tiff_help(self, parent):
        dlg = QDialog(parent)
        dlg.setWindowTitle('Merge TIFF — Help')
        dlg.resize(560, 540)
        v = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._MERGE_TIFF_HELP_HTML)
        v.addWidget(browser, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton('Close')
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        v.addLayout(btn_row)
        dlg.exec()

    def _run_merge_tiff_dialog(self, surface_label, default_design_layer):
        """Shared 'Merge TIFF' dialog for Channel Surface and Embankment
        Surface — pick the existing LiDAR raster + the design TIFF
        (defaults to whatever this tab's own Create TIFF last produced),
        a resampling method, and where to save the merged raster. The
        difference raster is loaded as a temporary layer for a quick
        visual check, same convention as the algorithm it's ported from."""
        dlg = QDialog(self)
        dlg.setWindowTitle('Merge TIFF')
        v = QVBoxLayout(dlg)

        title_row = QHBoxLayout()
        title_lbl = QLabel(f'Merge {surface_label} Surface')
        title_lbl.setStyleSheet('font-weight:bold;')
        title_row.addWidget(title_lbl, 1)
        btn_help = QToolButton()
        btn_help.setText('?')
        btn_help.setFixedSize(20, 20)
        btn_help.setToolTip('Help — what this does, outputs, and resampling method guidance')
        btn_help.setStyleSheet(
            'QToolButton { border: 1px solid #888; border-radius: 10px; font-weight: bold; }'
            'QToolButton:hover { background: #E0E0E0; }'
        )
        btn_help.clicked.connect(lambda: self._show_merge_tiff_help(dlg))
        title_row.addWidget(btn_help)
        v.addLayout(title_row)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Base Surface:'))
        combo_lidar = QgsMapLayerComboBox()
        combo_lidar.setFilters(_RASTER_FILTER)
        combo_lidar.setAllowEmptyLayer(True)
        combo_lidar.setCurrentIndex(0)
        combo_lidar.wheelEvent = lambda e: e.ignore()
        row1.addWidget(combo_lidar, 1)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(f'{surface_label} Design Surface:'))
        combo_design = QgsMapLayerComboBox()
        combo_design.setFilters(_RASTER_FILTER)
        combo_design.setAllowEmptyLayer(True)
        combo_design.setCurrentIndex(0)
        combo_design.wheelEvent = lambda e: e.ignore()
        if default_design_layer is not None:
            try:
                combo_design.setLayer(default_design_layer)
            except Exception:
                pass
        row2.addWidget(combo_design, 1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel('Resampling Method:'))
        combo_resample = QComboBox()
        combo_resample.addItems(self._MERGE_RESAMPLE_OPTIONS)
        combo_resample.setCurrentIndex(0)
        combo_resample.wheelEvent = lambda e: e.ignore()
        row3.addWidget(combo_resample, 1)
        v.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel('Output Merged Raster:'))
        edit_out = QLineEdit()
        default_name = f'{surface_label}_Merged_' + datetime.now().strftime('%Y-%m-%d') + '.tif'
        edit_out.setText(os.path.join(self._default_output_folder(), default_name))
        row4.addWidget(edit_out, 1)
        btn_browse = QPushButton('…')
        btn_browse.setFixedWidth(28)
        btn_browse.setStyleSheet(_style.BROWSE_BUTTON_STYLE)

        def _browse():
            path, _ = QFileDialog.getSaveFileName(
                dlg, 'Save Merged Raster', edit_out.text(),
                'GeoTIFF (*.tif);;All Files (*)')
            if path:
                if not path.lower().endswith('.tif'):
                    path += '.tif'
                edit_out.setText(path)
        btn_browse.clicked.connect(_browse)
        row4.addWidget(btn_browse)
        v.addLayout(row4)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_run = QPushButton('Run')
        btn_run.setStyleSheet('font-weight:bold;')
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_run)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _do_run():
            lidar_layer  = combo_lidar.currentLayer()
            design_layer = combo_design.currentLayer()
            if lidar_layer is None:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    'Select the existing LiDAR raster first.')
                return
            if design_layer is None:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    f'Select the {surface_label.lower()} design surface first\n'
                    '— run Create TIFF on this tab if you haven\'t yet.')
                return
            output_path = edit_out.text().strip()
            if not output_path:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    'Choose where to save the merged raster first.')
                return
            if not output_path.lower().endswith('.tif'):
                output_path += '.tif'
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(self, 'Advanced Profile Tool',
                                      f'Could not create output folder:\n{exc}')
                return
            diff_path = QgsProcessingUtils.generateTempFilename(
                f'{surface_label.lower()}_merge_difference.tif')
            resample_idx = combo_resample.currentIndex()

            btn_run.setEnabled(False)
            QApplication.setOverrideCursor(_WAIT_CURSOR)
            try:
                n_valid, n_total, diff_min, diff_max = self._merge_lidar_with_design(
                    lidar_layer, design_layer, resample_idx, output_path, diff_path)
            except Exception as exc:
                QApplication.restoreOverrideCursor()
                btn_run.setEnabled(True)
                QMessageBox.critical(self, 'Advanced Profile Tool', f'Merge TIFF failed:\n{exc}')
                return
            QApplication.restoreOverrideCursor()

            merged_layer = QgsRasterLayer(output_path, os.path.splitext(os.path.basename(output_path))[0])
            if merged_layer.isValid():
                QgsProject.instance().addMapLayer(merged_layer)
            diff_layer = QgsRasterLayer(diff_path, f'{surface_label} Merge Difference')
            if diff_layer.isValid():
                QgsProject.instance().addMapLayer(diff_layer)

            dlg.accept()
            pct = (100.0 * n_valid / n_total) if n_total else 0.0
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                f"Merged raster created: '{os.path.basename(output_path)}'.\n"
                f'Design surface covers {n_valid:,} of {n_total:,} pixels ({pct:.2f}%).\n'
                f'Difference range within design footprint: {diff_min:.3f} m to {diff_max:.3f} m.')

        btn_run.clicked.connect(_do_run)
        dlg.exec()

    def _on_terrain_invert_levels_changed(self):
        self._terrain_us_level = self.spin_terrain_us_level.value()
        self._terrain_ds_level = self.spin_terrain_ds_level.value()
        self._draw_terrain_vertical_decorations()

    def _refresh_terrain_diff_combo(self):
        """Keep the 'vs:' raster picker in sync with whatever DEM(s) are
        currently in the Raster Layers list, without losing the user's
        current pick if it's still valid."""
        raster_entries, _ve, _cm = self._collect_entries(
            raster_rows=self._terrain_raster_rows, vector_rows=[])
        cols = [col for _lyr, col in raster_entries]
        combo = self.combo_terrain_diff_col
        cur = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(cols)
        if cur in cols:
            combo.setCurrentText(cur)
        elif cols:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _on_terrain_diff_changed(self):
        self._terrain_show_diff = self.chk_terrain_diff.isChecked()
        if self._terrain_diff_ann is not None:
            self._terrain_diff_ann.set_visible(False)
            if MATPLOTLIB_AVAILABLE:
                self.canvas_plot.draw_idle()

    def _update_terrain_diff_annotation(self, ch):
        """Ground (picked raster) minus invert at chainage ch — used both
        on plain hover AND while a break point is being dragged, so the
        excavation depth stays visible and live-updates the whole time a
        break is being moved, not just when the mouse is merely hovering."""
        ann = self._terrain_diff_ann
        if ann is None:
            return
        if not self._terrain_show_diff or not MATPLOTLIB_AVAILABLE:
            ann.set_visible(False)
            return
        diff_col = self.combo_terrain_diff_col.currentText()
        gvals = self._profile_data_store.get(diff_col) if diff_col else None
        if not diff_col or not gvals or not self._profile_chainages:
            ann.set_visible(False)
            return
        clean = [v if v is not None else float('nan') for v in gvals]
        gval = float(np.interp(ch, self._profile_chainages, clean))
        if not np.isfinite(gval):
            ann.set_visible(False)
            return
        inv_val = self._terrain_invert_level_at(ch)
        delta = gval - inv_val
        ann.set_text(f'Excavation Δ: {delta:+.3f} m')
        ann.set_visible(True)

    def _toggle_terrain_break_tool(self, checked):
        self._terrain_add_break_mode = checked
        if checked:
            self._terrain_deactivate_all_tools(except_btn=self.btn_terrain_break)
        if not hasattr(self, 'lbl_terrain_status'):
            return
        if checked:
            self.lbl_terrain_status.setText(
                'Click along the invert line in the chart to add a break point.')
            self.lbl_terrain_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        elif self._terrain_geom is not None:
            length = self._terrain_geom.length()
            self.lbl_terrain_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
            self.lbl_terrain_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

    def _toggle_terrain_control_tool(self, checked):
        self._terrain_add_control_mode = checked
        if checked:
            self._terrain_deactivate_all_tools(except_btn=self.btn_terrain_control)
        if not hasattr(self, 'lbl_terrain_status'):
            return
        if checked:
            self.lbl_terrain_status.setText(
                'Click along the invert line in the chart to add a control section.')
            self.lbl_terrain_status.setStyleSheet('color:#00897B;font-style:italic;font-size:11px;')
        elif self._terrain_geom is not None:
            length = self._terrain_geom.length()
            self.lbl_terrain_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
            self.lbl_terrain_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

    def _on_terrain_bank_analysis_changed(self, _state):
        self._terrain_show_bank_analysis = self.chk_terrain_bank_analysis.isChecked()
        self._draw_terrain_vertical_decorations()

    def _add_terrain_break(self, xdata):
        if self._terrain_geom is None or self._terrain_us_level is None or self._terrain_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set the upstream and downstream levels first — the invert\n'
                'line needs both ends defined before you can add a break point.')
            return
        length = self._terrain_geom.length()
        ch = max(0.0, min(float(xdata), length))
        level = self._terrain_invert_level_at(ch)
        # Clicking very close to an existing break re-levels it in place
        # (at its current chainage) rather than adding a near-duplicate.
        tol = max(0.05, length * 0.002)
        for b in self._terrain_invert_breaks:
            if abs(b['chainage'] - ch) < tol:
                b['level'] = self._terrain_invert_level_at(b['chainage'])
                self._select_terrain_xs_node(id(b))
                return
        new_break = {'chainage': ch, 'level': level}
        self._terrain_invert_breaks.append(new_break)
        self._terrain_invert_breaks.sort(key=lambda b: b['chainage'])
        # Newly placed break becomes the selected cross-section station too
        # — you just created it, so seeing/setting its section right away
        # is the natural next step.
        self._select_terrain_xs_node(id(new_break))

    def _remove_terrain_break(self, idx):
        """Right-click on a break marker removes it — no separate 'delete'
        tool needed, and it can't be confused with left-click drag/add."""
        if not (0 <= idx < len(self._terrain_invert_breaks)):
            return
        removed = self._terrain_invert_breaks.pop(idx)
        if self._terrain_break_drag_obj is removed:
            self._terrain_break_drag_obj = None
        # The removed break's own two segments merge into one — whichever
        # cross-section was selected no longer corresponds to a real
        # segment, so it isn't safe to keep pointing at it.
        if self._terrain_selected_xs_key == id(removed):
            self._clear_terrain_xs_selection()
        self._draw_terrain_vertical_decorations()

    def _clear_terrain_xs_selection(self):
        """Resets the Cross-Section editor to its empty state — shared by
        removing whichever break point or control section is currently
        selected, since either one leaves the editor with nothing valid
        to point at."""
        self._terrain_selected_xs_key = None
        self._terrain_xs_ground = None
        for spin in (self.spin_terrain_xs_bottom, self.spin_terrain_xs_height,
                     self.spin_terrain_xs_left_inner, self.spin_terrain_xs_left_outer,
                     self.spin_terrain_xs_left_berm, self.combo_terrain_xs_left,
                     self.spin_terrain_xs_right_inner, self.spin_terrain_xs_right_outer,
                     self.spin_terrain_xs_right_berm, self.combo_terrain_xs_right,
                     self.spin_terrain_xs_min_embank, self.spin_terrain_xs_backfill_thresh,
                     self.btn_terrain_xs_copy_ds, self.btn_terrain_xs_copy_all,
                     self.btn_terrain_xs_copy_section, self.btn_terrain_xs_paste_section,
                     self.btn_terrain_xs_copy_right, self.btn_terrain_xs_copy_left):
            spin.setEnabled(False)
        self.lbl_terrain_xs_status.setText(
            'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
        self.lbl_terrain_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self._draw_terrain_xs_preview()

    def _add_terrain_control(self, xdata):
        """Places a CONTROL SECTION at the clicked chainage — same
        add/re-select-if-close convention as _add_terrain_break, but no
        level is stored (always read live from the invert) and there's no
        drag support. Starts with whatever cross-section the interpolation
        already gives at this chainage (_terrain_xs_interp_params), so
        placing one doesn't itself change anything — it just gives the
        user a real, directly-editable anchor to then adjust for a
        smoother transition."""
        if self._terrain_geom is None or self._terrain_us_level is None or self._terrain_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set the upstream and downstream levels first — the invert\n'
                'line needs both ends defined before you can add a control section.')
            return
        length = self._terrain_geom.length()
        ch = max(0.0, min(float(xdata), length))
        tol = max(0.05, length * 0.002)
        for cs in self._terrain_control_sections:
            if abs(cs['chainage'] - ch) < tol:
                self._select_terrain_xs_node(id(cs))
                return
        new_cs = {'chainage': ch}
        self._terrain_control_sections.append(new_cs)
        self._terrain_control_sections.sort(key=lambda c: c['chainage'])
        self._terrain_xs_params[id(new_cs)] = dict(self._terrain_xs_interp_params(ch))
        self._select_terrain_xs_node(id(new_cs))

    def _remove_terrain_control(self, cs):
        """Right-click on a control-section marker removes it — same
        convention as break points."""
        if cs not in self._terrain_control_sections:
            return
        self._terrain_control_sections.remove(cs)
        self._terrain_xs_params.pop(id(cs), None)
        if self._terrain_control_drag_obj is cs:
            self._terrain_control_drag_obj = None
        if self._terrain_selected_xs_key == id(cs):
            self._clear_terrain_xs_selection()
        self._draw_terrain_vertical_decorations()

    def _try_edit_terrain_text(self, event):
        """Typed-value alternative to dragging: a click that lands exactly
        on a level or slope % label opens an inline editor right over that
        label instead. Only checked while 'Add Vertical Break' is off, so
        it can never be confused with placing a new break there."""
        for entry in self._terrain_level_click_targets:
            contains, _ = entry['artist'].contains(event)
            if contains:
                self._edit_terrain_level(entry['artist'], entry['handle'])
                return True
        for entry in self._terrain_slope_click_targets:
            contains, _ = entry['artist'].contains(event)
            if contains:
                self._edit_terrain_slope(entry['artist'], entry['v0'], entry['v1'])
                return True
        return False

    def _set_terrain_vertex_level(self, handle, new_level):
        """Write a new level back to whatever the handle actually is —
        the US/DS spinboxes (kept in sync) or a break's own dict."""
        _ch, _lvl, kind, ref = handle
        if kind == 'us':
            self._terrain_us_level = new_level
            self.spin_terrain_us_level.blockSignals(True)
            self.spin_terrain_us_level.setValue(new_level)
            self.spin_terrain_us_level.blockSignals(False)
        elif kind == 'ds':
            self._terrain_ds_level = new_level
            self.spin_terrain_ds_level.blockSignals(True)
            self.spin_terrain_ds_level.setValue(new_level)
            self.spin_terrain_ds_level.blockSignals(False)
        else:
            ref['level'] = new_level

    def _edit_terrain_level(self, artist, handle):
        _ch, level, _kind, _ref = handle
        self._open_terrain_inline_editor(
            artist, f'{level:.3f}',
            lambda text, h=handle: self._commit_terrain_level(h, text))

    def _commit_terrain_level(self, handle, text):
        try:
            val = float(text)
        except (TypeError, ValueError):
            return
        self._set_terrain_vertex_level(handle, val)
        self._draw_terrain_vertical_decorations()

    def _edit_terrain_slope(self, artist, v0, v1):
        """Solves for v1's level given a typed slope %, keeping v0 (the
        upstream end of this segment) fixed — same convention as dragging
        the downstream end to hit a target grade."""
        x0, y0 = v0[0], v0[1]
        x1, y1 = v1[0], v1[1]
        dx = x1 - x0
        cur_slope = (y0 - y1) / dx * 100.0 if abs(dx) > 1e-9 else 0.0
        self._open_terrain_inline_editor(
            artist, f'{cur_slope:.3f}',
            lambda text, a=v0, b=v1: self._commit_terrain_slope(a, b, text))

    def _commit_terrain_slope(self, v0, v1, text):
        try:
            new_slope = float(text)
        except (TypeError, ValueError):
            return
        x0, y0 = v0[0], v0[1]
        x1, _y1 = v1[0], v1[1]
        dx = x1 - x0
        if abs(dx) < 1e-9:
            return
        new_y1 = y0 - new_slope / 100.0 * dx
        self._set_terrain_vertex_level(v1, new_y1)
        self._draw_terrain_vertical_decorations()

    def _open_terrain_inline_editor(self, artist, initial_text, commit_fn):
        """Float a small QLineEdit directly over the clicked label's own
        rendered position so a value can be typed in place, instead of a
        popup dialog stealing focus from the chart."""
        self._close_terrain_inline_editor(commit=False)
        try:
            renderer = self.canvas_plot.get_renderer()
            bbox = artist.get_window_extent(renderer)
        except Exception:
            return
        canvas_h = self.canvas_plot.height()
        x = max(0, int(bbox.x0) - 4)
        y = max(0, int(canvas_h - bbox.y1) - 3)
        w = max(56, int(bbox.width) + 16)
        h = max(20, int(bbox.height) + 8)

        editor = _InlineValueEdit(self.canvas_plot)
        editor.setText(initial_text)
        editor.setGeometry(x, y, w, h)
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter if hasattr(Qt, 'AlignmentFlag') else Qt.AlignCenter)
        editor.setStyleSheet(
            'background:#FFFDE7;border:2px solid #F57F17;'
            'font-size:8pt;font-weight:bold;padding:0px;'
        )
        editor.editingFinished.connect(lambda: self._close_terrain_inline_editor(commit=True))
        editor.escapePressed.connect(lambda: self._close_terrain_inline_editor(commit=False))
        self._terrain_inline_editor    = editor
        self._terrain_inline_commit_fn = commit_fn
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _close_terrain_inline_editor(self, commit):
        editor = self._terrain_inline_editor
        if editor is None:
            return
        self._terrain_inline_editor = None
        fn = self._terrain_inline_commit_fn
        self._terrain_inline_commit_fn = None
        try:
            editor.blockSignals(True)
            text = editor.text()
        except Exception:
            text = None
        try:
            editor.setParent(None)
            editor.deleteLater()
        except Exception:
            pass
        if commit and fn is not None and text is not None:
            fn(text)

    def _clear_terrain_break_map_bands(self):
        for band in self._terrain_break_map_bands:
            try: self.canvas.scene().removeItem(band)
            except Exception: pass
        self._terrain_break_map_bands = []
        for ann in self._terrain_break_annotations:
            self._remove_ann(ann)
        self._terrain_break_annotations = []

    def _clear_terrain_xs_map_marker(self):
        if self._terrain_xs_map_band is not None:
            try: self.canvas.scene().removeItem(self._terrain_xs_map_band)
            except Exception: pass
            self._terrain_xs_map_band = None
        if self._terrain_xs_annotation is not None:
            self._remove_ann(self._terrain_xs_annotation)
            self._terrain_xs_annotation = None

    def _draw_terrain_vertical_decorations(self):
        """Additive decoration step, same pattern as the 1D Profile tab's
        pipe/manhole drawing: _plot()/_reset_axes() just cleared the axes,
        so the invert line, cut/fill hatch and break-point markers are
        drawn fresh on top every time, and redrawn from cached state
        whenever only a level/break changes (no re-extraction needed)."""
        for art in self._terrain_invert_artists:
            try: art.remove()
            except Exception: pass
        self._terrain_invert_artists = []
        self._terrain_level_click_targets = []
        self._terrain_slope_click_targets = []
        self._clear_terrain_break_map_bands()
        self._clear_terrain_xs_map_marker()
        if not MATPLOTLIB_AVAILABLE:
            return
        handles = self._terrain_invert_vertex_handles()
        if handles is None:
            self.canvas_plot.draw_idle()
            return
        verts = [(h[0], h[1]) for h in handles]

        ax = self.ax
        inv_xs = [v[0] for v in verts]
        inv_ys = [v[1] for v in verts]

        ground_ys, invert_at_xs, xs_for_fill = None, None, None
        if (self._terrain_ground_col and self._profile_chainages
                and self._terrain_ground_col in self._profile_data_store):
            xs_for_fill = np.array(self._profile_chainages, dtype=float)
            ground_ys = np.array(
                [v if v is not None else np.nan
                 for v in self._profile_data_store[self._terrain_ground_col]], dtype=float)
            invert_at_xs = np.interp(xs_for_fill, inv_xs, inv_ys)

        ln, = ax.plot(inv_xs, inv_ys, color='#6D4C41', linewidth=2.5,
                       marker='o', markersize=4, markerfacecolor='#6D4C41',
                       zorder=8, label='Channel Invert')
        self._terrain_invert_artists.append(ln)

        # Design-height reference line — always drawn once there's a
        # profile, independent of 'Show Banks' (that toggle only adds the
        # two REAL-ground bank lines on top of this one — see
        # _terrain_design_height_profile / _terrain_bank_ground_profiles).
        # Drawn UNDER the break/control markers (lower zorder) since
        # these are reference aids, not the design itself.
        if self._terrain_show_bank_analysis:
            bank = self._terrain_bank_ground_profiles()
            if bank is not None:
                bch, bleft, bright, bheight = bank
                h_ln, = ax.plot(bch, bheight, color='#EF6C00', linewidth=1.4,
                                 linestyle='--', zorder=6, label='Design Top (Channel Height)')
                l_ln, = ax.plot(bch, [v if v is not None else np.nan for v in bleft],
                                 color='#7CB342', linewidth=1.8, linestyle=':', zorder=6,
                                 label='Left Bank Ground')
                r_ln, = ax.plot(bch, [v if v is not None else np.nan for v in bright],
                                 color='#E53935', linewidth=1.8,
                                 linestyle=':', zorder=6, label='Right Bank Ground')
                self._terrain_invert_artists.extend([h_ln, l_ln, r_ln])
        else:
            design = self._terrain_design_height_profile()
            if design is not None:
                bch, bheight = design
                h_ln, = ax.plot(bch, bheight, color='#EF6C00', linewidth=1.4,
                                 linestyle='--', zorder=6, label='Design Top (Channel Height)')
                self._terrain_invert_artists.append(h_ln)

        # Upstream/downstream end levels — triangular so they read as
        # visually distinct from interior break points (squares); these
        # only ever drag vertically (raise/lower the level) since their
        # chainage is fixed at the alignment's own two ends.
        us_dragging = (self._terrain_endpoint_drag == 'us')
        ds_dragging = (self._terrain_endpoint_drag == 'ds')
        mk_us, = ax.plot([inv_xs[0]], [inv_ys[0]], marker='^', markersize=12,
                          markerfacecolor='#6A1B9A' if us_dragging else '#AB47BC',
                          markeredgecolor='#4A148C', markeredgewidth=1.5, zorder=10)
        mk_ds, = ax.plot([inv_xs[-1]], [inv_ys[-1]], marker='^', markersize=12,
                          markerfacecolor='#6A1B9A' if ds_dragging else '#AB47BC',
                          markeredgecolor='#4A148C', markeredgewidth=1.5, zorder=10)
        self._terrain_invert_artists.extend([mk_us, mk_ds])

        if ground_ys is not None:
            valid = np.isfinite(ground_ys) & np.isfinite(invert_at_xs)
            # Invert ABOVE ground = material needs adding = fill (red).
            # Invert BELOW ground = material needs removing = cut (blue).
            fill_a = ax.fill_between(
                xs_for_fill, ground_ys, invert_at_xs, where=valid & (invert_at_xs > ground_ys),
                facecolor='#F44336', alpha=0.28, hatch='///',
                edgecolor='#F44336', linewidth=0.0, interpolate=True, zorder=2)
            cut_a = ax.fill_between(
                xs_for_fill, ground_ys, invert_at_xs, where=valid & (invert_at_xs < ground_ys),
                facecolor='#4FC3F7', alpha=0.28, hatch='///',
                edgecolor='#4FC3F7', linewidth=0.0, interpolate=True, zorder=2)
            self._terrain_invert_artists.extend([fill_a, cut_a])

        # Slope % for every segment between consecutive vertices (US ->
        # break -> ... -> DS) — with no break points this is just the one
        # US-to-DS segment, so a single slope shows with no special-casing.
        # (upstream - downstream) / length, matching the sign convention
        # the Network Profile tab already uses for pipe slopes (see
        # _draw_one_pipe_segment) — a normal falling channel reads
        # positive; a section that rises against the flow reads negative,
        # which is the thing worth flagging.
        for h0, h1 in zip(handles, handles[1:]):
            x0, y0 = h0[0], h0[1]
            x1, y1 = h1[0], h1[1]
            dx = x1 - x0
            slope_pct = (y0 - y1) / dx * 100.0 if abs(dx) > 1e-9 else 0.0
            mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            txt = ax.annotate(
                f'{slope_pct:.2f}%', xy=(mid_x, mid_y), xytext=(0, 9),
                textcoords='offset points', ha='center', fontsize=8,
                fontweight='bold', color='#4E342E',
                bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                          alpha=0.85, edgecolor='none'), zorder=9,
            )
            self._terrain_invert_artists.append(txt)
            self._terrain_slope_click_targets.append({'artist': txt, 'v0': h0, 'v1': h1})

        # Break points — large and high-zorder ('much visible' per the
        # brief) since these are the drag handles (chainage AND level both
        # change live on drag — see _on_chart_hover's drag branch); level
        # shown alongside each one, editable simply by dragging the marker
        # itself in any direction.
        for i, b in enumerate(self._terrain_invert_breaks):
            is_dragging = (self._terrain_break_drag_obj is b)
            mk, = ax.plot([b['chainage']], [b['level']], marker='s', markersize=10,
                          markerfacecolor='#FF5722' if is_dragging else '#FFEB3B',
                          markeredgecolor='#F57F17', markeredgewidth=2, zorder=10,
                          picker=8)
            self._terrain_invert_artists.append(mk)
            lvl_txt = ax.annotate(
                f'{b["level"]:.2f} m', xy=(b['chainage'], b['level']), xytext=(7, -3),
                textcoords='offset points', fontsize=7.5, fontweight='bold',
                color='#E65100',
                bbox=dict(boxstyle='round,pad=0.12', facecolor='white',
                          alpha=0.9, edgecolor='none'), zorder=11,
            )
            self._terrain_invert_artists.append(lvl_txt)
            self._terrain_level_click_targets.append(
                {'artist': lvl_txt, 'handle': (b['chainage'], b['level'], 'break', b)})

            # Map marker: same dotted-perpendicular-tick style as the major
            # chainage marks (deliberately, so it reads as 'a chainage
            # marker' at a glance) but thinner and dotted to stay visually
            # distinct from the solid major ticks, with its own label.
            tg = self._terrain_tick_geom(b['chainage'], 6.0)
            if tg is not None:
                line_geom, label_pt, angle_deg = tg
                band = QgsRubberBand(self.canvas, _LINE_GEOM)
                band.setColor(QColor(245, 127, 23, 230))
                band.setWidth(2)
                try:
                    band.setLineStyle(Qt.PenStyle.DotLine)
                except AttributeError:
                    band.setLineStyle(Qt.DotLine)  # type: ignore[attr-defined]
                band.setToGeometry(line_geom, None)
                self._terrain_break_map_bands.append(band)
                self._add_terrain_chainage_label(label_pt, b['chainage'], angle_deg,
                                                  color='#E65100',
                                                  target=self._terrain_break_annotations)

        # Control sections — a lightweight station with its OWN full
        # cross-section (same editor as a break point), drawn as a small
        # diamond right on the invert line since it carries no level of
        # its own — its Y position is always just wherever the invert
        # line itself currently is at this chainage, recomputed fresh
        # every redraw. Deliberately smaller/lower-zorder than break
        # markers so the two are never confused; click-to-select /
        # right-click-to-remove via _find_terrain_control_near, and
        # draggable along chainage only (never level) — see
        # _terrain_control_drag_obj.
        for cs in self._terrain_control_sections:
            ch = cs['chainage']
            level = self._terrain_invert_level_at(ch)
            is_selected = (self._terrain_selected_xs_key == id(cs))
            mk, = ax.plot([ch], [level], marker='D', markersize=8,
                          markerfacecolor='#00838F' if is_selected else '#26C6DA',
                          markeredgecolor='#006064',
                          markeredgewidth=1.5, zorder=9, picker=8)
            self._terrain_invert_artists.append(mk)
            lbl = ax.annotate(
                'CS', xy=(ch, level), xytext=(0, 10),
                textcoords='offset points', ha='center', fontsize=7,
                fontweight='bold', color='#006064',
                bbox=dict(boxstyle='round,pad=0.1', facecolor='white',
                          alpha=0.85, edgecolor='none'), zorder=9,
            )
            self._terrain_invert_artists.append(lbl)

        # Selected cross-section STATION — a single node now (US end, a
        # break point, the DS end, or a control section), not a reach
        # range: an 'X-S' marker right at its own chainage/level, both on
        # the chart and (dotted purple tick, same convention as the break
        # markers) on the map canvas.
        if self._terrain_selected_xs_key is not None:
            sel_handle = self._terrain_any_node_handle(self._terrain_selected_xs_key)
            if sel_handle is not None:
                node_ch, node_level = sel_handle[0], sel_handle[1]
                mk_x, = ax.plot([node_ch], [node_level], marker='x', markersize=13,
                                 markeredgewidth=3, color='#8E24AA', zorder=12)
                self._terrain_invert_artists.append(mk_x)

                tg = self._terrain_tick_geom(node_ch, 7.0)
                if tg is not None:
                    line_geom, label_pt, angle_deg = tg
                    xband = QgsRubberBand(self.canvas, _LINE_GEOM)
                    xband.setColor(QColor(142, 36, 170, 230))
                    xband.setWidth(3)
                    try:
                        xband.setLineStyle(Qt.PenStyle.DashDotLine)
                    except AttributeError:
                        xband.setLineStyle(Qt.DashDotLine)  # type: ignore[attr-defined]
                    xband.setToGeometry(line_geom, None)
                    self._terrain_xs_map_band = xband
                    _xs_ann_list = []
                    self._add_terrain_chainage_label(
                        label_pt, node_ch, angle_deg, color='#6A1B9A',
                        target=_xs_ann_list, text='X-S')
                    if _xs_ann_list:
                        self._terrain_xs_annotation = _xs_ann_list[0]

        # 'Check Cross-Section' ghost marker(s) — a dashed purple vertical
        # line (+ label) at the chainage each OPEN Check Cross-Section
        # window is CURRENTLY stepped to, so it's obvious at a glance
        # where along the alignment that popped-out view is actually
        # looking. Moves live as Prev/Next is clicked or the reach/
        # spacing changes (see TerrainCheckXSDialog._draw_current_step,
        # which calls back into this method) and disappears when that
        # window closes. Distinct colour from the break-point/control-
        # section markers and from Layer Profile's own orange marker
        # highlight, since this is a different kind of thing (a transient
        # 'what am I looking at' pointer, not a real station).
        import matplotlib.transforms as _mt
        for _dlg in self._terrain_check_xs_dialogs:
            _ch = _dlg.current_chainage()
            if _ch is None:
                continue
            g_ln = ax.axvline(x=_ch, color='#AA00FF', linewidth=1.6,
                               linestyle=(0, (4, 2)), alpha=0.85, zorder=9)
            _blend = _mt.blended_transform_factory(ax.transData, ax.transAxes)
            g_lbl = ax.text(
                _ch, 0.97, 'Check XS', transform=_blend, rotation=90,
                rotation_mode='anchor', fontsize=7, color='#AA00FF',
                ha='right', va='top', zorder=12,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          edgecolor='#AA00FF', alpha=0.85, linewidth=0.8))
            self._terrain_invert_artists.extend([g_ln, g_lbl])

        # 'Show Difference' readout — always created (invisible) so
        # _on_chart_hover always has a live artist to update; content and
        # visibility are only ever set on hover, gated there by the
        # checkbox, exactly like the Layer Profile Windows' own ΔY text.
        self._terrain_diff_ann = ax.text(
            0.99, 0.98, '', transform=ax.transAxes, fontsize=8,
            ha='right', va='top', color='#00695C', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      alpha=0.85, edgecolor='none'),
            visible=False, zorder=20,
        )
        self._terrain_invert_artists.append(self._terrain_diff_ann)

        try:
            ax.legend(fontsize=8, loc='best')
        except Exception:
            pass
        self.canvas_plot.draw_idle()

        # Keep the embedded Cross-Section preview live — this single hook
        # covers every caller of this method (break/level edits, typed
        # slope/level edits, adding/removing a break, fillet/chainage
        # setting changes, a fresh draw, AND raster row colour/linestyle
        # changes, since those already flow through _refresh_plot() ->
        # this method for tab 3) rather than needing each one wired up
        # individually. While a break/endpoint is actively being dragged,
        # only the cheap shape/level redraw runs (no raster re-sampling)
        # so dragging stays smooth; the real ground re-sample happens once
        # on release, when this method is called again with the drag
        # state already cleared.
        if self._terrain_selected_xs_key is not None:
            if self._terrain_break_drag_obj is not None or self._terrain_endpoint_drag is not None:
                self._draw_terrain_xs_preview()
            else:
                self._refresh_terrain_xs()

    # ------------------------------------------------------------------ Save results / run

    def _build_embank_tab(self):
        tab = QWidget()
        outer_l = QVBoxLayout(tab)
        outer_l.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True)
        sc.setFrameShape(_NO_FRAME)
        inner = QWidget()
        l = QVBoxLayout(inner)
        l.setContentsMargins(4, 6, 4, 4); l.setSpacing(3)

        # Two mini-tabs — Alignment (horizontal AND vertical together —
        # per explicit request, since both still belong to one 'lay the
        # geometry out' phase before any cross-section work starts) and
        # Cross-Section, so that step gets its own space without the
        # alignment controls crowding it, and editing one can't
        # accidentally touch a control that belongs to the other.
        # Switching between them is entirely manual — nothing in this
        # dock jumps tabs on its own, per explicit request that the user
        # drive that themselves.
        self.tabs_embank_workflow = QTabWidget()
        self.tabs_embank_workflow.setStyleSheet(_style.SUBTAB_STYLE)
        tab_a = QWidget(); la = QVBoxLayout(tab_a)
        la.setContentsMargins(4, 6, 4, 4); la.setSpacing(3)
        lh = la   # Horizontal Alignment content below goes into the shared tab
        lv = la   # Vertical Alignment content below goes into the same shared tab
        tab_x = QWidget(); lx = QVBoxLayout(tab_x)
        lx.setContentsMargins(4, 6, 4, 4); lx.setSpacing(3)

        lh.addWidget(_section_label('Horizontal Alignment'))

        # FlowLayout, not QHBoxLayout — this row picked up enough fixed-
        # width controls (label+spin, tag, three buttons) that it can
        # overflow a narrowed dock; wrapping onto a second line keeps
        # every control reachable instead of clipping some of them, same
        # convention as the other Modify Embank rows below.
        fillet_row = FlowLayout(h_spacing=6, v_spacing=4)
        fillet_row.addWidget(QLabel('Fillet radius:'))
        self.spin_embank_fillet = QDoubleSpinBox()
        self.spin_embank_fillet.setRange(0.0, 500.0)
        self.spin_embank_fillet.setValue(self._embank_fillet_radius)
        self.spin_embank_fillet.setSingleStep(1.0)
        self.spin_embank_fillet.setSuffix(' m')
        self.spin_embank_fillet.setFixedWidth(75)
        self.spin_embank_fillet.setToolTip(
            'Rounds a sharp bend with an arc of this radius. 0 = keep the\n'
            'corner sharp. Applies to all bends unless a vertex is selected\n'
            '(Edit Vertices) below, in which case it sets that bend only.'
        )
        self.spin_embank_fillet.valueChanged.connect(self._on_embank_fillet_changed)
        fillet_row.addWidget(self.spin_embank_fillet)

        self.lbl_embank_fillet_tag = QLabel('(default)')
        self.lbl_embank_fillet_tag.setStyleSheet('color:gray;font-size:10px;font-style:italic;')
        fillet_row.addWidget(self.lbl_embank_fillet_tag)

        # Own dedicated Draw Line button (not the shared Layer Profile
        # one) — lives here so it's right next to the tools it actually
        # controls, per explicit request to stop it taking bottom-row
        # space that's shared with an unrelated tab. Sits before Set
        # Global Radius per explicit request — drawing the line is the
        # first thing a user does, so it reads left-to-right as the
        # natural order of operations.
        self.btn_embank_draw = QPushButton('Draw Line')
        self.btn_embank_draw.setCheckable(True)
        self.btn_embank_draw.setToolTip(
            'Draw Horizontal Alignment.\nLeft-click to add vertices.\n'
            'Right-click or double-click to finish.\n'
            'Ctrl+Z to undo the last vertex.'
        )
        self.btn_embank_draw.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_draw.clicked.connect(self._toggle_embank_digitizing)
        fillet_row.addWidget(self.btn_embank_draw)

        self.btn_embank_import = QPushButton('Import Line')
        self.btn_embank_import.setToolTip(
            'Import an existing line (shapefile or GeoPackage) as the\n'
            'horizontal alignment, instead of drawing one by hand.\n'
            'Only the first feature in the file is used. Vertex editing,\n'
            'fillets, etc. all work exactly the same afterwards as a\n'
            'hand-drawn line.'
        )
        self.btn_embank_import.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_import.clicked.connect(self._on_embank_import_line)
        fillet_row.addWidget(self.btn_embank_import)

        # One-click fix for the 'radius tighter than the embankment's own
        # half-width' problem (see _embank_check_tight_bends, surfaced
        # as a warning in Create TIFF) — raises every bend up to AT LEAST
        # its own half-width in one go, rather than the user having to
        # hunt down and fix each flagged chainage by hand one at a time.
        # Named 'Set Global Radius' (not 'Set Half-width Radius') since
        # it applies to the WHOLE alignment at once, same as the Fillet
        # radius field above it — what it actually sets each bend TO
        # (its own half-width) is explained in the tooltip and the
        # confirmation prompt instead.
        self.btn_embank_set_halfwidth_radius = QPushButton('Set Global Radius')
        self.btn_embank_set_halfwidth_radius.setToolTip(
            "Sets every bend's fillet radius to AT LEAST its own embankment\n"
            'half-width there — the minimum needed for the inside edge to\n'
            "exist without folding over itself. Only RAISES a bend's\n"
            "radius if it's currently tighter than that; never lowers one\n"
            "that's already wider, and leaves intentional sharp (0 m)\n"
            'corners alone — those use a miter join instead (see Edit\n'
            'Vertices). Loosen any individual bend further afterwards the\n'
            'normal way: select it with Edit Vertices, then adjust the\n'
            'radius above.'
        )
        self.btn_embank_set_halfwidth_radius.clicked.connect(self._on_embank_set_halfwidth_radius)
        fillet_row.addWidget(self.btn_embank_set_halfwidth_radius)

        self.btn_embank_vertex_edit = QPushButton('Edit Vertices')
        self.btn_embank_vertex_edit.setCheckable(True)
        self.btn_embank_vertex_edit.setToolTip(
            'Click a vertex on the map to select it, then set its own\n'
            'fillet radius above (click the same vertex again to deselect).\n'
            'Drag a vertex to move it — including the two ends, which\n'
            'extends or shortens the alignment. Right-click a vertex to\n'
            'remove it. Break points and control sections stay attached\n'
            'to the same real-world spot as the alignment changes shape.'
        )
        self.btn_embank_vertex_edit.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_vertex_edit.clicked.connect(self._toggle_embank_vertex_edit)
        fillet_row.addWidget(self.btn_embank_vertex_edit)

        self.btn_embank_vertex_reset = QPushButton('Reset')
        self.btn_embank_vertex_reset.setFixedWidth(48)
        self.btn_embank_vertex_reset.setStyleSheet(_style.SECONDARY_BUTTON_STYLE)
        self.btn_embank_vertex_reset.setToolTip("Clear this vertex's override — use the default radius again")
        self.btn_embank_vertex_reset.clicked.connect(self._on_embank_vertex_reset)
        self.btn_embank_vertex_reset.setVisible(False)
        fillet_row.addWidget(self.btn_embank_vertex_reset)
        lh.addLayout(fillet_row)

        chainage_row = QHBoxLayout()
        chainage_row.setSpacing(4)
        chainage_row.addWidget(QLabel('Chainage:'))
        self.chk_embank_major_ch = QCheckBox('Major')
        self.chk_embank_major_ch.setChecked(self._embank_show_major_ch)
        self.chk_embank_major_ch.stateChanged.connect(self._on_embank_chainage_settings_changed)
        chainage_row.addWidget(self.chk_embank_major_ch)
        self.spin_embank_major_ch = QDoubleSpinBox()
        self.spin_embank_major_ch.setRange(1.0, 100000.0)
        self.spin_embank_major_ch.setValue(self._embank_major_interval)
        self.spin_embank_major_ch.setSuffix(' m')
        self.spin_embank_major_ch.setFixedWidth(80)
        self.spin_embank_major_ch.valueChanged.connect(self._on_embank_chainage_settings_changed)
        chainage_row.addWidget(self.spin_embank_major_ch)

        self.chk_embank_minor_ch = QCheckBox('Minor')
        self.chk_embank_minor_ch.setChecked(self._embank_show_minor_ch)
        self.chk_embank_minor_ch.stateChanged.connect(self._on_embank_chainage_settings_changed)
        chainage_row.addWidget(self.chk_embank_minor_ch)
        self.spin_embank_minor_ch = QDoubleSpinBox()
        self.spin_embank_minor_ch.setRange(0.5, 100000.0)
        self.spin_embank_minor_ch.setValue(self._embank_minor_interval)
        self.spin_embank_minor_ch.setSuffix(' m')
        self.spin_embank_minor_ch.setFixedWidth(80)
        self.spin_embank_minor_ch.valueChanged.connect(self._on_embank_chainage_settings_changed)
        chainage_row.addWidget(self.spin_embank_minor_ch)
        chainage_row.addStretch()
        lh.addLayout(chainage_row)

        lv.addSpacing(8)
        lv.addWidget(_section_label('Vertical Alignment'))
        lv.addWidget(_section_label('Raster Layers (DEM)', _raster_type_pix(12)))
        # Only ever ONE ground DEM here (unlike Layer Profile's multi-layer
        # list) — a single fixed row, no add/remove — but the same style
        # controls (visibility/linestyle/width/opacity/colour) as Layer
        # Profile's own raster rows, in the same row, since those are
        # still genuinely useful for a single line too.
        raster_row = QHBoxLayout()
        raster_row.setSpacing(3)

        _embank_raster_tog = QCheckBox()
        _embank_raster_tog.setChecked(True)
        _embank_raster_tog.setToolTip('Show/hide this layer on the chart')
        _embank_raster_tog.stateChanged.connect(self._refresh_plot)
        raster_row.addWidget(_embank_raster_tog)

        self.combo_embank_raster = QgsMapLayerComboBox()
        self.combo_embank_raster.setFilters(_RASTER_FILTER)
        self.combo_embank_raster.setAllowEmptyLayer(True)
        self.combo_embank_raster.setCurrentIndex(0)
        self.combo_embank_raster.wheelEvent = lambda e: e.ignore()
        self.combo_embank_raster.layerChanged.connect(lambda _: self._on_embank_raster_changed())
        raster_row.addWidget(self.combo_embank_raster, 1)

        _embank_raster_ls = QComboBox()
        _embank_raster_ls.setFixedWidth(80)
        for _code, _label in _LINESTYLES:
            _embank_raster_ls.addItem(_label, _code)
        # Default to Dash-dot / 1.0 / 75% / black — the DEM ground line
        # reads clearest against the design/invert lines at this style,
        # per explicit request, rather than the generic "first option"/
        # auto-assigned-colour defaults every other raster row gets.
        _embank_raster_ls.setCurrentIndex(max(0, _embank_raster_ls.findData('-.')))
        _embank_raster_ls.currentIndexChanged.connect(self._refresh_plot)
        _embank_raster_ls.wheelEvent = lambda e: e.ignore()
        raster_row.addWidget(_embank_raster_ls)

        _embank_raster_lw = QDoubleSpinBox()
        _embank_raster_lw.setRange(0.5, 5.0)
        _embank_raster_lw.setValue(1.0)
        _embank_raster_lw.setSingleStep(0.5)
        _embank_raster_lw.setDecimals(1)
        _embank_raster_lw.setFixedWidth(48)
        _embank_raster_lw.setToolTip('Line width')
        _embank_raster_lw.valueChanged.connect(self._refresh_plot)
        raster_row.addWidget(_embank_raster_lw)

        _embank_raster_al = QSpinBox()
        _embank_raster_al.setRange(10, 100)
        _embank_raster_al.setValue(75)
        _embank_raster_al.setSuffix('%')
        _embank_raster_al.setFixedWidth(52)
        _embank_raster_al.setToolTip('Opacity')
        _embank_raster_al.valueChanged.connect(self._refresh_plot)
        raster_row.addWidget(_embank_raster_al)

        _embank_raster_row_dict = {
            '_col': None, 'layer_combo': self.combo_embank_raster,
            'toggle': _embank_raster_tog, 'ls_combo': _embank_raster_ls,
            'lw_spin': _embank_raster_lw, 'al_spin': _embank_raster_al,
            'color': QColor('#000000'),
        }
        _embank_raster_c_btn = _color_btn(
            _embank_raster_row_dict['color'].name(), 'Change colour')
        _embank_raster_c_btn.clicked.connect(
            lambda: self._pick_raster_color(_embank_raster_row_dict, self._refresh_plot))
        _embank_raster_row_dict['color_btn'] = _embank_raster_c_btn
        raster_row.addWidget(_embank_raster_c_btn)

        self._embank_raster_rows = [_embank_raster_row_dict]
        lv.addLayout(raster_row)
        lv.addSpacing(4)

        def _flow_pair(label_text, widget):
            """A label glued tightly to its own field, as ONE widget — so
            a FlowLayout wrapping this row never separates a label from
            the field it describes onto different lines."""
            pair = QWidget()
            hb = QHBoxLayout(pair)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.setSpacing(4)
            hb.addWidget(QLabel(label_text))
            hb.addWidget(widget)
            return pair

        # How far PAST each end of the alignment the ground profile line
        # is sampled and drawn — a pure visualisation aid (seeing what
        # the ground does just beyond where the design starts/ends), per
        # explicit request. Purely additive: 0 (the default) reproduces
        # today's behaviour exactly (ground starts right at chainage 0,
        # ends right at the alignment's own end chainage). Never touches
        # the alignment, chainage marks, break points, control sections
        # or the design crest line itself — only the ground LINE and its
        # own sampled extent grow; see _embank_extended_ground_geom and
        # _refresh_embank_profile.
        extend_row = FlowLayout(h_spacing=10, v_spacing=6)
        extend_row.addWidget(QLabel('Extend ground plot:'))
        self.spin_embank_extend_us = QDoubleSpinBox()
        self.spin_embank_extend_us.setRange(0.0, 100000.0)
        self.spin_embank_extend_us.setValue(0.0)
        self.spin_embank_extend_us.setSuffix(' m')
        self.spin_embank_extend_us.setFixedWidth(80)
        self.spin_embank_extend_us.setToolTip(
            'Samples and draws the ground profile this much further\n'
            'PAST the upstream (chainage 0) end — purely a visual\n'
            "extension of the ground LINE; it doesn't move the\n"
            'alignment, chainage marks, or the design crest line.'
        )
        self.spin_embank_extend_us.valueChanged.connect(self._on_embank_extend_changed)
        extend_row.addWidget(_flow_pair('US:', self.spin_embank_extend_us))

        self.spin_embank_extend_ds = QDoubleSpinBox()
        self.spin_embank_extend_ds.setRange(0.0, 100000.0)
        self.spin_embank_extend_ds.setValue(0.0)
        self.spin_embank_extend_ds.setSuffix(' m')
        self.spin_embank_extend_ds.setFixedWidth(80)
        self.spin_embank_extend_ds.setToolTip(
            'Samples and draws the ground profile this much further\n'
            'PAST the downstream (end chainage) end — purely a visual\n'
            "extension of the ground LINE; it doesn't move the\n"
            'alignment, chainage marks, or the design crest line.'
        )
        self.spin_embank_extend_ds.valueChanged.connect(self._on_embank_extend_changed)
        extend_row.addWidget(_flow_pair('DS:', self.spin_embank_extend_ds))

        self.chk_embank_end_adjustment = QCheckBox('End Adjustment')
        self.chk_embank_end_adjustment.setToolTip(
            'Create TIFF closes the embankment out past each real end,\n'
            'over the SAME Extend US/DS distance above — crest level\n'
            "held CONSTANT (it doesn't taper down), each side's own\n"
            'section stack reused from its nearest real end, so the\n'
            'design collapses on its own as real ground rises to meet\n'
            'that level — a smooth closure into the natural ground,\n'
            'like a dam\'s abutments. OFF (default): ends exactly at the\n'
            'real start/end, same as today, regardless of Extend US/DS.'
        )
        self.chk_embank_end_adjustment.stateChanged.connect(self._on_embank_end_adjustment_changed)
        extend_row.addWidget(self.chk_embank_end_adjustment)
        lv.addLayout(extend_row)

        # FlowLayout, not QHBoxLayout — this row has 5 fairly wide
        # controls (Chart, two level fields, two mode-toggle buttons) that
        # don't all fit once the dock is narrowed to give the map canvas
        # more room; wrapping onto a second line keeps every control
        # visible and at its own real size, rather than clipping some of
        # them or requiring horizontal scrolling to reach them.
        vert_row = FlowLayout(h_spacing=10, v_spacing=6)
        # Own dedicated Chart button — fits the Vertical Alignment
        # workflow better here (levels -> breaks/control sections -> view
        # the chart) than the shared bottom row's Refresh/Chart/Clear,
        # which hides its own Chart button on this tab in favour of this
        # one (see _on_tab_changed).
        self.btn_embank_chart = QPushButton('Profile')
        self.btn_embank_chart.setToolTip('Show / Hide Chart')
        self.btn_embank_chart.clicked.connect(self._on_open_chart)
        vert_row.addWidget(self.btn_embank_chart)

        self.spin_embank_us_level = QDoubleSpinBox()
        self.spin_embank_us_level.setRange(-9999.0, 9999.0)
        self.spin_embank_us_level.setDecimals(3)
        self.spin_embank_us_level.setSuffix(' m')
        self.spin_embank_us_level.setFixedWidth(90)
        self.spin_embank_us_level.setToolTip(
            'Embankment crest level at chainage 0 (upstream end).\n'
            'Auto-filled from the ground profile once a DEM is added —\n'
            'change it, or drag the purple triangle on the chart, to set\n'
            'the design crest instead.'
        )
        self.spin_embank_us_level.valueChanged.connect(self._on_embank_invert_levels_changed)
        vert_row.addWidget(_flow_pair('US Crest:', self.spin_embank_us_level))

        self.spin_embank_ds_level = QDoubleSpinBox()
        self.spin_embank_ds_level.setRange(-9999.0, 9999.0)
        self.spin_embank_ds_level.setDecimals(3)
        self.spin_embank_ds_level.setSuffix(' m')
        self.spin_embank_ds_level.setFixedWidth(90)
        self.spin_embank_ds_level.setToolTip(
            'Embankment crest level at the downstream end (end chainage).\n'
            'Auto-filled from the ground profile once a DEM is added —\n'
            'change it, or drag the purple triangle on the chart, to set\n'
            'the design crest instead.'
        )
        self.spin_embank_ds_level.valueChanged.connect(self._on_embank_invert_levels_changed)
        vert_row.addWidget(_flow_pair('DS Crest:', self.spin_embank_ds_level))

        # Own dedicated button, moved here from the shared bottom row —
        # deliberately a separate tool from the generic chart 'cursor'
        # sketch tool (which drops a named reference line) so the two
        # never get confused.
        self.btn_embank_break = QPushButton('Vertical Break')
        self.btn_embank_break.setCheckable(True)
        self.btn_embank_break.setToolTip(
            'Click along the embankment crest line in the chart to add a\n'
            'vertical break point (a change of slope).\n'
            'Existing break points can always be dragged (left) or\n'
            'removed (right-click) regardless of this tool\'s state.\n'
            'With this OFF, click a level or slope % label instead to\n'
            'type an exact value.'
        )
        self.btn_embank_break.clicked.connect(self._toggle_embank_break_tool)
        vert_row.addWidget(self.btn_embank_break)

        # Places a CONTROL SECTION — a lightweight station with its own
        # full cross-section (edited in the SAME grid below as a break
        # point's), used to steer the Check Cross-Section reach-
        # interpolation blend between real stations, for both numeric
        # params AND Topo Adjustment (see _embank_control_sections /
        # _embank_xs_all_anchor_list). Unlike a break point: no level/
        # geometry of its own, and it can't be dragged.
        self.btn_embank_control = QPushButton('Control Section')
        self.btn_embank_control.setCheckable(True)
        self.btn_embank_control.setToolTip(
            'Click along the embankment crest line to add a control section\n'
            '— a station with its own full cross-section (edit it in the\n'
            'grid below, same as a break point) used to steer the\n'
            'interpolated blend between two real stations, for both\n'
            'widths/slopes AND Topo Adjustment. Unlike a break point this\n'
            'has no level of its own and can\'t be dragged — only affects\n'
            'the interpolated preview / eventual TIFF, not the real\n'
            'alignment geometry.\n'
            'Existing control sections can always be clicked (select) or\n'
            'removed (right-click) regardless of this tool\'s state.'
        )
        self.btn_embank_control.clicked.connect(self._toggle_embank_control_tool)
        vert_row.addWidget(self.btn_embank_control)
        lv.addLayout(vert_row)

        # Fill Depth and Interval share ONE row.
        toe_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.chk_embank_diff = QCheckBox('Fill Depth [m]')
        self.chk_embank_diff.setToolTip(
            'Shows Crest minus Ground (Y1 - Y2) at the cursor on hover.\n'
            'Y1 is always the embankment crest; Y2 is the raster picked here.\n'
            'Positive = fill this much; negative = ground is above the\n'
            'design crest (cut needed there instead).'
        )
        self.chk_embank_diff.stateChanged.connect(self._on_embank_diff_changed)
        toe_row.addWidget(self.chk_embank_diff)

        self.combo_embank_diff_col = QComboBox()
        self.combo_embank_diff_col.setFixedWidth(120)
        self.combo_embank_diff_col.wheelEvent = lambda e: e.ignore()
        self.combo_embank_diff_col.currentIndexChanged.connect(self._on_embank_diff_changed)
        toe_row.addWidget(_flow_pair('vs:', self.combo_embank_diff_col))

        self.spin_embank_interval = QDoubleSpinBox()
        self.spin_embank_interval.setRange(0.001, 100000.0)
        self.spin_embank_interval.setValue(1.0)
        self.spin_embank_interval.setDecimals(3)
        self.spin_embank_interval.setSuffix(' m')
        self.spin_embank_interval.setFixedWidth(80)
        self.spin_embank_interval.setToolTip(
            'Sampling interval along the alignment and cross-sections\n'
            'on this tab (own setting — independent of Layer Profile\'s).'
        )
        toe_row.addWidget(_flow_pair('Interval:', self.spin_embank_interval))
        lv.addLayout(toe_row)
        # Anchors any leftover height the QTabWidget hands this page at
        # the BOTTOM (as one harmless gap) instead of Qt spreading it
        # between the rows above — see the matching lx.addStretch() too.
        la.addStretch()

        self.lbl_embank_xs_status = QLabel(
            'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
        self.lbl_embank_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_embank_xs_status.setWordWrap(True)
        lx.addWidget(self.lbl_embank_xs_status)

        # Left/Right mini-tabs — each side is edited independently now
        # (own section stack, own Crest Width field shown on its own
        # Section 1 row — see _build_embank_section_row), per explicit
        # request. Each tab holds a scrollable stack of section rows
        # (see _rebuild_embank_section_rows_side) capped to a fixed
        # height so a long stack never pushes the embedded Cross-Section
        # Preview chart below out of view, a shared action area (Flush
        # with Ground / Add Section / Remove) that reacts to whichever
        # row is currently selected, and a Copy to Other Half button.
        self._embank_xs_side_ui = {'left': None, 'right': None}
        self.tabs_embank_xs_side = QTabWidget()
        self.tabs_embank_xs_side.setStyleSheet(_style.SUBTAB_STYLE)
        self.tabs_embank_xs_side.addTab(self._build_embank_xs_side_tab('left'), 'Left Half')
        self.tabs_embank_xs_side.addTab(self._build_embank_xs_side_tab('right'), 'Right Half')
        # Belt-and-braces: force a fresh rebuild of whichever side's rows
        # just became visible. Both sides are already rebuilt together on
        # every selection change (_rebuild_embank_section_rows), so this
        # is only a safety net for a tab that was never actually shown
        # since the last rebuild — Qt can leave a QTabWidget page that's
        # never been raised without a proper first layout pass, which
        # read as 'the other side is empty' even though its data was
        # there all along.
        self.tabs_embank_xs_side.currentChanged.connect(
            lambda idx: self._rebuild_embank_section_rows_side('left' if idx == 0 else 'right'))
        lx.addWidget(self.tabs_embank_xs_side)

        # Copy shortcuts + Check Cross-Section — one row, since there's no
        # more fixed 4-column grid for them to sit inside.
        xs_btn_row = FlowLayout(h_spacing=10, v_spacing=6)
        self.btn_embank_xs_copy_ds = QPushButton('Copy to All DS Section')
        self.btn_embank_xs_copy_ds.setToolTip(
            "Copy the currently selected station's WHOLE section stack to\n"
            'every station DOWNSTREAM of it (including control sections) —\n'
            'everything upstream, including this station itself, is left\n'
            'unchanged (asks to confirm first).'
        )
        self.btn_embank_xs_copy_ds.setEnabled(False)
        self.btn_embank_xs_copy_ds.clicked.connect(self._on_embank_xs_copy_ds_all)
        xs_btn_row.addWidget(self.btn_embank_xs_copy_ds)

        self.btn_embank_xs_copy_all = QPushButton('Copy Section to All')
        self.btn_embank_xs_copy_all.setToolTip(
            "Copy the currently selected station's WHOLE section stack to\n"
            'every other station on this alignment (asks to confirm first).'
        )
        self.btn_embank_xs_copy_all.setEnabled(False)
        self.btn_embank_xs_copy_all.clicked.connect(self._on_embank_xs_copy_all)
        xs_btn_row.addWidget(self.btn_embank_xs_copy_all)

        self.btn_embank_check_xs = QPushButton('Check Cross-Section…')
        self.btn_embank_check_xs.setToolTip(
            'Preview how the cross-section is interpolated between two\n'
            'break points along a reach, stepping through it at a chosen\n'
            'spacing — opens in its own window.'
        )
        self.btn_embank_check_xs.clicked.connect(self._open_embank_check_xs)
        xs_btn_row.addWidget(self.btn_embank_check_xs)
        lx.addLayout(xs_btn_row)

        # Embedded preview — deliberately INSIDE this dock (unlike the
        # Layer Profile tab's cross-section, which pops out into its own
        # XSectionDialog window) since the brief specifically asked for
        # the section plot to live in the palette itself. The exact shape
        # this draws is a placeholder until the four parameters' precise
        # geometric meaning is confirmed — see _draw_embank_xs_preview.
        if MATPLOTLIB_AVAILABLE:
            self.figure_embank_xs = Figure(figsize=(4, 2.0), dpi=100)
            self.ax_embank_xs = self.figure_embank_xs.add_subplot(111)
            self.canvas_embank_xs = FigureCanvas(self.figure_embank_xs)
            self.canvas_embank_xs.setMinimumHeight(170)
            # Expanding + a stretch factor when added below — otherwise
            # this widget just sits at its minimum height and any extra
            # room in the dock goes unused as blank space beneath it.
            self.canvas_embank_xs.setSizePolicy(_EXPAND, _EXPAND)
            self.canvas_embank_xs.mpl_connect('scroll_event', self._on_embank_xs_scroll_zoom)
            self.canvas_embank_xs.mpl_connect('button_press_event', self._on_embank_xs_press)
            self.canvas_embank_xs.mpl_connect('motion_notify_event', self._on_embank_xs_motion)
            self.canvas_embank_xs.mpl_connect('button_release_event', self._on_embank_xs_release)
            self.canvas_embank_xs.mpl_connect('resize_event', self._on_embank_xs_canvas_resize)
            lx.addWidget(self.canvas_embank_xs, 1)

            # Floating spot-elevation button OVER the chart itself (top-
            # right corner), not in the surrounding palette — click it
            # on, then click a point on the chart to read its elevation
            # (snapped onto the design line or the ground profile,
            # whichever is nearer — see _embank_xs_snap_point); click
            # again to remove that reading. Shows the same inverted-
            # triangle glyph as the marker it places, rather than the
            # word 'Elev', per explicit request.
            self.btn_embank_xs_elev = QPushButton('▽', self.canvas_embank_xs)
            self.btn_embank_xs_elev.setCheckable(True)
            self.btn_embank_xs_elev.setFixedSize(26, 26)
            self.btn_embank_xs_elev.setToolTip(
                'Click, then click a point on the chart to read its\n'
                'elevation — snaps onto the design line or the ground\n'
                'profile, whichever is nearer. Click the chart again to\n'
                'remove it.'
            )
            self.btn_embank_xs_elev.setStyleSheet(
                'QPushButton{background:rgba(255,255,255,210);border:1px solid #888;'
                'border-radius:13px;font-size:13px;font-weight:bold;color:#7A0000;}'
                'QPushButton:checked{background:#FFB74D;border:1px solid #E65100;}'
            )
            self.btn_embank_xs_elev.clicked.connect(self._toggle_embank_xs_elev_tool)
            self.btn_embank_xs_elev.raise_()

            # Floating '+' button right below the elevation tool — picks a
            # raster (defaulting to the last Create-TIFF'd layer) to
            # sample and plot alongside the design/ground lines, for
            # checking the actual generated surface against what it was
            # built from.
            self.btn_embank_xs_add_compare = QPushButton('+', self.canvas_embank_xs)
            self.btn_embank_xs_add_compare.setFixedSize(26, 26)
            self.btn_embank_xs_add_compare.setToolTip(
                'Pick a raster layer (e.g. an Embankment Design TIFF) to\n'
                'sample and plot alongside this preview, to compare it\n'
                'against the design/ground profile.'
            )
            self.btn_embank_xs_add_compare.setStyleSheet(
                'QPushButton{background:rgba(255,255,255,210);border:1px solid #888;'
                'border-radius:13px;font-size:15px;font-weight:bold;color:#2E7D32;}'
                'QPushButton:hover{background:rgba(255,255,255,255);}'
            )
            self.btn_embank_xs_add_compare.clicked.connect(self._on_embank_xs_add_compare)
            self.btn_embank_xs_add_compare.raise_()

            # Floating 'Z:' factor control in the TOP-LEFT corner (the
            # Elev/Add buttons stay top-right) — vertical exaggeration,
            # same convention as QGIS's own embankment Z factor (1 = true
            # scale). This preview's OWN value, independent of Check
            # Cross-Section's; see _on_embank_xs_zfactor_changed.
            self.zf_embank_xs_widget = QWidget(self.canvas_embank_xs)
            self.zf_embank_xs_widget.setFixedSize(60, 22)
            self.zf_embank_xs_widget.setStyleSheet(
                'QWidget{background:rgba(255,255,255,200);border:1px solid #888;border-radius:3px;}'
            )
            _zf_l = QHBoxLayout(self.zf_embank_xs_widget)
            _zf_l.setContentsMargins(3, 0, 2, 0)
            _zf_l.setSpacing(2)
            _zf_lbl = QLabel('Z:')
            _zf_lbl.setStyleSheet('font-size:9px;border:none;background:transparent;')
            _zf_l.addWidget(_zf_lbl)
            self.spin_embank_xs_zfactor = QDoubleSpinBox()
            self.spin_embank_xs_zfactor.setRange(0.1, 100.0)
            self.spin_embank_xs_zfactor.setValue(self._embank_xs_z_factor)
            self.spin_embank_xs_zfactor.setSingleStep(0.5)
            self.spin_embank_xs_zfactor.setDecimals(1)
            self.spin_embank_xs_zfactor.setFixedWidth(42)
            self.spin_embank_xs_zfactor.setStyleSheet(
                'QDoubleSpinBox{font-size:9px;border:none;background:transparent;}')
            self.spin_embank_xs_zfactor.wheelEvent = lambda e: e.ignore()
            self.spin_embank_xs_zfactor.setToolTip(
                "Vertical exaggeration — same idea as QGIS's own embankment\n"
                'Z factor. 1 = true scale (1 m vertical = 1 m horizontal\n'
                'on screen); 2 = vertical scale doubled, and so on.\n'
                "This preview's own setting — independent of Check\n"
                "Cross-Section's own Z factor."
            )
            self.spin_embank_xs_zfactor.valueChanged.connect(self._on_embank_xs_zfactor_changed)
            _zf_l.addWidget(self.spin_embank_xs_zfactor)
            self.zf_embank_xs_widget.raise_()

            self._position_embank_xs_elev_btn()

            self._draw_embank_xs_preview()
        else:
            self.canvas_embank_xs = None

        lx.addStretch()
        self.tabs_embank_workflow.addTab(tab_a, 'Alignment')
        self.tabs_embank_workflow.addTab(tab_x, 'Cross-Section')
        l.addWidget(self.tabs_embank_workflow)
        l.addSpacing(6)
        self.lbl_embank_status = QLabel('Horizontal alignment: not drawn')
        self.lbl_embank_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_embank_status.setWordWrap(True)
        l.addWidget(self.lbl_embank_status)

        sc.setWidget(inner)
        outer_l.addWidget(sc)
        return tab

    def _embank_tool_buttons(self):
        """Every one of Embankment Surface's own checkable 'mode' tool
        buttons (Draw Line, Edit Vertices, Vertical Break, Control
        Section, the Cross-Section Preview's own spot-elevation tool),
        paired with its own toggle handler — the single list
        _embank_deactivate_all_tools and _embank_escape_deactivate_tool
        both walk, so only ever one of these is active at a time."""
        pairs = [
            (self.btn_embank_draw, self._toggle_embank_digitizing),
            (self.btn_embank_vertex_edit, self._toggle_embank_vertex_edit),
            (self.btn_embank_break, self._toggle_embank_break_tool),
            (self.btn_embank_control, self._toggle_embank_control_tool),
        ]
        if getattr(self, 'btn_embank_xs_elev', None) is not None:
            pairs.append((self.btn_embank_xs_elev, self._toggle_embank_xs_elev_tool))
        return pairs

    def _embank_deactivate_all_tools(self, except_btn=None):
        """Turns off whichever Embankment Surface tool button(s) are
        currently checked, other than `except_btn` (the one just turned
        ON, when this is called from inside that button's own toggle
        handler to clear everyone else first). Also the target of
        Escape (see _embank_escape_deactivate_tool) — calling this with
        no argument turns everything off."""
        for btn, toggle_fn in self._embank_tool_buttons():
            if btn is except_btn or not btn.isChecked():
                continue
            btn.setChecked(False)
            toggle_fn(False)

    def _embank_escape_deactivate_tool(self):
        """Escape cancels whichever Embankment Surface tool is currently
        active — same effect as clicking that tool's own button again,
        per explicit request so a mode never has to be manually toggled
        back off. A no-op (and harmless) when nothing's active, so this
        can be bound broadly without worrying about stealing Escape from
        something unrelated."""
        if self._active_tab == _TAB_EMBANK:
            self._embank_deactivate_all_tools()

    def _toggle_embank_digitizing(self, checked):
        if checked:
            self._embank_deactivate_all_tools(except_btn=self.btn_embank_draw)
            self._prev_tool = self.canvas.mapTool()
            self._embank_draw_start_extent = self.canvas.extent()
            self._embank_map_tool.reset()
            self.canvas.setMapTool(self._embank_map_tool)
            self.lbl_embank_status.setText('Click to add vertices — right-click to finish…')
            self.lbl_embank_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._embank_map_tool:
                self.canvas.unsetMapTool(self._embank_map_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)

    def _on_embank_import_line(self):
        self._import_alignment_line('Embankment', self._on_embank_line_captured)

    def _on_embank_line_captured(self, geom):
        if geom.isMultipart():
            parts = geom.asMultiPolyline()
            pts = parts[0] if parts else []
        else:
            pts = geom.asPolyline()
        self._embank_raw_pts = [QgsPointXY(p) for p in pts]
        # A freshly-drawn alignment has all-new vertex indices and a new
        # chainage range — any previous per-vertex fillet overrides,
        # vertical break points, and end levels (tied to the OLD line's
        # geometry) no longer mean anything. Levels reset to None so they
        # re-seed from the new ground profile on the next refresh.
        self._embank_vertex_radii = {}
        self._embank_selected_vertex = None
        self._embank_invert_breaks = []
        self._embank_us_level = None
        self._embank_ds_level = None
        self._embank_break_drag_obj = None
        self._embank_endpoint_drag = None
        self._embank_xs_params = {}
        self._embank_control_sections = []
        self._embank_selected_xs_key = None
        self._embank_xs_ground = None
        if hasattr(self, 'lbl_embank_xs_status'):
            self.btn_embank_xs_copy_ds.setEnabled(False)
            self.btn_embank_xs_copy_all.setEnabled(False)
            self._rebuild_embank_section_rows()
            self.lbl_embank_xs_status.setText(
                'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
            self.lbl_embank_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            self._draw_embank_xs_preview()
        if hasattr(self, 'spin_embank_us_level'):
            self.spin_embank_us_level.blockSignals(True)
            self.spin_embank_us_level.setValue(0.0)
            self.spin_embank_us_level.blockSignals(False)
            self.spin_embank_ds_level.blockSignals(True)
            self.spin_embank_ds_level.setValue(0.0)
            self.spin_embank_ds_level.blockSignals(False)
        if self.btn_embank_break.isChecked():
            self.btn_embank_break.setChecked(False)
            self._toggle_embank_break_tool(False)
        if self.btn_embank_control.isChecked():
            self.btn_embank_control.setChecked(False)
            self._toggle_embank_control_tool(False)
        self.btn_embank_draw.setChecked(False)
        self._toggle_embank_digitizing(False)
        # Deferred (QTimer.singleShot) rather than called directly: this
        # handler runs synchronously INSIDE the map canvas's own
        # double-click dispatch (ProfileLineTool.canvasDoubleClickEvent ->
        # _finalise -> line_completed, a direct same-thread connection),
        # and _rebuild_embank_alignment() flows into
        # _refresh_embank_profile(), which calls
        # QApplication.processEvents() before a potentially slow raster
        # extract — doing that while still inside the canvas's own event
        # handler risks Qt processing queued events re-entrantly mid-
        # gesture. Deferring lets the double-click event finish cleanly
        # first.
        #
        # Snapshot-and-restore the canvas extent around it as a defensive
        # belt-and-braces measure on top of that: nothing in this plugin
        # ever calls setExtent/zoomTo/setCenter anywhere (checked), so
        # whatever nudges the extent right at this moment is an indirect
        # QGIS-side effect (most likely the dock's own layout changing
        # size — new status text, spinboxes enabling — causing the canvas
        # WIDGET to resize, which QGIS then reads as 'show more/less area
        # at the new aspect ratio', i.e. a zoom nobody asked for) rather
        # than anything this code explicitly requests. Restored extent is
        # the one from BEFORE 'Draw Line' was even pressed (captured in
        # _toggle_embank_digitizing), per explicit request to end up
        # exactly where the user started rather than wherever it happened
        # to be at the moment of the final double-click — any drift that
        # crept in gradually while adding vertices gets undone too, not
        # just drift from the finishing click itself.
        restore_extent = self._embank_draw_start_extent

        def _rebuild_and_restore_extent():
            self._rebuild_embank_alignment()
            if restore_extent is not None:
                self.canvas.setExtent(restore_extent)
                self.canvas.refresh()

        QTimer.singleShot(0, _rebuild_and_restore_extent)
        self._update_embank_fillet_ui()

    @staticmethod
    def _fillet_alignment(pts, default_radius, overrides=None):
        """Round sharp interior bends of a polyline with a circular arc.
        Each interior vertex (index i, 1..len(pts)-2) uses overrides.get(i,
        default_radius) as its own radius — so bends can be rounded by
        different amounts instead of one setting for the whole alignment.
        A per-vertex radius <= 0 keeps that corner sharp. Each arc is sized
        to the shorter of its two adjacent segments, so fillets on
        tightly-spaced vertices never overlap or overshoot."""
        overrides = overrides or {}
        if len(pts) < 3:
            return list(pts)
        out = [pts[0]]
        n = len(pts)
        for i in range(1, n - 1):
            radius = overrides.get(i, default_radius)
            prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
            if radius is None or radius <= 1e-6:
                out.append(cur)
                continue
            v1x, v1y = prev.x() - cur.x(), prev.y() - cur.y()
            v2x, v2y = nxt.x() - cur.x(), nxt.y() - cur.y()
            len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if len1 < 1e-9 or len2 < 1e-9:
                out.append(cur)
                continue
            u1x, u1y = v1x / len1, v1y / len1
            u2x, u2y = v2x / len2, v2y / len2
            dot = max(-1.0, min(1.0, u1x * u2x + u1y * u2y))
            theta = math.acos(dot)
            if theta > math.radians(179.0) or theta < 1e-6:
                out.append(cur)  # near-straight — no corner to round
                continue
            half = theta / 2.0
            tan_half = math.tan(half)
            t = radius / tan_half if tan_half > 1e-9 else 0.0
            t = min(t, 0.999 * min(len1, len2))
            r_eff = t * tan_half
            if t < 1e-6 or r_eff < 1e-6:
                out.append(cur)
                continue
            A = QgsPointXY(cur.x() + u1x * t, cur.y() + u1y * t)
            B = QgsPointXY(cur.x() + u2x * t, cur.y() + u2y * t)
            bx, by = u1x + u2x, u1y + u2y
            blen = math.hypot(bx, by)
            if blen < 1e-9:
                out.append(cur)
                continue
            bx, by = bx / blen, by / blen
            d = r_eff / math.sin(half)
            cx, cy = cur.x() + bx * d, cur.y() + by * d
            ang_a = math.atan2(A.y() - cy, A.x() - cx)
            ang_b = math.atan2(B.y() - cy, B.x() - cx)
            da = (ang_b - ang_a) % (2 * math.pi)
            if da > math.pi:
                da -= 2 * math.pi
            n_seg = max(2, int(abs(math.degrees(da)) / 10) + 1)
            out.append(A)
            for s in range(1, n_seg):
                a = ang_a + da * (s / n_seg)
                out.append(QgsPointXY(cx + r_eff * math.cos(a), cy + r_eff * math.sin(a)))
            out.append(B)
        out.append(pts[-1])
        return out

    @staticmethod
    def _fillet_ring(pts, default_radius, overrides=None):
        """Same idea as _fillet_alignment, but for a CLOSED ring (Wetland
        Pond's own polygon boundary) — every vertex, including the
        wrap-around corner between the last and first point, can be
        rounded, since a closed ring has no 'ends' the way an open
        alignment does (whose own first/last points are never filleted
        there, since there's nothing for them to turn between). Expects
        pts WITHOUT the duplicate closing point OGC polygon rings
        otherwise carry (first == last) — strip that before calling,
        re-close the result after."""
        overrides = overrides or {}
        n = len(pts)
        if n < 3:
            return list(pts)
        out = []
        for i in range(n):
            radius = overrides.get(i, default_radius)
            prev, cur, nxt = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
            if radius is None or radius <= 1e-6:
                out.append(cur)
                continue
            v1x, v1y = prev.x() - cur.x(), prev.y() - cur.y()
            v2x, v2y = nxt.x() - cur.x(), nxt.y() - cur.y()
            len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if len1 < 1e-9 or len2 < 1e-9:
                out.append(cur)
                continue
            u1x, u1y = v1x / len1, v1y / len1
            u2x, u2y = v2x / len2, v2y / len2
            dot = max(-1.0, min(1.0, u1x * u2x + u1y * u2y))
            theta = math.acos(dot)
            if theta > math.radians(179.0) or theta < 1e-6:
                out.append(cur)
                continue
            half = theta / 2.0
            tan_half = math.tan(half)
            t = radius / tan_half if tan_half > 1e-9 else 0.0
            t = min(t, 0.999 * min(len1, len2))
            r_eff = t * tan_half
            if t < 1e-6 or r_eff < 1e-6:
                out.append(cur)
                continue
            A = QgsPointXY(cur.x() + u1x * t, cur.y() + u1y * t)
            B = QgsPointXY(cur.x() + u2x * t, cur.y() + u2y * t)
            bx, by = u1x + u2x, u1y + u2y
            blen = math.hypot(bx, by)
            if blen < 1e-9:
                out.append(cur)
                continue
            bx, by = bx / blen, by / blen
            d = r_eff / math.sin(half)
            cx, cy = cur.x() + bx * d, cur.y() + by * d
            ang_a = math.atan2(A.y() - cy, A.x() - cx)
            ang_b = math.atan2(B.y() - cy, B.x() - cx)
            da = (ang_b - ang_a) % (2 * math.pi)
            if da > math.pi:
                da -= 2 * math.pi
            n_seg = max(2, int(abs(math.degrees(da)) / 10) + 1)
            out.append(A)
            for s in range(1, n_seg):
                a = ang_a + da * (s / n_seg)
                out.append(QgsPointXY(cx + r_eff * math.cos(a), cy + r_eff * math.sin(a)))
            out.append(B)
        return out

    def _rebuild_embank_alignment(self):
        if not self._embank_raw_pts or len(self._embank_raw_pts) < 2:
            return
        filleted = self._fillet_alignment(self._embank_raw_pts, self._embank_fillet_radius,
                                           self._embank_vertex_radii)
        self._embank_geom = QgsGeometry.fromPolylineXY(filleted)
        # Mirrors self.profile_geom exactly like the Network Profile tab's
        # concatenated pipe chain does — from here on this behaves like a
        # drawn/selected Layer Profile line, so the existing chart hover
        # cursor (_on_chart_hover) moves the map marker along it without
        # any tab-specific hover logic needing to exist.
        self.profile_geom = self._embank_geom

        if self._embank_align_band:
            try: self.canvas.scene().removeItem(self._embank_align_band)
            except Exception: pass
        self._embank_align_band = QgsRubberBand(self.canvas, _LINE_GEOM)
        self._embank_align_band.setColor(QColor(255, 111, 0, 220))
        self._embank_align_band.setWidth(3)
        self._embank_align_band.setToGeometry(self._embank_geom, None)
        self._update_embank_extend_band()

        length = self._embank_geom.length()
        self.lbl_embank_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
        self.lbl_embank_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

        self._draw_embank_vertex_markers()
        self._draw_embank_chainage_marks()
        self._refresh_embank_profile()

    def _clear_embank_alignment(self):
        self._close_embank_inline_editor(commit=False)
        self._embank_raw_pts = []
        self._embank_geom = None
        self.profile_geom = None
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
        self._profile_markers        = []
        self._profile_marker_artists = []
        self._clear_marker_map_points()
        self._hide_hover()
        self._embank_vertex_radii = {}
        self._embank_selected_vertex = None
        if self.btn_embank_vertex_edit.isChecked():
            self.btn_embank_vertex_edit.setChecked(False)
            self._toggle_embank_vertex_edit(False)
        if self._embank_align_band:
            try: self.canvas.scene().removeItem(self._embank_align_band)
            except Exception: pass
            self._embank_align_band = None
        self._update_embank_extend_band()
        self._clear_embank_chainage_marks()
        self._clear_embank_vertex_markers()
        self._embank_map_tool.reset()

        # Vertical alignment resets together with the horizontal one — a
        # break point or end level tied to a line that no longer exists
        # has nothing left to mean.
        self._embank_us_level = None
        self._embank_ds_level = None
        self._embank_invert_breaks = []
        self._embank_ground_col = None
        self._embank_break_drag_obj = None
        self._embank_endpoint_drag = None
        if hasattr(self, 'btn_embank_break') and self.btn_embank_break.isChecked():
            self.btn_embank_break.setChecked(False)
            self._toggle_embank_break_tool(False)
        if hasattr(self, 'btn_embank_control') and self.btn_embank_control.isChecked():
            self.btn_embank_control.setChecked(False)
            self._toggle_embank_control_tool(False)
        if hasattr(self, 'spin_embank_us_level'):
            self.spin_embank_us_level.blockSignals(True)
            self.spin_embank_us_level.setValue(0.0)
            self.spin_embank_us_level.blockSignals(False)
            self.spin_embank_ds_level.blockSignals(True)
            self.spin_embank_ds_level.setValue(0.0)
            self.spin_embank_ds_level.blockSignals(False)
        self._clear_embank_break_map_bands()
        self._clear_embank_xs_map_marker()
        for art in self._embank_invert_artists:
            try: art.remove()
            except Exception: pass
        self._embank_invert_artists = []
        self._embank_diff_ann = None

        self._embank_xs_params = {}
        self._embank_control_sections = []
        self._embank_selected_xs_key = None
        self._embank_xs_ground = None
        if hasattr(self, 'lbl_embank_xs_status'):
            self.btn_embank_xs_copy_ds.setEnabled(False)
            self.btn_embank_xs_copy_all.setEnabled(False)
            self._rebuild_embank_section_rows()
            self.lbl_embank_xs_status.setText(
                'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
            self.lbl_embank_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
            self._draw_embank_xs_preview()

        if hasattr(self, 'btn_embank_draw'):
            self.btn_embank_draw.setChecked(False)
        if hasattr(self, 'lbl_embank_status'):
            self.lbl_embank_status.setText('Horizontal alignment: not drawn')
            self.lbl_embank_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        if hasattr(self, 'spin_embank_fillet'):
            self._update_embank_fillet_ui()
        if MATPLOTLIB_AVAILABLE and self._active_tab == _TAB_EMBANK:
            self._reset_axes()
            self.canvas_plot.draw()
        if self.btn_save_png is not None:
            self.btn_save_png.setEnabled(False)

    def _clear_embank_chainage_marks(self):
        for _attr in ('_embank_major_band', '_embank_minor_band'):
            band = getattr(self, _attr, None)
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
                setattr(self, _attr, None)
        for ann in self._embank_chainage_annotations:
            self._remove_ann(ann)
        self._embank_chainage_annotations = []

    def _add_embank_chainage_label(self, pt, chainage, angle_deg=0.0, color='#B71C1C', target=None, text=None):
        target = self._embank_chainage_annotations if target is None else target
        label_text = f'{chainage:.0f}' if text is None else text
        try:
            from qgis.core import QgsAnnotationPointTextItem, QgsTextFormat, QgsTextBufferSettings
            from qgis.PyQt.QtGui import QFont, QColor as _QColor
            item = QgsAnnotationPointTextItem(label_text, QgsPointXY(pt))
            try:
                item.setAngle(angle_deg)   # aligns the text with the tick mark's own direction
            except Exception:
                pass
            fmt = QgsTextFormat()
            font = QFont('Sans Serif', 7); font.setBold(True)
            fmt.setFont(font)
            fmt.setColor(_QColor(color))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True); buf.setSize(1.5)
            buf.setColor(_QColor(255, 255, 255))
            fmt.setBuffer(buf)
            item.setFormat(fmt)
            ann_layer = QgsProject.instance().mainAnnotationLayer()
            item_id = ann_layer.addItem(item)
            target.append((ann_layer, item_id))
        except Exception:
            pass

    def _cleanup_stray_embank_annotations(self):
        """One-time sweep, run once when this dock is created, for stray
        chainage/break/'X-S' labels left behind in QGIS's own
        mainAnnotationLayer() from a PREVIOUS session. These labels are
        meant to be purely transient UI (they get cleared and redrawn
        constantly as the alignment is edited), but the layer they live
        in is a real, PROJECT-PERSISTED layer — if the project was saved
        while one was on screen, reopening that project brings it back
        even though this freshly-created dock has no memory of it (the
        Python object that could have removed it belonged to a previous
        session). Restarting QGIS does NOT clear these, since they're
        baked into the saved project, not just runtime canvas state.

        Removing them safely (without risking a user's own, unrelated,
        manually-placed QGIS annotations) means never matching on text
        content alone — instead this requires BOTH a matching text
        pattern (purely numeric, or exactly 'X-S') AND this exact
        style/formatting fingerprint (_add_embank_chainage_label always
        uses the same font/buffer settings for every label it creates),
        which a manually-placed annotation — created through QGIS's own
        Annotation tool, with whatever default styling that applies —
        essentially can't coincidentally match."""
        try:
            ann_layer = QgsProject.instance().mainAnnotationLayer()
            if ann_layer is None:
                return
            items = dict(ann_layer.items())
        except Exception:
            return
        for item_id, item in items.items():
            try:
                text = item.text()
            except Exception:
                continue
            if not (text == 'X-S' or text.strip().lstrip('-').isdigit()):
                continue
            try:
                fmt = item.format()
                font = fmt.font()
                buf = fmt.buffer()
                looks_like_ours = (
                    font.family() == 'Sans Serif' and font.pointSize() == 7
                    and font.bold() and buf.enabled() and abs(buf.size() - 1.5) < 1e-6
                )
            except Exception:
                looks_like_ours = False
            if looks_like_ours:
                try:
                    ann_layer.removeItem(item_id)
                except Exception:
                    pass

    def _embank_tick_geom(self, d, half_len):
        """Perpendicular tick line + label anchor/angle at chainage d along
        self._embank_geom — shared by the major/minor chainage marks and
        the vertical-break map markers, so both read the exact same
        alignment direction and label placement/rotation convention."""
        geom = self._embank_geom
        if geom is None or geom.isEmpty():
            return None
        pt_g = geom.interpolate(d)
        if pt_g is None or pt_g.isEmpty():
            return None
        pt = pt_g.asPoint()
        try:
            ang = geom.interpolateAngle(d)
        except Exception:
            ang = 0.0
        fx, fy = math.sin(ang), math.cos(ang)   # forward dir (bearing, clockwise from N)
        px, py = fy, -fx                        # perpendicular to it — the tick's own direction
        p1 = QgsPointXY(pt.x() + px * half_len, pt.y() + py * half_len)
        p2 = QgsPointXY(pt.x() - px * half_len, pt.y() - py * half_len)
        # Label anchor sits just past the tick's outer end (p1 side,
        # always — consistently the same side of the alignment rather
        # than alternating) instead of on the alignment line itself,
        # and is rotated to run along the tick instead of floating
        # nearby at a fixed horizontal angle.
        label_pt = QgsPointXY(pt.x() + px * (half_len + 2.0), pt.y() + py * (half_len + 2.0))
        tick_angle = math.degrees(math.atan2(py, px)) % 360
        if 90 < tick_angle < 270:
            tick_angle -= 180   # keep the text reading left-to-right, never upside-down
        return QgsGeometry.fromPolylineXY([p1, p2]), label_pt, tick_angle

    def _draw_embank_chainage_marks(self):
        self._clear_embank_chainage_marks()
        geom = self._embank_geom
        if geom is None or geom.isEmpty():
            return
        length = geom.length()
        if length <= 0:
            return

        def _ticks(interval):
            if not interval or interval <= 0:
                return []
            out, d = [], 0.0
            while d <= length + 1e-6:
                out.append(min(d, length))
                d += interval
            return out

        major_list = _ticks(self._embank_major_interval) if self._embank_show_major_ch else []
        minor_list = _ticks(self._embank_minor_interval) if self._embank_show_minor_ch else []
        # The end chainage always gets a labelled mark, regardless of the
        # Major setting/interval — it rarely lands on a round interval
        # (e.g. a 96.94 m alignment with 20 m majors), but it's the one
        # chainage a modeller always needs to see.
        if not any(abs(d - length) < 1e-6 for d in major_list):
            major_list = major_list + [length]
        major_set  = {round(d, 3) for d in major_list}

        if minor_list:
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(120, 120, 120, 200))
            band.setWidth(1)
            drawn = False
            for d in minor_list:
                if round(d, 3) in major_set:
                    continue
                tg = self._embank_tick_geom(d, 3.0)
                if tg is None:
                    continue
                band.addGeometry(tg[0], None)
                drawn = True
            if drawn:
                self._embank_minor_band = band
            else:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass

        if major_list:
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(213, 0, 0, 230))
            band.setWidth(2)
            drawn = False
            for d in major_list:
                tg = self._embank_tick_geom(d, 6.0)
                if tg is None:
                    continue
                line_geom, label_pt, angle_deg = tg
                band.addGeometry(line_geom, None)
                self._add_embank_chainage_label(label_pt, d, angle_deg)
                drawn = True
            if drawn:
                self._embank_major_band = band
            else:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass

    def _on_embank_fillet_changed(self, value):
        idx = self._embank_selected_vertex
        if idx is not None:
            self._embank_vertex_radii[idx] = value
            self.btn_embank_vertex_reset.setVisible(True)
        else:
            self._embank_fillet_radius = value
        if self._embank_raw_pts:
            self._rebuild_embank_alignment()

    def _on_embank_vertex_reset(self):
        idx = self._embank_selected_vertex
        if idx is None:
            return
        self._embank_vertex_radii.pop(idx, None)
        self._update_embank_fillet_ui()
        if self._embank_raw_pts:
            self._rebuild_embank_alignment()

    def _on_embank_set_halfwidth_radius(self):
        """Raises every bend's fillet radius up to (at least) its own
        embankment half-width there — a one-click fix for the 'radius
        tighter than the embankment's own half-width' geometric problem
        (see _embank_check_tight_bends, the same check Create TIFF
        warns with) — below that radius the inside edge can't exist
        without folding over itself, which neither the bend densification
        nor the miter join can fix. Only ever RAISES a radius, never
        lowers one that's already adequate or larger, and leaves
        intentional sharp (0 m) corners alone entirely — those are
        deliberately mitered rather than rounded, see Edit Vertices'
        own tooltip. Loosen any individual bend further afterwards the
        normal way: select it with Edit Vertices and adjust the radius
        spinbox above. Confirms first (explaining what 'global' actually
        sets each bend TO) since the button's own name no longer spells
        out 'half-width' — see _build_embank_tab."""
        pts = self._embank_raw_pts
        if self._embank_geom is None or len(pts) <= 2:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Draw a horizontal alignment with at least one bend first.')
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            "Set Global Radius raises every bend's fillet radius to AT\n"
            "LEAST its own embankment half-width there (never lowers one\n"
            "that's already adequate, and leaves intentional sharp 0 m\n"
            'corners alone). Continue?',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        updated = 0
        for i in range(1, len(pts) - 1):
            r = self._embank_vertex_radii.get(i, self._embank_fillet_radius)
            if r is None or r <= 1e-6:
                continue   # intentional sharp corner — leave it mitered, not rounded
            try:
                ch_v = self._embank_geom.lineLocatePoint(QgsGeometry.fromPointXY(pts[i]))
            except Exception:
                continue
            params = self._embank_xs_interp_params(ch_v)
            half_w = self._embank_params_half_width(params)
            if r < half_w:
                self._embank_vertex_radii[i] = half_w
                updated += 1
        if updated == 0:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                "Every bend already has at least its own embankment half-width\n"
                'as its radius (or is an intentional sharp corner) — nothing to change.')
            return
        self._rebuild_embank_alignment()
        self._update_embank_fillet_ui()
        self.lbl_embank_status.setText(
            f'Raised {updated} bend(s) up to their own embankment half-width as fillet radius.')
        self.lbl_embank_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')

    def _embank_raw_chainage(self, idx):
        """Distance along the un-filleted alignment up to raw vertex idx —
        an approximate but cheap-to-compute chainage for labelling which
        vertex is currently selected (the actual filleted geometry shifts
        slightly near a rounded corner, but not enough to matter here)."""
        d = 0.0
        pts = self._embank_raw_pts
        for i in range(1, idx + 1):
            d += math.hypot(pts[i].x() - pts[i - 1].x(), pts[i].y() - pts[i - 1].y())
        return d

    def _update_embank_fillet_ui(self):
        idx = self._embank_selected_vertex
        pts = self._embank_raw_pts
        is_endpoint = idx is not None and (idx == 0 or idx == len(pts) - 1)
        self.spin_embank_fillet.blockSignals(True)
        if idx is None:
            self.spin_embank_fillet.setValue(self._embank_fillet_radius)
            self.lbl_embank_fillet_tag.setText('(default)')
            self.btn_embank_vertex_reset.setVisible(False)
            self.spin_embank_fillet.setEnabled(True)
        elif is_endpoint:
            # The two ends have no corner to round — fillet radius doesn't
            # apply, but they're still a selected vertex you can drag to
            # extend/shorten the alignment or right-click to remove.
            self.lbl_embank_fillet_tag.setText('(end point — drag to move, right-click to remove)')
            self.btn_embank_vertex_reset.setVisible(False)
            self.spin_embank_fillet.setEnabled(False)
        else:
            val = self._embank_vertex_radii.get(idx, self._embank_fillet_radius)
            self.spin_embank_fillet.setValue(val)
            ch = self._embank_raw_chainage(idx)
            self.lbl_embank_fillet_tag.setText(f'(vertex @ ~{ch:.1f} m)')
            self.btn_embank_vertex_reset.setVisible(idx in self._embank_vertex_radii)
            self.spin_embank_fillet.setEnabled(True)
        self.spin_embank_fillet.blockSignals(False)

    def _set_embank_selected_vertex(self, idx):
        self._embank_selected_vertex = idx
        self._update_embank_fillet_ui()
        self._draw_embank_vertex_markers()

    def _on_embank_vertex_clicked(self, raw_idx):
        if raw_idx is None:
            self._set_embank_selected_vertex(None)
            return
        if self._embank_selected_vertex == raw_idx:
            self._set_embank_selected_vertex(None)  # click same vertex again -> deselect
        else:
            self._set_embank_selected_vertex(raw_idx)

    def _embank_capture_chainage_anchors(self):
        """[(dict, old_world_QgsPointXY), ...] for every break point and
        control section, read off the CURRENT (pre-edit) self._embank_geom
        — must be called before that geometry is rebuilt. Paired with
        _embank_reproject_chainage_anchors so a vertex move/delete keeps
        break points/control sections 'attached' to the ground they were
        actually placed over, rather than silently drifting to a
        different physical spot (or past the end of a shortened
        alignment) just because the alignment's own vertices changed."""
        if self._embank_geom is None:
            return []
        anchors = []
        for b in self._embank_invert_breaks:
            anchors.append((b, self._embank_geom.interpolate(b['chainage']).asPoint()))
        for cs in self._embank_control_sections:
            anchors.append((cs, self._embank_geom.interpolate(cs['chainage']).asPoint()))
        return anchors

    def _embank_reproject_chainage_anchors(self, anchors, new_geom):
        """Updates each anchor dict's 'chainage' in place to wherever its
        OLD real-world point now falls on new_geom (nearest point along
        the edited line), clamped to the new length. See
        _embank_capture_chainage_anchors for why."""
        if not anchors or new_geom is None:
            return
        length = new_geom.length()
        for d, pt in anchors:
            try:
                ch = new_geom.lineLocatePoint(QgsGeometry.fromPointXY(pt))
            except Exception:
                ch = d['chainage']
            d['chainage'] = max(0.0, min(ch, length))
        self._embank_invert_breaks.sort(key=lambda b: b['chainage'])

    def _embank_refillet_geom(self):
        """The filleted QgsGeometry self._embank_raw_pts/_vertex_radii
        would currently produce, WITHOUT touching self._embank_geom —
        used to re-project chainage anchors against the about-to-be-
        current geometry before _rebuild_embank_alignment() commits it
        (that method recomputes the identical geometry itself right
        after; the small duplicate fillet cost is cheap and keeps this
        self-contained rather than threading a 'new_geom' through it)."""
        if len(self._embank_raw_pts) < 2:
            return None
        filleted = self._fillet_alignment(self._embank_raw_pts, self._embank_fillet_radius,
                                           self._embank_vertex_radii)
        return QgsGeometry.fromPolylineXY(filleted)

    def _embank_commit_endpoint_chainage_drag(self):
        """Called on mouse release after a US/DS marker drag — if it
        moved horizontally (self._embank_endpoint_drag_chainage differs
        from that end's real chainage), actually extends/trims the real
        alignment by sliding that end along its own existing segment
        direction (same math _embank_extended_ground_geom already uses
        to extrapolate ground sampling, via _embank_end_extension_dir),
        while correspondingly shrinking/growing Extend US/DS so the
        total visible ground-profile extent stays exactly the same —
        dragging outward spends down that end's Extend budget, dragging
        inward (trimming) grows it back to cover the now-exposed ground.
        A pure vertical (level-only) drag leaves this as a no-op."""
        end = self._embank_endpoint_drag
        c = self._embank_endpoint_drag_chainage
        if end is None or c is None or self._embank_geom is None:
            return
        if len(self._embank_raw_pts) < 2:
            return
        length = self._embank_geom.length()
        shift = -c if end == 'us' else c - length
        if abs(shift) < 1e-6:
            return
        dirn = self._embank_end_extension_dir(end)
        if dirn is None:
            return
        p0, ux, uy = dirn
        new_pt = QgsPointXY(p0.x() + ux * shift, p0.y() + uy * shift)
        anchors = self._embank_capture_chainage_anchors()
        if end == 'us':
            self._embank_raw_pts[0] = new_pt
            self._embank_extend_us = max(0.0, self._embank_extend_us - shift)
            self.spin_embank_extend_us.blockSignals(True)
            self.spin_embank_extend_us.setValue(self._embank_extend_us)
            self.spin_embank_extend_us.blockSignals(False)
        else:
            self._embank_raw_pts[-1] = new_pt
            self._embank_extend_ds = max(0.0, self._embank_extend_ds - shift)
            self.spin_embank_extend_ds.blockSignals(True)
            self.spin_embank_extend_ds.setValue(self._embank_extend_ds)
            self.spin_embank_extend_ds.blockSignals(False)
        self._embank_reproject_chainage_anchors(anchors, self._embank_refillet_geom())
        self._rebuild_embank_alignment()
        self._embank_vertex_tool.set_vertices(self._embank_raw_pts)

    def _on_embank_vertex_moved(self, raw_idx, new_pt):
        if not (0 <= raw_idx < len(self._embank_raw_pts)):
            return
        anchors = self._embank_capture_chainage_anchors()
        self._embank_raw_pts[raw_idx] = new_pt
        self._embank_reproject_chainage_anchors(anchors, self._embank_refillet_geom())
        self._rebuild_embank_alignment()
        self._update_embank_fillet_ui()
        self._embank_vertex_tool.set_vertices(self._embank_raw_pts)

    def _on_embank_vertex_deleted(self, raw_idx):
        if len(self._embank_raw_pts) <= 2:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'An alignment needs at least 2 points — this vertex can\'t be removed.')
            return
        if not (0 <= raw_idx < len(self._embank_raw_pts)):
            return
        anchors = self._embank_capture_chainage_anchors()
        self._embank_raw_pts.pop(raw_idx)
        self._embank_vertex_radii = {
            (k if k < raw_idx else k - 1): v
            for k, v in self._embank_vertex_radii.items() if k != raw_idx
        }
        if self._embank_selected_vertex is not None:
            if self._embank_selected_vertex == raw_idx:
                self._embank_selected_vertex = None
            elif self._embank_selected_vertex > raw_idx:
                self._embank_selected_vertex -= 1
        self._embank_reproject_chainage_anchors(anchors, self._embank_refillet_geom())
        self._rebuild_embank_alignment()
        self._update_embank_fillet_ui()
        self._embank_vertex_tool.set_vertices(self._embank_raw_pts)

    def _toggle_embank_vertex_edit(self, checked):
        if checked:
            self._embank_deactivate_all_tools(except_btn=self.btn_embank_vertex_edit)
            if len(self._embank_raw_pts) < 2:
                QMessageBox.information(
                    self, 'Advanced Profile Tool',
                    'Draw a horizontal alignment first.')
                self.btn_embank_vertex_edit.setChecked(False)
                return
            self._embank_vertex_tool.set_vertices(self._embank_raw_pts)
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._embank_vertex_tool)
            self.lbl_embank_status.setText(
                'Click a vertex to select it (set fillet radius above), drag to move it '
                '(including the two ends — extends/shortens the alignment), or '
                'right-click to remove it.')
            self.lbl_embank_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        else:
            if self.canvas.mapTool() is self._embank_vertex_tool:
                self.canvas.unsetMapTool(self._embank_vertex_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)
            self._set_embank_selected_vertex(None)

    def _clear_embank_vertex_markers(self):
        for band in self._embank_vertex_bands:
            try: self.canvas.scene().removeItem(band)
            except Exception: pass
        self._embank_vertex_bands = []
        if self._embank_vertex_selected_band is not None:
            try: self.canvas.scene().removeItem(self._embank_vertex_selected_band)
            except Exception: pass
            self._embank_vertex_selected_band = None

    def _draw_embank_vertex_markers(self):
        self._clear_embank_vertex_markers()
        pts = self._embank_raw_pts
        if len(pts) < 2:
            return
        for i, pt in enumerate(pts):
            band = QgsRubberBand(self.canvas, _POINT_GEOM)
            has_override = i in self._embank_vertex_radii
            band.setColor(QColor(255, 152, 0, 200) if has_override else QColor(3, 169, 244, 130))
            band.setIcon(_ICON_CIRCLE)
            band.setIconSize(12)
            band.setToGeometry(QgsGeometry.fromPointXY(pt), None)
            self._embank_vertex_bands.append(band)

        idx = self._embank_selected_vertex
        if idx is not None and 0 <= idx < len(pts):
            sel = QgsRubberBand(self.canvas, _POINT_GEOM)
            sel.setColor(QColor(255, 87, 34, 230))
            sel.setIcon(_ICON_BOX)
            sel.setIconSize(18)
            sel.setToGeometry(QgsGeometry.fromPointXY(pts[idx]), None)
            self._embank_vertex_selected_band = sel

    def _on_embank_chainage_settings_changed(self):
        self._embank_major_interval = self.spin_embank_major_ch.value()
        self._embank_minor_interval = self.spin_embank_minor_ch.value()
        self._embank_show_major_ch  = self.chk_embank_major_ch.isChecked()
        self._embank_show_minor_ch  = self.chk_embank_minor_ch.isChecked()
        if self._embank_geom is not None:
            self._draw_embank_chainage_marks()

    def _on_embank_raster_changed(self):
        if self._embank_geom is not None:
            self._refresh_embank_profile()

    def _on_embank_extend_changed(self, _value):
        self._embank_extend_us = self.spin_embank_extend_us.value()
        self._embank_extend_ds = self.spin_embank_extend_ds.value()
        self._update_embank_extend_band()
        if self._embank_geom is not None:
            self._refresh_embank_profile()

    def _on_embank_end_adjustment_changed(self, _state):
        self._embank_end_adjustment = self.chk_embank_end_adjustment.isChecked()

    def _embank_extended_ground_geom(self):
        """The alignment's own geometry, extended straight out from each
        end by self._embank_extend_us/_embank_extend_ds (each continuing
        that end's own last segment direction) — used ONLY for ground
        sampling, never for the real alignment, chainage marks, break
        points/control sections or the design crest line, which all stay
        exactly as drawn today. 0 for both (the default) returns
        self._embank_geom completely unchanged.

        Returns (geom, chain_shift) — chain_shift is how far the
        returned geometry's OWN chainage 0 sits before the real
        alignment's own start (0.0 unless extend_us > 0); the caller
        subtracts it from whatever chainages _extract() reports so they
        stay expressed in the real alignment's own chainage space (0 at
        the real start, length at the real end) rather than the
        extended sampling line's."""
        extend_us = max(0.0, self._embank_extend_us)
        extend_ds = max(0.0, self._embank_extend_ds)
        if extend_us <= 0.0 and extend_ds <= 0.0:
            return self._embank_geom, 0.0
        geom = self._embank_geom
        parts = geom.asMultiPolyline() if geom.isMultipart() else None
        pts = parts[0] if parts else geom.asPolyline()
        if len(pts) < 2:
            return geom, 0.0
        pts = [QgsPointXY(p) for p in pts]
        new_pts = list(pts)
        chain_shift = 0.0
        if extend_us > 0.0:
            p0, p1 = pts[0], pts[1]
            dx, dy = p0.x() - p1.x(), p0.y() - p1.y()
            d = math.hypot(dx, dy)
            if d > 1e-9:
                new_start = QgsPointXY(p0.x() + dx / d * extend_us, p0.y() + dy / d * extend_us)
                new_pts = [new_start] + new_pts
                chain_shift = extend_us
        if extend_ds > 0.0:
            p_last, p_prev = pts[-1], pts[-2]
            dx, dy = p_last.x() - p_prev.x(), p_last.y() - p_prev.y()
            d = math.hypot(dx, dy)
            if d > 1e-9:
                new_end = QgsPointXY(p_last.x() + dx / d * extend_ds, p_last.y() + dy / d * extend_ds)
                new_pts = new_pts + [new_end]
        return QgsGeometry.fromPolylineXY(new_pts), chain_shift

    def _embank_end_extension_dir(self, end):
        """(end_point, unit_dx, unit_dy) for sliding the 'us' or 'ds' end
        further outward — the exact same direction
        _embank_extended_ground_geom extrapolates ground sampling along
        (read off self._embank_geom, post-fillet, not the raw clicked
        points), so a dragged real endpoint or the dashed map-canvas
        Extend US/DS preview both line up with wherever the extended
        ground plot already is. None if there aren't 2 points to get a
        direction from."""
        geom = self._embank_geom
        if geom is None:
            return None
        parts = geom.asMultiPolyline() if geom.isMultipart() else None
        pts = parts[0] if parts else geom.asPolyline()
        if len(pts) < 2:
            return None
        pts = [QgsPointXY(p) for p in pts]
        p0, p1 = (pts[0], pts[1]) if end == 'us' else (pts[-1], pts[-2])
        dx, dy = p0.x() - p1.x(), p0.y() - p1.y()
        d = math.hypot(dx, dy)
        if d <= 1e-9:
            return None
        return p0, dx / d, dy / d

    def _update_embank_extend_band(self):
        """Dashed blue rubber band(s) on the map canvas showing where the
        Extend US/DS zone actually sits in space — the real alignment's
        own end(s) continued along their own existing segment direction
        by self._embank_extend_us/_ds, exactly as
        _embank_extended_ground_geom already samples ground for the
        extended vertical-profile plot. Cleared (band set to None) for
        whichever side has extend == 0."""
        for attr in ('_embank_extend_band_us', '_embank_extend_band_ds'):
            band = getattr(self, attr, None)
            if band is not None:
                try: self.canvas.scene().removeItem(band)
                except Exception: pass
            setattr(self, attr, None)
        if self._embank_geom is None:
            return
        for end, attr, extend in (('us', '_embank_extend_band_us', self._embank_extend_us),
                                   ('ds', '_embank_extend_band_ds', self._embank_extend_ds)):
            extend = max(0.0, extend)
            if extend <= 0.0:
                continue
            dirn = self._embank_end_extension_dir(end)
            if dirn is None:
                continue
            p0, ux, uy = dirn
            far = QgsPointXY(p0.x() + ux * extend, p0.y() + uy * extend)
            line = [far, p0] if end == 'us' else [p0, far]
            band = QgsRubberBand(self.canvas, _LINE_GEOM)
            band.setColor(QColor(33, 150, 243, 220))
            band.setWidth(3)
            try:
                band.setLineStyle(Qt.PenStyle.DashLine)
            except AttributeError:
                band.setLineStyle(Qt.DashLine)  # type: ignore[attr-defined]
            band.setToGeometry(QgsGeometry.fromPolylineXY(line), None)
            setattr(self, attr, band)

    def _refresh_embank_profile(self):
        if self._embank_geom is None:
            QMessageBox.warning(self, 'Advanced Profile Tool', 'Draw a channel alignment first.')
            return
        raster_entries, _ve, col_meta = self._collect_entries(
            raster_rows=self._embank_raster_rows, vector_rows=[])
        if not raster_entries:
            self._embank_ground_col = None
            self._refresh_embank_diff_combo()
            if MATPLOTLIB_AVAILABLE:
                self._reset_axes()
                self._draw_embank_vertical_decorations()
            return
        if QgsProject.instance().crs().isGeographic():
            QMessageBox.critical(self, 'Advanced Profile Tool — CRS Error',
                'Project CRS is Geographic (degrees).\n'
                'Switch to a Projected Metric CRS first.')
            return
        self.btn_run.setEnabled(False)
        self.progress.setRange(0, 0)
        self.lbl_status.setText('Refreshing…')
        QApplication.processEvents()
        sample_geom, chain_shift = self._embank_extended_ground_geom()
        try:
            profile_data, raw_chainages = self._extract(
                sample_geom, raster_entries, [], self.spin_embank_interval.value())
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool — Error', str(exc))
            self.btn_run.setEnabled(True)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.lbl_status.setText('Failed — see error dialog.')
            return
        # Re-expressed so chainage 0 is still the REAL alignment's own
        # start (and its own length is still the real end), regardless of
        # how far _embank_extended_ground_geom stretched the sampling
        # geometry — the extended portion just shows up as chainages < 0
        # or > length, exactly like real ground beyond the design would.
        chainages = [c - chain_shift for c in raw_chainages] if chain_shift else raw_chainages
        self.lbl_status.setText(f'Done — {len(chainages)} pts, {len(profile_data)} layer(s).')
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

        # First raster added is the ground reference for cut/fill shading.
        # Upstream/downstream levels auto-seed from it (chainage 0 / end)
        # the first time it's available, so the invert line starts
        # somewhere sane instead of both ends defaulting to 0 m — the user
        # can freely override either afterwards. Looked up by NEAREST
        # chainage (not just the array's first/last entry) since an
        # extended ground plot means chainage 0/length may no longer be
        # the first/last sample.
        self._embank_ground_col = raster_entries[0][1]
        self._refresh_embank_diff_combo()
        length = self._embank_geom.length()

        def _ground_near(target_ch):
            vals = profile_data.get(self._embank_ground_col, [])
            best_v, best_d = None, None
            for c, v in zip(chainages, vals):
                if v is None:
                    continue
                d = abs(c - target_ch)
                if best_d is None or d < best_d:
                    best_d, best_v = d, v
            return best_v

        if self._embank_us_level is None:
            v = _ground_near(0.0)
            if v is not None:
                self._embank_us_level = round(v, 3)
                self.spin_embank_us_level.blockSignals(True)
                self.spin_embank_us_level.setValue(self._embank_us_level)
                self.spin_embank_us_level.blockSignals(False)
        if self._embank_ds_level is None:
            v = _ground_near(length)
            if v is not None:
                self._embank_ds_level = round(v, 3)
                self.spin_embank_ds_level.blockSignals(True)
                self.spin_embank_ds_level.setValue(self._embank_ds_level)
                self.spin_embank_ds_level.blockSignals(False)

        if MATPLOTLIB_AVAILABLE:
            self._plot(chainages, profile_data, col_meta)
            self._draw_embank_vertical_decorations()
            if self.btn_save_png is not None:
                self.btn_save_png.setEnabled(True)
        self.btn_run.setEnabled(True)

    # ---- vertical alignment (channel invert) -----------------------------

    def _embank_invert_vertex_handles(self):
        """Like _embank_invert_vertices() but keeps a handle to WHAT each
        vertex actually is — ('us', None), ('break', that break's dict) or
        ('ds', None) — so a typed-in edit (slope or level text click) or a
        drag can write the new value back to the right place. None until
        both end levels are set."""
        if (self._embank_geom is None or self._embank_us_level is None
                or self._embank_ds_level is None):
            return None
        length = self._embank_geom.length()
        handles = [(0.0, self._embank_us_level, 'us', None)]
        for b in self._embank_invert_breaks:
            if 0.0 < b['chainage'] < length:
                handles.append((b['chainage'], b['level'], 'break', b))
        handles.append((length, self._embank_ds_level, 'ds', None))
        return handles

    def _embank_invert_vertices(self):
        """Sorted [(chainage, level), ...] for the whole invert polyline —
        chainage 0 at the upstream level, the end chainage at the
        downstream level, with any interior break points (PVIs) in
        between. None until both end levels are set."""
        handles = self._embank_invert_vertex_handles()
        return None if handles is None else [(h[0], h[1]) for h in handles]

    def _embank_invert_level_at(self, ch):
        """Plain linear interpolation (no numpy) so this stays usable even
        if matplotlib/numpy aren't available — in practice the break-point
        click handler that calls this can't fire without a chart anyway,
        but there's no reason to depend on numpy for a 2-point lerp."""
        verts = self._embank_invert_vertices()
        if not verts:
            return 0.0
        if ch <= verts[0][0]:
            return verts[0][1]
        for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
            if x0 <= ch <= x1:
                if x1 == x0:
                    return y1
                return y0 + (ch - x0) / (x1 - x0) * (y1 - y0)
        return verts[-1][1]

    # ---- Cross-Section (per vertical-alignment NODE) -----------------------
    #
    # Cross-sections are defined at each STATION — the US end, every break
    # point, and the DS end — not per reach. A reach between two stations
    # doesn't own a shape of its own; it's whatever eventually gets
    # interpolated between its two bounding node sections (a job for the
    # future raster-generation step, not this preview). This deliberately
    # replaced an earlier per-reach-midpoint design: with one hard-edged
    # shape per reach, two adjacent reaches met at a break point with a
    # visible discontinuity — exactly the "confusing at the junction"
    # problem the user flagged. Node keys are simple and already stable:
    # 'us', a break's own id() (mutated in place, never replaced), or 'ds'.

    @staticmethod
    def _embank_node_key(handle):
        kind, ref = handle[2], handle[3]
        return id(ref) if kind in ('break', 'control') else kind   # 'us' / <id> / 'ds'

    def _embank_node_handle(self, key):
        """The (chainage, level, kind, ref) handle for a given REAL station
        key ('us' / a break's id() / 'ds'), or None if it no longer exists
        (e.g. its break was removed). Does NOT resolve control sections —
        see _embank_any_node_handle for that."""
        if key is None:
            return None
        handles = self._embank_invert_vertex_handles()
        if handles is None:
            return None
        return next((h for h in handles if self._embank_node_key(h) == key), None)

    def _embank_control_handle(self, key):
        """Like _embank_node_handle but for a CONTROL SECTION's own id()
        — control sections aren't part of the invert's real vertex handles
        (no level/geometry of their own), so they need their own lookup,
        deriving 'level' live from the invert rather than storing one."""
        for cs in self._embank_control_sections:
            if id(cs) == key:
                ch = cs['chainage']
                return (ch, self._embank_invert_level_at(ch), 'control', cs)
        return None

    def _embank_any_node_handle(self, key):
        """A REAL station's handle, or (if not one) a control section's —
        the combined lookup used everywhere the Cross-Section editor
        needs to resolve 'whatever is currently selected', since a
        control section shares the exact same selection/editing
        machinery as a real station despite not being one."""
        handle = self._embank_node_handle(key)
        if handle is not None:
            return handle
        return self._embank_control_handle(key)

    def _embank_selected_node_position(self):
        """(chainage, level) for whichever node self._embank_selected_xs_key
        currently refers to, or None."""
        handle = self._embank_any_node_handle(self._embank_selected_xs_key)
        return None if handle is None else (handle[0], handle[1])

    @staticmethod
    def _embank_xs_half_width(params):
        """Wide enough to show the whole design shape plus a bit of
        ground context either side — recomputed from the station's own
        (independent per side) section stacks rather than a fixed/user-
        set extent, since there's no separate extent control on this
        embedded (dialog-free) preview. Sums each section's own
        horizontal run (slope x height, plus its own LEADING berm width
        for every section after the first) until it hits that side's
        flush (open-ended) section, which gets extra headroom since it
        searches outward dynamically until it meets real ground rather
        than stopping at a fixed offset. Takes whichever side runs
        wider."""
        half_bw = params['bottom_width'] / 2.0

        def _run(sections):
            r = 0.0
            for idx, sec in enumerate(sections):
                if idx > 0:
                    r += sec.get('berm_width', 0.0)
                height = sec.get('height')
                if height is None:
                    return r + 15.0
                r += sec.get('slope', 0.0) * height
            return r

        run = half_bw + max(_run(params.get('left_sections', [])), _run(params.get('right_sections', [])))
        return max(10.0, run + 5.0)

    def _embank_xs_node_list(self):
        """[(key, chainage), ...] for every station (US end, each break,
        DS end), in upstream-to-downstream order — the ordering Copy US
        Section walks to find 'the previous station'."""
        handles = self._embank_invert_vertex_handles()
        if handles is None:
            return []
        return [(self._embank_node_key(h), h[0]) for h in handles]

    def _embank_xs_all_anchor_list(self):
        """[(key, chainage), ...] combining every REAL station and every
        CONTROL SECTION, sorted by chainage — the anchor set used to
        interpolate the cross-section's whole section stack
        (_embank_xs_interp_params), so a control section can steer it
        too, not just real stations. Kept separate from
        _embank_xs_node_list, which stays REAL-station-only since that
        one still defines the actual reaches the Check Cross-Section
        window picks between."""
        anchors = list(self._embank_xs_node_list())
        anchors.extend((id(cs), cs['chainage']) for cs in self._embank_control_sections)
        anchors.sort(key=lambda a: a[1])
        return anchors

    def _embank_xs_reach_list(self):
        """[(label, ch0, ch1), ...] for each reach between two CONSECUTIVE
        stations (US->break, break->break, ..., break->DS) — what the
        Check Cross-Section window's reach picker offers, since a reach
        (not a single station) is what gets interpolated along."""
        nodes = self._embank_xs_node_list()
        reaches = []
        for i in range(len(nodes) - 1):
            (_k0, ch0), (_k1, ch1) = nodes[i], nodes[i + 1]
            reaches.append((f'Reach {i + 1}:  {ch0:.2f} m → {ch1:.2f} m', ch0, ch1))
        return reaches

    def _embank_xs_interp_params(self, ch):
        """Cross-section params at an arbitrary chainage ch, linearly
        interpolated between the two anchors (real stations AND control
        sections — see _embank_xs_all_anchor_list) bounding it — the same
        _embank_xs_build_shape() is then reused as-is on the result, so
        an interpolated chainage's shape is built exactly like a real
        station's. 'bottom_width' (Crest width) blends continuously; each
        side's own section stack blends independently via
        _embank_xs_blend_sections, which itself falls back to nearest-
        anchor-wins wholesale when that side's two anchors' stacks don't
        structurally match (different length) — matching a reach's two
        ends structurally (e.g. by placing a Control Section right where
        a stack needs to change shape) is the user's own responsibility,
        same as every other per-reach choice in this tool.

        Every return path goes through _embank_xs_copy_params rather than
        a plain dict(...)/dict(self._embank_xs_default), since
        'left_sections'/'right_sections' are themselves lists of dicts —
        a shallow copy would hand back a result that shares those SAME
        lists by reference with whatever station (or the global default)
        it was copied from, so editing the result's stack would silently
        mutate that other station's (or everyone's default) too."""
        nodes = self._embank_xs_all_anchor_list()
        if not nodes:
            return self._embank_xs_default_params()
        if ch <= nodes[0][1]:
            return self._embank_xs_copy_params(self._embank_xs_params.get(nodes[0][0], self._embank_xs_default))
        if ch >= nodes[-1][1]:
            return self._embank_xs_copy_params(self._embank_xs_params.get(nodes[-1][0], self._embank_xs_default))
        for i in range(len(nodes) - 1):
            k0, ch0 = nodes[i]
            k1, ch1 = nodes[i + 1]
            if ch0 <= ch <= ch1:
                p0 = self._embank_xs_params.get(k0, self._embank_xs_default)
                p1 = self._embank_xs_params.get(k1, self._embank_xs_default)
                t = 0.0 if ch1 == ch0 else (ch - ch0) / (ch1 - ch0)
                bw_default = self._embank_xs_default['bottom_width']
                out = {
                    'bottom_width': p0.get('bottom_width', bw_default)
                                     + t * (p1.get('bottom_width', bw_default) - p0.get('bottom_width', bw_default)),
                }
                for side in ('left_sections', 'right_sections'):
                    s0 = p0.get(side) or self._embank_xs_default[side]
                    s1 = p1.get(side) or self._embank_xs_default[side]
                    out[side] = self._embank_xs_blend_sections(s0, s1, t)
                return out
        return self._embank_xs_default_params()

    def _embank_xs_default_params(self):
        """A fresh, independent copy of _embank_xs_default — see
        _embank_xs_interp_params for why a plain dict(...) isn't safe
        here."""
        d = self._embank_xs_default
        return {'bottom_width': d['bottom_width'],
                'left_sections':  [dict(s) for s in d['left_sections']],
                'right_sections': [dict(s) for s in d['right_sections']]}

    def _embank_xs_copy_params(self, params):
        """Deep-enough copy of a params dict for assigning to a DIFFERENT
        station (Copy to All/Copy to All DS Section, or seeding a new
        break/control section from the interpolated value at its own
        chainage) — see _embank_xs_interp_params for why the section
        lists can't just be shallow-copied."""
        d = self._embank_xs_default
        return {'bottom_width': params.get('bottom_width', d['bottom_width']),
                'left_sections':  [dict(s) for s in (params.get('left_sections')  or d['left_sections'])],
                'right_sections': [dict(s) for s in (params.get('right_sections') or d['right_sections'])]}

    @staticmethod
    def _embank_xs_blend_sections(s0, s1, t):
        """Linearly blends two section stacks' own numeric fields
        (slope/height/berm_width) index-by-index — used to interpolate
        one SIDE's Cross-Section along a reach between two stations.
        Only meaningful when both stacks have the SAME length; otherwise
        this falls back to whichever end is NEAREST wholesale (a fresh
        copy, never the original list), the same 'nearest-anchor-wins'
        convention already used for every other categorical choice in
        this tool. A section whose height is None (flush) on EITHER side
        blends to None (flush wins) — the two candidates disagree on
        where the stack even ends at that index, so there's no sensible
        numeric average to take."""
        if len(s0) != len(s1):
            return [dict(s) for s in (s0 if t < 0.5 else s1)]
        out = []
        for a, b in zip(s0, s1):
            sec = {}
            sec['slope'] = a.get('slope', 0.0) + t * (b.get('slope', 0.0) - a.get('slope', 0.0))
            sec['berm_width'] = a.get('berm_width', 0.0) + t * (b.get('berm_width', 0.0) - a.get('berm_width', 0.0))
            ha, hb = a.get('height'), b.get('height')
            sec['height'] = None if (ha is None or hb is None) else ha + t * (hb - ha)
            out.append(sec)
        return out

    def _on_embank_xs_copy_ds_all(self):
        """Pushes the currently selected station's cross-section onto
        every station DOWNSTREAM of it (real stations AND control
        sections, via the same chainage-sorted anchor list Copy to All
        uses) — everything upstream, including the selected section
        itself, is left untouched. Replaces the old 'Copy US Section'
        (which pulled FROM the upstream neighbour) per explicit request —
        this one pushes forward instead of pulling backward."""
        key = self._embank_selected_xs_key
        if key is None:
            return
        nodes = self._embank_xs_all_anchor_list()
        idx = next((i for i, (k, _ch) in enumerate(nodes) if k == key), None)
        if idx is None:
            return
        ds_keys = [k for k, _ch in nodes[idx + 1:]]
        if not ds_keys:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'This is already the most downstream station — there is\n'
                'nothing further downstream to copy to.')
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f'Copy this station\'s cross-section to all {len(ds_keys)} '
            f'station(s)\ndownstream of it (including control sections)?\n'
            'Everything upstream, including this section itself, is left\n'
            'unchanged. This overwrites their current settings.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = self._embank_xs_params.get(key, self._embank_xs_default)
        for k in ds_keys:
            self._embank_xs_params[k] = self._embank_xs_copy_params(params)
        self.lbl_embank_xs_status.setText(
            f'Copied this station to {len(ds_keys)} downstream station(s).')
        self.lbl_embank_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')
        self._refresh_embank_xs()

    def _on_embank_xs_copy_all(self):
        key = self._embank_selected_xs_key
        if key is None:
            return
        nodes = self._embank_xs_all_anchor_list()
        other_keys = [k for k, _ch in nodes if k != key]
        if not other_keys:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f'Copy this station\'s cross-section to all {len(other_keys)} other '
            f'station(s) on this alignment?\nThis overwrites their current settings.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = self._embank_xs_params.get(key, self._embank_xs_default)
        for k in other_keys:
            self._embank_xs_params[k] = self._embank_xs_copy_params(params)
        self.lbl_embank_xs_status.setText(f'Copied this station to {len(other_keys)} other station(s).')
        self.lbl_embank_xs_status.setStyleSheet('color:#2E7D32;font-style:italic;font-size:11px;')
        self._refresh_embank_xs()

    def _select_embank_xs_node(self, key):
        handle = self._embank_any_node_handle(key)
        if handle is None:
            return
        ch, level, kind, _ref = handle
        self._embank_selected_xs_key = key
        if key not in self._embank_xs_params:
            self._embank_xs_params[key] = self._embank_xs_default_params()
        self.btn_embank_xs_copy_ds.setEnabled(True)
        self.btn_embank_xs_copy_all.setEnabled(True)
        self._embank_xs_side_ui['left']['sel_idx'] = None
        self._embank_xs_side_ui['right']['sel_idx'] = None
        self._rebuild_embank_section_rows()
        label = {'us': 'Upstream end', 'ds': 'Downstream end',
                 'control': 'Control section'}.get(kind, 'Break point')
        self.lbl_embank_xs_status.setText(
            f'{label} at chainage {ch:.2f} m (level {level:.3f} m) selected.')
        self.lbl_embank_xs_status.setStyleSheet('color:#6A1B9A;font-style:italic;font-size:11px;')
        # _refresh_embank_xs() runs automatically as part of this, now
        # that self._embank_selected_xs_key is set — see the end of
        # _draw_embank_vertical_decorations.
        self._draw_embank_vertical_decorations()

    def _on_embank_crest_width_changed(self, value, source_side):
        """Crest width is shared (one number, shown in BOTH tabs' Section
        1 row, since it's where both sides' own first section starts) —
        syncs the OTHER tab's own crest-width spin directly rather than
        a full rows rebuild, which would steal keyboard focus from the
        spin the user is actively typing/dragging in."""
        key = self._embank_selected_xs_key
        if key is None:
            return
        self._embank_xs_params.setdefault(key, self._embank_xs_default_params())['bottom_width'] = value
        other = 'right' if source_side == 'left' else 'left'
        other_spin = self._embank_xs_side_ui[other].get('crest_spin')
        if other_spin is not None:
            other_spin.blockSignals(True)
            other_spin.setValue(value)
            other_spin.blockSignals(False)
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _on_embank_section_field_changed(self, sec, field, value):
        """A single numeric field (slope/berm_width/height) on an
        existing section row changed — mutates that section's own dict
        IN PLACE (sec is the actual dict living inside the selected
        station's own section list, captured directly by the row
        widget's closure), so no rebuild of the rows is needed, just a
        redraw."""
        sec[field] = value
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _embank_section_action_target(self, side):
        """(sections, idx) for the currently selected row on this side,
        or (None, None) if nothing's selected / no station is active —
        shared by every action handler below so they all agree on what
        'the selected row' means."""
        key = self._embank_selected_xs_key
        if key is None:
            return None, None
        sections = self._embank_xs_params[key][f'{side}_sections']
        idx = self._embank_xs_side_ui[side]['sel_idx']
        return sections, idx

    def _on_embank_section_row_clicked(self, side, idx):
        ui = self._embank_xs_side_ui[side]
        if ui['sel_idx'] == idx:
            return
        ui['sel_idx'] = idx
        self._rebuild_embank_section_rows_side(side)

    def _on_embank_section_flush_side(self, side):
        """'Flush with Ground' on the selected row — only acts when that
        row is actually the LAST one (the action area only enables this
        button in that case, but the handler checks again regardless,
        since it's cheap and keeps this safe to call from anywhere).
        That section now runs at its own slope until it meets existing
        ground."""
        sections, idx = self._embank_section_action_target(side)
        if sections is None or idx is None or idx != len(sections) - 1:
            return
        sections[idx]['height'] = None
        self._rebuild_embank_section_rows_side(side)
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _on_embank_section_add_side(self, side):
        """'Add Section' below the selected (LAST) row — gives that
        section a real height first if it was still flush (a flush
        section runs open-endedly to ground, so something has to end
        before another section can start), then appends the new one,
        itself flush by default, and selects it."""
        sections, idx = self._embank_section_action_target(side)
        if sections is None or idx is None or idx != len(sections) - 1:
            return
        if sections[idx].get('height') is None:
            sections[idx]['height'] = 2.0
        sections.append({'slope': 3.0, 'berm_width': 0.0, 'height': None})
        self._embank_xs_side_ui[side]['sel_idx'] = len(sections) - 1
        self._rebuild_embank_section_rows_side(side)
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _on_embank_section_remove_side(self, side):
        """Removes the selected (LAST) row — undoes the most recent Add
        Section — re-flushing whatever is now the new last section,
        since there must always be exactly one flush (open-ended)
        section terminating the stack. Section 1 can never be removed
        this way (there's nothing to fall back to)."""
        sections, idx = self._embank_section_action_target(side)
        if sections is None or idx is None or idx != len(sections) - 1 or len(sections) <= 1:
            return
        sections.pop()
        sections[-1]['height'] = None
        self._embank_xs_side_ui[side]['sel_idx'] = len(sections) - 1
        self._rebuild_embank_section_rows_side(side)
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _on_embank_copy_to_other_half(self, side):
        """Overwrites the OTHER side's whole section stack with this
        side's own — the quick way to start the other half from a
        matching shape before tweaking it, per explicit request."""
        key = self._embank_selected_xs_key
        if key is None:
            return
        other = 'right' if side == 'left' else 'left'
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            f"Copy this station's {side} half to the {other} half?\n"
            f"This overwrites the {other} half's current section stack.",
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        params = self._embank_xs_params[key]
        params[f'{other}_sections'] = [dict(s) for s in params[f'{side}_sections']]
        self._embank_xs_side_ui[other]['sel_idx'] = None
        self._rebuild_embank_section_rows_side(other)
        self._refresh_embank_xs()
        self._draw_embank_vertical_decorations()

    def _build_embank_xs_side_tab(self, side):
        """Builds one side's ('left'/'right') whole mini-tab: a
        scrollable stack of section rows (capped height, so a long stack
        never pushes the Cross-Section Preview chart below out of view),
        a shared action area that reacts to whichever row is currently
        selected (see _on_embank_section_row_clicked), and a Copy to
        Other Half button."""
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Each row's own sizeHint is 44px + 5px inter-row spacing — a
        # MAXIMUM alone doesn't stop this scroll area being squeezed well
        # below that by its siblings (the Cross-Section Preview chart
        # below competing for the same limited dock height), since a
        # QScrollArea's own minimum sizeHint is tiny; a MINIMUM sized for
        # 4 rows (4*44 + 3*5 = 191, rounded up) is what actually keeps at
        # least 4 visible without scrolling, per explicit request.
        scroll.setMinimumHeight(200)
        scroll.setMaximumHeight(360)   # allows a 5th/6th row to grow the box before scrolling
        scroll.setFrameShape(_NO_FRAME)
        rows_container = QWidget()
        rows_vbox = QVBoxLayout(rows_container)
        rows_vbox.setContentsMargins(0, 0, 0, 0)
        rows_vbox.setSpacing(5)
        rows_vbox.addStretch()   # keeps rows top-aligned as the list grows/shrinks
        scroll.setWidget(rows_container)
        v.addWidget(scroll)

        # One single row — Flush with Ground, Add Section, Remove, Copy to
        # [Other] Section, in that exact order — reacting to whichever row
        # is currently selected (see _on_embank_section_row_clicked); no
        # separate status text, the selected row's own highlight colour
        # (and these buttons' own enabled state) already says everything
        # that needs saying.
        action_row = FlowLayout(h_spacing=6, v_spacing=4)
        btn_flush = QPushButton('Flush with Ground')
        btn_flush.setToolTip(
            'Selected (last) section runs at its own slope until it\n'
            'meets existing ground — nothing below it.')
        btn_flush.clicked.connect(lambda: self._on_embank_section_flush_side(side))
        action_row.addWidget(btn_flush)
        btn_add = QPushButton('Add Section')
        btn_add.setToolTip('Adds another section below the selected (last) one.')
        btn_add.clicked.connect(lambda: self._on_embank_section_add_side(side))
        action_row.addWidget(btn_add)
        btn_remove = QPushButton('Remove')
        btn_remove.setToolTip(
            'Removes the selected (last) section, reverting the one\n'
            'before it back to Flush with Ground (undoes the last Add\n'
            'Section).')
        btn_remove.clicked.connect(lambda: self._on_embank_section_remove_side(side))
        action_row.addWidget(btn_remove)

        other_label = 'Right' if side == 'left' else 'Left'
        btn_copy = QPushButton(f'Copy to {other_label} Section')
        btn_copy.setToolTip(
            f"Copies this side's whole section stack to the {other_label.lower()}\n"
            'side, overwriting it (asks to confirm first). Each side is\n'
            "edited independently — they don't have to look alike; this\n"
            'is just a shortcut to start one from the other.')
        btn_copy.clicked.connect(lambda: self._on_embank_copy_to_other_half(side))
        action_row.addWidget(btn_copy)
        v.addLayout(action_row)

        self._embank_xs_side_ui[side] = {
            'rows_vbox': rows_vbox, 'sel_idx': None, 'crest_spin': None,
            'btn_flush': btn_flush, 'btn_add': btn_add, 'btn_remove': btn_remove,
        }
        return tab

    @staticmethod
    def _embank_flow_pair(label_text, widget):
        """A label glued tightly to its own field, as ONE widget — same
        convention as _build_embank_tab's own local _flow_pair, pulled
        out as a real method since the dynamic section rows are built
        outside that function's scope."""
        pair = QWidget()
        hb = QHBoxLayout(pair)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(4)
        hb.addWidget(QLabel(label_text))
        hb.addWidget(widget)
        return pair

    def _rebuild_embank_section_rows(self):
        """Rebuilds BOTH sides' dynamic section-stack rows — called on
        selection change; each side's own structural actions only ever
        rebuild that one side (_rebuild_embank_section_rows_side)."""
        self._rebuild_embank_section_rows_side('left')
        self._rebuild_embank_section_rows_side('right')

    def _rebuild_embank_section_rows_side(self, side):
        """Rebuilds one side's rows from scratch, reading
        self._embank_xs_params[selected key][f'{side}_sections'] —
        called on selection change AND after any row action that
        changes that side's stack length (Add Section/Flush with
        Ground/Remove), since those can't just update an existing
        widget's value the way a plain field edit can. Fresh widgets
        every time (not a persistent pool kept in sync with a list
        whose length changes) — far simpler, at the cost of losing
        focus/cursor position on every STRUCTURAL edit (never on a plain
        value edit, which doesn't call this)."""
        ui = self._embank_xs_side_ui[side]
        vbox = ui['rows_vbox']
        while vbox.count() > 1:   # leave the trailing stretch in place
            item = vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        key = self._embank_selected_xs_key
        if key is None:
            ui['sel_idx'] = None
            ui['crest_spin'] = None
            self._update_embank_section_action_area(side)
            return
        params = self._embank_xs_params.setdefault(key, self._embank_xs_default_params())
        side_field = f'{side}_sections'
        sections = params.setdefault(side_field, [dict(s) for s in self._embank_xs_default[side_field]])

        sel_idx = ui['sel_idx']
        if sel_idx is None or sel_idx >= len(sections):
            sel_idx = len(sections) - 1
        ui['sel_idx'] = sel_idx

        for idx in range(len(sections)):
            row = self._build_embank_section_row(side, params, sections, idx, selected=(idx == sel_idx))
            vbox.insertWidget(idx, row)
        self._update_embank_section_action_area(side)

    def _update_embank_section_action_area(self, side):
        """Enables/disables the shared Flush with Ground / Add Section /
        Remove buttons for whichever row is selected on this side — only
        the LAST row in the stack can be flushed, extended or removed;
        an earlier (already 'closed') row is select-to-inspect only. No
        separate status text — the selected row's own highlight colour,
        plus these buttons simply being enabled or not, already says
        everything that needs saying."""
        ui = self._embank_xs_side_ui[side]
        key = self._embank_selected_xs_key
        if key is None:
            ui['btn_flush'].setEnabled(False)
            ui['btn_add'].setEnabled(False)
            ui['btn_remove'].setEnabled(False)
            return
        sections = self._embank_xs_params[key][f'{side}_sections']
        sel_idx = ui['sel_idx']
        is_last = sel_idx is not None and sel_idx == len(sections) - 1
        ui['btn_flush'].setEnabled(is_last)
        ui['btn_add'].setEnabled(is_last)
        ui['btn_remove'].setEnabled(is_last and len(sections) > 1)

    def _build_embank_section_row(self, side, params, sections, idx, selected):
        """One section's own single-line editable row: section number,
        (Crest Width on Section 1 only — it's where this side's own
        first section starts), (Berm, on every section AFTER the first —
        its own LEADING flat bench, since 'the section always ends at
        the start of the berm, so the berm belongs to the section after
        it'), Slope, and Height (or a '-> ground' note while this
        section is still the flush/open-ended one). The WHOLE row is
        clickable (see _on_embank_section_row_clicked) to select it —
        selecting drives the shared action area below the list, since
        only the actual LAST row can be flushed/extended/removed."""
        sec = sections[idx]
        is_last = (idx == len(sections) - 1)
        row = QWidget()
        row.setStyleSheet(
            'QWidget{background:%s;border-radius:5px;}' % ('#C8E0F9' if selected else '#EFEFEF'))
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(10)

        hdr = QLabel(f'Section {idx + 1}')
        hdr.setStyleSheet('font-weight:bold;font-size:11px;color:#455A64;')
        hdr.setFixedWidth(58)
        h.addWidget(hdr)

        def _mk_spin(minimum, maximum, value, suffix, slot):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(2)
            spin.setSuffix(suffix)
            spin.setFixedWidth(88)
            spin.setMinimumHeight(28)
            spin.setValue(value)
            spin.wheelEvent = lambda e: e.ignore()
            spin.valueChanged.connect(slot)
            return spin

        if idx == 0:
            crest_spin = _mk_spin(
                0.0, 500.0, params['bottom_width'], ' m',
                lambda v: self._on_embank_crest_width_changed(v, side))
            h.addWidget(self._embank_flow_pair('Crest Width:', crest_spin))
            self._embank_xs_side_ui[side]['crest_spin'] = crest_spin
        else:
            berm_spin = _mk_spin(
                0.0, 200.0, sec.get('berm_width', 0.0), ' m',
                lambda v: self._on_embank_section_field_changed(sec, 'berm_width', v))
            h.addWidget(self._embank_flow_pair('Berm:', berm_spin))

        slope_spin = _mk_spin(
            0.0, 20.0, sec.get('slope', 3.0), ' :1',
            lambda v: self._on_embank_section_field_changed(sec, 'slope', v))
        h.addWidget(self._embank_flow_pair('Slope:', slope_spin))

        height = sec.get('height')
        if height is None:
            flush_lbl = QLabel('→ ground')
            flush_lbl.setStyleSheet('color:gray;font-style:italic;font-size:10px;')
            h.addWidget(flush_lbl)
        else:
            height_spin = _mk_spin(
                0.01, 200.0, height, ' m',
                lambda v: self._on_embank_section_field_changed(sec, 'height', v))
            h.addWidget(self._embank_flow_pair('Height:', height_spin))

        h.addStretch()
        row.mousePressEvent = lambda e, s=side, i=idx: self._on_embank_section_row_clicked(s, i)
        return row

    def _embank_xs_sample_ground(self, ch, params, direction=None, scale=1.0, align_geom=None):
        """Ground raster sample at an arbitrary chainage (not necessarily
        a station) — same perpendicular-line-then-extract pipeline as
        below, generalised so the Check Cross-Section reach-interpolation
        window can resample at every step chainage, not just a selected
        station's. None if there's no raster data or the sample fails.

        direction/scale, if given, are for a MITER station at a sharp
        Modify Embank corner (see _embank_vertex_miter_bisector):
        direction overrides the sampling line's own orientation (the
        corner's bisector instead of the normal per-leg tangent), and
        scale is the miter amplification factor applied to that
        station's own design offsets. The sampling line itself is
        extended by `scale` so it reaches as far out as the FINAL
        (scaled) design edges will — but the returned 'xs' values are
        then divided back down by `scale`, staying in the same nominal/
        un-scaled coordinate space _embank_xs_build_interpolated_shape
        already builds the (not-yet-scaled) shape in. Without this
        rescale, a ground value read at nominal offset x would actually
        describe the ground at x (unscaled), not at the design's own
        FINAL position x * scale once the whole shape is scaled up
        afterward — i.e. the 'Flush with Ground' search would be
        comparing the design against the wrong point on the ground.

        align_geom, if given, is passed straight through to
        _make_perp_line_geom (see its own docstring) — used by the End
        Adjustment closure to sample ground past the real alignment's
        own start/end."""
        half_extent = self._embank_xs_half_width(params)
        raster_entries, _ve, col_meta = self._collect_entries(
            raster_rows=self._embank_raster_rows, vector_rows=[])
        if not raster_entries:
            return None
        scaled_extent = half_extent * scale
        geom = self._make_perp_line_geom(ch, scaled_extent, scaled_extent,
                                           direction=direction, align_geom=align_geom)
        if geom is None:
            return None
        try:
            data, chainages = self._extract(
                geom, raster_entries, [], self.spin_embank_interval.value())
        except Exception:
            return None
        xs = [(c - scaled_extent) / scale for c in chainages]
        return {'xs': xs, 'data': data, 'col_meta': col_meta}

    def _embank_xs_sample_compare_layer(self, ch, half_extent, layer):
        """Ground-style sample of an ARBITRARY raster layer (e.g. a just-
        generated channel-design TIFF) along the perpendicular line at
        chainage ch — same perpendicular-line-then-extract pipeline as
        _embank_xs_sample_ground, generalised to any single layer rather
        than the configured ground raster rows, for the Cross-Section
        Preview's 'Add' comparison overlay. None if there's no layer, no
        alignment, or the sample fails."""
        if layer is None:
            return None
        geom = self._make_perp_line_geom(ch, half_extent, half_extent)
        if geom is None:
            return None
        try:
            data, chainages = self._extract(
                geom, [(layer, 'compare')], [], self.spin_embank_interval.value())
        except Exception:
            return None
        vals = data.get('compare')
        if vals is None:
            return None
        xs = [c - half_extent for c in chainages]
        return {'xs': xs, 'values': vals}

    def _refresh_embank_xs(self):
        """Re-samples the ground raster at whichever STATION (node) is
        selected — same perpendicular-line-then-extract pipeline as the
        Layer Profile tab's own Cross-Section (XSectionDialog): build a
        perpendicular line at the node's own chainage via the already-
        generic _make_perp_line_geom (it reads self.profile_geom, which
        already aliases self._embank_geom), then run it through the same
        _extract() every other tab uses. Extent is sized to bracket the
        current design shape rather than a fixed/user-set value, since
        this embedded preview has no separate extent control. Called on
        both selection change AND every shape-parameter edit, since either
        can change how much ground context is needed either side."""
        self._embank_xs_ground = None
        self._embank_xs_compare = None
        pos = self._embank_selected_node_position()
        if pos is not None:
            ch, _level = pos
            key = self._embank_selected_xs_key
            params = self._embank_xs_params.get(key, self._embank_xs_default)
            self._embank_xs_ground = self._embank_xs_sample_ground(ch, params)
            if self._embank_xs_compare_layer is not None:
                half_extent = self._embank_xs_half_width(params)
                self._embank_xs_compare = self._embank_xs_sample_compare_layer(
                    ch, half_extent, self._embank_xs_compare_layer)
        self._draw_embank_xs_preview()

    def _embank_xs_ground_value_at(self, x, ground):
        """Ground elevation at offset x from centreline, linear-
        interpolated over the given sampled cross-section raster data
        dict (an explicit param rather than always self._embank_xs_ground
        so this also works for a chainage sampled on the fly, e.g. by the
        Check Cross-Section reach-interpolation window). Uses the FIRST/
        primary raster column — same one the vertical alignment's own
        cut/fill hatch uses. None if there's no ground data, x falls
        outside what was sampled, or the bracketing samples are nodata."""
        col = self._embank_ground_col
        if ground is None or col is None or col not in ground['data']:
            return None
        xs = ground['xs']
        ys = ground['data'][col]
        if not xs:
            return None
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                y0, y1 = ys[i], ys[i + 1]
                if y0 is None or y1 is None:
                    return None
                if xs[i + 1] == xs[i]:
                    return y0
                t = (x - xs[i]) / (xs[i + 1] - xs[i])
                return y0 + t * (y1 - y0)
        return None

    def _embank_xs_find_ground_intersection(self, x_start, y_start, run_per_rise,
                                              direction, ground, max_search_height=30.0):
        """Walk a slope line starting at (x_start, y_start), moving outward
        (direction=-1 left / +1 right) at run_per_rise:1 (H:V), until it
        meets the existing ground profile — going UP if the start point is
        below ground (the normal 'wall rising up to meet ground' case) or
        DOWN if the start point is already at/above ground (e.g. a berm at
        a fixed channel height that ended up higher than the surrounding
        land — its outer slope has to come back DOWN to meet it, not keep
        rising). Returns a LIST of (x, y) points to extend the design
        polyline with — almost always just one (the real crossing point),
        but see below for when it's more.

        Falls back to a fixed nominal rise if there's no ground data at
        all. In the 'climbing to meet ground from below' direction (the
        only one with a real no-cut risk — see the sign < 0 branch below
        for why the other direction doesn't need any of this), the WHOLE
        search range is walked first rather than stopping at the first
        point the nominal slope line rises above ground: real ground can
        curve (e.g. gentle near the start, steepening further out), so the
        nominal line can cross above it briefly and then be overtaken
        again later — stopping at that first, premature crossing and
        drawing one straight chord onward from it is exactly what cut
        back into the real (still-rising) ground in testing. Instead, the
        LAST point still below ground is found first, everything up to
        and including it is traced directly onto ground (hugging however
        it curves — including any brief above-ground stretch in between,
        left as the nominal line there rather than dragged down to
        ground), and only the point right after that final dip — now
        genuinely clear of ground for the rest of the search — is used as
        the hand-off back to a straight line. `ground` is an explicit
        param (not always self._embank_xs_ground) so this also works for
        a chainage sampled on the fly by the Check Cross-Section reach-
        interpolation window."""
        if ground is None or not ground['xs']:
            h = min(max_search_height, _TERRAIN_XS_OUTER_RISE)
            return [(x_start + direction * run_per_rise * h, y_start + h)]

        xs = ground['xs']
        x_lo, x_hi = xs[0], xs[-1]
        if x_start < x_lo or x_start > x_hi:
            return [(x_start, y_start)]
        gy0 = self._embank_xs_ground_value_at(x_start, ground)
        if gy0 is None:
            return [(x_start, y_start)]
        # +1 = rise to meet ground from below; -1 = fall to meet it from above
        sign = 1.0 if y_start < gy0 else -1.0

        x_step = max(0.05, (x_hi - x_lo) / 500.0)
        dh = (x_step / run_per_rise) if run_per_rise > 1e-6 else 0.05

        # Walk the whole range up front, recording every step's nominal
        # (unclamped) point and its ground-relative diff.
        steps = [(x_start, y_start, y_start - gy0)]
        h = 0.0
        while h <= max_search_height:
            h += dh
            x = x_start + direction * run_per_rise * h
            y = y_start + sign * h
            if x < x_lo or x > x_hi:
                break
            gy = self._embank_xs_ground_value_at(x, ground)
            if gy is None:
                break
            steps.append((x, y, y - gy))

        if sign < 0:
            # Coming DOWN to meet ground from above — not converging just
            # leaves the design a little above ground, never cutting into
            # it, so the plain first-crossing result is already safe.
            prev_x, prev_y, prev_diff = steps[0]
            for x, y, diff in steps[1:]:
                if prev_diff > 0.0 >= diff:
                    span = diff - prev_diff
                    t = (-prev_diff / span) if abs(span) > 1e-9 else 0.0
                    return [(prev_x + t * (x - prev_x), prev_y + t * (y - prev_y))]
                prev_x, prev_y, prev_diff = x, y, diff
            return [(prev_x, prev_y)]

        # sign > 0 — find the LAST step still below ground.
        last_below = None
        for i, (_x, _y, diff) in enumerate(steps):
            if diff < 0.0:
                last_below = i
        if last_below is None:
            # Never actually below ground (shouldn't normally happen,
            # since sign > 0 was decided from y_start < gy0) — nothing to
            # clamp.
            x, y, _d = steps[-1]
            return [(x, y)]

        pts = []
        for x, y, diff in steps[:last_below + 1]:
            if diff < 0.0:
                gy = self._embank_xs_ground_value_at(x, ground)
                pts.append((x, gy if gy is not None else y))
            else:
                pts.append((x, y))
        if last_below + 1 < len(steps):
            x0, y0, d0 = steps[last_below]
            x1, y1, d1 = steps[last_below + 1]
            span = d1 - d0
            t = (-d0 / span) if abs(span) > 1e-9 else 0.0
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
        return pts

    def _embank_xs_section_endpoint(self, x_start, y_start, run, direction, ground, height):
        """Where ONE section's own slope actually ends: its own nominal
        point after dropping the full `height` — OR, if real ground is
        reached SOONER, the point where the slope first meets it. An
        embankment is never allowed to dip below existing ground (no
        cut), so if ground is already higher than where this section
        would otherwise end, the design simply flushes to it right
        there instead of continuing on to a berm/further section that
        isn't actually needed — 'the additional sections and berm only
        apply when the ground is below', per explicit request. Returns
        (x, y, hit_ground) — hit_ground tells the caller (see
        _embank_xs_build_side_sections) to stop the whole stack here
        rather than build anything further down it."""
        x_nominal = x_start + direction * run * height
        y_nominal = y_start - height
        if ground is None or not ground['xs']:
            return x_nominal, y_nominal, False
        gy0 = self._embank_xs_ground_value_at(x_start, ground)
        if gy0 is None:
            return x_nominal, y_nominal, False
        if y_start <= gy0:
            # Already at/below ground right at the start of this section.
            return x_start, y_start, True
        xs = ground['xs']
        x_step = max(0.05, (xs[-1] - xs[0]) / 500.0)
        dh = (x_step / run) if run > 1e-6 else 0.05
        h = 0.0
        prev_x, prev_y, prev_diff = x_start, y_start, y_start - gy0
        while h < height:
            h = min(h + dh, height)
            x = x_start + direction * run * h
            y = y_start - h
            gy = self._embank_xs_ground_value_at(x, ground)
            if gy is None:
                return x_nominal, y_nominal, False
            diff = y - gy
            if prev_diff > 0.0 >= diff:
                span = diff - prev_diff
                t = (-prev_diff / span) if abs(span) > 1e-9 else 0.0
                return prev_x + t * (x - prev_x), prev_y + t * (y - prev_y), True
            prev_x, prev_y, prev_diff = x, y, diff
        return x_nominal, y_nominal, False

    def _embank_xs_build_side_sections(self, direction, invert_level, half_bw, sections, ground):
        """Builds one side's design polyline by walking an ordered STACK
        of sections outward/downward from the crest-edge vertex (half the
        crest width out from centreline) — the general replacement for
        the old fixed Side-slope/Berm/Batter-slope template, letting a
        side have as many stacked sections as needed. A wall is just a
        section with slope 0 (a plain vertical face) — no separate kind,
        one walk handles every case. Each section (other than the first)
        starts with its own LEADING berm (a flat run at the previous
        section's own ending level) before its own slope begins — 'the
        section always ends at the start of the berm, so the berm is
        part of the section after it', per explicit request; the very
        first section has no berm since nothing precedes it.

        Every FIXED-height section's own slope is checked against real
        ground before it's allowed to reach its full nominal height (see
        _embank_xs_section_endpoint) — an embankment never cuts, so a
        section (and everything stacked after it: its berm, and any
        further sections) only actually gets built where the ground is
        genuinely deep enough to need it; where it's shallower, the
        design collapses back to a plain flush slope right where it
        meets the real ground, per explicit request.

        Returned left-to-right ready (near-centre first, outward last)
        for the LEFT side's own natural build order; _embank_xs_build_
        shape reverses it to get the final left-to-right polyline."""
        pts = [(direction * half_bw, invert_level)]
        x, y = direction * half_bw, invert_level
        for idx, sec in enumerate(sections):
            if idx > 0:
                berm_w = sec.get('berm_width', 0.0)
                if berm_w:
                    x = x + direction * berm_w
                    pts.append((x, y))
            run = sec.get('slope', 0.0)
            height = sec.get('height')
            if height is None:
                new_pts = self._embank_xs_find_ground_intersection(x, y, run, direction, ground)
                pts.extend(new_pts)
                x, y = new_pts[-1]
                break
            x, y, hit_ground = self._embank_xs_section_endpoint(x, y, run, direction, ground, height)
            pts.append((x, y))
            if hit_ground:
                break
        return pts

    def _embank_xs_build_shape(self, invert_level, params, ground):
        """Design cross-section polyline (left-top -> ... -> right-top,
        x monotonically increasing) for a single, definite set of params
        — left_sections and right_sections are independent (each side
        edited separately in its own mini-tab). `ground` is an explicit
        param (not always self._embank_xs_ground) so the same builder
        works for an arbitrary chainage sampled on the fly."""
        half_bw = params['bottom_width'] / 2.0
        left_sections  = params.get('left_sections')  or self._embank_xs_default['left_sections']
        right_sections = params.get('right_sections') or self._embank_xs_default['right_sections']
        left_pts  = self._embank_xs_build_side_sections(-1, invert_level, half_bw, left_sections, ground)
        right_pts = self._embank_xs_build_side_sections(1, invert_level, half_bw, right_sections, ground)
        poly = list(reversed(left_pts)) + list(right_pts)
        return [p[0] for p in poly], [p[1] for p in poly]

    def _embank_xs_build_interpolated_shape(self, ch, invert_level, ground):
        """Design cross-section at an arbitrary chainage along a reach,
        for the Check Cross-Section window — interpolated params (see
        _embank_xs_interp_params, which blends the whole section stack
        numerically) fed through the normal single-shape builder. Unlike
        Channel Surface's own version, there's no separate multi-
        candidate shape-morphing step here: since a section stack blends
        at the PARAMS level (matching stacks index-by-index) rather than
        by building two candidate shapes and morphing between them, the
        blended params already describe one definite, buildable shape."""
        params = self._embank_xs_interp_params(ch)
        return self._embank_xs_build_shape(invert_level, params, ground)

    @staticmethod
    def _embank_xs_design_elev_at(poly_x, poly_y, xa):
        """Design elevation at each x in the sorted array xa, walking the
        polyline's own SEGMENTS rather than np.interp(xa, poly_x, poly_y)
        — np.interp silently assumes poly_x is strictly increasing, which
        breaks wherever a 'wall' section (slope 0) makes the design run
        perfectly vertical (two y-values at one x). At a vertical
        segment's own x this returns its TOP y (the highest point the
        design reaches there), so a cut/fill hatch built from this fills
        right up to the wall instead of stopping short of it. Returns a
        float array the same length as xa, NaN wherever no segment
        covers that x."""
        out = np.full(len(xa), np.nan)
        for i in range(len(poly_x) - 1):
            x0, x1 = poly_x[i], poly_x[i + 1]
            y0, y1 = poly_y[i], poly_y[i + 1]
            lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
            mask = (xa >= lo) & (xa <= hi)
            if not mask.any():
                continue
            if abs(x1 - x0) < 1e-9:
                vals = max(y0, y1)
            else:
                t = (xa[mask] - x0) / (x1 - x0)
                vals = y0 + t * (y1 - y0)
            cur = out[mask]
            out[mask] = np.where(np.isnan(cur), vals, np.maximum(cur, vals))
        return out

    def _embank_draw_xs_axes(self, ax, poly_x, poly_y, ground, title='Cross-Section Preview',
                                compare=None, z_factor=None):
        """Shared cross-section drawing — ground profile + cut/fill hatch
        + channel design line, in real elevation so the design shape and
        the ground raster overlay sit in the same frame (same centred-at-0
        offset convention as Layer Profile's own XSectionDialog._draw_xs).
        Cut/fill hatching matches the vertical alignment chart's own
        convention: design below ground = cut (blue), design above ground
        = fill (red). Used by both the embedded per-station preview
        (_draw_embank_xs_preview) and the popped-out Check Cross-Section
        reach-interpolation window — it takes an already-built (poly_x,
        poly_y) rather than building the shape itself, since the two
        callers build it differently (a real station's single definite
        shape vs. the reach window's US/DS-blended interpolated one).

        `compare`, if given, is an extra {'xs': [...], 'values': [...]}
        sample (see _embank_xs_sample_compare_layer) — typically a just-
        generated channel-design TIFF, sampled along this SAME
        perpendicular line — plotted as its own line so the actual
        rasterised surface can be checked directly against the design/
        ground profile it was generated from, per the 'Add' button in the
        Cross-Section Preview.

        `z_factor`, if given, overrides self._embank_xs_z_factor (the
        embedded preview's own Z factor) for just this draw — used by
        Check Cross-Section, which keeps its OWN independent Z factor per
        explicit request that the two views' vertical exaggeration not
        affect each other."""
        ax.clear()
        ax.set_title(title, fontsize=9, fontweight='bold')
        ax.grid(True, alpha=0.3)

        # Ground profile (raster) — same overlay technique as the Layer
        # Profile tab's own Cross-Section dialog, just embedded here.
        if ground is not None:
            for col, vals in ground['data'].items():
                meta = ground['col_meta'].get(col, {})
                if not meta.get('visible', True):
                    continue
                color = meta.get('color', QColor('#2196F3'))
                color_str = color.name() if hasattr(color, 'name') else color
                ys = [v if v is not None else float('nan') for v in vals]
                ax.plot(ground['xs'], ys, label=_prune_mid(col), color=color_str,
                         linewidth=meta.get('linewidth', 1.5),
                         linestyle=meta.get('linestyle', '-'),
                         alpha=meta.get('alpha', 1.0), zorder=5)

            # Cut (blue) / Fill (red) hatch between the design line and
            # ground — same colours/convention as the vertical alignment
            # chart's own cut/fill shading. Evaluated on the GROUND's own
            # dense sample grid (not just the design polygon's few
            # vertices) so the hatch boundary actually tracks the real
            # (wiggly) ground shape between vertices, not a crude
            # straight-line guess between them — but the design polygon's
            # OWN vertex x-positions (poly_x) are merged into that grid
            # too. Without this, a narrow pinch region (e.g. a small
            # berm's outer/batter slope closing in on the ground over a
            # short run) can fall entirely between two raster sample
            # points and never get a fill_between segment at all, leaving
            # a visible unfilled gap right where the hatch should taper
            # to a point. Restricted to the design shape's OWN x-range
            # (its left-top to right-top vertices) — np.interp clamps to
            # the edge value outside that range, which would otherwise
            # paint a hatch clear across the rest of the sampled ground
            # that has nothing to do with the channel at all.
            col = self._embank_ground_col
            if col and col in ground['data'] and poly_x:
                ground_vals = [v if v is not None else float('nan') for v in ground['data'][col]]
                xs_ground = np.array(ground['xs'], dtype=float)
                ga_ground = np.array(ground_vals, dtype=float)
                if xs_ground.size:
                    poly_xa = np.array(poly_x, dtype=float)
                    x_lo, x_hi = poly_xa[0], poly_xa[-1]
                    extra = poly_xa[(poly_xa >= xs_ground[0]) & (poly_xa <= xs_ground[-1])]
                    xa = np.union1d(xs_ground, extra)
                    ga = np.interp(xa, xs_ground, ga_ground)
                    # NOT np.interp(xa, poly_xa, poly_y) — np.interp requires
                    # strictly increasing x, but a 'wall' section (slope 0)
                    # makes the design polyline run perfectly VERTICAL (two
                    # y-values at the same x), which np.interp has no defined
                    # behaviour for and visibly mis-hatched (the fill stopped
                    # short of the wall instead of running right up to it).
                    # _embank_xs_design_elev_at walks the actual segments
                    # instead, so a vertical run resolves to its own TOP y at
                    # that one x — the hatch then fills right up to the wall.
                    da = self._embank_xs_design_elev_at(poly_xa, poly_y, xa)
                    in_shape = (xa >= x_lo) & (xa <= x_hi)
                    valid = in_shape & np.isfinite(ga) & np.isfinite(da)
                    ax.fill_between(xa, ga, da, where=valid & (da < ga), interpolate=True,
                                     facecolor='#4FC3F7', alpha=0.30, hatch='///',
                                     edgecolor='#4FC3F7', linewidth=0.0, zorder=2)
                    ax.fill_between(xa, ga, da, where=valid & (da > ga), interpolate=True,
                                     facecolor='#F44336', alpha=0.30, hatch='///',
                                     edgecolor='#F44336', linewidth=0.0, zorder=2)

        ax.plot(poly_x, poly_y, color='#6D4C41', linewidth=2, marker='o', markersize=3,
                 label='Embankment Design', zorder=6)

        if compare is not None and compare.get('xs') and compare.get('values') is not None:
            cvals = [v if v is not None else float('nan') for v in compare['values']]
            ax.plot(compare['xs'], cvals, color='#E53935', linewidth=1,
                     linestyle='-', alpha=0.5, label='Embankment Design TIFF', zorder=7)

        ax.axvline(x=0, color='#212121', linewidth=0.8, linestyle='-.', alpha=0.7, zorder=4)
        ax.set_xlabel('Distance from centre  [ − Left | + Right ]  [m]', fontsize=8)
        ax.set_ylabel('Elevation [masl]', fontsize=8)
        ax.tick_params(labelsize=7)
        try:
            ax.legend(fontsize=7, loc='best')
        except Exception:
            pass
        # Vertical exaggeration (Z factor) — same convention as QGIS's own
        # embankment Z factor: 1.0 means TRUE 1:1 scale (1 m on X takes the
        # same screen distance as 1 m on Y, matplotlib's own 'equal'
        # aspect), 2.0 doubles the vertical scale relative to that, etc.
        # Without this, matplotlib's independent auto-scaling of X/Y picks
        # whatever ratio fills the plot area, which for a wide-shallow
        # channel stretches the Y range out until it looks like a deep
        # narrow slot. Each cross-section VIEW keeps its own independent
        # factor (the z_factor param, when given — e.g. Check Cross-
        # Section's own self._z_factor — falls back to this embedded
        # preview's own self._embank_xs_z_factor otherwise), per explicit
        # request that changing one view's Z doesn't affect the other.
        # 'datalim' keeps this subplot's own box filling its normal space
        # — it extends the shorter axis's DATA range instead of shrinking
        # the box. Only applied to actual cross-sections (transverse to
        # the channel) — never the longitudinal/long-reach charts, which
        # are conventionally shown with vertical exaggeration since they
        # cover distances far longer than their elevation range.
        z = self._embank_xs_z_factor if z_factor is None else z_factor
        try:
            ax.set_aspect(max(0.01, z), adjustable='datalim')
        except Exception:
            pass
        # Without this, the default matplotlib margins leave too little
        # room at a small canvas height and the x-axis label/tick labels
        # get clipped at the bottom of the chart.
        try:
            ax.figure.tight_layout(pad=1.0)
        except Exception:
            pass

    def _draw_embank_xs_preview(self):
        """Embedded (not popped-out) cross-section preview, drawn at the
        selected station's own chainage — see _embank_draw_xs_axes for
        the actual drawing, shared with the Check Cross-Section window."""
        if not MATPLOTLIB_AVAILABLE or self.canvas_embank_xs is None:
            return
        ax = self.ax_embank_xs
        self._embank_xs_elev_markers = []   # wiped by ax.clear() below; drop the stale references
        key = self._embank_selected_xs_key
        if key is None:
            ax.clear()
            ax.set_title('Cross-Section Preview', fontsize=9, fontweight='bold')
            ax.grid(True, alpha=0.3)
            ax.text(0.5, 0.5, 'Click a break point (or the US/DS end) in the\n'
                              'chart above to preview its cross-section.',
                    ha='center', va='center', fontsize=9, color='gray', transform=ax.transAxes)
            ax.set_xticks([]); ax.set_yticks([])
            self.canvas_embank_xs.draw_idle()
            return

        pos = self._embank_selected_node_position()
        invert_level = pos[1] if pos is not None else 0.0
        params = self._embank_xs_params.get(key, self._embank_xs_default)
        poly_x, poly_y = self._embank_xs_build_shape(invert_level, params, self._embank_xs_ground)
        self._embank_draw_xs_axes(ax, poly_x, poly_y, self._embank_xs_ground,
                                    compare=self._embank_xs_compare)
        self.canvas_embank_xs.draw_idle()
        self._position_embank_xs_elev_btn()

    def _open_embank_check_xs(self):
        """Launch the (non-modal, popped-out) Check Cross-Section window —
        lets the user step through the interpolated shape along a reach
        between two break points at a chosen spacing, ahead of eventually
        using the same interpolation for raster/TIFF generation."""
        if self._embank_geom is None or self._embank_us_level is None or self._embank_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set up the horizontal alignment and both end levels first\n'
                '— there needs to be at least one reach to step through.')
            return
        if not self._embank_xs_reach_list():
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Need at least two stations (e.g. the US and DS ends) to\n'
                'define a reach to interpolate along.')
            return
        dlg = EmbankCheckXSDialog(self)
        self._embank_check_xs_dialogs.append(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._on_embank_check_xs_closed(d))
        dlg.show()
        dlg.raise_()
        # Shows this window's ghost marker on the main ground-profile
        # chart right away — see _draw_embank_vertical_decorations.
        self._draw_embank_vertical_decorations()

    def _on_embank_check_xs_closed(self, dlg):
        if dlg in self._embank_check_xs_dialogs:
            self._embank_check_xs_dialogs.remove(dlg)
        self._draw_embank_vertical_decorations()

    def _embank_find_or_load_raster(self, info):
        """Resolves a saved DEM reference back to a real QgsRasterLayer:
        first by matching `source` against every raster already loaded in
        the project (the common case — the same DEM is still open), else
        by loading it fresh from that same source path if it still exists
        on disk. Returns None if neither works (caller surfaces this as a
        'pick it manually' note rather than failing the whole load)."""
        source = info.get('source')
        if not source:
            return None
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer) and lyr.source() == source:
                return lyr
        if os.path.exists(source):
            name = info.get('name') or os.path.basename(source)
            lyr = QgsRasterLayer(source, name, info.get('provider') or 'gdal')
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr)
                return lyr
        return None

    def _embank_collect_save_state(self):
        """Serialises every multi-step input the Modify Embank tab
        carries — horizontal alignment, vertical alignment/breaks,
        per-station cross-section params, control sections, DEM pick —
        into one plain-dict/JSON-safe structure. Works perfectly well on a
        still-BLANK tab too (no alignment drawn yet) — every field below
        just comes out empty/None in that case — per explicit request
        that a project folder can be created and saved right at the start
        of a work session, before anything's been drawn, not only once a
        full design exists.

        Cross-section params (self._embank_xs_params) are keyed by
        id(break-or-control dict) in memory, which is only valid for this
        running process — re-keyed here to a stable 'us'/'ds'/'break:<i>'/
        'control:<i>' scheme (i = that break's/control's own index in
        self._embank_invert_breaks / self._embank_control_sections,
        lists this method and _embank_apply_save_state both walk in the
        same order) so it can be restored correctly after a reload, when
        every dict gets a brand new id()."""
        break_ids = {id(b): i for i, b in enumerate(self._embank_invert_breaks)}
        control_ids = {id(c): i for i, c in enumerate(self._embank_control_sections)}

        def _stable_key(key):
            if key == 'us' or key == 'ds':
                return key
            if key in break_ids:
                return f'break:{break_ids[key]}'
            if key in control_ids:
                return f'control:{control_ids[key]}'
            return None   # stale entry from an already-removed break/control

        xs_params_out = {}
        for key, params in self._embank_xs_params.items():
            save_key = _stable_key(key)
            if save_key is None:
                continue
            xs_params_out[save_key] = dict(params)

        dem_info = None
        if self._embank_raster_rows:
            row = self._embank_raster_rows[0]
            layer = row['layer_combo'].currentLayer()
            if layer is not None:
                dem_info = {
                    'source': layer.source(), 'provider': layer.providerType(),
                    'name': layer.name(), 'enabled': row['toggle'].isChecked(),
                    'linestyle': row['ls_combo'].currentData(),
                    'line_width': row['lw_spin'].value(),
                    'opacity': row['al_spin'].value(),
                    'color': row['color'].name(),
                }

        return {
            'version': 1,
            'crs': QgsProject.instance().crs().authid(),
            'raw_points': [[p.x(), p.y()] for p in self._embank_raw_pts],
            'vertex_radii': {str(k): v for k, v in self._embank_vertex_radii.items()},
            'default_fillet_radius': self._embank_fillet_radius,
            'major_interval': self._embank_major_interval,
            'minor_interval': self._embank_minor_interval,
            'show_major_ch': self._embank_show_major_ch,
            'show_minor_ch': self._embank_show_minor_ch,
            'sampling_interval': self.spin_embank_interval.value(),
            'extend_us': self._embank_extend_us,
            'extend_ds': self._embank_extend_ds,
            'end_adjustment': self._embank_end_adjustment,
            'us_level': self._embank_us_level,
            'ds_level': self._embank_ds_level,
            'invert_breaks': [dict(b) for b in self._embank_invert_breaks],
            'control_sections': [dict(c) for c in self._embank_control_sections],
            'xs_params': xs_params_out,
            'selected_station': _stable_key(self._embank_selected_xs_key),
            'dem_layer': dem_info,
            'show_diff': self._embank_show_diff,
            'diff_col': self.combo_embank_diff_col.currentText()
                        if hasattr(self, 'combo_embank_diff_col') else None,
        }

    def _embank_apply_save_state(self, data):
        """Restores everything _embank_collect_save_state captured,
        resetting the tab first (same full reset 'Clear' already uses) so
        nothing from whatever was open before bleeds into the loaded
        design. Returns the DEM layer's own name/source if it couldn't be
        found/loaded automatically (caller surfaces this as a note — the
        rest of the design still loads fine, it just needs that layer
        picked manually), or None if the DEM resolved cleanly or none was
        saved."""
        self._clear_embank_alignment()

        missing_dem = None
        dem_info = data.get('dem_layer')
        if dem_info and self._embank_raster_rows:
            row = self._embank_raster_rows[0]
            layer = self._embank_find_or_load_raster(dem_info)
            if layer is not None:
                row['layer_combo'].setLayer(layer)
            else:
                missing_dem = dem_info.get('name') or dem_info.get('source')
            row['toggle'].setChecked(dem_info.get('enabled', True))
            idx = row['ls_combo'].findData(dem_info.get('linestyle', '-.'))
            if idx >= 0:
                row['ls_combo'].setCurrentIndex(idx)
            row['lw_spin'].setValue(dem_info.get('line_width', 1.0))
            row['al_spin'].setValue(dem_info.get('opacity', 75))
            color = QColor(dem_info.get('color', '#000000'))
            row['color'] = color
            row['color_btn'].setStyleSheet(
                f'background-color:{color.name()};border:1px solid #888;border-radius:2px;')

        self._embank_raw_pts = [QgsPointXY(x, y) for x, y in data.get('raw_points', [])]
        self._embank_vertex_radii = {int(k): v for k, v in data.get('vertex_radii', {}).items()}
        self._embank_fillet_radius = data.get('default_fillet_radius', 0.0)

        self._embank_major_interval = data.get('major_interval', 20.0)
        self._embank_minor_interval = data.get('minor_interval', 5.0)
        self._embank_show_major_ch = data.get('show_major_ch', True)
        self._embank_show_minor_ch = data.get('show_minor_ch', True)
        if hasattr(self, 'spin_embank_major_ch'):
            self.spin_embank_major_ch.blockSignals(True)
            self.spin_embank_major_ch.setValue(self._embank_major_interval)
            self.spin_embank_major_ch.blockSignals(False)
            self.spin_embank_minor_ch.blockSignals(True)
            self.spin_embank_minor_ch.setValue(self._embank_minor_interval)
            self.spin_embank_minor_ch.blockSignals(False)
            self.chk_embank_major_ch.blockSignals(True)
            self.chk_embank_major_ch.setChecked(self._embank_show_major_ch)
            self.chk_embank_major_ch.blockSignals(False)
            self.chk_embank_minor_ch.blockSignals(True)
            self.chk_embank_minor_ch.setChecked(self._embank_show_minor_ch)
            self.chk_embank_minor_ch.blockSignals(False)
        if hasattr(self, 'spin_embank_interval'):
            self.spin_embank_interval.setValue(data.get('sampling_interval', 1.0))
        self._embank_extend_us = data.get('extend_us', 0.0)
        self._embank_extend_ds = data.get('extend_ds', 0.0)
        if hasattr(self, 'spin_embank_extend_us'):
            self.spin_embank_extend_us.blockSignals(True)
            self.spin_embank_extend_us.setValue(self._embank_extend_us)
            self.spin_embank_extend_us.blockSignals(False)
            self.spin_embank_extend_ds.blockSignals(True)
            self.spin_embank_extend_ds.setValue(self._embank_extend_ds)
            self.spin_embank_extend_ds.blockSignals(False)
        self._embank_end_adjustment = data.get('end_adjustment', False)
        if hasattr(self, 'chk_embank_end_adjustment'):
            self.chk_embank_end_adjustment.blockSignals(True)
            self.chk_embank_end_adjustment.setChecked(self._embank_end_adjustment)
            self.chk_embank_end_adjustment.blockSignals(False)

        self._embank_us_level = data.get('us_level')
        self._embank_ds_level = data.get('ds_level')
        if hasattr(self, 'spin_embank_us_level'):
            if self._embank_us_level is not None:
                self.spin_embank_us_level.blockSignals(True)
                self.spin_embank_us_level.setValue(self._embank_us_level)
                self.spin_embank_us_level.blockSignals(False)
            if self._embank_ds_level is not None:
                self.spin_embank_ds_level.blockSignals(True)
                self.spin_embank_ds_level.setValue(self._embank_ds_level)
                self.spin_embank_ds_level.blockSignals(False)

        self._embank_invert_breaks = [dict(b) for b in data.get('invert_breaks', [])]
        self._embank_control_sections = [dict(c) for c in data.get('control_sections', [])]

        def _resolve_key(save_key):
            """Inverse of _embank_collect_save_state's _stable_key — maps
            a saved 'us'/'ds'/'break:<i>'/'control:<i>' string back to a
            REAL (new-this-load) id()-based key, now that
            self._embank_invert_breaks/_embank_control_sections above
            have been rebuilt in the exact same order they were saved in.
            None if save_key is unset or no longer resolves (e.g. the
            saved file is malformed, or references an index that no
            longer exists)."""
            if save_key is None:
                return None
            if save_key in ('us', 'ds'):
                return save_key
            if save_key.startswith('break:'):
                i = int(save_key.split(':', 1)[1])
                return id(self._embank_invert_breaks[i]) if i < len(self._embank_invert_breaks) else None
            if save_key.startswith('control:'):
                i = int(save_key.split(':', 1)[1])
                return id(self._embank_control_sections[i]) if i < len(self._embank_control_sections) else None
            return None

        self._embank_xs_params = {}
        for save_key, params in data.get('xs_params', {}).items():
            key = _resolve_key(save_key)
            if key is None:
                continue
            self._embank_xs_params[key] = dict(params)

        self._embank_show_diff = data.get('show_diff', False)
        if hasattr(self, 'chk_embank_diff'):
            self.chk_embank_diff.blockSignals(True)
            self.chk_embank_diff.setChecked(self._embank_show_diff)
            self.chk_embank_diff.blockSignals(False)

        self._update_embank_fillet_ui()
        self._rebuild_embank_alignment()
        self._refresh_embank_diff_combo()
        diff_col = data.get('diff_col')
        if diff_col and hasattr(self, 'combo_embank_diff_col'):
            idx = self.combo_embank_diff_col.findText(diff_col)
            if idx >= 0:
                self.combo_embank_diff_col.setCurrentIndex(idx)

        # Re-select whichever station was selected at Save time — without
        # this the Cross-Section grid/preview stays on its empty 'click a
        # break point…' placeholder even though every station's params
        # loaded correctly, since nothing else sets
        # self._embank_selected_xs_key back. Must run AFTER
        # _rebuild_embank_alignment() above, which is what makes the
        # invert/handles resolvable to select against in the first place.
        selected_key = _resolve_key(data.get('selected_station'))
        if selected_key is not None and self._embank_any_node_handle(selected_key) is not None:
            self._select_embank_xs_node(selected_key)

        return missing_dem

    def _on_embank_project_save(self):
        """Saves the current embankment design. If a project folder is
        already open (self._embank_project_folder — set by a previous
        Save or by Open), re-saves straight into it with no prompt, so
        repeated Saves during testing are a single click. Otherwise (the
        very first Save, or any Save after Close) asks for a folder name
        under the 'Result Folder' path already used for CSV/PNG/SHP
        export (self.csv_edit), then remembers it as the open project.
        Works on a still-blank tab too, per explicit request — a project
        folder can be created and saved into right at the start of a work
        session, before anything's been drawn yet."""
        folder = self._embank_project_folder
        if not folder:
            base = self.csv_edit.text().strip() or self._default_embank_output_folder()
            default_name = 'Embankment_' + datetime.now().strftime('%Y-%m-%d_%H%M')
            name, ok = QInputDialog.getText(
                self, 'Save Embankment Design',
                f'Save under:\n{base}\n\nFolder name for this design:',
                text=default_name)
            if not ok or not name.strip():
                return
            folder = os.path.join(base, name.strip())
        try:
            os.makedirs(folder, exist_ok=True)
            state = self._embank_collect_save_state()
            with open(os.path.join(folder, 'embankment_design.json'), 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool', f'Could not save design:\n{exc}')
            return
        self._embank_project_folder = folder
        QMessageBox.information(
            self, 'Advanced Profile Tool', f'Embankment design saved to:\n{folder}')

    def _on_embank_project_open(self):
        """Opens a previously saved embankment design folder (picked under
        the same 'Result Folder' base as Save), replacing whatever is
        currently on this tab — the counterpart to _on_embank_project_
        save, meant to avoid redrawing the alignment and retyping every
        cross-section by hand on each test run. Remembers the opened
        folder so the next Save re-saves straight into it."""
        base = self.csv_edit.text().strip() or self._default_embank_output_folder()
        folder = QFileDialog.getExistingDirectory(self, 'Open Embankment Design Folder', base)
        if not folder:
            return
        path = os.path.join(folder, 'embankment_design.json')
        if not os.path.isfile(path):
            QMessageBox.critical(
                self, 'Advanced Profile Tool',
                f"No 'embankment_design.json' found in:\n{folder}")
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool', f'Could not read design:\n{exc}')
            return
        cur_crs = QgsProject.instance().crs().authid()
        saved_crs = state.get('crs')
        if saved_crs and saved_crs != cur_crs:
            reply = QMessageBox.question(
                self, 'Advanced Profile Tool',
                f'This design was saved under CRS {saved_crs}, but the\n'
                f'current project CRS is {cur_crs}. Coordinates will be\n'
                'used as-is, NOT reprojected — continue anyway?',
                _MB_YES | _MB_NO)
            if reply != _MB_YES:
                return
        missing_dem = self._embank_apply_save_state(state)
        self._embank_project_folder = folder
        msg = f'Embankment design loaded from:\n{folder}'
        if missing_dem:
            msg += (f"\n\nNote: DEM layer '{missing_dem}' could not be found or\n"
                    'reloaded automatically — pick it manually in Raster\n'
                    'Layers (DEM).')
        QMessageBox.information(self, 'Advanced Profile Tool', msg)

    def _on_embank_project_close(self):
        """Closes the current embankment design — clears the Modify Embank
        tab back to blank (same full reset as Clear) and forgets which
        folder was open, so the next Save asks for a new name instead of
        silently overwriting the just-closed one. Confirms first since
        this discards any changes made since the last Save."""
        if self._embank_geom is None and self._embank_project_folder is None:
            return
        reply = QMessageBox.question(
            self, 'Advanced Profile Tool',
            'Close the current embankment design?\n'
            'Any changes made since the last Save will be lost.',
            _MB_YES | _MB_NO)
        if reply != _MB_YES:
            return
        self._clear_embank_alignment()
        self._embank_project_folder = None

    def _on_embank_create_tiff(self):
        """Opens the Create TIFF dialog — generates a standalone GeoTIFF of
        ONLY the embankment design (NOT merged into any existing DEM —
        that's Merge TIFF's job, deliberately separate so this one can be
        checked on its own first). Built the way 12d/Civil 3D build a
        corridor design surface: per-station cross-sections + longitudinal
        breaklines -> constrained-Delaunay TIN -> rasterise (see
        _generate_embank_channel_tiff), not a plain scattered-point
        interpolation — researched specifically for this feature."""
        if self._embank_geom is None or self._embank_us_level is None or self._embank_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set up the horizontal alignment and both end levels first\n'
                '— there needs to be an embankment design to generate a TIFF from.')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle('Create TIFF')
        v = QVBoxLayout(dlg)
        crs_name = self.canvas.mapSettings().destinationCrs().authid()
        lbl = QLabel(
            'Generates a GeoTIFF of the EMBANKMENT DESIGN ONLY — not merged\n'
            'into any existing DEM yet (check it first, then use Merge\n'
            f'TIFF). Output projection: {crs_name} (the current map canvas CRS).'
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Resolution (grid size):'))
        spin_res = QDoubleSpinBox()
        spin_res.setRange(0.01, 1000.0)
        spin_res.setDecimals(2)
        spin_res.setSuffix(' m')
        spin_res.setValue(self.spin_embank_interval.value())
        spin_res.setToolTip(
            'Output raster cell size, and also the spacing between\n'
            'cross-sections generated along the alignment to build the\n'
            'design surface.'
        )
        row1.addWidget(spin_res)
        row1.addStretch()
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Reference DEM (optional):'))
        combo_dem = QgsMapLayerComboBox()
        combo_dem.setFilters(_RASTER_FILTER)
        combo_dem.setAllowEmptyLayer(True)
        combo_dem.setCurrentIndex(0)
        combo_dem.wheelEvent = lambda e: e.ignore()
        row2.addWidget(combo_dem, 1)
        v.addLayout(row2)
        lbl2 = QLabel(
            "If given, the output raster's grid origin is snapped onto\n"
            "this DEM's own pixel grid (whole Resolution steps from its\n"
            'corner) so the two line up pixel-for-pixel when merged later.\n'
            "Only its grid alignment is used — CRS is always the canvas's."
        )
        lbl2.setWordWrap(True)
        lbl2.setStyleSheet('font-size:9px;color:#666;')
        v.addWidget(lbl2)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_run = QPushButton('Run')
        btn_run.setStyleSheet('font-weight:bold;')
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_run)
        v.addLayout(btns)
        btn_cancel.clicked.connect(dlg.reject)

        def _do_run():
            resolution = spin_res.value()
            dem_layer = combo_dem.currentLayer()
            # A bend whose fillet radius is smaller than the channel's OWN
            # half-width there can't be represented as a simple swept
            # corridor at all — the inside edge would have to fold over
            # itself to exist at that radius. No amount of station
            # density or orientation fixing (what the miter join and bend
            # densification above actually solve) can resolve THIS one;
            # it's a real geometric constraint, not a sampling artifact.
            # Warn with the exact chainage(s) rather than let it surface
            # as an unexplained bad patch in the generated TIFF. A true
            # ZERO-radius corner is NOT flagged — that's the miter join's
            # own, already-handled case.
            tight = self._embank_check_tight_bends()
            if tight:
                lines = '\n'.join(
                    f'  • Chainage {ch:.2f} m — radius {r:.2f} m, '
                    f'channel half-width ~{hw:.2f} m there'
                    for ch, r, hw in tight[:8])
                more = f'\n  …and {len(tight) - 8} more' if len(tight) > 8 else ''
                reply = QMessageBox.question(
                    self, 'Advanced Profile Tool',
                    'The fillet radius is tighter than the channel\'s own '
                    'half-width at:\n\n' + lines + more +
                    '\n\nThe inside edge of the channel can\'t be represented '
                    'cleanly at these — a surface artifact there is likely. '
                    'Loosening the fillet radius (or narrowing the channel) '
                    'at these chainages is the real fix.\n\nGenerate anyway?',
                    _MB_YES | _MB_NO)
                if reply != _MB_YES:
                    return
            btn_run.setEnabled(False)
            QApplication.setOverrideCursor(_WAIT_CURSOR)
            try:
                layer = self._generate_embank_channel_tiff(resolution, dem_layer)
            except Exception as exc:
                QApplication.restoreOverrideCursor()
                btn_run.setEnabled(True)
                QMessageBox.critical(self, 'Advanced Profile Tool', f'Create TIFF failed:\n{exc}')
                return
            QApplication.restoreOverrideCursor()
            self._embank_last_tiff_layer = layer
            dlg.accept()
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                f"Embankment design TIFF created: '{layer.name()}'.\n"
                "Use the 'Add' button in the Cross-Section Preview to\n"
                'compare it against the design/ground profile.')

        btn_run.clicked.connect(_do_run)
        dlg.exec()

    def _embank_xs_local_points_to_world(self, chainage, poly_x, poly_y, geom_cache=None, direction=None,
                                            align_geom=None):
        """Converts a local cross-section (poly_x = offset from
        centreline, negative=left/positive=right; poly_y = elevation) at
        the given chainage into real-world (x, y, z) points.

        The perpendicular direction is taken from the ACTUAL straight
        segment of self.profile_geom's own polyline that this chainage
        falls on — NOT a finite-difference blend over a fixed window
        either side (what _make_perp_line_geom still uses, which is fine
        there since it's only ever called at a handful of user-picked
        chainages, never swept densely across a sharp corner). A window-
        based tangent straddles a SHARP, un-filleted corner for every
        station within the window's own distance of it, giving those
        stations an increasingly skewed/averaged direction that belongs
        to neither leg — which is what was producing a twisted, pinched
        TIN right at sharp (near-90°) bends in Create TIFF. Reading the
        direction off the real segment instead is exact and has no such
        ambiguity except exactly AT a vertex, where either adjacent
        segment is an equally reasonable choice for a single station's
        own orientation — UNLESS `direction` is given explicitly (a unit
        (tx, ty) tuple), which overrides the segment lookup entirely; see
        _embank_vertex_miter_bisector — the one sharp-vertex MITER
        station built at each un-filleted corner uses the bisector of its
        two legs instead of either one's own direction, which is what
        actually needs this override.

        geom_cache, if given, is (pts, cumulative_lengths) — see
        _embank_corridor_geom_cache — so repeated calls across many
        stations (the normal case, from _embank_build_channel_corridor_
        data) don't re-extract the polyline's vertices and re-walk its
        cumulative length on every single call. Built fresh from
        align_geom (or self.profile_geom) when omitted.

        align_geom, if given, is used INSTEAD of self.profile_geom for
        BOTH the geom_cache fallback and the final interpolate() call —
        every other caller leaves this at its default; the End
        Adjustment closure (see _embank_build_channel_corridor_data)
        passes its own EXTENDED alignment geometry (and a geom_cache
        built from that SAME geometry) so a chainage past the real
        alignment's own start/end still lands in the right physical
        place instead of being clamped back onto it.

        Returns [] if there's no alignment or the geometry is
        degenerate (fewer than 2 vertices, or a zero-length segment)."""
        geom = align_geom if align_geom is not None else self.profile_geom
        if geom is None:
            return []
        if direction is not None:
            tx, ty = direction
        else:
            if geom_cache is None:
                geom_cache = self._embank_corridor_geom_cache(geom)
            pts, cum = geom_cache
            if len(pts) < 2:
                return []
            total = cum[-1]
            chainage = max(0.0, min(chainage, total))
            seg_i = bisect.bisect_right(cum, chainage) - 1
            seg_i = max(0, min(seg_i, len(pts) - 2))
            p1, p2 = pts[seg_i], pts[seg_i + 1]
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            if dist < 1e-10:
                return []
            tx, ty = dx / dist, dy / dist
        total = geom.length()
        chainage = max(0.0, min(chainage, total))
        px, py = -ty, tx
        ctr = geom.interpolate(chainage).asPoint()
        cx, cy = ctr.x(), ctr.y()
        return [(cx - px * xo, cy - py * xo, zo) for xo, zo in zip(poly_x, poly_y)]

    @staticmethod
    def _embank_corridor_geom_cache(geom):
        """(pts, cumulative_lengths) for geom's own polyline vertices —
        built once per Create TIFF run (see
        _embank_build_channel_corridor_data) and passed into every
        _embank_xs_local_points_to_world call via geom_cache, rather
        than re-extracting/re-walking the same polyline on every one of
        what can be thousands of stations."""
        pts = geom.asPolyline()
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + math.hypot(pts[i].x() - pts[i - 1].x(),
                                              pts[i].y() - pts[i - 1].y()))
        return pts, cum

    def _embank_vertex_miter_bisector(self, idx):
        """(bisector_tx, bisector_ty, miter_factor) for raw vertex idx —
        the 'forward' direction the ONE cross-section built exactly at a
        sharp, un-filleted corner (see _embank_build_channel_corridor_
        data's sharp_chs) should be oriented perpendicular to, and the
        factor every one of its offsets (poly_x — both left AND right,
        the same factor applies to both by symmetry) needs scaling by so
        its edges land exactly where the two legs' OWN offset edges would
        actually intersect — same technique a buffer/offset 'miter' join
        uses. Without the reorientation, the two legs' edges either gap
        apart (convex/outside) or overlap (concave/inside) right at the
        corner; without the scaling, reorienting alone still falls short
        of where they actually meet.

        Geometry: with u1/u2 the legs' own unit tangents and b their
        normalised bisector, u1 and u2 both make the same angle (half the
        total turn) with b by construction, so u1 . b = cos(half-angle)
        directly — and the distance from the vertex to where either leg's
        offset edge crosses the bisector line is EXACTLY half_width /
        cos(half-angle) (same relation for left and right, by symmetry).

        factor is capped at _TERRAIN_MITER_LIMIT, same 'miter limit'
        concept CAD/vector-graphics stroke joins use — past that the turn
        is sharp enough that a true miter point would shoot out an
        unreasonable distance from the corner.

        None if idx isn't an interior vertex, either adjoining segment is
        degenerate (zero length), or the two legs point directly opposite
        (a dead-end reversal has no well-defined bisector) — the caller
        falls back to the normal per-leg tangent (no override) in any of
        these cases."""
        pts = self._embank_raw_pts
        if not (0 < idx < len(pts) - 1):
            return None
        prev, cur, nxt = pts[idx - 1], pts[idx], pts[idx + 1]
        v1x, v1y = cur.x() - prev.x(), cur.y() - prev.y()
        v2x, v2y = nxt.x() - cur.x(), nxt.y() - cur.y()
        len1, len2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
        if len1 < 1e-9 or len2 < 1e-9:
            return None
        u1x, u1y = v1x / len1, v1y / len1
        u2x, u2y = v2x / len2, v2y / len2
        bx, by = u1x + u2x, u1y + u2y
        blen = math.hypot(bx, by)
        if blen < 1e-9:
            return None   # legs point directly opposite — no well-defined bisector
        bx, by = bx / blen, by / blen
        cos_half = max(-1.0, min(1.0, u1x * bx + u1y * by))
        if cos_half < 1e-3:
            return None
        factor = min(1.0 / cos_half, _TERRAIN_MITER_LIMIT)
        return bx, by, factor

    @staticmethod
    def _embank_params_half_width(params):
        """Conservative embankment half-width from interpolated cross-
        section params — crest half-width plus every FIXED-height
        section's own horizontal run (slope x height, plus its own
        LEADING berm width for every section after the first), stopping
        at that side's flush (open-ended) section, which isn't counted
        (it searches dynamically to ground rather than having a fixed
        extent). Takes whichever side runs wider. Shared by
        _embank_check_tight_bends, _embank_densify_bend_stations and
        _on_embank_set_halfwidth_radius so the 'is this bend tight enough
        to flag/fix' threshold can never drift between them."""
        def _run(sections):
            r = 0.0
            for idx, sec in enumerate(sections):
                if idx > 0:
                    r += sec.get('berm_width', 0.0)
                height = sec.get('height')
                if height is None:
                    break
                r += sec.get('slope', 0.0) * height
            return r

        return params['bottom_width'] / 2.0 + max(
            _run(params.get('left_sections', [])), _run(params.get('right_sections', [])))

    def _embank_check_tight_bends(self):
        """Scans every interior vertex and flags any whose fillet radius
        (its own override, or the default) is SMALLER than the channel's
        own half-width there. That's a genuine geometric impossibility
        for a simple swept corridor, not a sampling/orientation issue —
        the inside edge would have to fold over itself to exist at that
        radius — so neither the bend densification nor the miter join
        can fix it; the only real fix is a larger radius (or a narrower
        channel) at that chainage. A true ZERO-radius vertex is NOT
        flagged here — that's the miter join's own, already-handled
        case, not this one.

        Returns [(chainage, configured_radius, half_width), ...] sorted
        by chainage; empty if nothing's tight enough to flag, or there's
        no alignment yet."""
        flagged = []
        pts = self._embank_raw_pts
        if self._embank_geom is None or len(pts) <= 2:
            return flagged
        for i in range(1, len(pts) - 1):
            r = self._embank_vertex_radii.get(i, self._embank_fillet_radius)
            if r is None or r <= 1e-6:
                continue   # a true sharp corner — the miter join's own case
            try:
                ch_v = self._embank_geom.lineLocatePoint(QgsGeometry.fromPointXY(pts[i]))
            except Exception:
                continue
            params = self._embank_xs_interp_params(ch_v)
            half_w = self._embank_params_half_width(params)
            if r < half_w:
                flagged.append((ch_v, r, half_w))
        flagged.sort(key=lambda t: t[0])
        return flagged

    def _embank_tangent_angle(self, ch):
        """Local tangent direction (radians) of self._embank_geom at
        chainage ch, via a small centred finite difference. Cheap and
        only needs to be roughly right — used solely to estimate local
        curvature for bend-adaptive station densification (see
        _embank_densify_bend_stations), not for anything needing high
        precision."""
        total = self._embank_geom.length()
        delta = min(0.25, max(total * 0.001, 0.01))
        ch1 = max(0.0, ch - delta)
        ch2 = min(total, ch + delta)
        if ch2 - ch1 < 1e-9:
            return 0.0
        p1 = self._embank_geom.interpolate(ch1).asPoint()
        p2 = self._embank_geom.interpolate(ch2).asPoint()
        return math.atan2(p2.y() - p1.y(), p2.x() - p1.x())

    def _embank_densify_bend_stations(self, chs, spacing):
        """Inserts EXTRA chainages within tight bends so the OUTSIDE edge
        of the channel — which, at a bend, covers more real distance than
        the centreline does over that same chainage range — still gets
        sampled at roughly `spacing` m intervals, not just the centreline
        itself. For each consecutive pair of stations, estimates the
        local radius of curvature from how much the tangent direction
        rotates between them (angle / chainage-length), gets a rough
        half-width for that stretch from the interpolated cross-section
        params, and if the OUTSIDE edge's own arc length over that
        stretch (~ seg_len * (1 + half_width / radius)) exceeds spacing,
        subdivides it into enough equal steps to bring it back under.
        Straight runs (negligible tangent rotation) are left untouched —
        this only adds stations where the geometry actually needs them.
        Per-interval subdivision is capped (50 extra steps) so a
        near-zero-radius corner can't blow this up into an unbounded
        number of stations."""
        if len(chs) < 2:
            return chs
        out = [chs[0]]
        for ch_a, ch_b in zip(chs[:-1], chs[1:]):
            seg_len = ch_b - ch_a
            if seg_len <= 1e-9:
                out.append(ch_b)
                continue
            ang_a = self._embank_tangent_angle(ch_a)
            ang_b = self._embank_tangent_angle(ch_b)
            dtheta = abs(math.atan2(math.sin(ang_b - ang_a), math.cos(ang_b - ang_a)))
            if dtheta < 1e-6:
                out.append(ch_b)
                continue
            radius = seg_len / dtheta
            mid_params = self._embank_xs_interp_params((ch_a + ch_b) / 2.0)
            half_w = self._embank_params_half_width(mid_params)
            outer_len = seg_len * (1.0 + half_w / max(radius, 0.01))
            n = min(50, max(1, math.ceil(outer_len / spacing)))
            if n > 1:
                for i in range(1, n):
                    out.append(ch_a + seg_len * i / n)
            out.append(ch_b)
        return out

    def _embank_build_channel_corridor_data(self, station_spacing):
        """Builds the point/breakline data used to interpolate the
        channel design as a constrained TIN (see
        _generate_embank_channel_tiff) — the same idea 12d/Civil 3D use
        for a corridor design surface:
        - one TRANSVERSE breakline per station — its own design cross-
          section, built exactly like the Check Cross-Section window
          builds one (_embank_xs_build_interpolated_shape, so Topo
          Adjustment blending, the ground re-solve fix, and Earthwork
          backfill are all already correctly reflected).
        - the invert itself as ONE longitudinal breakline — already
          exactly known from the vertical alignment, no need to derive it
          from the cross-sections.
        - the two OUTERMOST left/right edges (each station's own first/
          last vertex — 'top of bank' either side) as two more
          longitudinal breaklines. These stay well-defined regardless of
          how many INTERIOR vertices (berm corners etc.) a given
          station's own shape happens to have, which can vary station to
          station (e.g. Flush = 2 points a side, Fixed = 4) — tracking
          every interior 'role' consistently across that varying vertex
          count isn't attempted; the dense per-station cross-sections
          (one every `station_spacing`) let the TIN interpolate between
          neighbouring stations' interior corners reasonably smoothly on
          their own.
        Real stations AND control sections' own exact chainages are
        always included as their own station, on top of the regular
        spacing grid, so a carefully-placed control section's shape is
        captured exactly rather than approximated by whichever regular
        grid point happens to be nearby.

        Extra stations are also inserted within tight BENDS (see
        _embank_densify_bend_stations) — spacing stations evenly along
        the CENTRELINE under-samples the OUTSIDE edge of a bend, which
        covers more real distance than the centreline over the same
        chainage range (the tighter the bend relative to the channel's
        own half-width, the worse this gets) — left as-is, this produced
        a visibly faceted/irregular TIN surface specifically on the
        outside of sharp bends.

        A SHARP (un-filleted) corner's own forced-in station is built as
        a MITER JOIN rather than a normal perpendicular cross-section —
        oriented along the bisector of its two legs and scaled so its
        edges land exactly where the two legs' own offset edges would
        actually meet (see _embank_vertex_miter_bisector) — the same
        technique a buffer/offset 'miter' join uses. Without this, a
        sharp corner's edges would either gap apart (outside) or overlap
        (inside) right at the point, which is what produced a twisted,
        pinched look specifically at hard (near-90°+) bends even once
        every station's own orientation was otherwise correct.

        Every vertex of every cross-section also becomes a POINT
        (redundant with the breaklines' own vertices — expected; the
        breaklines constrain the triangulation's edges, the points supply
        the actual elevation data).

        Returns (points, breaklines, bbox, footprint) — points:
        [(x,y,z), ...]; breaklines: [[(x,y,z), ...], ...]; bbox:
        QgsRectangle covering every generated point; footprint: a closed
        ring of (x, y) pairs tracing the corridor's own true outline
        (every station's left edge, down to the last station, back along
        every station's right edge) — this is what lets the raster get
        CLIPPED to the real swept channel shape afterwards instead of
        just its bounding rectangle, which otherwise triangulates/fills
        the rectangle's corners too (visible as extra fill poking out
        past the channel at a bend, since the rectangle doesn't taper the
        way the real corridor does). None (whole return) if there's no
        alignment/levels set up yet, or nothing could be built from
        them; footprint alone is None if there weren't enough edge
        points to close a ring."""
        if self._embank_geom is None or self._embank_us_level is None or self._embank_ds_level is None:
            return None
        length = self._embank_geom.length()
        spacing = max(0.01, station_spacing)
        chs = []
        ch = 0.0
        while ch < length - 1e-9:
            chs.append(ch)
            ch += spacing
        if chs and (length - chs[-1]) <= 1e-6:
            chs[-1] = length
        else:
            chs.append(length)
        anchor_chs = [ch for _k, ch in self._embank_xs_all_anchor_list()]
        # A SHARP (un-filleted, or near-zero-radius) corner is kept as an
        # exact vertex in self._embank_geom's own polyline (see
        # _fillet_alignment) — its own chainage is forced in as a station
        # here so the design shape is evaluated precisely AT the corner,
        # from both legs, rather than only ever approximated by whichever
        # regular-spacing grid point happens to land nearby. miter_at maps
        # that station's own (rounded) chainage back to its raw vertex
        # index, so the station-building loop below knows to orient and
        # scale its cross-section as a MITER join there (see
        # _embank_vertex_miter_bisector) instead of using the normal
        # per-leg tangent.
        sharp_chs = []
        miter_at = {}
        if len(self._embank_raw_pts) > 2:
            for i in range(1, len(self._embank_raw_pts) - 1):
                r = self._embank_vertex_radii.get(i, self._embank_fillet_radius)
                if r is None or r <= 1e-6:
                    try:
                        ch_v = self._embank_geom.lineLocatePoint(
                            QgsGeometry.fromPointXY(self._embank_raw_pts[i]))
                    except Exception:
                        continue
                    sharp_chs.append(ch_v)
                    miter_at[round(ch_v, 6)] = i
        chs = sorted(set(round(c, 6) for c in chs) | set(round(c, 6) for c in anchor_chs)
                     | set(round(c, 6) for c in sharp_chs))
        chs = sorted(set(round(c, 6) for c in self._embank_densify_bend_stations(chs, spacing)))

        points = []
        breaklines = []
        left_edge = []
        right_edge = []
        invert_line = []

        def _append_station(world_pts, centre_pt):
            breaklines.append(world_pts)
            points.extend(world_pts)
            left_edge.append(world_pts[0])
            right_edge.append(world_pts[-1])
            if centre_pt:
                invert_line.append(centre_pt[0])

        # END ADJUSTMENT — closes the embankment out past each real end
        # (US/DS) by continuing to build stations into the SAME Extend
        # US/DS distance the ground plot already samples (see
        # _embank_extended_ground_geom), crest level held CONSTANT at
        # that end's own level (no tapering) and each extension
        # station's own section stack reused from its nearest real end.
        # The existing ground-aware 'no cut' logic in
        # _embank_xs_build_side_sections then collapses the design on
        # its own wherever real ground rises to meet that level — a
        # smooth closure into the natural ground, like a dam's
        # abutments — without needing any dedicated tapering maths here.
        # OFF (or both Extend values 0): ext_chs_us/ds stay empty and
        # this reproduces today's behaviour exactly.
        end_adj = (self._embank_end_adjustment
                   and (self._embank_extend_us > 0.0 or self._embank_extend_ds > 0.0))
        ext_geom, chain_shift = (self._embank_extended_ground_geom() if end_adj else (None, 0.0))
        ext_geom_cache = self._embank_corridor_geom_cache(ext_geom) if end_adj else None

        def _ext_chainages(extend):
            if extend <= 0.0:
                return []
            vals, d = [], spacing
            while d < extend - 1e-9:
                vals.append(d)
                d += spacing
            vals.append(extend)
            return sorted(set(round(v, 6) for v in vals))

        ext_chs_us = [-v for v in reversed(_ext_chainages(self._embank_extend_us))] if end_adj else []
        ext_chs_ds = [length + v for v in _ext_chainages(self._embank_extend_ds)] if end_adj else []

        # ext_chs_us is already ascending (most-negative first, approaching
        # 0) and ext_chs_ds is ascending too (length+spacing upward), so
        # processing them in this order and always appending keeps every
        # longitudinal breakline (left_edge/right_edge/invert_line) in
        # correct chainage order throughout: far US end -> real US end ->
        # ... -> real DS end -> far DS end.
        for ch in ext_chs_us + ext_chs_ds:
            invert_level = self._embank_us_level if ch < 0.0 else self._embank_ds_level
            params = self._embank_xs_interp_params(0.0 if ch < 0.0 else length)
            shifted = ch + chain_shift
            ground = self._embank_xs_sample_ground(shifted, params, align_geom=ext_geom)
            poly_x, poly_y = self._embank_xs_build_shape(invert_level, params, ground)
            if not poly_x:
                continue
            world_pts = self._embank_xs_local_points_to_world(
                shifted, poly_x, poly_y, ext_geom_cache, align_geom=ext_geom)
            if not world_pts:
                continue
            centre = self._embank_xs_local_points_to_world(
                shifted, [0.0], [invert_level], ext_geom_cache, align_geom=ext_geom)
            _append_station(world_pts, centre)

        geom_cache = self._embank_corridor_geom_cache(self.profile_geom)
        for i, ch in enumerate(chs):
            if i % 200 == 0:
                QApplication.processEvents()
            invert_level = self._embank_invert_level_at(ch)
            params = self._embank_xs_interp_params(ch)
            # Resolve the miter direction/factor BEFORE sampling ground —
            # the ground sample itself needs to follow the corner's
            # bisector and reach as far as the final (scaled) edges will,
            # not the normal per-leg direction/extent (see
            # _embank_xs_sample_ground's own docstring for why this
            # ordering, not just the final point conversion, matters).
            direction = None
            miter_factor = 1.0
            vtx = miter_at.get(round(ch, 6))
            if vtx is not None:
                miter = self._embank_vertex_miter_bisector(vtx)
                if miter is not None:
                    direction = (miter[0], miter[1])
                    miter_factor = miter[2]
            ground = self._embank_xs_sample_ground(ch, params, direction=direction, scale=miter_factor)
            poly_x, poly_y = self._embank_xs_build_interpolated_shape(ch, invert_level, ground)
            if not poly_x:
                continue
            if direction is not None:
                poly_x = [xo * miter_factor for xo in poly_x]
            world_pts = self._embank_xs_local_points_to_world(
                ch, poly_x, poly_y, geom_cache, direction=direction)
            if not world_pts:
                continue
            centre = self._embank_xs_local_points_to_world(ch, [0.0], [invert_level], geom_cache)
            _append_station(world_pts, centre)

        if not points:
            return None
        if len(left_edge) >= 2:
            breaklines.append(left_edge)
        if len(right_edge) >= 2:
            breaklines.append(right_edge)
        if len(invert_line) >= 2:
            breaklines.append(invert_line)

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox = QgsRectangle(min(xs), min(ys), max(xs), max(ys))

        footprint = None
        if len(left_edge) >= 2 and len(right_edge) >= 2:
            ring = [(x, y) for x, y, _z in left_edge] + [(x, y) for x, y, _z in reversed(right_edge)]
            ring.append(ring[0])
            footprint = ring

        return points, breaklines, bbox, footprint

    def _generate_embank_channel_tiff(self, resolution, dem_layer):
        """Builds the embankment design surface as a constrained-Delaunay TIN
        (breaklines = every station's own cross-section, the invert, and
        both top-of-bank edges — see _embank_build_channel_corridor_data)
        and rasterises it to a GeoTIFF, using QGIS's OWN native TIN
        interpolation engine (qgis.analysis.QgsTinInterpolator /
        QgsGridFileWriter — the exact classes the 'TIN interpolation'
        Processing algorithm itself calls internally) rather than a
        third-party triangulation library or a plain scattered-point
        interpolator — no new dependency, and it's the same engine that
        already ships with every QGIS install. This produces the embankment
        design ONLY; merging it into a copy of the existing DEM is Merge
        TIFF's separate job, so the result can be checked on its own
        first.

        dem_layer, if given, affects ONLY the output grid's ORIGIN
        (snapped onto its own pixel grid using the resolution given here)
        so a later merge lines up pixel-for-pixel — it never changes the
        output CRS, which is always the map canvas's own.

        The raw TIN raster is then CLIPPED to the corridor's own true
        footprint polygon (every station's left edge out, right edge
        back — see _embank_build_channel_corridor_data) rather than
        left at its bounding rectangle: pixels outside the real swept
        embankment shape are set to NoData via GDAL's own cutline clip
        (gdal:cliprasterbymasklayer, CROP_TO_CUTLINE=False so the extent/
        resolution stay exactly as generated — only pixels outside the
        polygon change). Without this, a bend or taper in the alignment
        left triangulated fill sitting in the rectangle's corners, well
        outside the actual embankment.

        Returns the new QgsRasterLayer (already added to the project).
        Raises RuntimeError with a user-facing message on failure."""
        data = self._embank_build_channel_corridor_data(resolution)
        if data is None:
            raise RuntimeError(
                'No embankment design to generate from — set up the\n'
                'horizontal alignment and both end levels first.')
        points, breaklines, bbox, footprint = data

        crs = self.canvas.mapSettings().destinationCrs()

        pts_layer = QgsVectorLayer(f'PointZ?crs={crs.authid()}', 'embankment_points', 'memory')
        pts_layer.startEditing()
        for x, y, z in points:
            feat = QgsFeature(pts_layer.fields())
            feat.setGeometry(QgsGeometry(QgsPoint(x, y, z)))
            pts_layer.addFeature(feat)
        pts_layer.commitChanges()

        lines_layer = QgsVectorLayer(f'LineStringZ?crs={crs.authid()}', 'embankment_breaklines', 'memory')
        lines_layer.startEditing()
        for pts in breaklines:
            if len(pts) < 2:
                continue
            feat = QgsFeature(lines_layer.fields())
            feat.setGeometry(QgsGeometry(QgsLineString([QgsPoint(x, y, z) for x, y, z in pts])))
            lines_layer.addFeature(feat)
        lines_layer.commitChanges()

        margin = resolution
        x_min, x_max = bbox.xMinimum() - margin, bbox.xMaximum() + margin
        y_min, y_max = bbox.yMinimum() - margin, bbox.yMaximum() + margin

        if dem_layer is not None:
            dem_extent = dem_layer.extent()
            if dem_layer.crs() != crs:
                try:
                    xform = QgsCoordinateTransform(dem_layer.crs(), crs, QgsProject.instance())
                    dem_extent = xform.transformBoundingBox(dem_extent)
                except Exception:
                    pass
            dem_x0, dem_y0 = dem_extent.xMinimum(), dem_extent.yMinimum()
            x_min = dem_x0 + math.floor((x_min - dem_x0) / resolution) * resolution
            y_min = dem_y0 + math.floor((y_min - dem_y0) / resolution) * resolution
            x_max = dem_x0 + math.ceil((x_max - dem_x0) / resolution) * resolution
            y_max = dem_y0 + math.ceil((y_max - dem_y0) / resolution) * resolution

        extent = QgsRectangle(x_min, y_min, x_max, y_max)
        if extent.width() <= 0 or extent.height() <= 0 or not math.isfinite(extent.width()) \
                or not math.isfinite(extent.height()):
            raise RuntimeError(
                'Computed output extent is empty or invalid '
                f'({extent.width():.3f} x {extent.height():.3f} {crs.authid()} units).\n'
                'This usually means the Reference DEM is in a very different\n'
                "location/CRS than the embankment alignment, or doesn't actually\n"
                'overlap it — try running Create TIFF again without a\n'
                'Reference DEM to check.')
        columns = max(1, math.ceil(extent.width() / resolution))
        rows = max(1, math.ceil(extent.height() / resolution))
        if columns * rows > 40_000_000:
            raise RuntimeError(
                f'Requested grid is {columns} x {rows} = {columns * rows:,} cells\n'
                f'for a {extent.width():.1f} x {extent.height():.1f} m extent at\n'
                f'{resolution} m resolution — too fine for this extent. Use a\n'
                'coarser Resolution, or check the Reference DEM actually\n'
                'overlaps the embankment (a mismatched one can inflate the\n'
                'extent enormously).')

        feedback = QgsFeedback()
        layer_data = []
        for src, source_type in ((pts_layer, QgsInterpolator.SourcePoints),
                                   (lines_layer, QgsInterpolator.SourceBreakLines)):
            d = QgsInterpolator.LayerData()
            d.source = src
            d.transformContext = QgsProject.instance().transformContext()
            d.valueSource = QgsInterpolator.ValueZ
            d.interpolationAttribute = -1
            d.sourceType = source_type
            layer_data.append(d)

        interpolator = QgsTinInterpolator(layer_data, QgsTinInterpolator.Linear, feedback)
        raw_path = QgsProcessingUtils.generateTempFilename('embankment_design_raw.tif')
        writer = QgsGridFileWriter(interpolator, raw_path, extent, columns, rows)
        writer.writeFile(feedback)

        raw_layer = QgsRasterLayer(raw_path, 'Embankment Design (raw)')
        if not raw_layer.isValid():
            raise RuntimeError(
                'The raw TIN raster could not be written/loaded — this is\n'
                'before the clip step, so it points at the triangulation\n'
                'itself rather than the clip.\n'
                f'Points: {len(points)}, breaklines: {len(breaklines)}, '
                f'grid: {columns} x {rows} @ {resolution} m\n'
                f'({extent.xMinimum():.1f}, {extent.yMinimum():.1f}) - '
                f'({extent.xMaximum():.1f}, {extent.yMaximum():.1f}) {crs.authid()}.\n'
                'Check the main ground DEM (Raster Layers (DEM), in the\n'
                "Vertical Alignment section) is still actually selected —\n"
                "it can be lost after Open if that layer couldn't be\n"
                're-found automatically.')

        out_path = raw_path
        clip_note = ''
        if footprint is not None and len(footprint) >= 4:
            # A tight bend (the inside radius of the curve tighter than
            # the embankment's own half-width) can make this ring self-
            # intersect — the left/right edges cross over each other
            # instead of staying a simple outline, i.e. a 'bowtie'
            # polygon. GDAL's cutline clip can quietly fail on that (no
            # exception — just an empty/invalid output, which is the
            # 'Clip step produced no valid output' fallback below).
            # makeValid() repairs a self-intersecting ring into a valid
            # (Multi)Polygon GDAL can actually use as a cutline — it can
            # lose a sliver right at the self-intersection, but stays a
            # true-to-shape mask everywhere else, which is a solid
            # trade-off against just not clipping that bend at all.
            mask_geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in footprint]])
            if not mask_geom.isGeosValid():
                mask_geom = mask_geom.makeValid()
                # A genuinely self-intersecting ring's repair can come
                # back as a GeometryCollection mixing polygon pieces with
                # degenerate line/point slivers at the crossing — a
                # strictly-typed 'Polygon' memory layer below silently
                # REJECTS anything that isn't a clean (Multi)Polygon,
                # which is what was leaving the mask layer with zero
                # features (and the clip with nothing to cut against,
                # hence 'no valid output') even though makeValid() itself
                # hadn't failed. Keep only the real polygon part(s) and
                # re-union them into one clean (Multi)Polygon geometry.
                if mask_geom.type() != _POLYGON_GEOM:
                    parts = [g for g in mask_geom.asGeometryCollection()
                              if g.type() == _POLYGON_GEOM and not g.isEmpty()]
                    mask_geom = QgsGeometry.unaryUnion(parts) if parts else QgsGeometry()
            mask_geom.convertToMultiType()
            mask_layer = QgsVectorLayer(f'MultiPolygon?crs={crs.authid()}', 'embankment_footprint', 'memory')
            mask_layer.startEditing()
            feat = QgsFeature(mask_layer.fields())
            feat.setGeometry(mask_geom)
            mask_added = (not mask_geom.isEmpty()) and mask_layer.addFeature(feat)
            mask_layer.commitChanges()
            if not mask_added or mask_layer.featureCount() == 0:
                clip_note = ('\n\n(Could not build a valid clip shape from this alignment '
                              '— likely an extremely tight bend even the repair step '
                              "couldn't resolve — showing the unclipped rectangle instead. "
                              'Try a larger fillet radius on the sharpest bend.)')
            else:
                try:
                    import processing
                    clip_path = QgsProcessingUtils.generateTempFilename('embankment_design.tif')
                    processing.run('gdal:cliprasterbymasklayer', {
                        'INPUT': raw_path, 'MASK': mask_layer,
                        'SOURCE_CRS': crs, 'TARGET_CRS': crs,
                        'NODATA': -9999.0, 'ALPHA_BAND': False,
                        'CROP_TO_CUTLINE': False, 'KEEP_RESOLUTION': True,
                        'MULTITHREADING': False, 'OUTPUT': clip_path,
                    })
                    # processing.run() can return normally without actually
                    # having written a valid file (e.g. an OUTPUT path quirk,
                    # or the GDAL step itself failing quietly) — checked
                    # explicitly here rather than trusting the absence of an
                    # exception, so a bad clip falls back to the known-good
                    # raw raster instead of taking the WHOLE Create TIFF down
                    # with it.
                    clip_layer = QgsRasterLayer(clip_path, 'Embankment Design (clip check)')
                    if clip_layer.isValid():
                        out_path = clip_path
                    else:
                        clip_note = ('\n\n(Clip step produced no valid output — showing the '
                                      'unclipped rectangle instead. Most likely cause: a tight '
                                      "bend in the alignment made the embankment's own outline "
                                      'self-intersect, which the repair above could not fully '
                                      'resolve — try a larger fillet radius on that bend.)')
                except Exception as exc:
                    # Falls back to the unclipped rectangle rather than
                    # failing the whole Create TIFF — e.g. the 'gdal'
                    # Processing provider not being available for some
                    # reason. The rest of the design is still correct, it
                    # just has the rectangle-corner fill this clip exists to
                    # remove.
                    clip_note = f'\n\n(Clip step failed ({exc}) — showing the unclipped rectangle instead.)'

        layer = QgsRasterLayer(out_path, 'Embankment Design (temp)')
        if not layer.isValid():
            raise RuntimeError(
                'The generated TIFF could not be loaded as a raster layer.\n'
                f'Points: {len(points)}, breaklines: {len(breaklines)}, '
                f'grid: {columns} x {rows} @ {resolution} m, '
                f'output: {out_path}{clip_note}')
        if clip_note:
            QMessageBox.warning(self, 'Advanced Profile Tool', clip_note.strip())
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _on_embank_merge_tiff(self):
        """Pastes a previously created embankment-design TIFF into a copy
        of an existing LiDAR/ground raster, replacing only the
        embankment's own footprint (same idea as a GIS/CAD 'paste
        surface'). See _run_merge_tiff_dialog / _merge_lidar_with_design."""
        self._run_merge_tiff_dialog('Embankment', self._embank_last_tiff_layer)

    def _on_embank_invert_levels_changed(self):
        self._embank_us_level = self.spin_embank_us_level.value()
        self._embank_ds_level = self.spin_embank_ds_level.value()
        self._draw_embank_vertical_decorations()

    def _refresh_embank_diff_combo(self):
        """Keep the 'vs:' raster picker in sync with whatever DEM(s) are
        currently in the Raster Layers list, without losing the user's
        current pick if it's still valid."""
        raster_entries, _ve, _cm = self._collect_entries(
            raster_rows=self._embank_raster_rows, vector_rows=[])
        cols = [col for _lyr, col in raster_entries]
        combo = self.combo_embank_diff_col
        cur = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(cols)
        if cur in cols:
            combo.setCurrentText(cur)
        elif cols:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _on_embank_diff_changed(self):
        self._embank_show_diff = self.chk_embank_diff.isChecked()
        if self._embank_diff_ann is not None:
            self._embank_diff_ann.set_visible(False)
            if MATPLOTLIB_AVAILABLE:
                self.canvas_plot.draw_idle()

    def _update_embank_diff_annotation(self, ch):
        """Crest minus ground (picked raster) at chainage ch — used both
        on plain hover AND while a break point is being dragged, so the
        fill depth stays visible and live-updates the whole time a break
        is being moved, not just when the mouse is merely hovering.
        Positive = fill needed to reach the design crest; negative =
        ground is already above the crest (cut needed there instead) —
        opposite sign convention from Channel Surface's own Excavation Δ,
        since this tab is fill-dominant and 'positive = fill' reads more
        naturally here."""
        ann = self._embank_diff_ann
        if ann is None:
            return
        if not self._embank_show_diff or not MATPLOTLIB_AVAILABLE:
            ann.set_visible(False)
            return
        diff_col = self.combo_embank_diff_col.currentText()
        gvals = self._profile_data_store.get(diff_col) if diff_col else None
        if not diff_col or not gvals or not self._profile_chainages:
            ann.set_visible(False)
            return
        clean = [v if v is not None else float('nan') for v in gvals]
        gval = float(np.interp(ch, self._profile_chainages, clean))
        if not np.isfinite(gval):
            ann.set_visible(False)
            return
        inv_val = self._embank_invert_level_at(ch)
        delta = inv_val - gval
        ann.set_text(f'Fill Depth Δ: {delta:+.3f} m')
        ann.set_visible(True)

    def _toggle_embank_break_tool(self, checked):
        self._embank_add_break_mode = checked
        if checked:
            self._embank_deactivate_all_tools(except_btn=self.btn_embank_break)
        if not hasattr(self, 'lbl_embank_status'):
            return
        if checked:
            self.lbl_embank_status.setText(
                'Click along the crest line in the chart to add a break point.')
            self.lbl_embank_status.setStyleSheet('color:#FB8C00;font-style:italic;font-size:11px;')
        elif self._embank_geom is not None:
            length = self._embank_geom.length()
            self.lbl_embank_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
            self.lbl_embank_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

    def _toggle_embank_control_tool(self, checked):
        self._embank_add_control_mode = checked
        if checked:
            self._embank_deactivate_all_tools(except_btn=self.btn_embank_control)
        if not hasattr(self, 'lbl_embank_status'):
            return
        if checked:
            self.lbl_embank_status.setText(
                'Click along the crest line in the chart to add a control section.')
            self.lbl_embank_status.setStyleSheet('color:#00897B;font-style:italic;font-size:11px;')
        elif self._embank_geom is not None:
            length = self._embank_geom.length()
            self.lbl_embank_status.setText(f'Horizontal alignment: {length:.2f} m — ready.')
            self.lbl_embank_status.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')

    def _add_embank_break(self, xdata):
        if self._embank_geom is None or self._embank_us_level is None or self._embank_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set the upstream and downstream levels first — the invert\n'
                'line needs both ends defined before you can add a break point.')
            return
        length = self._embank_geom.length()
        ch = max(0.0, min(float(xdata), length))
        level = self._embank_invert_level_at(ch)
        # Clicking very close to an existing break re-levels it in place
        # (at its current chainage) rather than adding a near-duplicate.
        tol = max(0.05, length * 0.002)
        for b in self._embank_invert_breaks:
            if abs(b['chainage'] - ch) < tol:
                b['level'] = self._embank_invert_level_at(b['chainage'])
                self._select_embank_xs_node(id(b))
                return
        new_break = {'chainage': ch, 'level': level}
        self._embank_invert_breaks.append(new_break)
        self._embank_invert_breaks.sort(key=lambda b: b['chainage'])
        # Newly placed break becomes the selected cross-section station too
        # — you just created it, so seeing/setting its section right away
        # is the natural next step.
        self._select_embank_xs_node(id(new_break))

    def _remove_embank_break(self, idx):
        """Right-click on a break marker removes it — no separate 'delete'
        tool needed, and it can't be confused with left-click drag/add."""
        if not (0 <= idx < len(self._embank_invert_breaks)):
            return
        removed = self._embank_invert_breaks.pop(idx)
        if self._embank_break_drag_obj is removed:
            self._embank_break_drag_obj = None
        # The removed break's own two segments merge into one — whichever
        # cross-section was selected no longer corresponds to a real
        # segment, so it isn't safe to keep pointing at it.
        if self._embank_selected_xs_key == id(removed):
            self._clear_embank_xs_selection()
        self._draw_embank_vertical_decorations()

    def _clear_embank_xs_selection(self):
        """Resets the Cross-Section editor to its empty state — shared by
        removing whichever break point or control section is currently
        selected, since either one leaves the editor with nothing valid
        to point at."""
        self._embank_selected_xs_key = None
        self._embank_xs_ground = None
        self.btn_embank_xs_copy_ds.setEnabled(False)
        self.btn_embank_xs_copy_all.setEnabled(False)
        self._rebuild_embank_section_rows()
        self.lbl_embank_xs_status.setText(
            'Click a break point (or the US/DS end) in the chart above to edit its cross-section.')
        self.lbl_embank_xs_status.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self._draw_embank_xs_preview()

    def _add_embank_control(self, xdata):
        """Places a CONTROL SECTION at the clicked chainage — same
        add/re-select-if-close convention as _add_embank_break, but no
        level is stored (always read live from the invert) and there's no
        drag support. Starts with whatever cross-section the interpolation
        already gives at this chainage (_embank_xs_interp_params), so
        placing one doesn't itself change anything — it just gives the
        user a real, directly-editable anchor to then adjust for a
        smoother transition."""
        if self._embank_geom is None or self._embank_us_level is None or self._embank_ds_level is None:
            QMessageBox.information(
                self, 'Advanced Profile Tool',
                'Set the upstream and downstream levels first — the invert\n'
                'line needs both ends defined before you can add a control section.')
            return
        length = self._embank_geom.length()
        ch = max(0.0, min(float(xdata), length))
        tol = max(0.05, length * 0.002)
        for cs in self._embank_control_sections:
            if abs(cs['chainage'] - ch) < tol:
                self._select_embank_xs_node(id(cs))
                return
        new_cs = {'chainage': ch}
        self._embank_control_sections.append(new_cs)
        self._embank_control_sections.sort(key=lambda c: c['chainage'])
        self._embank_xs_params[id(new_cs)] = self._embank_xs_interp_params(ch)
        self._select_embank_xs_node(id(new_cs))

    def _remove_embank_control(self, cs):
        """Right-click on a control-section marker removes it — same
        convention as break points."""
        if cs not in self._embank_control_sections:
            return
        self._embank_control_sections.remove(cs)
        self._embank_xs_params.pop(id(cs), None)
        if self._embank_selected_xs_key == id(cs):
            self._clear_embank_xs_selection()
        self._draw_embank_vertical_decorations()

    def _try_edit_embank_text(self, event):
        """Typed-value alternative to dragging: a click that lands exactly
        on a level or slope % label opens an inline editor right over that
        label instead. Only checked while 'Add Vertical Break' is off, so
        it can never be confused with placing a new break there."""
        for entry in self._embank_level_click_targets:
            contains, _ = entry['artist'].contains(event)
            if contains:
                self._edit_embank_level(entry['artist'], entry['handle'])
                return True
        for entry in self._embank_slope_click_targets:
            contains, _ = entry['artist'].contains(event)
            if contains:
                self._edit_embank_slope(entry['artist'], entry['v0'], entry['v1'])
                return True
        return False

    def _set_embank_vertex_level(self, handle, new_level):
        """Write a new level back to whatever the handle actually is —
        the US/DS spinboxes (kept in sync) or a break's own dict."""
        _ch, _lvl, kind, ref = handle
        if kind == 'us':
            self._embank_us_level = new_level
            self.spin_embank_us_level.blockSignals(True)
            self.spin_embank_us_level.setValue(new_level)
            self.spin_embank_us_level.blockSignals(False)
        elif kind == 'ds':
            self._embank_ds_level = new_level
            self.spin_embank_ds_level.blockSignals(True)
            self.spin_embank_ds_level.setValue(new_level)
            self.spin_embank_ds_level.blockSignals(False)
        else:
            ref['level'] = new_level

    def _edit_embank_level(self, artist, handle):
        _ch, level, _kind, _ref = handle
        self._open_embank_inline_editor(
            artist, f'{level:.3f}',
            lambda text, h=handle: self._commit_embank_level(h, text))

    def _commit_embank_level(self, handle, text):
        try:
            val = float(text)
        except (TypeError, ValueError):
            return
        self._set_embank_vertex_level(handle, val)
        self._draw_embank_vertical_decorations()

    def _edit_embank_slope(self, artist, v0, v1):
        """Solves for v1's level given a typed slope %, keeping v0 (the
        upstream end of this segment) fixed — same convention as dragging
        the downstream end to hit a target grade."""
        x0, y0 = v0[0], v0[1]
        x1, y1 = v1[0], v1[1]
        dx = x1 - x0
        cur_slope = (y0 - y1) / dx * 100.0 if abs(dx) > 1e-9 else 0.0
        self._open_embank_inline_editor(
            artist, f'{cur_slope:.3f}',
            lambda text, a=v0, b=v1: self._commit_embank_slope(a, b, text))

    def _commit_embank_slope(self, v0, v1, text):
        try:
            new_slope = float(text)
        except (TypeError, ValueError):
            return
        x0, y0 = v0[0], v0[1]
        x1, _y1 = v1[0], v1[1]
        dx = x1 - x0
        if abs(dx) < 1e-9:
            return
        new_y1 = y0 - new_slope / 100.0 * dx
        self._set_embank_vertex_level(v1, new_y1)
        self._draw_embank_vertical_decorations()

    def _open_embank_inline_editor(self, artist, initial_text, commit_fn):
        """Float a small QLineEdit directly over the clicked label's own
        rendered position so a value can be typed in place, instead of a
        popup dialog stealing focus from the chart."""
        self._close_embank_inline_editor(commit=False)
        try:
            renderer = self.canvas_plot.get_renderer()
            bbox = artist.get_window_extent(renderer)
        except Exception:
            return
        canvas_h = self.canvas_plot.height()
        x = max(0, int(bbox.x0) - 4)
        y = max(0, int(canvas_h - bbox.y1) - 3)
        w = max(56, int(bbox.width) + 16)
        h = max(20, int(bbox.height) + 8)

        editor = _InlineValueEdit(self.canvas_plot)
        editor.setText(initial_text)
        editor.setGeometry(x, y, w, h)
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter if hasattr(Qt, 'AlignmentFlag') else Qt.AlignCenter)
        editor.setStyleSheet(
            'background:#FFFDE7;border:2px solid #F57F17;'
            'font-size:8pt;font-weight:bold;padding:0px;'
        )
        editor.editingFinished.connect(lambda: self._close_embank_inline_editor(commit=True))
        editor.escapePressed.connect(lambda: self._close_embank_inline_editor(commit=False))
        self._embank_inline_editor    = editor
        self._embank_inline_commit_fn = commit_fn
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _close_embank_inline_editor(self, commit):
        editor = self._embank_inline_editor
        if editor is None:
            return
        self._embank_inline_editor = None
        fn = self._embank_inline_commit_fn
        self._embank_inline_commit_fn = None
        try:
            editor.blockSignals(True)
            text = editor.text()
        except Exception:
            text = None
        try:
            editor.setParent(None)
            editor.deleteLater()
        except Exception:
            pass
        if commit and fn is not None and text is not None:
            fn(text)

    def _clear_embank_break_map_bands(self):
        for band in self._embank_break_map_bands:
            try: self.canvas.scene().removeItem(band)
            except Exception: pass
        self._embank_break_map_bands = []
        for ann in self._embank_break_annotations:
            self._remove_ann(ann)
        self._embank_break_annotations = []

    def _clear_embank_xs_map_marker(self):
        if self._embank_xs_map_band is not None:
            try: self.canvas.scene().removeItem(self._embank_xs_map_band)
            except Exception: pass
            self._embank_xs_map_band = None
        if self._embank_xs_annotation is not None:
            self._remove_ann(self._embank_xs_annotation)
            self._embank_xs_annotation = None

    def _draw_embank_vertical_decorations(self):
        """Additive decoration step, same pattern as the 1D Profile tab's
        pipe/manhole drawing: _plot()/_reset_axes() just cleared the axes,
        so the invert line, cut/fill hatch and break-point markers are
        drawn fresh on top every time, and redrawn from cached state
        whenever only a level/break changes (no re-extraction needed)."""
        for art in self._embank_invert_artists:
            try: art.remove()
            except Exception: pass
        self._embank_invert_artists = []
        self._embank_level_click_targets = []
        self._embank_slope_click_targets = []
        self._clear_embank_break_map_bands()
        self._clear_embank_xs_map_marker()
        if not MATPLOTLIB_AVAILABLE:
            return
        handles = self._embank_invert_vertex_handles()
        if handles is None:
            self.canvas_plot.draw_idle()
            return
        verts = [(h[0], h[1]) for h in handles]

        ax = self.ax
        inv_xs = [v[0] for v in verts]
        inv_ys = [v[1] for v in verts]

        ground_ys, invert_at_xs, xs_for_fill = None, None, None
        if (self._embank_ground_col and self._profile_chainages
                and self._embank_ground_col in self._profile_data_store):
            xs_for_fill = np.array(self._profile_chainages, dtype=float)
            ground_ys = np.array(
                [v if v is not None else np.nan
                 for v in self._profile_data_store[self._embank_ground_col]], dtype=float)
            invert_at_xs = np.interp(xs_for_fill, inv_xs, inv_ys)

        ln, = ax.plot(inv_xs, inv_ys, color='#6D4C41', linewidth=2.5,
                       marker='o', markersize=4, markerfacecolor='#6D4C41',
                       zorder=8, label='Embankment Crest')
        self._embank_invert_artists.append(ln)

        # Upstream/downstream end levels — triangular so they read as
        # visually distinct from interior break points (squares); these
        # only ever drag vertically (raise/lower the level) since their
        # chainage is fixed at the alignment's own two ends.
        us_dragging = (self._embank_endpoint_drag == 'us')
        ds_dragging = (self._embank_endpoint_drag == 'ds')
        # While a drag is live, the marker itself previews the new
        # chainage it would land on (see _on_chart_hover) — the crest
        # LINE stays put at the real inv_xs[0]/[-1] until release commits
        # the change, so a thin dashed connector makes clear what's
        # about to move.
        us_x = (self._embank_endpoint_drag_chainage
                if us_dragging and self._embank_endpoint_drag_chainage is not None else inv_xs[0])
        ds_x = (self._embank_endpoint_drag_chainage
                if ds_dragging and self._embank_endpoint_drag_chainage is not None else inv_xs[-1])
        if us_dragging and us_x != inv_xs[0]:
            connector_us, = ax.plot([us_x, inv_xs[0]], [inv_ys[0], inv_ys[0]],
                                     color='#6A1B9A', linewidth=1.5, linestyle='--', zorder=9)
            self._embank_invert_artists.append(connector_us)
        if ds_dragging and ds_x != inv_xs[-1]:
            connector_ds, = ax.plot([inv_xs[-1], ds_x], [inv_ys[-1], inv_ys[-1]],
                                     color='#6A1B9A', linewidth=1.5, linestyle='--', zorder=9)
            self._embank_invert_artists.append(connector_ds)
        mk_us, = ax.plot([us_x], [inv_ys[0]], marker='^', markersize=12,
                          markerfacecolor='#6A1B9A' if us_dragging else '#AB47BC',
                          markeredgecolor='#4A148C', markeredgewidth=1.5, zorder=10)
        mk_ds, = ax.plot([ds_x], [inv_ys[-1]], marker='^', markersize=12,
                          markerfacecolor='#6A1B9A' if ds_dragging else '#AB47BC',
                          markeredgecolor='#4A148C', markeredgewidth=1.5, zorder=10)
        self._embank_invert_artists.extend([mk_us, mk_ds])

        if ground_ys is not None:
            # Restricted to the real alignment's own chainage range — an
            # extended ground plot (see _embank_extended_ground_geom) can
            # hand this xs_for_fill chainages below 0 or above length,
            # where np.interp just clamps invert_at_xs to the nearest
            # real end level rather than extrapolating a real design, so
            # hatching out there would show a fabricated cut/fill against
            # a flat line that was never actually designed.
            in_design = (xs_for_fill >= inv_xs[0]) & (xs_for_fill <= inv_xs[-1])
            valid = in_design & np.isfinite(ground_ys) & np.isfinite(invert_at_xs)
            # Invert ABOVE ground = material needs adding = fill (red).
            # Invert BELOW ground = material needs removing = cut (blue).
            fill_a = ax.fill_between(
                xs_for_fill, ground_ys, invert_at_xs, where=valid & (invert_at_xs > ground_ys),
                facecolor='#F44336', alpha=0.28, hatch='///',
                edgecolor='#F44336', linewidth=0.0, interpolate=True, zorder=2)
            cut_a = ax.fill_between(
                xs_for_fill, ground_ys, invert_at_xs, where=valid & (invert_at_xs < ground_ys),
                facecolor='#4FC3F7', alpha=0.28, hatch='///',
                edgecolor='#4FC3F7', linewidth=0.0, interpolate=True, zorder=2)
            self._embank_invert_artists.extend([fill_a, cut_a])

        # Slope % for every segment between consecutive vertices (US ->
        # break -> ... -> DS) — with no break points this is just the one
        # US-to-DS segment, so a single slope shows with no special-casing.
        # (upstream - downstream) / length, matching the sign convention
        # the Network Profile tab already uses for pipe slopes (see
        # _draw_one_pipe_segment) — a normal falling channel reads
        # positive; a section that rises against the flow reads negative,
        # which is the thing worth flagging.
        for h0, h1 in zip(handles, handles[1:]):
            x0, y0 = h0[0], h0[1]
            x1, y1 = h1[0], h1[1]
            dx = x1 - x0
            slope_pct = (y0 - y1) / dx * 100.0 if abs(dx) > 1e-9 else 0.0
            mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            txt = ax.annotate(
                f'{slope_pct:.2f}%', xy=(mid_x, mid_y), xytext=(0, 9),
                textcoords='offset points', ha='center', fontsize=8,
                fontweight='bold', color='#4E342E',
                bbox=dict(boxstyle='round,pad=0.15', facecolor='white',
                          alpha=0.85, edgecolor='none'), zorder=9,
            )
            self._embank_invert_artists.append(txt)
            self._embank_slope_click_targets.append({'artist': txt, 'v0': h0, 'v1': h1})

        # Break points — large and high-zorder ('much visible' per the
        # brief) since these are the drag handles (chainage AND level both
        # change live on drag — see _on_chart_hover's drag branch); level
        # shown alongside each one, editable simply by dragging the marker
        # itself in any direction.
        for i, b in enumerate(self._embank_invert_breaks):
            is_dragging = (self._embank_break_drag_obj is b)
            mk, = ax.plot([b['chainage']], [b['level']], marker='s', markersize=10,
                          markerfacecolor='#FF5722' if is_dragging else '#FFEB3B',
                          markeredgecolor='#F57F17', markeredgewidth=2, zorder=10,
                          picker=8)
            self._embank_invert_artists.append(mk)
            lvl_txt = ax.annotate(
                f'{b["level"]:.2f} m', xy=(b['chainage'], b['level']), xytext=(7, -3),
                textcoords='offset points', fontsize=7.5, fontweight='bold',
                color='#E65100',
                bbox=dict(boxstyle='round,pad=0.12', facecolor='white',
                          alpha=0.9, edgecolor='none'), zorder=11,
            )
            self._embank_invert_artists.append(lvl_txt)
            self._embank_level_click_targets.append(
                {'artist': lvl_txt, 'handle': (b['chainage'], b['level'], 'break', b)})

            # Map marker: same dotted-perpendicular-tick style as the major
            # chainage marks (deliberately, so it reads as 'a chainage
            # marker' at a glance) but thinner and dotted to stay visually
            # distinct from the solid major ticks, with its own label.
            tg = self._embank_tick_geom(b['chainage'], 6.0)
            if tg is not None:
                line_geom, label_pt, angle_deg = tg
                band = QgsRubberBand(self.canvas, _LINE_GEOM)
                band.setColor(QColor(245, 127, 23, 230))
                band.setWidth(2)
                try:
                    band.setLineStyle(Qt.PenStyle.DotLine)
                except AttributeError:
                    band.setLineStyle(Qt.DotLine)  # type: ignore[attr-defined]
                band.setToGeometry(line_geom, None)
                self._embank_break_map_bands.append(band)
                self._add_embank_chainage_label(label_pt, b['chainage'], angle_deg,
                                                  color='#E65100',
                                                  target=self._embank_break_annotations)

        # Control sections — a lightweight station with its OWN full
        # cross-section (same editor as a break point), drawn as a small
        # diamond right on the invert line since it carries no level of
        # its own. Deliberately smaller/lower-zorder than break markers so
        # the two are never confused; still click-to-select (same as a
        # break) / right-click-to-remove via _find_embank_control_near —
        # just never draggable.
        for cs in self._embank_control_sections:
            ch = cs['chainage']
            level = self._embank_invert_level_at(ch)
            is_selected = (self._embank_selected_xs_key == id(cs))
            mk, = ax.plot([ch], [level], marker='D', markersize=8,
                          markerfacecolor='#00838F' if is_selected else '#26C6DA',
                          markeredgecolor='#006064',
                          markeredgewidth=1.5, zorder=9, picker=8)
            self._embank_invert_artists.append(mk)
            lbl = ax.annotate(
                'CS', xy=(ch, level), xytext=(0, 10),
                textcoords='offset points', ha='center', fontsize=7,
                fontweight='bold', color='#006064',
                bbox=dict(boxstyle='round,pad=0.1', facecolor='white',
                          alpha=0.85, edgecolor='none'), zorder=9,
            )
            self._embank_invert_artists.append(lbl)

        # Selected cross-section STATION — a single node now (US end, a
        # break point, the DS end, or a control section), not a reach
        # range: an 'X-S' marker right at its own chainage/level, both on
        # the chart and (dotted purple tick, same convention as the break
        # markers) on the map canvas.
        if self._embank_selected_xs_key is not None:
            sel_handle = self._embank_any_node_handle(self._embank_selected_xs_key)
            if sel_handle is not None:
                node_ch, node_level = sel_handle[0], sel_handle[1]
                mk_x, = ax.plot([node_ch], [node_level], marker='x', markersize=13,
                                 markeredgewidth=3, color='#8E24AA', zorder=12)
                self._embank_invert_artists.append(mk_x)

                tg = self._embank_tick_geom(node_ch, 7.0)
                if tg is not None:
                    line_geom, label_pt, angle_deg = tg
                    xband = QgsRubberBand(self.canvas, _LINE_GEOM)
                    xband.setColor(QColor(142, 36, 170, 230))
                    xband.setWidth(3)
                    try:
                        xband.setLineStyle(Qt.PenStyle.DashDotLine)
                    except AttributeError:
                        xband.setLineStyle(Qt.DashDotLine)  # type: ignore[attr-defined]
                    xband.setToGeometry(line_geom, None)
                    self._embank_xs_map_band = xband
                    _xs_ann_list = []
                    self._add_embank_chainage_label(
                        label_pt, node_ch, angle_deg, color='#6A1B9A',
                        target=_xs_ann_list, text='X-S')
                    if _xs_ann_list:
                        self._embank_xs_annotation = _xs_ann_list[0]

        # 'Check Cross-Section' ghost marker(s) — a dashed purple vertical
        # line (+ label) at the chainage each OPEN Check Cross-Section
        # window is CURRENTLY stepped to, so it's obvious at a glance
        # where along the alignment that popped-out view is actually
        # looking. Moves live as Prev/Next is clicked or the reach/
        # spacing changes (see EmbankCheckXSDialog._draw_current_step,
        # which calls back into this method) and disappears when that
        # window closes. Distinct colour from the break-point/control-
        # section markers and from Layer Profile's own orange marker
        # highlight, since this is a different kind of thing (a transient
        # 'what am I looking at' pointer, not a real station).
        import matplotlib.transforms as _mt
        for _dlg in self._embank_check_xs_dialogs:
            _ch = _dlg.current_chainage()
            if _ch is None:
                continue
            g_ln = ax.axvline(x=_ch, color='#AA00FF', linewidth=1.6,
                               linestyle=(0, (4, 2)), alpha=0.85, zorder=9)
            _blend = _mt.blended_transform_factory(ax.transData, ax.transAxes)
            g_lbl = ax.text(
                _ch, 0.97, 'Check XS', transform=_blend, rotation=90,
                rotation_mode='anchor', fontsize=7, color='#AA00FF',
                ha='right', va='top', zorder=12,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          edgecolor='#AA00FF', alpha=0.85, linewidth=0.8))
            self._embank_invert_artists.extend([g_ln, g_lbl])

        # 'Show Difference' readout — always created (invisible) so
        # _on_chart_hover always has a live artist to update; content and
        # visibility are only ever set on hover, gated there by the
        # checkbox, exactly like the Layer Profile Windows' own ΔY text.
        self._embank_diff_ann = ax.text(
            0.99, 0.98, '', transform=ax.transAxes, fontsize=8,
            ha='right', va='top', color='#00695C', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      alpha=0.85, edgecolor='none'),
            visible=False, zorder=20,
        )
        self._embank_invert_artists.append(self._embank_diff_ann)

        try:
            ax.legend(fontsize=8, loc='best')
        except Exception:
            pass
        self.canvas_plot.draw_idle()

        # Keep the embedded Cross-Section preview live — this single hook
        # covers every caller of this method (break/level edits, typed
        # slope/level edits, adding/removing a break, fillet/chainage
        # setting changes, a fresh draw, AND raster row colour/linestyle
        # changes, since those already flow through _refresh_plot() ->
        # this method for tab 3) rather than needing each one wired up
        # individually. While a break/endpoint is actively being dragged,
        # only the cheap shape/level redraw runs (no raster re-sampling)
        # so dragging stays smooth; the real ground re-sample happens once
        # on release, when this method is called again with the drag
        # state already cleared.
        if self._embank_selected_xs_key is not None:
            if self._embank_break_drag_obj is not None or self._embank_endpoint_drag is not None:
                self._draw_embank_xs_preview()
            else:
                self._refresh_embank_xs()

    # ------------------------------------------------------------------ Save results / run


    def _default_output_folder(self):
        """Return ~/Downloads/ProfilePlot_YYYY-MM-DD, creating it if needed."""
        downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
        folder = os.path.join(downloads, 'ProfilePlot_' + datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(folder, exist_ok=True)
        return folder

    def _default_terrain_output_folder(self):
        """Like _default_output_folder, but for Channel Surface's own
        design Save/Open — named 'Add Channel' rather than 'ProfilePlot',
        since this isn't a profile plot export. Only used as the base
        when the 'Result Folder' field is left blank."""
        downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
        folder = os.path.join(downloads, 'Add Channel_' + datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(folder, exist_ok=True)
        return folder

    def _default_embank_output_folder(self):
        """Like _default_terrain_output_folder, but for Embankment
        Surface's own design Save/Open."""
        downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
        folder = os.path.join(downloads, 'Add Embankment_' + datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(folder, exist_ok=True)
        return folder

    def _browse_result_folder(self):
        start = self.csv_edit.text().strip() or os.path.join(os.path.expanduser('~'), 'Downloads')
        folder = QFileDialog.getExistingDirectory(self, 'Select Result Folder', start)
        if folder:
            self.csv_edit.setText(folder)

    def _cols_for_active_windows(self):
        """Return ordered column names visible across all enabled profile windows."""
        all_cols = list(self._profile_data_store.keys())
        shown, seen = [], set()
        all_p = [self.ax] + self._extra_axes
        for j, cfg in enumerate(self._win_cfgs):
            if j >= len(all_p):
                break
            if j > 0 and not cfg['enabled_cb'].isChecked():
                continue
            checked = cfg['col_combo'].checked_cols()
            for c in (checked if checked else all_cols):
                if c not in seen and c in self._profile_data_store:
                    shown.append(c); seen.add(c)
        return shown

    def _write_result_csv(self, folder, prefix):
        path = os.path.join(folder, f'{prefix}_data.csv')
        cols = self._cols_for_active_windows()
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['Chainage_m'] + cols)
            for i, ch in enumerate(self._profile_chainages):
                row = [ch]
                for col in cols:
                    v = self._profile_data_store[col][i]
                    row.append('' if v is None else round(v, 4))
                w.writerow(row)
        return path

    def _write_profile_shp(self, folder, prefix):
        path = os.path.join(folder, f'{prefix}_line.shp')
        writer = QgsVectorFileWriter(
            path, 'UTF-8', QgsFields(),
            QgsWkbTypes.LineString,
            QgsProject.instance().crs(),
            'ESRI Shapefile'
        )
        feat = QgsFeature()
        feat.setGeometry(self.profile_geom)
        writer.addFeature(feat)
        del writer
        return path

    def _write_markers_shp(self, folder, prefix):
        """Writes every point placed with the Marker sketch tool to its
        own 'marker' shapefile — one point feature per marker, in the
        project's CRS (the markers' own stored QgsPointXY values are
        already in that CRS — interpolated from self.profile_geom, which
        is itself in the project CRS)."""
        path = os.path.join(folder, f'{prefix}_marker.shp')
        fields = QgsFields()
        fields.append(QgsField('name', QVariant.String, len=50))
        fields.append(QgsField('chainage', QVariant.Double))
        writer = QgsVectorFileWriter(
            path, 'UTF-8', fields,
            QgsWkbTypes.Point,
            QgsProject.instance().crs(),
            'ESRI Shapefile'
        )
        for m in self._profile_markers:
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPointXY(m['point']))
            feat.setAttributes([m.get('name', ''), m.get('chainage', 0.0)])
            writer.addFeature(feat)
        del writer
        return path

    def _save_plot(self):
        if not MATPLOTLIB_AVAILABLE:
            return
        folder = self.csv_edit.text().strip() or self._default_output_folder()
        if not os.path.isdir(folder):
            QMessageBox.warning(self, 'Advanced Profile Tool',
                f'Result folder does not exist:\n{folder}')
            return
        prefix = 'profile_' + datetime.now().strftime('%Y%m%d_%H%M%S')
        saved, errors = [], []
        try:
            png_path = os.path.join(folder, f'{prefix}_plot.png')
            self.figure.savefig(png_path, dpi=150, bbox_inches='tight')
            saved.append(f'Plot:      {png_path}')
        except Exception as exc:
            errors.append(f'PNG failed: {exc}')
        try:
            saved.append(f'Data:      {self._write_result_csv(folder, prefix)}')
        except Exception as exc:
            errors.append(f'CSV failed: {exc}')
        if self.profile_geom is not None:
            try:
                saved.append(f'Line:      {self._write_profile_shp(folder, prefix)}')
            except Exception as exc:
                errors.append(f'SHP failed: {exc}')
        if self._profile_markers:
            try:
                saved.append(f'Markers:   {self._write_markers_shp(folder, prefix)}')
            except Exception as exc:
                errors.append(f'Marker SHP failed: {exc}')
        self.lbl_status.setText(f'Results saved to: {folder}')
        msg = '\n'.join(saved)
        if errors:
            msg += '\n\nErrors:\n' + '\n'.join(errors)
        QMessageBox.information(self, 'Results Saved', msg)

    def _run(self):
        if self._active_tab == _TAB_EMBANK:
            self._refresh_embank_profile()
            return
        if self._active_tab == _TAB_CHANNEL:
            self._refresh_terrain_profile()
            return
        if self.profile_geom is None:
            QMessageBox.warning(self, 'Advanced Profile Tool', 'Draw a profile line first.')
            return
        raster_entries, vector_entries, col_meta = self._collect_entries()
        if not raster_entries and not vector_entries:
            QMessageBox.warning(self, 'Advanced Profile Tool',
                'No layers selected. Check at least one raster or add a vector Z-field.')
            return
        if QgsProject.instance().crs().isGeographic():
            QMessageBox.critical(self, 'Advanced Profile Tool — CRS Error',
                'Project CRS is Geographic (degrees).\n'
                'Switch to a Projected Metric CRS first.')
            return
        self.btn_run.setEnabled(False)
        self.progress.setRange(0, 0)  # indeterminate while extracting
        self.lbl_status.setText('Refreshing…')
        QApplication.processEvents()
        try:
            profile_data, chainages = self._extract(
                self.profile_geom, raster_entries, vector_entries,
                self.interval_spin.value()
            )
        except Exception as exc:
            QMessageBox.critical(self, 'Advanced Profile Tool — Error', str(exc))
            self.btn_run.setEnabled(True)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.lbl_status.setText('Failed — see error dialog.')
            return

        self.lbl_status.setText(
            f'Done — {len(chainages)} pts, {len(profile_data)} layer(s). '
            f'Click "Save Plot" to export results.'
        )
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if MATPLOTLIB_AVAILABLE:
            self._plot(chainages, profile_data, col_meta)
            if self.btn_save_png is not None:
                self.btn_save_png.setEnabled(True)
        self.btn_run.setEnabled(True)

    # ------------------------------------------------------------------ extraction

    def _extract(self, line_geom, raster_entries, vector_entries, interval):
        if line_geom.isMultipart():
            parts = line_geom.asMultiPolyline()
            if not parts: raise ValueError('Profile line geometry is empty.')
            line_geom = QgsGeometry.fromPolylineXY(parts[0])

        total = line_geom.length()
        chainages, pts = [], []
        ch = 0.0
        while ch <= total + 1e-9:
            ech = min(ch, total)
            p = line_geom.interpolate(ech).asPoint()
            chainages.append(round(ech, 6)); pts.append(QgsPointXY(p))
            ch += interval
        if chainages and chainages[-1] < total - 1e-6:
            p = line_geom.interpolate(total).asPoint()
            chainages.append(round(total, 6)); pts.append(QgsPointXY(p))

        n = len(chainages); data = {}

        for rl, col_name in raster_entries:
            provider = rl.dataProvider()
            nodata = (provider.sourceNoDataValue(1)
                      if provider.sourceHasNoDataValue(1) else None)
            vals = []
            for i, pt in enumerate(pts):
                if i % 500 == 0: QApplication.processEvents()
                try:
                    v, ok = provider.sample(pt, 1)
                    if ok and (nodata is None or abs(v - nodata) > 1e-6):
                        vals.append(v)
                    else:
                        vals.append(None)
                except Exception:
                    vals.append(None)
            data[col_name] = vals

        lyr_fields = defaultdict(list); lyr_obj = {}
        for vl, field, col_name in vector_entries:
            lyr_fields[vl.id()].append((field, col_name)); lyr_obj[vl.id()] = vl

        # Pre-compute all profile points as a single numpy array (reused per layer)
        pts_xy = (np.array([(pt.x(), pt.y()) for pt in pts], dtype=float)
                  if (MATPLOTLIB_AVAILABLE and _mpath is not None and n > 0 and lyr_fields)
                  else None)

        # Bounding box of the entire profile line — used to pre-filter features at
        # the provider level before they reach Python (huge saving for large datasets).
        profile_bbox = line_geom.boundingBox()

        for lid, field_pairs in lyr_fields.items():
            vl = lyr_obj[lid]
            field_indices = [vl.fields().indexOf(f) for f, _ in field_pairs
                             if vl.fields().indexOf(f) >= 0]
            req = (QgsFeatureRequest()
                   .setSubsetOfAttributes(field_indices)
                   .setFilterRect(profile_bbox))
            vals_map = {col_name: [None] * n for _, col_name in field_pairs}

            geom_type_int = vl.geometryType()

            # ------------------------------------------------------------------
            # Fast path — polygon layers (e.g. ICM 2D mesh results)
            #
            # Two-phase strategy optimised for contiguous polygon meshes:
            #
            # Phase 1 — load once:
            #   Build a matplotlib Path per feature and insert into a spatial
            #   index.  Path is constructed once and reused for every profile
            #   point — O(N_features) ring-array allocations total.
            #   Holes handled via make_compound_path (winding-number rule;
            #   OGC ring orientation: exterior CCW, holes CW).
            #
            # Phase 2 — per profile point:
            #   Spatial-index bbox query returns only the 1–4 candidate
            #   elements that actually overlap the search radius around the
            #   current profile point.  Path.contains_point (single point,
            #   C-level) is called on each candidate until a hit is found,
            #   then we break immediately.  For a contiguous mesh (ICM) each
            #   point is in exactly one element → typically 1–2 C calls per
            #   profile point, not N_features calls.
            # ------------------------------------------------------------------
            if pts_xy is not None and geom_type_int == _POLYGON_GEOM:
                # ---- Phase 1: build Path cache + spatial index ---------------
                sp_idx     = QgsSpatialIndex()
                path_cache = {}          # fid -> ([Path, …], {col_name: z})
                feat_count = 0
                for feat in vl.getFeatures(req):
                    feat_count += 1
                    if feat_count % 200 == 0:
                        QApplication.processEvents()
                    geom = feat.geometry()
                    if geom.isEmpty():
                        continue
                    fld_vals = {}
                    for field, col_name in field_pairs:
                        raw = feat[field]
                        if raw in (None, ''):
                            continue
                        try:
                            fld_vals[col_name] = float(raw)
                        except (TypeError, ValueError):
                            pass
                    if not fld_vals:
                        continue
                    polys = (geom.asMultiPolygon() if geom.isMultipart()
                             else [geom.asPolygon()])
                    feat_paths = []
                    for poly in polys:
                        if not poly:
                            continue
                        ring_paths = []
                        for ring in poly:
                            if len(ring) < 3:
                                continue
                            ring_xy = np.array([(p.x(), p.y()) for p in ring],
                                               dtype=float)
                            ring_paths.append(_mpath.Path(ring_xy))
                        if not ring_paths:
                            continue
                        feat_paths.append(
                            _mpath.Path.make_compound_path(*ring_paths)
                            if len(ring_paths) > 1 else ring_paths[0]
                        )
                    if not feat_paths:
                        continue
                    path_cache[feat.id()] = (feat_paths, fld_vals)
                    sp_idx.insertFeature(feat)

                # ---- Phase 2: per-point lookup  ------------------------------
                # A zero-area point rectangle is sufficient: for any polygon
                # that geometrically contains point P, its bounding box also
                # contains P, so the spatial index always returns it.
                # No search-radius estimate needed — no overcounting risk.
                for i, pt in enumerate(pts):
                    if i % 500 == 0:
                        QApplication.processEvents()
                    x, y  = pt.x(), pt.y()
                    cands = sp_idx.intersects(QgsRectangle(x, y, x, y))
                    for fid in cands:
                        entry = path_cache.get(fid)
                        if entry is None:
                            continue
                        feat_paths, fld_vals = entry
                        for p in feat_paths:
                            if p.contains_point((x, y)):
                                for col_name, z in fld_vals.items():
                                    if vals_map[col_name][i] is None:
                                        vals_map[col_name][i] = z
                                break   # this fid contains the point
                        else:
                            continue    # no path in this fid matched
                        break           # first matching fid wins; mesh is contiguous

            # ------------------------------------------------------------------
            # Fast path — point layers: nearest profile-point assignment
            # ------------------------------------------------------------------
            elif pts_xy is not None and geom_type_int == _POINT_GEOM:
                snap_sq = (interval * 1.5) ** 2
                for feat in vl.getFeatures(req):
                    geom = feat.geometry()
                    if geom.isEmpty():
                        continue
                    fp_list = (geom.asMultiPoint() if geom.isMultipart()
                               else [geom.asPoint()])
                    for fp in fp_list:
                        dx = pts_xy[:, 0] - fp.x()
                        dy = pts_xy[:, 1] - fp.y()
                        d2 = dx * dx + dy * dy
                        i  = int(np.argmin(d2))
                        if d2[i] > snap_sq:
                            continue
                        for field, col_name in field_pairs:
                            raw = feat[field]
                            if raw in (None, ''):
                                continue
                            try:
                                z = float(raw)
                            except (TypeError, ValueError):
                                continue
                            if vals_map[col_name][i] is None:
                                vals_map[col_name][i] = z

            # ------------------------------------------------------------------
            # Fallback — line layers or no-numpy fallback
            # Uses half the sampling interval as snap tolerance so each profile
            # sample point picks up the nearest line/polygon feature within reach.
            # geometry.distance() is GEOS-based and correct for all geometry types.
            # ------------------------------------------------------------------
            else:
                snap_dist = interval * 0.5
                sp_idx    = QgsSpatialIndex()
                feat_map  = {}
                for f in vl.getFeatures(req):
                    feat_map[f.id()] = f
                    sp_idx.insertFeature(f)
                for i, pt in enumerate(pts):
                    if i % 500 == 0:
                        QApplication.processEvents()
                    x, y  = pt.x(), pt.y()
                    # Expand search rect by snap_dist so line features are caught
                    cands = sp_idx.intersects(
                        QgsRectangle(x - snap_dist, y - snap_dist,
                                     x + snap_dist, y + snap_dist)
                    )
                    if not cands:
                        continue
                    pt_geom  = QgsGeometry.fromPointXY(pt)
                    best_fid = None
                    best_d   = snap_dist + 1.0
                    for fid in cands:
                        d = feat_map[fid].geometry().distance(pt_geom)
                        if d <= snap_dist and d < best_d:
                            best_d   = d
                            best_fid = fid
                    if best_fid is None:
                        continue
                    cand = feat_map[best_fid]
                    for field, col_name in field_pairs:
                        raw = cand[field]
                        if raw not in (None, ''):
                            try:
                                vals_map[col_name][i] = float(raw)
                            except (TypeError, ValueError):
                                pass

            for _, col_name in field_pairs:
                data[col_name] = vals_map[col_name]

        return data, chainages

    # ------------------------------------------------------------------ cross-section window

    def _draw_layer_fill(self, xs_w, z_w, e_cap, col_str, hatch_pat, valid, cutfill_active):
        """Draw one layer's airspace fill from the profile up to e_cap.

        Returns the PolyCollection so the caller can store and later remove it.
        """
        clip      = valid & (z_w < e_cap)
        e_cap_arr = np.full(len(xs_w), e_cap)
        if cutfill_active:
            return self.ax.fill_between(
                xs_w, z_w, e_cap_arr, where=clip, interpolate=True,
                facecolor='none', edgecolor=col_str,
                hatch=hatch_pat, linewidth=0.5, alpha=0.85, zorder=6,
            )
        else:
            return self.ax.fill_between(
                xs_w, z_w, e_cap_arr, where=clip, interpolate=True,
                facecolor=col_str, edgecolor='none',
                linewidth=0, alpha=0.22, zorder=6,
            )

    def _draw_xsec_window(self, profile_data, col_meta, xs, d_d, z_top):
        """
        Draw the cross-section assessment rectangle and per-layer airspace shading.

        Rectangle bounds (auto-calculated by _calc_stage_area):
          Left / Right : xsec_from, xsec_to  (vertical lines — a-a and b-b)
          Bottom (d-d) : min(both layers in window)       — line 4-3
          Top    (1-2) : max(both layers in window) + 2 m — line 1-2

        Airspace shading per layer (above each profile up to the rectangle top):
          - Only cross-section enabled  → solid fill with layer colour, light alpha
          - Cross-section + cut/fill    → hatched (Layer 1 = ///, Layer 2 = ..)
        """
        ch_from = self.xsec_from.value()
        ch_to   = self.xsec_to.value()
        if ch_from >= ch_to or d_d is None or z_top is None or z_top <= d_d or _MplRect is None:
            return

        mask = (xs >= ch_from) & (xs <= ch_to)
        if not np.any(mask):
            return

        xs_w    = xs[mask]
        idx_win = np.where(mask)[0]
        cutfill_active = True  # always shade in cross-section view

        # --- Resolve layer keys --------------------------------------------------
        y1_key = self.cutfill_y1.currentText() if self.cutfill_y1.count() > 0 else ''
        y2_key = self.cutfill_y2.currentText() if self.cutfill_y2.count() > 0 else ''
        visible = [c for c in profile_data if col_meta.get(c, {}).get('visible', True)]
        if y1_key not in profile_data:
            y1_key = visible[0] if visible else ''
        if y2_key not in profile_data:
            y2_key = visible[1] if len(visible) > 1 else y1_key

        def _zw(key):
            vals = profile_data.get(key, [])
            return np.array(
                [vals[i] if (i < len(vals) and vals[i] is not None) else np.nan
                 for i in idx_win],
                dtype=float,
            )

        # --- Per-layer airspace fill (profile → z_top) ---------------------------
        # Layer 1: diagonal-line hatch (///)   when cut/fill also active
        # Layer 2: dot hatch (..)              when cut/fill also active
        # Both:    solid semi-transparent fill  when only xsec is active
        layer_pairs = []
        if y1_key in profile_data:
            c1 = col_meta.get(y1_key, {}).get('color', QColor('#2196F3'))
            layer_pairs.append((y1_key, _zw(y1_key), c1, '///'))
        if y2_key in profile_data and y2_key != y1_key:
            c2 = col_meta.get(y2_key, {}).get('color', QColor('#F44336'))
            layer_pairs.append((y2_key, _zw(y2_key), c2, '..'))

        # Store window arrays for dynamic hover redraw
        self._xsec_xs_w      = xs_w
        self._xsec_fill_data = []
        self._xsec_fill_cols = []   # ax.clear() already removed any previous fills

        for lyr_key, z_w, color, hatch_pat in layer_pairs:
            col_str = color.name() if hasattr(color, 'name') else '#2196F3'
            valid   = np.isfinite(z_w)
            short   = lyr_key if len(lyr_key) <= 20 else lyr_key[:19] + '…'
            lbl     = f'Area [{short}]'

            # Static legend proxy — persists even when fills are dynamically removed/re-added
            self.ax.plot([], [], 's', color=col_str, alpha=0.5,
                         markersize=9, label=lbl)

            # Store fill parameters for hover redraws
            self._xsec_fill_data.append({
                'z_w': z_w, 'col_str': col_str,
                'hatch': hatch_pat, 'valid': valid,
            })

            # Initial fill at full z_top (shows total airspace before any hover)
            fc = self._draw_layer_fill(xs_w, z_w, z_top, col_str,
                                       hatch_pat, valid, cutfill_active)
            self._xsec_fill_cols.append(fc)

        # --- Assessment rectangle outline ----------------------------------------
        rect = _MplRect(
            (ch_from, d_d), ch_to - ch_from, z_top - d_d,
            fill=False, linestyle=(0, (6, 4)),
            edgecolor='#546E7A', linewidth=1.4, alpha=0.9, zorder=8,
        )
        self.ax.add_patch(rect)

        # Vertical boundary lines (a-a left, b-b right)
        self.ax.axvline(ch_from, linestyle=':', color='#546E7A',
                        linewidth=1.0, alpha=0.7, zorder=7)
        self.ax.axvline(ch_to,   linestyle=':', color='#546E7A',
                        linewidth=1.0, alpha=0.7, zorder=7)

        # d-d horizontal line (base datum / lower limit)
        self.ax.hlines(d_d, ch_from, ch_to, colors='#455A64',
                       linestyles='--', linewidth=1.0, alpha=0.75, zorder=9)

        # Corner labels: a (top-left), b (top-right), d / d-d (bottom)
        kw = dict(fontsize=7, clip_on=False, zorder=11)
        self.ax.text(ch_from, z_top, ' a',  color='#546E7A', va='bottom', **kw)
        self.ax.text(ch_to,   z_top, 'b ',  color='#546E7A', va='bottom', ha='right', **kw)
        self.ax.text(ch_from, d_d,   ' d',  color='#455A64', va='top',    **kw)
        self.ax.text(ch_to,   d_d,   'd ',  color='#455A64', va='top',    ha='right', **kw)
        mid_ch = (ch_from + ch_to) / 2
        self.ax.text(mid_ch, d_d, 'd-d', color='#455A64',
                     va='top', ha='center', fontsize=7, zorder=11)

    # ------------------------------------------------------------------ independent airspace stage-area

    def _calc_stage_area(self, profile_data, col_meta):
        """
        Independent airspace calculus per layer (Riemann column summation).

        Layer 1 = cutfill_y1 selection, Layer 2 = cutfill_y2 selection.
        Falls back to first two visible layers when combos are unset.

        BOUNDARY DEFINITIONS (within window_from … window_to):
          Z_datum = min(both layers in window) — rectangle bottom  (d-d / line 4-3)
          Z_top   = max(both layers in window) + 2.0 m — rectangle top  (line 1-2)
          Z_cap   = user-controlled water-level slider (c-c, must lie inside rectangle)

        INDEPENDENT LAYER INTEGRATION:
          A_layer(E) = Σ_i max(0, E − Z_layer_i) × delta_s_i
          Each layer's rating curve starts from its own profile minimum so that a
          higher-sitting layer begins its Stage-Area curve detached from the datum.

        Returns:
          (curves_dict, z_datum, z_top)
            curves_dict: {col_name: (elevations_array, areas_array, QColor)}
            z_datum: float — bottom of the assessment rectangle
            z_top:   float — top  of the assessment rectangle (auto = max + 2 m)
          or (None, None, None) on failure.
        """
        if not self._profile_chainages:
            return None, None, None

        ch_from  = self.xsec_from.value()
        ch_to    = self.xsec_to.value()
        interval = self.interval_spin.value()

        if ch_from >= ch_to or interval <= 0:
            return None, None, None

        arr  = np.array(self._profile_chainages)
        mask = (arr >= ch_from) & (arr <= ch_to)
        indices = np.where(mask)[0]
        if len(indices) < 2:
            return None, None, None

        # --- Resolve Layer 1 and Layer 2 -----------------------------------------
        y1_key  = self.cutfill_y1.currentText() if self.cutfill_y1.count() > 0 else ''
        y2_key  = self.cutfill_y2.currentText() if self.cutfill_y2.count() > 0 else ''
        visible = [c for c, m in col_meta.items()
                   if m.get('visible', True) and c in profile_data]
        if y1_key not in profile_data:
            y1_key = visible[0] if visible else ''
        if y2_key not in profile_data:
            y2_key = visible[1] if len(visible) > 1 else y1_key
        if y1_key not in profile_data:
            return None, None, None

        def _win(key):
            vals = profile_data.get(key, [])
            return np.array(
                [vals[i] if (i < len(vals) and vals[i] is not None) else np.nan
                 for i in indices],
                dtype=float,
            )

        z1_w = _win(y1_key)
        z2_w = _win(y2_key)

        # --- Rectangle bounds: global min/max across BOTH layers in window -------
        all_finite = np.concatenate([z1_w[np.isfinite(z1_w)],
                                     z2_w[np.isfinite(z2_w)]])
        if len(all_finite) < 2:
            return None, None, None

        z_datum    = float(all_finite.min())       # line 4-3 (lower limit)
        z_both_max = float(all_finite.max())
        _diff = z_both_max - z_datum
        z_top = z_both_max + (0.2 * _diff if _diff > 0 else 0.5)  # line 1-2

        # --- Update UI ------------------------------------------------------------
        if hasattr(self, 'lbl_dd'):
            self.lbl_dd.setText(f'Base datum (d-d): {z_datum:.3f} m  [auto]')

        e_top = z_top   # rating curve spans from each layer's own floor up to z_top

        # --- Per-point step widths from actual chainage spacing in window ---------
        ch_win  = arr[mask]
        delta_s = np.gradient(ch_win) if len(ch_win) > 1 else np.array([interval])

        # --- Rating curve: independent airspace per layer -------------------------
        # A_layer(E) = Σ_i max(0, E − Z_layer_i) × delta_s_i
        # Each layer's elevation range starts from its own profile minimum so that
        # a higher-sitting layer begins its curve detached from the datum floor.
        curves = {}

        layer_pairs = [(y1_key, z1_w)]
        if y2_key != y1_key:
            layer_pairs.append((y2_key, z2_w))

        for key, z_w in layer_pairs:
            finite_z = z_w[np.isfinite(z_w)]
            if len(finite_z) < 2:
                continue
            z_layer_min = float(finite_z.min())
            elevations  = np.linspace(z_layer_min, e_top, 300)
            areas = np.array([
                float(np.nansum(np.maximum(0.0, E - z_w) * delta_s))
                for E in elevations
            ])
            color = col_meta.get(key, {}).get('color', QColor('#2196F3'))
            curves[key] = (elevations, areas, color)

        return curves, z_datum, z_top

    # ------------------------------------------------------------------ plot

    def _plot(self, chainages, profile_data, col_meta):
        if self._sketch_pending_specs is not None:
            _sketch_save = self._sketch_pending_specs
            self._sketch_pending_specs = None
        else:
            _sketch_save = self._sketch_serialise()
        self._reset_axes()
        xs   = np.array(chainages, dtype=float)
        keys = list(profile_data.keys())
        all_p = [self.ax] + self._extra_axes   # all active profile axes

        # Auto-update window spinbox ranges and smart overlap defaults
        if len(chainages) > 1:
            max_ch = chainages[-1]
            for spin in (self.xsec_from, self.xsec_to):
                spin.blockSignals(True)
                spin.setMaximum(max_ch)
                spin.blockSignals(False)

            if self.xsec_to.value() > max_ch or self.xsec_to.value() == 100.0:
                y1_k = self.cutfill_y1.currentText()
                y2_k = self.cutfill_y2.currentText()
                vis  = [c for c in profile_data
                        if col_meta.get(c, {}).get('visible', True)]
                if y1_k not in profile_data:
                    y1_k = vis[0] if vis else ''
                if y2_k not in profile_data:
                    y2_k = vis[1] if len(vis) > 1 else y1_k

                def _extent(key):
                    vals  = profile_data.get(key, [])
                    vidx  = [i for i, v in enumerate(vals) if v is not None]
                    return (chainages[vidx[0]], chainages[vidx[-1]]) if vidx else (None, None)

                starts, ends = [], []
                for k in dict.fromkeys([y1_k, y2_k]):
                    s, e = _extent(k)
                    if s is not None:
                        starts.append(s); ends.append(e)

                if starts and ends and max(starts) < min(ends):
                    self.xsec_from.blockSignals(True)
                    self.xsec_to.blockSignals(True)
                    self.xsec_from.setValue(max(starts))
                    self.xsec_to.setValue(min(ends))
                    self.xsec_from.blockSignals(False)
                    self.xsec_to.blockSignals(False)
                else:
                    self.xsec_to.blockSignals(True)
                    self.xsec_to.setValue(max_ch)
                    self.xsec_to.blockSignals(False)

        # Draw data on every active profile axis (filtered per-window assignment)
        self._plot_cols_per_ax = {}
        self._cf_ann_texts = {}
        for j, ax_j in enumerate(all_p):
            cfg_j    = self._win_cfgs[j] if j < len(self._win_cfgs) else None
            win_cols = set(cfg_j['col_combo'].checked_cols()) if cfg_j else set()  # empty = All

            # Per-window cut/fill shading
            if (cfg_j is not None
                    and cfg_j['cutfill_cb'].isChecked()
                    and cfg_j['cf_y1'].count() > 0 and cfg_j['cf_y2'].count() > 0):
                _y1k = cfg_j['cf_y1'].currentText()
                _y2k = cfg_j['cf_y2'].currentText()
                if _y1k in profile_data and _y2k in profile_data and _y1k != _y2k:
                    _cf1 = np.array([v if v is not None else np.nan
                                     for v in profile_data[_y1k]], dtype=float)
                    _cf2 = np.array([v if v is not None else np.nan
                                     for v in profile_data[_y2k]], dtype=float)
                    _cfv = np.isfinite(_cf1) & np.isfinite(_cf2)
                    ax_j.fill_between(xs, _cf1, _cf2, where=_cfv & (_cf2 > _cf1),
                                       color='#F44336', alpha=0.12, interpolate=True, zorder=1)
                    ax_j.fill_between(xs, _cf1, _cf2, where=_cfv & (_cf2 < _cf1),
                                       color='#1565C0', alpha=0.12, interpolate=True, zorder=1)

            # ΔY annotation (shown on hover, hidden until mouse enters)
            _ann = ax_j.text(
                0.99, 0.98, '', transform=ax_j.transAxes,
                fontsize=8, ha='right', va='top', color='#D32F2F',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          alpha=0.8, edgecolor='none'),
                visible=False, zorder=20,
            )
            self._cf_ann_texts[j] = _ann

            for col, vals in profile_data.items():
                if win_cols and col not in win_cols:
                    continue   # skip columns not assigned to this window
                meta = col_meta.get(col, {})
                if not meta.get('visible', True):
                    continue
                color = meta.get('color', QColor('#2196F3'))
                ls    = meta.get('linestyle', '-')
                lw    = meta.get('linewidth', 1.5)
                alpha = meta.get('alpha', 1.0)
                ys    = np.array([v if v is not None else np.nan for v in vals], dtype=float)
                ax_j.plot(xs, ys, label=_prune_mid(col), color=color.name(),
                          linewidth=lw, linestyle=ls, alpha=alpha)
                self._plot_cols_per_ax.setdefault(j, []).append(col)
            ax_j.legend(fontsize=8, loc='best')

        # Per-window Y limits — each window scales to its own visible columns only
        for j, ax_j in enumerate(all_p):
            cfg      = self._win_cfgs[j] if j < len(self._win_cfgs) else None
            auto     = (cfg is None) or cfg['auto_cb'].isChecked()
            if not auto:
                ymin_v = cfg['ymin'].value()
                ymax_v = cfg['ymax'].value()
                if ymin_v < ymax_v:
                    ax_j.set_ylim(ymin_v, ymax_v)
                    continue
            # Auto: gather only values from columns displayed in this window
            win_cols_j = set(cfg['col_combo'].checked_cols()) if cfg else set()
            _y_win = []
            for col, vals in profile_data.items():
                if win_cols_j and col not in win_cols_j:
                    continue
                _y_win.extend(v for v in vals if v is not None)
            if _y_win:
                _y_min_j = min(_y_win);  _y_max_j = max(_y_win)
                _span_j  = _y_max_j - _y_min_j
                _pad_j   = max(0.3, _span_j * 0.08)
                ax_j.set_ylim(_y_min_j - _pad_j, _y_max_j + _pad_j)

        xsec_active = self.xsec_cb.isChecked()
        d_d   = None
        z_top = None

        # Cross-section analysis (main axis / stage-area panel only)
        if xsec_active and self.ax_xsec is not None:
            curves, d_d, z_top = self._calc_stage_area(profile_data, col_meta)
            self._xsec_curves_store = curves or {}
            self._xsec_z_top        = z_top

            if curves:
                for col_key, (elevs, areas, color) in curves.items():
                    col_str = color.name() if hasattr(color, 'name') else str(color)
                    lbl = col_key if len(col_key) <= 16 else col_key[:15] + '…'
                    self.ax_xsec.plot(areas, elevs,
                                      color=col_str, linewidth=1.8, zorder=5, label=lbl)
                if d_d is not None:
                    self.ax_xsec.axhline(
                        y=d_d, color='#455A64', linestyle='--', linewidth=0.9, alpha=0.7
                    )

            self._xsec_hline = self.ax_xsec.axhline(
                y=0, color='#D32F2F', alpha=0.55, linewidth=1.0,
                linestyle='--', visible=False
            )

        # Cross-section window outline + cc horizontal hover lines
        if xsec_active:
            self._draw_xsec_window(profile_data, col_meta, xs, d_d, z_top)
            self._cc_hlines = [
                ax_j.axhline(y=0, color='#0288D1', alpha=0.75, linewidth=1.2,
                              linestyle='-.', visible=False)
                for ax_j in all_p
            ]
        else:
            self._cc_hlines         = []
            self._xsec_fill_data    = []
            self._xsec_fill_cols    = []
            self._xsec_curves_store = {}
            self._xsec_z_top        = None

        self.ax.set_xlim(left=0)

        if xsec_active and self.ax_xsec is not None:
            self.ax_xsec.set_ylim(self.ax.get_ylim())
            self.ax_xsec.autoscale_on = False

        # Check levels on all profile axes; text + area annotation on main axis only
        for cl in self._check_levels:
            for ax_j in all_p:
                ax_j.axhline(y=cl, color='#1B5E20', linestyle='--',
                              linewidth=1.2, alpha=0.85, zorder=7)
            self.ax.text(
                0.01, cl, f'CL {cl:.3f} m',
                transform=_blended_tf(self.ax.transAxes, self.ax.transData),
                fontsize=7, color='#1B5E20', va='bottom',
            )
            if self.ax_xsec is not None:
                self.ax_xsec.axhline(y=cl, color='#1B5E20', linestyle='--',
                                     linewidth=1.2, alpha=0.85, zorder=7)
                if self._xsec_curves_store:
                    a_strs = []
                    for ck, (elevs, areas, _c) in self._xsec_curves_store.items():
                        a = float(np.interp(cl, elevs, areas,
                                            left=0.0, right=float(areas[-1])))
                        a_strs.append(f'{a:.2f}')
                    self.ax_xsec.text(
                        0.04, cl, '  '.join(a_strs) + ' m²',
                        transform=_blended_tf(self.ax_xsec.transAxes,
                                              self.ax_xsec.transData),
                        fontsize=7, color='#1B5E20', va='bottom',
                    )

        # Floating area text in stage-area panel
        self._xsec_area_text = None
        if self.ax_xsec is not None:
            self._xsec_area_text = self.ax_xsec.text(
                0.04, 0, '',
                transform=_blended_tf(self.ax_xsec.transAxes, self.ax_xsec.transData),
                fontsize=8, color='#D32F2F', va='center', ha='left',
                bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                          alpha=0.80, edgecolor='none'),
                visible=False, zorder=20,
            )

        # Cursor lines on all profile axes (legends already added in the drawing loop)
        self._cursor_lines = [
            ax_j.axvline(x=0, color='#D32F2F', alpha=0.75, linewidth=1.2, visible=False)
            for ax_j in all_p
        ]

        # Store
        self._profile_chainages  = list(chainages)
        self._profile_data_store = {k: list(v) for k, v in profile_data.items()}
        self._xsec_dd = d_d

        self._update_win_col_combos(keys)

        self._do_tight_layout()
        self._sketch_restore(_sketch_save)
        self._refresh_chainage_cursors()
        self._refresh_profile_markers()
        # Sync cut/fill state to any open cross-section dialogs
        for _xsd in list(self._xs_dialogs):
            try:
                _xsd._refresh_cutfill()
            except Exception:
                pass
        self.canvas_plot.draw()


# ---------------------------------------------------------------------------
# Cross-section floating dialog
# ---------------------------------------------------------------------------

class XSectionDialog(QDialog):
    """Floating cross-section plot at a chainage cursor, perpendicular to the profile."""

    def __init__(self, parent_dock, cursor):
        super().__init__(parent_dock.iface.mainWindow())
        self.parent_dock = parent_dock
        self._canvas     = parent_dock.canvas   # stored at init; safe to use in closeEvent
        self.cursor      = cursor
        self.left_m      = 10.0
        self.right_m     = 10.0
        self._map_band            = None   # rubber band for the XS line on map canvas
        self._xs_map_line_color   = QColor(57, 255, 20)
        self._xs_map_line_width   = 3
        self._xs_map_line_opacity = 255
        self._xs_hover_band = None   # rubber band for moving hover point on map canvas
        # Sketch state
        self._xs_sketch_mode       = None
        self._xs_sketch_objects    = []
        self._xs_sketch_color      = '#E53935'
        self._xs_sketch_lw         = 2.0
        self._xs_sketch_ls         = '-'
        self._xs_sketch_pressed    = False
        self._xs_level_snap_art    = None
        self._xs_sketch_press_data = None
        self._xs_sketch_current    = None
        self._xs_sketch_pen_pts    = None
        self._xs_sketch_drag_info  = None
        self._xs_pen_poly_mode     = False
        self._xs_pen_poly_pts      = ([], [])
        self._xs_pen_poly_art      = None
        self._xs_sketch_btns       = {}
        # Cached data for hover interpolation
        self._xs_dist         = []
        self._xs_data         = {}
        self._xs_meta         = {}
        self._cf1_arr         = None
        self._cf2_arr         = None
        self._xs_visible_cols    = []   # visible cols in window 0 (for hover)
        self._xs_vcols_per_win   = []   # visible cols per window
        self._xs_extra_axes      = []   # extra XS axes (mirrors profile _extra_axes)
        # Scroll-zoom / middle-drag pan state — same convention as the
        # main chart's own _on_scroll_zoom/_on_mouse_press, scoped to
        # whichever stacked window's axis the mouse is actually over;
        # x stays in sync across every window on its own (they already
        # share x via sharex in _rebuild_xs_figure), y is independent.
        self._xs_pan_ax         = None
        self._xs_pan_press_px   = None
        self._xs_pan_xlim0      = None
        self._xs_pan_ylim0      = None
        self._xs_pan_transform  = None
        # Hover artists
        self._cursor_vline  = None        # single compat ref (= _cursor_vlines[0])
        self._cursor_vlines = []          # one per axis
        self._xpos_ann      = None
        self._cf_ann        = None        # compat ref (= _cf_anns[0])
        self._cf_anns       = []          # one per axis
        self._cf_pairs      = []          # [(cf1, cf2), ...] per window

        self._resample_timer = QTimer(self)
        self._resample_timer.setSingleShot(True)
        self._resample_timer.timeout.connect(self._do_resample)

        name = cursor.get('name', 'XS')
        ch   = cursor['chainage']
        self.setWindowTitle(f'Cross-Section  {name}   Ch: {ch:.1f} m')
        self.resize(680, 440)

        # Connect finished (fires on both Accept and Reject / X-button close)
        # as a belt-and-suspenders complement to closeEvent
        self.finished.connect(self._on_finished)

        self._setup_ui()
        self._update_map_band()
        self._do_resample()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel('Left extent:'))
        self.left_spin = QDoubleSpinBox()
        self.left_spin.setRange(0.5, 99999.0)
        self.left_spin.setValue(self.left_m)
        self.left_spin.setSuffix(' m')
        self.left_spin.setDecimals(1)
        self.left_spin.setFixedWidth(90)
        self.left_spin.valueChanged.connect(lambda: self._resample_timer.start(400))
        self.left_spin.editingFinished.connect(self._apply_extent)
        hdr.addWidget(self.left_spin)
        hdr.addSpacing(16)
        hdr.addWidget(QLabel('Right extent:'))
        self.right_spin = QDoubleSpinBox()
        self.right_spin.setRange(0.5, 99999.0)
        self.right_spin.setValue(self.right_m)
        self.right_spin.setSuffix(' m')
        self.right_spin.setDecimals(1)
        self.right_spin.setFixedWidth(90)
        self.right_spin.valueChanged.connect(lambda: self._resample_timer.start(400))
        self.right_spin.editingFinished.connect(self._apply_extent)
        hdr.addWidget(self.right_spin)
        hdr.addStretch()
        lbl_hint = QLabel('Press Enter or Tab after typing to apply extent')
        lbl_hint.setStyleSheet('font-size:9px; color:#90A4AE;')
        hdr.addWidget(lbl_hint)
        hdr.addSpacing(12)
        self._lbl_xy = QLabel('')
        self._lbl_xy.setMinimumWidth(160)
        self._lbl_xy.setAlignment(Qt.AlignmentFlag.AlignCenter
                                   if hasattr(Qt, 'AlignmentFlag')
                                   else Qt.AlignCenter)  # type: ignore
        self._lbl_xy.setStyleSheet(
            'font-family: monospace; font-size: 10px; padding: 1px 6px;'
            'border: 1px solid #1565C0; border-radius: 3px; color: #1565C0;'
            'background: white;')
        self._lbl_xy.setVisible(False)
        hdr.addWidget(self._lbl_xy)
        hdr.addSpacing(8)
        hdr.addSpacing(8)
        self._btn_xs_prev_us = QPushButton('◀ US')
        self._btn_xs_prev_us.setFixedWidth(52)
        self._btn_xs_prev_us.setToolTip(
            'Jump this window to the PREVIOUS chainage-cursor marker\n'
            'upstream of the one currently shown (same Left/Right extent\n'
            'kept as-is).'
        )
        self._btn_xs_prev_us.clicked.connect(lambda: self._step_to_marker(-1))
        hdr.addWidget(self._btn_xs_prev_us)
        self._btn_xs_next_ds = QPushButton('DS ▶')
        self._btn_xs_next_ds.setFixedWidth(52)
        self._btn_xs_next_ds.setToolTip(
            'Jump this window to the NEXT chainage-cursor marker\n'
            'downstream of the one currently shown (same Left/Right\n'
            'extent kept as-is).'
        )
        self._btn_xs_next_ds.clicked.connect(lambda: self._step_to_marker(1))
        hdr.addWidget(self._btn_xs_next_ds)

        btn_save = QPushButton('Save XS')
        btn_save.setFixedWidth(70)
        btn_save.setToolTip('Save cross-section plot as PNG')
        btn_save.clicked.connect(self._save_png)
        hdr.addWidget(btn_save)
        outer.addLayout(hdr)

        # ── Map XS line style ──────────────────────────────────────────────
        _mxs = QHBoxLayout()
        _mxs.setSpacing(4)
        _mxs.setContentsMargins(2, 0, 2, 0)
        _mxs.addWidget(QLabel('Map XS line:'))
        self._xs_lw_spin = QDoubleSpinBox()
        self._xs_lw_spin.setRange(0.5, 10.0)
        self._xs_lw_spin.setValue(3.0)
        self._xs_lw_spin.setSingleStep(0.5)
        self._xs_lw_spin.setDecimals(1)
        self._xs_lw_spin.setFixedWidth(58)
        self._xs_lw_spin.setToolTip('XS rubber band width on map canvas (px)')
        self._xs_lw_spin.valueChanged.connect(self._apply_xs_map_line_style)
        _mxs.addWidget(self._xs_lw_spin)
        _mxs.addWidget(QLabel('px'))
        _mxs.addSpacing(8)
        _mxs.addWidget(QLabel('Opacity:'))
        self._xs_op_spin = QSpinBox()
        self._xs_op_spin.setRange(5, 100)
        self._xs_op_spin.setValue(100)
        self._xs_op_spin.setSuffix('%')
        self._xs_op_spin.setFixedWidth(60)
        self._xs_op_spin.setToolTip('XS rubber band opacity on map canvas')
        self._xs_op_spin.valueChanged.connect(self._apply_xs_map_line_style)
        _mxs.addWidget(self._xs_op_spin)
        _mxs.addSpacing(8)
        self._xs_line_color_btn = _color_btn('#39FF14', 'Map XS line colour')
        self._xs_line_color_btn.clicked.connect(self._pick_xs_map_line_color)
        _mxs.addWidget(self._xs_line_color_btn)
        _mxs.addStretch()
        outer.addLayout(_mxs)

        # ── Sketch toolbar ────────────────────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            _sk = QHBoxLayout()
            _sk.setSpacing(3)
            _sk.setContentsMargins(2, 0, 2, 2)
            _tool_style = (
                'QPushButton{font-size:10px;border:1px solid #B0BEC5;'
                'border-radius:3px;background:#FAFAFA;padding:0 2px;}'
                'QPushButton:checked{background:#1565C0;color:white;border-color:#1565C0;}'
                'QPushButton:hover:!checked{background:#E3F2FD;}'
            )
            for _mode, _label, _tip in [
                ('pen',    'Pen',  'Freehand pen — drag.\nOr Ctrl+click a chain of '
                                    'points; double-click (or click without Ctrl) to finish.'),
                ('line',   'Line', 'Straight line — drag.\nOr Ctrl+click a chain of '
                                    'points; double-click (or click without Ctrl) to finish.'),
                ('arrow',  '→',    'Arrow — drag tail to head'),
                ('text',   'Text', 'Text annotation — click'),
                ('level',  '▽',    'Level marker — click to place a horizontal water-level line with inverted triangle'),
                ('rect',   'Rect', 'Rectangle — drag'),
                ('circle', '○',    'Ellipse — drag from centre'),
                ('eraser', '✕',    'Eraser — click or drag'),
                ('move',   '⇔',    'Move annotation — drag'),
                ('edit',   '✎',    'Edit — click an annotation to change its colour, thickness or text style'),
            ]:
                _sb = QPushButton(_label)
                _sb.setCheckable(True)
                _sb.setFixedSize(38, 22)
                _sb.setToolTip(_tip)
                _sb.setStyleSheet(_tool_style)
                _sb.clicked.connect(
                    lambda chk, m=_mode:
                    self._xs_sketch_activate(m) if chk else self._xs_sketch_deactivate()
                )
                _sk.addWidget(_sb)
                self._xs_sketch_btns[_mode] = _sb

            _sk.addSpacing(8)
            _lw = QDoubleSpinBox()
            _lw.setRange(0.5, 10.0); _lw.setValue(2.0); _lw.setSingleStep(0.5)
            _lw.setDecimals(1); _lw.setFixedWidth(58)
            _lw.setToolTip('Line thickness')
            _lw.valueChanged.connect(lambda v: setattr(self, '_xs_sketch_lw', v))
            _sk.addWidget(_lw)
            _lsc = QComboBox()
            _lsc.addItems(['Solid', 'Dashed', 'Dotted', 'DashDot'])
            _lsc.setFixedWidth(72)
            _lsc.setToolTip('Line style')
            _lsc_map = {'Solid': '-', 'Dashed': '--', 'Dotted': ':', 'DashDot': '-.'}
            _lsc.currentTextChanged.connect(
                lambda t: setattr(self, '_xs_sketch_ls', _lsc_map.get(t, '-')))
            _sk.addWidget(_lsc)
            _sk.addSpacing(6)
            for _c in ('#E53935', '#1565C0', '#2E7D32', '#F57F17',
                       '#6A1B9A', '#00695C', '#212121', '#FFFFFF'):
                _cb = QPushButton()
                _cb.setFixedSize(18, 18)
                _cb.setStyleSheet(
                    f'background:{_c};border:1px solid #888;border-radius:2px;')
                _cb.setToolTip(_c)
                _cb.clicked.connect(
                    lambda _chk, c=_c: setattr(self, '_xs_sketch_color', c))
                _sk.addWidget(_cb)
            _sk.addStretch()
            _clr = QPushButton('Clear')
            _clr.setFixedSize(45, 22)
            _clr.setStyleSheet(
                'font-size:10px;border:1px solid #EF9A9A;border-radius:3px;'
                'background:#FFF3F3;color:#C62828;')
            _clr.clicked.connect(self._xs_sketch_clear)
            _sk.addWidget(_clr)
            outer.addLayout(_sk)

        if MATPLOTLIB_AVAILABLE:
            self.figure    = Figure(figsize=(7, 4))
            self.ax        = self.figure.add_subplot(111)
            self.canvas_xs = FigureCanvas(self.figure)
            self.canvas_xs.setFocusPolicy(_STRONG_FOCUS)
            self.canvas_xs.installEventFilter(self.parent_dock._ctrl_tracker)
            self.canvas_xs.mpl_connect('motion_notify_event', self._on_hover)
            self.canvas_xs.mpl_connect('axes_leave_event',    self._on_leave)
            self.canvas_xs.mpl_connect('button_press_event',   self._xs_sketch_on_press)
            self.canvas_xs.mpl_connect('motion_notify_event',  self._xs_sketch_on_motion)
            self.canvas_xs.mpl_connect('button_release_event', self._xs_sketch_on_release)
            self.canvas_xs.mpl_connect('scroll_event',         self._on_xs_scroll_zoom)
            self.canvas_xs.mpl_connect('button_press_event',   self._on_xs_pan_press)
            self.canvas_xs.mpl_connect('motion_notify_event',  self._on_xs_pan_motion)
            self.canvas_xs.mpl_connect('button_release_event', self._on_xs_pan_release)
            outer.addWidget(self.canvas_xs, 1)
        else:
            outer.addWidget(QLabel('Matplotlib is not available.'))

    # ------------------------------------------------------------------ zoom/pan

    def _on_xs_scroll_zoom(self, event):
        """Scroll-to-zoom, same convention as the main chart's own
        _on_scroll_zoom — scoped to whichever stacked window's axis is
        under the cursor. X stays in sync across every window on its own
        (they already share x via sharex, set up in _rebuild_xs_figure);
        Y zooms independently per window."""
        all_xs = [self.ax] + self._xs_extra_axes
        if event.inaxes not in all_xs or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y = event.xdata, event.ydata
        ax = event.inaxes
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        ax.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        self.canvas_xs.draw_idle()

    def _on_xs_pan_press(self, event):
        """Middle-mouse-drag pan — same 'hold the scroll wheel and drag'
        gesture as the main chart and the embedded Modify Terrain
        preview, own state so it never interferes with the sketch tool
        (left-click only) or hover (no button held)."""
        if event.button != 2:
            return
        all_xs = [self.ax] + self._xs_extra_axes
        if event.inaxes not in all_xs:
            return
        self._xs_pan_ax        = event.inaxes
        self._xs_pan_press_px  = (event.x, event.y)
        self._xs_pan_xlim0     = self._xs_pan_ax.get_xlim()
        self._xs_pan_ylim0     = self._xs_pan_ax.get_ylim()
        self._xs_pan_transform = self._xs_pan_ax.transData.inverted().frozen()

    def _on_xs_pan_motion(self, event):
        if self._xs_pan_press_px is None or self._xs_pan_transform is None:
            return
        if event.x is None or event.y is None:
            return
        x0d, y0d = self._xs_pan_transform.transform(self._xs_pan_press_px)
        x1d, y1d = self._xs_pan_transform.transform((event.x, event.y))
        dx, dy = x0d - x1d, y0d - y1d
        xl0, xr0 = self._xs_pan_xlim0
        yb0, yt0 = self._xs_pan_ylim0
        self._xs_pan_ax.set_xlim([xl0 + dx, xr0 + dx])
        self._xs_pan_ax.set_ylim([yb0 + dy, yt0 + dy])
        self.canvas_xs.draw_idle()

    def _on_xs_pan_release(self, event):
        if event.button == 2:
            self._xs_pan_ax = self._xs_pan_press_px = self._xs_pan_xlim0 = \
                self._xs_pan_ylim0 = self._xs_pan_transform = None

    # ------------------------------------------------------------------ extent

    def _step_to_marker(self, direction):
        """Jumps THIS window to the next placed chainage-cursor marker
        upstream (direction=-1, 'US' button) or downstream (direction=1,
        'DS' button) of whichever one it's currently showing — paging
        between the user's OWN markers (self.parent_dock._ch_cursors),
        not sampling at some arbitrary step distance. self.left_m/
        right_m (the Left/Right extent) are left exactly as they are —
        per explicit request, whatever extent was set for the first
        section shown carries over to every marker paged to afterwards."""
        cursors = sorted(self.parent_dock._ch_cursors, key=lambda c: c['chainage'])
        cur_ch = self.cursor['chainage']
        if direction > 0:
            candidates = [c for c in cursors if c['chainage'] > cur_ch + 1e-9]
            target = candidates[0] if candidates else None
            end_msg = 'This is already the most downstream marker.'
        else:
            candidates = [c for c in cursors if c['chainage'] < cur_ch - 1e-9]
            target = candidates[-1] if candidates else None
            end_msg = 'This is already the most upstream marker.'
        if target is None:
            QMessageBox.information(self, 'Cross-Section', end_msg)
            return
        self.cursor = target
        name = target.get('name', 'XS')
        ch = target['chainage']
        self.setWindowTitle(f'Cross-Section  {name}   Ch: {ch:.1f} m')
        self._update_map_band()
        self._do_resample()
        # Moves this window's highlight on the main chart to the new
        # marker (see _refresh_chainage_cursors' active_ids).
        self.parent_dock._refresh_main_chart_xs_highlight()

    def _update_xs_nav_buttons(self):
        """Enables/disables the US/DS paging buttons based on whether a
        marker actually exists further in that direction — called after
        every resample so it stays correct if markers are added/removed
        elsewhere while this window is open."""
        cursors = self.parent_dock._ch_cursors
        cur_ch = self.cursor['chainage']
        self._btn_xs_prev_us.setEnabled(any(c['chainage'] < cur_ch - 1e-9 for c in cursors))
        self._btn_xs_next_ds.setEnabled(any(c['chainage'] > cur_ch + 1e-9 for c in cursors))

    def _apply_extent(self):
        """Immediate apply — called on editingFinished (Enter / Tab)."""
        self._resample_timer.stop()
        self.left_m  = self.left_spin.value()
        self.right_m = self.right_spin.value()
        self._update_map_band()
        self._do_resample()

    # ------------------------------------------------------------------ save

    def _save_png(self):
        if not MATPLOTLIB_AVAILABLE:
            return
        name = self.cursor.get('name', 'XS')
        ch   = self.cursor['chainage']
        out_folder = self.parent_dock._default_output_folder()
        default = os.path.join(out_folder, f'{name}_Ch{ch:.0f}.png')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Cross-Section Plot', default,
            'PNG Image (*.png);;All Files (*)')
        if path:
            try:
                self.figure.savefig(path, dpi=150, bbox_inches='tight')
            except Exception as e:
                QMessageBox.warning(self, 'Save Failed', str(e))

    # ------------------------------------------------------------------ map band

    def _pick_xs_map_line_color(self):
        c = QColorDialog.getColor(self._xs_map_line_color, self)
        if c.isValid():
            self._xs_map_line_color = c
            self._xs_line_color_btn.setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;')
            self._apply_xs_map_line_style()

    def _apply_xs_map_line_style(self):
        """Apply width/opacity/colour spinbox values to the XS rubber band."""
        self._xs_map_line_width   = self._xs_lw_spin.value()
        self._xs_map_line_opacity = int(self._xs_op_spin.value() * 255 / 100)
        if self._map_band:
            c = QColor(self._xs_map_line_color); c.setAlpha(self._xs_map_line_opacity)
            self._map_band.setColor(c)
            self._map_band.setWidth(int(round(self._xs_map_line_width)))
            self._canvas.refresh()

    def _update_map_band(self):
        self._clear_map_band()
        try:
            geom = self.parent_dock._make_perp_line_geom(
                self.cursor['chainage'], self.left_m, self.right_m)
            if geom is None:
                return
            self._map_band = QgsRubberBand(self._canvas, _LINE_GEOM)
            _xc = QColor(self._xs_map_line_color); _xc.setAlpha(self._xs_map_line_opacity)
            self._map_band.setColor(_xc)
            self._map_band.setWidth(int(round(self._xs_map_line_width)))
            try:
                self._map_band.setLineStyle(Qt.PenStyle.DashLine)
            except AttributeError:
                self._map_band.setLineStyle(Qt.DashLine)  # type: ignore[attr-defined]
            self._map_band.setToGeometry(geom, None)
        except Exception:
            pass

    @staticmethod
    def _delete_band(band):
        """Forcefully destroy a QgsRubberBand — removes it from canvas immediately."""
        if band is None:
            return
        try:
            import sip
            sip.delete(band)
            return
        except Exception:
            pass
        # Fallback: reset geometry (makes it invisible) + hide
        for fn in (lambda: band.reset(), lambda: band.setVisible(False)):
            try:
                fn()
            except Exception:
                pass

    def _clear_map_band(self):
        band, self._map_band = self._map_band, None
        self._delete_band(band)

    def _clear_xs_hover_band(self):
        band, self._xs_hover_band = self._xs_hover_band, None
        self._delete_band(band)

    def _on_finished(self, _result=None):
        """Called when the dialog closes (either X-button or programmatic)."""
        self._resample_timer.stop()
        self._clear_map_band()
        self._clear_xs_hover_band()

    def closeEvent(self, event):
        self._on_finished()
        super().closeEvent(event)

    # ------------------------------------------------------------------ resample

    def _do_resample(self):
        self.left_m  = self.left_spin.value()
        self.right_m = self.right_spin.value()
        self._update_map_band()          # keep map canvas line in sync with both spinboxes
        if not MATPLOTLIB_AVAILABLE:
            return
        try:
            geom = self.parent_dock._make_perp_line_geom(
                self.cursor['chainage'], self.left_m, self.right_m)
            if geom is None:
                return
            raster_entries, vector_entries, col_meta = \
                self.parent_dock._collect_entries()
            if not raster_entries and not vector_entries:
                self._xs_dist = []; self._xs_data = {}; self._xs_meta = {}
                self._cf1_arr = None; self._cf2_arr = None
                self._draw_xs(); return
            interval = self.parent_dock.interval_spin.value()
            data, chainages = self.parent_dock._extract(
                geom, raster_entries, vector_entries, interval)
            xs_dist = [ch - self.left_m for ch in chainages]

            self._xs_dist = xs_dist
            self._xs_data = data
            self._xs_meta = col_meta
            self._refresh_cutfill()
        except Exception:
            pass
        self._update_xs_nav_buttons()

    def _refresh_cutfill(self):
        """Re-evaluate cut/fill for all active windows, refresh meta, redraw."""
        if not MATPLOTLIB_AVAILABLE:
            return
        try:
            # Refresh style meta (linewidth, alpha, linestyle) without re-extracting data
            try:
                _, _, col_meta = self.parent_dock._collect_entries()
                self._xs_meta = col_meta
            except Exception:
                pass
            pdock    = self.parent_dock
            data     = self._xs_data
            n_wins   = pdock._n_active_wins()
            cf_pairs = []
            for j in range(n_wins):
                cfg = pdock._win_cfgs[j] if j < len(pdock._win_cfgs) else None
                cf1, cf2 = None, None
                if (cfg is not None
                        and cfg['cutfill_cb'].isChecked()
                        and cfg['cf_y1'].count() > 0 and cfg['cf_y2'].count() > 0):
                    y1k = cfg['cf_y1'].currentText()
                    y2k = cfg['cf_y2'].currentText()
                    if y1k in data and y2k in data and y1k != y2k:
                        cf1 = np.array([v if v is not None else np.nan
                                        for v in data[y1k]], dtype=float)
                        cf2 = np.array([v if v is not None else np.nan
                                        for v in data[y2k]], dtype=float)
                cf_pairs.append((cf1, cf2))
            self._cf_pairs  = cf_pairs
            # Backward-compat single refs (window 0)
            self._cf1_arr, self._cf2_arr = cf_pairs[0] if cf_pairs else (None, None)
            self._draw_xs()
        except Exception:
            pass

    # ------------------------------------------------------------------ draw

    def _rebuild_xs_figure(self, n_wins):
        """Rebuild the XS figure with n_wins stacked subplots sharing the x-axis."""
        self.figure.clear()
        if n_wins == 1:
            self.ax = self.figure.add_subplot(111)
            self._xs_extra_axes = []
        else:
            gs = self.figure.add_gridspec(n_wins, 1, hspace=0.40)
            self.ax = self.figure.add_subplot(gs[0])
            self._xs_extra_axes = [
                self.figure.add_subplot(gs[i], sharex=self.ax)
                for i in range(1, n_wins)
            ]
        self._cursor_vlines     = []
        self._cf_anns           = []
        self._xs_level_snap_art = None   # recreated by level tool on next activation

    def _draw_xs(self):
        pdock  = self.parent_dock
        n_wins = pdock._n_active_wins()

        # Rebuild axes if window count changed
        all_xs = [self.ax] + self._xs_extra_axes
        if n_wins != len(all_xs):
            _saved_sketches = list(self._xs_sketch_objects)
            self._rebuild_xs_figure(n_wins)
        else:
            _saved_sketches = list(self._xs_sketch_objects)

        all_xs = [self.ax] + self._xs_extra_axes

        # Clear all axes
        for _ax in all_xs:
            _ax.clear()

        self._cursor_vlines   = []
        self._cf_anns         = []
        self._xpos_ann        = None
        self._xs_vcols_per_win = [[] for _ in range(n_wins)]

        xs       = np.array(self._xs_dist, dtype=float) if self._xs_dist else np.array([])
        data     = self._xs_data
        col_meta = self._xs_meta
        import matplotlib.transforms as _mt

        for j, ax_j in enumerate(all_xs):
            cfg_j     = pdock._win_cfgs[j] if j < len(pdock._win_cfgs) else None
            win_cols  = set(cfg_j['col_combo'].checked_cols()) if cfg_j else set()
            win_label = cfg_j['name_edit'].text().strip() if cfg_j else ''

            vis_cols_j = []
            for col, vals in data.items():
                if win_cols and col not in win_cols:
                    continue
                meta = col_meta.get(col, {})
                if not meta.get('visible', True):
                    continue
                color = meta.get('color', QColor('#2196F3'))
                if isinstance(color, QColor):
                    color = color.name()
                ls    = meta.get('linestyle', '-')
                lw    = meta.get('linewidth', 1.5)
                alpha = meta.get('alpha', 1.0)
                ys = np.array([v if v is not None else np.nan for v in vals], dtype=float)
                ax_j.plot(xs, ys, label=col, color=color, linewidth=lw, linestyle=ls, alpha=alpha)
                vis_cols_j.append(col)
            self._xs_vcols_per_win[j] = vis_cols_j

            # Cut/fill shading for this window
            if j < len(self._cf_pairs):
                cf1, cf2 = self._cf_pairs[j]
                if cf1 is not None and cf2 is not None and len(xs) == len(cf1):
                    valid = np.isfinite(cf1) & np.isfinite(cf2)
                    ax_j.fill_between(xs, cf1, cf2, where=valid & (cf2 > cf1),
                                      color='#F44336', alpha=0.20, interpolate=True,
                                      label='_nolegend_')
                    ax_j.fill_between(xs, cf1, cf2, where=valid & (cf2 < cf1),
                                      color='#1565C0', alpha=0.20, interpolate=True,
                                      label='_nolegend_')

            # Centre line
            ax_j.axvline(x=0, color='#212121', linewidth=0.8, linestyle='-.', alpha=0.7, zorder=5)

            ax_j.set_ylabel(win_label or 'Z value', fontsize=9)
            ax_j.grid(True, alpha=0.3)
            if vis_cols_j:
                ax_j.legend(fontsize=8, loc='best')

            # Hover cursor vline (hidden until mouse enters)
            vl = ax_j.axvline(x=0, color='#D32F2F', linewidth=0.8,
                               linestyle='--', alpha=0.0, zorder=15)
            self._cursor_vlines.append(vl)

            # ΔY annotation per axis
            cf_ann = ax_j.text(
                0.98, 0.97, '', transform=ax_j.transAxes,
                fontsize=8, ha='right', va='top', color='#D32F2F',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                          alpha=0.88, edgecolor='none'),
                visible=False, zorder=20)
            self._cf_anns.append(cf_ann)

        # Left / Right direction labels on bottom axis only
        if len(xs) > 1:
            blend_bot = _mt.blended_transform_factory(all_xs[-1].transData,
                                                      all_xs[-1].transAxes)
            all_xs[-1].text(-self.left_m * 0.98, 0.02, '← Left',
                            transform=blend_bot, fontsize=7.5, color='#546E7A',
                            ha='left', va='bottom')
            all_xs[-1].text(self.right_m * 0.98, 0.02, '+ Right →',
                            transform=blend_bot, fontsize=7.5, color='#546E7A',
                            ha='right', va='bottom')

        # Title on top axis, X-label on bottom axis
        name = self.cursor.get('name', 'XS')
        ch   = self.cursor['chainage']
        self.ax.set_title(f'{name}   Ch: {ch:.1f} m', fontsize=10, fontweight='bold')
        all_xs[-1].set_xlabel('Distance from centre  [ − Left  |  + Right ]  [m]', fontsize=9)

        # Backward-compat single refs
        self._cursor_vline = self._cursor_vlines[0] if self._cursor_vlines else None
        self._cf_ann       = self._cf_anns[0]       if self._cf_anns       else None
        self._xs_visible_cols = self._xs_vcols_per_win[0] if self._xs_vcols_per_win else []

        # Re-attach sketch objects to first axis after clear
        for _obj in _saved_sketches:
            try:
                if isinstance(_obj, (_MplRect, _MplEllipse)):
                    self.ax.add_patch(_obj)
                else:
                    self.ax.add_artist(_obj)
            except Exception:
                pass
        self._xs_sketch_objects = _saved_sketches

        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas_xs.draw()

    # ------------------------------------------------------------------ hover

    def _on_hover(self, event):
        # Level tool: update persistent snap indicator (+) in place
        if self._xs_sketch_mode == 'level':
            if (self._xs_level_snap_art is not None
                    and event.inaxes == self.ax and event.xdata is not None):
                snapped_x, snapped_y, snap_col, snap_type = _snap_level(self.ax, event.xdata, event.ydata)
                col = snap_col or '#888888'
                self._xs_level_snap_art.set_data([snapped_x], [snapped_y])
                if snap_type == 'vertex':
                    self._xs_level_snap_art.set_marker('s')
                    self._xs_level_snap_art.set_markersize(8)
                    self._xs_level_snap_art.set_markerfacecolor('none')
                else:
                    self._xs_level_snap_art.set_marker('+')
                    self._xs_level_snap_art.set_markersize(13)
                self._xs_level_snap_art.set_markeredgecolor(col)
                self._xs_level_snap_art.set_alpha(1.0 if snap_col else 0.5)
                self._xs_level_snap_art.set_visible(True)
                self.canvas_xs.draw_idle()
            return

        if self._xs_sketch_mode is not None:   # other sketch modes suppress hover display
            return
        all_xs = [self.ax] + self._xs_extra_axes
        if (event.inaxes not in all_xs or not self._xs_dist
                or not self._cursor_vlines or event.xdata is None):
            return
        j  = all_xs.index(event.inaxes)   # which window is being hovered
        x  = event.xdata
        xs = np.array(self._xs_dist, dtype=float)

        # Move ALL cursor vlines to same x
        for vl in self._cursor_vlines:
            vl.set_xdata([x, x])
            vl.set_alpha(0.7)

        # Update top-bar (x, y) label using first visible col of hovered window
        if hasattr(self, '_lbl_xy'):
            vis_j  = self._xs_vcols_per_win[j] if j < len(self._xs_vcols_per_win) else []
            _y_disp = ''
            if vis_j:
                _col0 = vis_j[0]
                _ys0  = np.array(
                    [v if v is not None else np.nan
                     for v in self._xs_data.get(_col0, [])], dtype=float)
                if len(_ys0) == len(xs):
                    _yv = float(np.interp(x, xs, _ys0))
                    if np.isfinite(_yv):
                        _y_disp = f'{_yv:.3f}'
            _x_disp = f'{x:+.2f}'
            self._lbl_xy.setText(
                f'(x, y) = ({_x_disp}, {_y_disp})' if _y_disp else f'x = {_x_disp} m')
            self._lbl_xy.setVisible(True)

        # Update legend on every axis with interpolated values
        for jj, ax_jj in enumerate(all_xs):
            lgd = ax_jj.get_legend()
            if not lgd:
                continue
            vis_jj = self._xs_vcols_per_win[jj] if jj < len(self._xs_vcols_per_win) else []
            texts  = lgd.get_texts()
            for i, col in enumerate(vis_jj):
                if i >= len(texts):
                    break
                vals = self._xs_data.get(col, [])
                ys   = np.array([v if v is not None else np.nan for v in vals], dtype=float)
                v    = float(np.interp(x, xs, ys))
                texts[i].set_text(f'{col} [{v:.3f}]' if np.isfinite(v) else col)

        # ΔY per window
        for jj, cf_ann_jj in enumerate(self._cf_anns):
            if jj < len(self._cf_pairs):
                cf1, cf2 = self._cf_pairs[jj]
                if cf1 is not None and cf2 is not None and cf_ann_jj is not None:
                    dy = float(np.interp(x, xs, cf2 - cf1))
                    if np.isfinite(dy):
                        cf_ann_jj.set_text(f'ΔY: {dy:+.3f} m')
                        cf_ann_jj.set_visible(True)

        self.canvas_xs.draw_idle()

        # Move hover point on map canvas along the XS line
        try:
            geom = self.parent_dock._make_perp_line_geom(
                self.cursor['chainage'], self.left_m, self.right_m)
            if geom is not None:
                dist_along = max(0.0, self.left_m + x)   # x is signed; left_m + x = offset from left end
                pt = geom.interpolate(dist_along).asPoint()
                if self._xs_hover_band is None:
                    self._xs_hover_band = QgsRubberBand(
                        self._canvas, _POINT_GEOM)
                    self._xs_hover_band.setIcon(_ICON_CIRCLE)
                    self._xs_hover_band.setIconSize(10)
                    self._xs_hover_band.setColor(QColor(211, 47, 47, 230))
                    try:
                        self._xs_hover_band.setFillColor(QColor(211, 47, 47, 180))
                    except Exception:
                        pass
                self._xs_hover_band.reset(_POINT_GEOM)
                self._xs_hover_band.addPoint(QgsPointXY(pt))
        except Exception:
            pass

    def _on_leave(self, event):
        if self._xs_level_snap_art is not None:
            self._xs_level_snap_art.set_visible(False)
        for vl in self._cursor_vlines:
            vl.set_alpha(0.0)
        for cf_ann in self._cf_anns:
            if cf_ann is not None:
                cf_ann.set_visible(False)
        if hasattr(self, '_lbl_xy'):
            self._lbl_xy.setVisible(False)
        # Reset all legend texts to plain column names
        all_xs = [self.ax] + self._xs_extra_axes
        for jj, ax_jj in enumerate(all_xs):
            lgd = ax_jj.get_legend()
            if not lgd:
                continue
            vis_jj = self._xs_vcols_per_win[jj] if jj < len(self._xs_vcols_per_win) else []
            texts  = lgd.get_texts()
            for i, col in enumerate(vis_jj):
                if i < len(texts):
                    texts[i].set_text(col)
        # Hide hover point on map canvas
        if self._xs_hover_band is not None:
            try:
                self._xs_hover_band.reset(_POINT_GEOM)
            except Exception:
                pass
        self.canvas_xs.draw_idle()

    # ------------------------------------------------------------------ sketch

    def _xs_sketch_activate(self, mode):
        for m, b in self._xs_sketch_btns.items():
            if m != mode:
                b.setChecked(False)
        self._xs_sketch_mode = mode
        if mode == 'level':
            if self._xs_level_snap_art is not None:
                try: self._xs_level_snap_art.remove()
                except Exception: pass
                self._xs_level_snap_art = None
            if MATPLOTLIB_AVAILABLE and hasattr(self, 'ax'):
                try:
                    self._xs_level_snap_art, = self.ax.plot(
                        [], [], marker='+', markersize=13, markeredgewidth=1.5,
                        markerfacecolor='none', markeredgecolor='#888888',
                        linestyle='none', zorder=30, clip_on=False, visible=False)
                except Exception:
                    pass
        try:
            _cs = Qt.CursorShape
            _cur = {
                'move':   _cs.SizeAllCursor,
                'eraser': _cs.ForbiddenCursor,
                'edit':   _cs.PointingHandCursor,
            }.get(mode, _cs.CrossCursor)
        except AttributeError:
            _cur = {
                'move':   Qt.SizeAllCursor,    # type: ignore
                'eraser': Qt.ForbiddenCursor,  # type: ignore
                'edit':   Qt.PointingHandCursor,  # type: ignore
            }.get(mode, Qt.CrossCursor)  # type: ignore
        self.canvas_xs.setCursor(_cur)

    def _xs_sketch_deactivate(self):
        for b in self._xs_sketch_btns.values():
            b.setChecked(False)
        self._xs_sketch_mode = None
        self._xs_sketch_pressed = False
        self._xs_sketch_current = None
        if self._xs_pen_poly_mode:
            self._xs_pen_poly_mode = False
            self._xs_pen_poly_pts  = ([], [])
            self._xs_pen_poly_art  = None
        if self._xs_level_snap_art is not None:
            try:
                self._xs_level_snap_art.remove()
            except Exception:
                pass
            self._xs_level_snap_art = None
            self.canvas_xs.draw_idle()
        self.canvas_xs.unsetCursor()

    def _xs_sketch_clear(self):
        for obj in self._xs_sketch_objects:
            try: obj.remove()
            except Exception: pass
        self._xs_sketch_objects = []
        self.canvas_xs.draw_idle()

    def _xs_sketch_erase_at(self, event):
        for obj in reversed(self._xs_sketch_objects):
            try:
                hit, _ = obj.contains(event)
            except Exception:
                hit = False
            if hit:
                try: obj.remove()
                except Exception: pass
                self._xs_sketch_objects.remove(obj)
                self.canvas_xs.draw_idle()
                break

    def _xs_sketch_on_press(self, event):
        if self._xs_sketch_mode is None or event.button != 1:
            return
        if event.inaxes != self.ax or event.xdata is None:
            return
        self._xs_sketch_pressed = True
        fx, fy = event.xdata, event.ydata
        self._xs_sketch_press_data = (fx, fy)
        ax = self.ax

        if self._xs_sketch_mode in ('pen', 'line'):
            ctrl = False
            try:
                ctrl = self.parent_dock._ctrl_tracker.ctrl_held
                if not ctrl:
                    ctrl = bool(QApplication.queryKeyboardModifiers() & _CTRL_MOD)
            except Exception:
                ctrl = False

            if self._xs_pen_poly_mode:
                # Already building a click-polyline — a double-click OR a
                # plain click without Ctrl both finish it now; Ctrl+click
                # keeps extending it.
                if event.dblclick or not ctrl:
                    self._xs_pen_poly_mode  = False
                    self._xs_pen_poly_pts   = ([], [])
                    self._xs_pen_poly_art   = None
                    self._xs_sketch_pressed = False
                    self.canvas_xs.draw_idle()
                    return
                self._xs_pen_poly_pts[0].append(fx)
                self._xs_pen_poly_pts[1].append(fy)
                self._xs_pen_poly_art.set_data(*self._xs_pen_poly_pts)
                self._xs_sketch_pressed = False
                self.canvas_xs.draw_idle()
                return

            if ctrl:
                self._xs_sketch_pressed = False
                self._xs_pen_poly_mode  = True
                self._xs_pen_poly_pts   = ([fx], [fy])
                line, = ax.plot(
                    [fx, fx], [fy, fy],
                    color=self._xs_sketch_color, linewidth=self._xs_sketch_lw,
                    linestyle=self._xs_sketch_ls,
                    solid_capstyle='round', solid_joinstyle='round', zorder=10
                )
                self._xs_pen_poly_art = line
                self._xs_sketch_objects.append(line)
                self.canvas_xs.draw_idle()
                return

            # No Ctrl, not mid-polyline — normal click-and-drag behaviour.
            if self._xs_sketch_mode == 'pen':
                self._xs_sketch_pen_pts = ([fx], [fy])
                line, = ax.plot([fx], [fy],
                                color=self._xs_sketch_color, linewidth=self._xs_sketch_lw,
                                linestyle=self._xs_sketch_ls,
                                solid_capstyle='round', solid_joinstyle='round', zorder=10)
            else:
                line, = ax.plot([fx, fx], [fy, fy],
                                color=self._xs_sketch_color, linewidth=self._xs_sketch_lw,
                                linestyle=self._xs_sketch_ls,
                                solid_capstyle='round', zorder=10)
            self._xs_sketch_current = (line,)
            self._xs_sketch_objects.append(line)

        elif self._xs_sketch_mode == 'arrow':
            ann = ax.annotate('', xy=(fx, fy), xytext=(fx, fy),
                              xycoords='data', textcoords='data',
                              arrowprops=dict(arrowstyle='->',
                                              color=self._xs_sketch_color,
                                              lw=self._xs_sketch_lw,
                                              linestyle=self._xs_sketch_ls),
                              zorder=10)
            self._xs_sketch_current = (ann,)
            self._xs_sketch_objects.append(ann)

        elif self._xs_sketch_mode == 'rect':
            rect = _MplRect((fx, fy), 0, 0,
                             linewidth=self._xs_sketch_lw, linestyle=self._xs_sketch_ls,
                             edgecolor=self._xs_sketch_color, facecolor='none', zorder=10)
            ax.add_patch(rect)
            self._xs_sketch_current = (rect, fx, fy)
            self._xs_sketch_objects.append(rect)

        elif self._xs_sketch_mode == 'circle':
            ell = _MplEllipse((fx, fy), 0, 0,
                               linewidth=self._xs_sketch_lw, linestyle=self._xs_sketch_ls,
                               edgecolor=self._xs_sketch_color, facecolor='none', zorder=10)
            ax.add_patch(ell)
            self._xs_sketch_current = (ell, fx, fy)
            self._xs_sketch_objects.append(ell)

        elif self._xs_sketch_mode == 'level':
            self._xs_sketch_pressed = False
            if self._xs_level_snap_art is not None:
                self._xs_level_snap_art.set_visible(False)
            snapped_x, snapped_y, snap_col, _ = _snap_level(ax, event.xdata, event.ydata)
            col = snap_col or self._xs_sketch_color
            lvl_tri, = ax.plot(
                [snapped_x], [snapped_y],
                marker=_get_tri_tip_path(), markersize=14, linestyle='none',
                markerfacecolor=col, markeredgecolor=col,
                markeredgewidth=1.0, zorder=11)
            lvl_ann = ax.annotate(
                f'{snapped_y:.3f}',
                xy=(snapped_x, snapped_y), xycoords='data',
                xytext=(7, 2), textcoords='offset points',
                fontsize=7, color=col, va='bottom', ha='left',
                zorder=11, annotation_clip=False,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                          edgecolor='none', alpha=0.85))
            self._xs_sketch_objects.append(lvl_tri)
            self._xs_sketch_objects.append(lvl_ann)
            self.canvas_xs.draw_idle()
            return

        elif self._xs_sketch_mode == 'text':
            self._xs_sketch_pressed = False
            text, ok = QInputDialog.getText(self, 'Add Annotation', 'Text:')
            if ok and text.strip():
                ann = ax.text(fx, fy, text.strip(),
                              color=self._xs_sketch_color, fontsize=9, fontweight='bold',
                              zorder=10,
                              bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                                        edgecolor=self._xs_sketch_color, alpha=0.85))
                self._xs_sketch_objects.append(ann)
                self.canvas_xs.draw_idle()
            return

        elif self._xs_sketch_mode == 'eraser':
            self._xs_sketch_erase_at(event)
            return

        elif self._xs_sketch_mode == 'move':
            self._xs_sketch_drag_info = None
            for obj in reversed(self._xs_sketch_objects):
                try: hit, _ = obj.contains(event)
                except Exception: hit = False
                if not hit:
                    continue
                if isinstance(obj, _MplEllipse):
                    cx, cy = obj.center
                    self._xs_sketch_drag_info = {'obj': obj, 'type': 'ellipse',
                                                  'ox': cx - fx, 'oy': cy - fy}
                elif isinstance(obj, _MplRect):
                    bx, by = obj.get_xy()
                    self._xs_sketch_drag_info = {'obj': obj, 'type': 'rect',
                                                  'ox': bx - fx, 'oy': by - fy}
                elif hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None:
                    hx, hy = float(obj.xy[0]), float(obj.xy[1])
                    tx, ty = obj.get_position()
                    self._xs_sketch_drag_info = {'obj': obj, 'type': 'arrow',
                                                  'hox': hx - fx, 'hoy': hy - fy,
                                                  'tox': tx - fx, 'toy': ty - fy}
                elif hasattr(obj, 'get_text') and obj.get_text():
                    px, py = obj.get_position()
                    self._xs_sketch_drag_info = {'obj': obj, 'type': 'text',
                                                  'ox': px - fx, 'oy': py - fy}
                elif hasattr(obj, 'get_xdata'):
                    self._xs_sketch_drag_info = {'obj': obj, 'type': 'line2d',
                                                  'x0': list(obj.get_xdata()),
                                                  'y0': list(obj.get_ydata()),
                                                  'px': fx, 'py': fy}
                if self._xs_sketch_drag_info:
                    break
            self._xs_sketch_pressed = self._xs_sketch_drag_info is not None
            return

        elif self._xs_sketch_mode == 'edit':
            self._xs_sketch_pressed = False
            _hit_obj = None
            for obj in reversed(self._xs_sketch_objects):
                try: hit, _ = obj.contains(event)
                except Exception: hit = False
                if hit:
                    _hit_obj = obj
                    break
            if _hit_obj is not None:
                QTimer.singleShot(0, lambda o=_hit_obj: self._xs_sketch_edit_object(o))
            return

        self.canvas_xs.draw_idle()

    def _xs_sketch_on_motion(self, event):
        # Ctrl+pen polyline: live rubber band without holding mouse button
        if (self._xs_pen_poly_mode and self._xs_pen_poly_art is not None
                and event.inaxes == self.ax
                and event.xdata is not None and event.ydata is not None):
            xs = list(self._xs_pen_poly_pts[0]) + [event.xdata]
            ys = list(self._xs_pen_poly_pts[1]) + [event.ydata]
            self._xs_pen_poly_art.set_data(xs, ys)
            self.canvas_xs.draw_idle()
            return
        if not self._xs_sketch_pressed or self._xs_sketch_mode is None:
            return
        if event.inaxes != self.ax or event.xdata is None:
            return
        fx, fy = event.xdata, event.ydata
        cur = self._xs_sketch_current

        if self._xs_sketch_mode == 'pen' and cur:
            line, = cur
            self._xs_sketch_pen_pts[0].append(fx)
            self._xs_sketch_pen_pts[1].append(fy)
            line.set_data(self._xs_sketch_pen_pts[0], self._xs_sketch_pen_pts[1])

        elif self._xs_sketch_mode == 'line' and cur:
            line, = cur
            x0, y0 = self._xs_sketch_press_data
            line.set_data([x0, fx], [y0, fy])

        elif self._xs_sketch_mode == 'arrow' and cur:
            ann, = cur
            ann.xy = (fx, fy)

        elif self._xs_sketch_mode == 'rect' and cur:
            rect, x0, y0 = cur
            rect.set_xy((min(x0, fx), min(y0, fy)))
            rect.set_width(abs(fx - x0))
            rect.set_height(abs(fy - y0))

        elif self._xs_sketch_mode == 'circle' and cur:
            ell, x0, y0 = cur
            dx, dy = abs(fx - x0), abs(fy - y0)
            if event.key == 'shift':
                dx = dy = max(dx, dy)
            ell.set_width(2 * dx)
            ell.set_height(2 * dy)

        elif self._xs_sketch_mode == 'eraser':
            self._xs_sketch_erase_at(event)
            return

        elif self._xs_sketch_mode == 'move':
            d = self._xs_sketch_drag_info
            if d is None:
                return
            obj = d['obj']
            if d['type'] == 'ellipse':
                obj.set_center((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'rect':
                obj.set_xy((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'arrow':
                obj.xy = (fx + d['hox'], fy + d['hoy'])
                obj.set_position((fx + d['tox'], fy + d['toy']))
            elif d['type'] == 'text':
                obj.set_position((fx + d['ox'], fy + d['oy']))
            elif d['type'] == 'line2d':
                ddx, ddy = fx - d['px'], fy - d['py']
                obj.set_data([v + ddx for v in d['x0']],
                             [v + ddy for v in d['y0']])

        self.canvas_xs.draw_idle()

    def _xs_sketch_edit_object(self, obj):
        """Open a property-editor dialog for an XS sketch object and apply changes."""
        try:
            self._xs_sketch_edit_object_impl(obj)
        except Exception as exc:
            QMessageBox.critical(self, 'Sketch Edit Error', str(exc))

    def _xs_sketch_edit_object_impl(self, obj):
        is_text  = (hasattr(obj, 'get_text') and bool(obj.get_text())
                    and not (hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None))
        is_arrow = hasattr(obj, 'arrow_patch') and obj.arrow_patch is not None
        is_line  = hasattr(obj, 'get_xdata')
        is_patch = isinstance(obj, (_MplRect, _MplEllipse))

        if is_text:
            cur_color = _to_hex_color(obj.get_color())
        elif is_arrow:
            cur_color = _to_hex_color(obj.arrow_patch.get_edgecolor())
        elif is_line:
            cur_color = _to_hex_color(obj.get_color())
        elif is_patch:
            cur_color = _to_hex_color(obj.get_edgecolor())
        else:
            cur_color = '#000000'

        def _cur_lw():
            if is_arrow: return obj.arrow_patch.get_linewidth()
            if is_line:  return obj.get_linewidth()
            if is_patch: return obj.get_linewidth()
            return 2.0

        def _cur_ls():
            _norm = {'solid': '-', 'dashed': '--', 'dotted': ':', 'dashdot': '-.'}
            if is_arrow: raw = obj.arrow_patch.get_linestyle()
            elif is_line:  raw = obj.get_linestyle()
            elif is_patch: raw = obj.get_linestyle()
            else: return '-'
            return _norm.get(raw, raw)

        dlg = QDialog(self)
        dlg.setWindowTitle('Edit Annotation')
        dlg.setMinimumWidth(260)
        try:
            dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        except AttributeError:
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint)  # type: ignore
        root = QVBoxLayout(dlg)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        _lbl_style = 'font-size:10px; color:#546E7A;'

        color_val = [cur_color]
        cr = QHBoxLayout()
        _clbl = QLabel('Colour:'); _clbl.setStyleSheet(_lbl_style)
        cr.addWidget(_clbl)
        color_swatch = QPushButton()
        color_swatch.setFixedSize(50, 22)
        color_swatch.setStyleSheet(
            f'background:{cur_color};border:1px solid #888;border-radius:3px;')
        def _pick():
            c = QColorDialog.getColor(QColor(color_val[0]), dlg)
            if c.isValid():
                color_val[0] = c.name()
                color_swatch.setStyleSheet(
                    f'background:{c.name()};border:1px solid #888;border-radius:3px;')
        color_swatch.clicked.connect(_pick)
        cr.addWidget(color_swatch); cr.addStretch()
        root.addLayout(cr)

        lw_spin  = None
        ls_combo = None
        if not is_text:
            lwr = QHBoxLayout()
            _wlbl = QLabel('Width:'); _wlbl.setStyleSheet(_lbl_style)
            lwr.addWidget(_wlbl)
            lw_spin = QDoubleSpinBox()
            lw_spin.setRange(0.5, 10.0); lw_spin.setSingleStep(0.5)
            lw_spin.setDecimals(1); lw_spin.setFixedWidth(65)
            lw_spin.setValue(_cur_lw())
            lwr.addWidget(lw_spin); lwr.addStretch()
            root.addLayout(lwr)

            lsr = QHBoxLayout()
            _slbl = QLabel('Style:'); _slbl.setStyleSheet(_lbl_style)
            lsr.addWidget(_slbl)
            ls_combo = QComboBox(); ls_combo.setFixedWidth(130)
            for _v, _n in [('-',  '─── Solid'), ('--', '-- Dashed'),
                           (':',  '··· Dotted'), ('-.', '-·- Dash-dot')]:
                ls_combo.addItem(_n, _v)
            _cur = _cur_ls()
            for _i in range(ls_combo.count()):
                if ls_combo.itemData(_i) == _cur:
                    ls_combo.setCurrentIndex(_i); break
            lsr.addWidget(ls_combo); lsr.addStretch()
            root.addLayout(lsr)

        font_combo = size_spin = bold_cb = italic_cb = None
        if is_text:
            fr = QHBoxLayout()
            _flbl = QLabel('Font:'); _flbl.setStyleSheet(_lbl_style)
            fr.addWidget(_flbl)
            font_combo = QComboBox(); font_combo.setFixedWidth(140)
            for _fn in ['sans-serif', 'serif', 'monospace',
                        'DejaVu Sans', 'Arial', 'Courier New']:
                font_combo.addItem(_fn)
            try:
                _ff = (obj.get_fontfamily() or ['sans-serif'])[0]
                _fi = font_combo.findText(_ff)
                if _fi >= 0: font_combo.setCurrentIndex(_fi)
            except Exception:
                pass
            fr.addWidget(font_combo); fr.addStretch()
            root.addLayout(fr)

            sr = QHBoxLayout()
            _szlbl = QLabel('Size:'); _szlbl.setStyleSheet(_lbl_style)
            sr.addWidget(_szlbl)
            size_spin = QSpinBox()
            size_spin.setRange(6, 72); size_spin.setFixedWidth(60)
            size_spin.setValue(int(obj.get_fontsize()))
            sr.addWidget(size_spin); sr.addStretch()
            root.addLayout(sr)

            bir = QHBoxLayout()
            bold_cb   = QCheckBox('Bold')
            italic_cb = QCheckBox('Italic')
            bold_cb.setChecked(str(obj.get_fontweight()) in ('bold', '700', '800', '900'))
            italic_cb.setChecked(obj.get_fontstyle() == 'italic')
            bir.addWidget(bold_cb); bir.addWidget(italic_cb); bir.addStretch()
            root.addLayout(bir)

        sep = QFrame(); sep.setFrameShape(_HLINE); sep.setFrameShadow(_SUNKEN)
        root.addWidget(sep)
        btn_row = QHBoxLayout()
        btn_ok     = QPushButton('OK');     btn_ok.setFixedWidth(70)
        btn_cancel = QPushButton('Cancel'); btn_cancel.setFixedWidth(70)
        btn_row.addStretch(); btn_row.addWidget(btn_ok); btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)
        btn_cancel.clicked.connect(dlg.reject)

        def _apply():
            nc = color_val[0]
            if is_text:
                obj.set_color(nc)
                bp = obj.get_bbox_patch()
                if bp: bp.set_edgecolor(nc)
            elif is_arrow:
                obj.arrow_patch.set_edgecolor(nc)
                obj.arrow_patch.set_facecolor(nc)
            elif is_line:
                obj.set_color(nc)
            elif is_patch:
                obj.set_edgecolor(nc)
            if lw_spin is not None:
                nlw = lw_spin.value()
                if is_arrow: obj.arrow_patch.set_linewidth(nlw)
                elif is_line: obj.set_linewidth(nlw)
                elif is_patch: obj.set_linewidth(nlw)
            if ls_combo is not None:
                nls = ls_combo.currentData()
                if is_arrow: obj.arrow_patch.set_linestyle(nls)
                elif is_line: obj.set_linestyle(nls)
                elif is_patch: obj.set_linestyle(nls)
            if is_text:
                if font_combo: obj.set_fontfamily(font_combo.currentText())
                if size_spin:  obj.set_fontsize(size_spin.value())
                if bold_cb:    obj.set_fontweight('bold' if bold_cb.isChecked() else 'normal')
                if italic_cb:  obj.set_fontstyle('italic' if italic_cb.isChecked() else 'normal')
                bp = obj.get_bbox_patch()
                if bp: bp.set_edgecolor(nc)
            self.canvas_xs.draw_idle()
            dlg.accept()

        btn_ok.clicked.connect(_apply)
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    def _xs_sketch_on_release(self, event):
        self._xs_sketch_pressed = False
        self._xs_sketch_current = None
        self._xs_sketch_pen_pts = None
        self._xs_sketch_drag_info = None


class TerrainCheckXSDialog(QDialog):
    """Modify Terrain's 'Check Cross-Section' window — steps through the
    cross-section INTERPOLATED between two break points along a chosen
    reach, at a user-set spacing, so the interpolation can be sanity-
    checked visually before it's trusted for eventual raster/TIFF
    generation (same interpolation helper, dock._terrain_xs_interp_params,
    that a raster step would later reuse at dense per-cell spacing).

    Deliberately a separate popped-out window (like the Layer Profile
    tab's own XSectionDialog) rather than embedded in the dock — a reach
    can be long, and stepping through it wants more room than the docked
    per-station preview has, plus the two operate on different concepts
    (a single station vs a whole reach) so they don't need to share a
    canvas."""

    def __init__(self, dock):
        super().__init__(dock.iface.mainWindow())
        self.dock = dock
        self._reach = None      # (ch0, ch1) of the currently picked reach
        self._step_idx = 0      # which sample step along the reach we're on
        self._step_chainages = []
        # Scroll-zoom / middle-drag pan state — same convention as the
        # embedded Modify Terrain per-station preview's own
        # _on_terrain_xs_scroll_zoom/_on_terrain_xs_press.
        self._pan_press_px   = None
        self._pan_xlim0      = None
        self._pan_ylim0      = None
        self._pan_transform  = None
        # Vertical exaggeration — THIS window's own value, independent of
        # the embedded Cross-Section Preview's (dock._terrain_xs_z_factor)
        # and of any other open Check Cross-Section window — per explicit
        # request, after trying a shared value, that Z factor only affect
        # the one plot it's actually changed in.
        self._z_factor = 1.0

        self.setWindowTitle('Check Cross-Section')
        self.resize(640, 460)
        self._setup_ui()
        self._on_reach_changed()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel('Reach:'))
        self.combo_reach = QComboBox()
        self.combo_reach.setMinimumWidth(200)
        for label, ch0, ch1 in self.dock._terrain_xs_reach_list():
            self.combo_reach.addItem(label, (ch0, ch1))
        self.combo_reach.currentIndexChanged.connect(self._on_reach_changed)
        top.addWidget(self.combo_reach)
        top.addSpacing(12)
        top.addWidget(QLabel('Spacing:'))
        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.01, 10000.0)
        self.spin_spacing.setDecimals(2)
        self.spin_spacing.setValue(1.0)
        self.spin_spacing.setSuffix(' m')
        self.spin_spacing.setFixedWidth(90)
        self.spin_spacing.valueChanged.connect(self._on_reach_changed)
        top.addWidget(self.spin_spacing)
        top.addSpacing(12)
        top.addWidget(QLabel('Z factor:'))
        self.spin_zfactor = QDoubleSpinBox()
        self.spin_zfactor.setRange(0.1, 100.0)
        self.spin_zfactor.setValue(self._z_factor)
        self.spin_zfactor.setSingleStep(0.5)
        self.spin_zfactor.setDecimals(1)
        self.spin_zfactor.setFixedWidth(60)
        self.spin_zfactor.setToolTip(
            "Vertical exaggeration — same idea as QGIS's own terrain Z\n"
            'factor. 1 = true scale; 2 = vertical scale doubled, and so\n'
            "on. This window's own setting — independent of the embedded\n"
            "Cross-Section Preview's own Z factor."
        )
        self.spin_zfactor.valueChanged.connect(self._on_zfactor_changed)
        top.addWidget(self.spin_zfactor)
        top.addStretch()
        outer.addLayout(top)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(5, 3.2), dpi=100)
            self.ax = self.figure.add_subplot(111)
            self.canvas = FigureCanvas(self.figure)
            self.canvas.setSizePolicy(_EXPAND, _EXPAND)
            self.canvas.mpl_connect('resize_event', self._on_canvas_resize)
            self.canvas.mpl_connect('scroll_event',         self._on_scroll_zoom)
            self.canvas.mpl_connect('button_press_event',   self._on_pan_press)
            self.canvas.mpl_connect('motion_notify_event',  self._on_pan_motion)
            self.canvas.mpl_connect('button_release_event', self._on_pan_release)
            outer.addWidget(self.canvas, 1)
        else:
            self.canvas = None
            outer.addWidget(QLabel('matplotlib is not available — cannot preview.'))

        nav = QHBoxLayout()
        self.btn_prev = QPushButton('◀  Prev (US)')
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        nav.addWidget(self.btn_prev)
        self.lbl_step = QLabel('')
        self.lbl_step.setAlignment(Qt.AlignmentFlag.AlignCenter
                                    if hasattr(Qt, 'AlignmentFlag')
                                    else Qt.AlignCenter)  # type: ignore
        self.lbl_step.setStyleSheet('font-size:10px;color:#455A64;')
        nav.addWidget(self.lbl_step, 1)
        self.btn_next = QPushButton('Next (DS)  ▶')
        self.btn_next.clicked.connect(lambda: self._step(1))
        nav.addWidget(self.btn_next)
        outer.addLayout(nav)

    def _on_reach_changed(self):
        data = self.combo_reach.currentData()
        self._step_idx = 0
        if data is None:
            self._reach = None
            self._step_chainages = []
        else:
            self._reach = data
            ch0, ch1 = data
            spacing = max(0.01, self.spin_spacing.value())
            # Step at exactly the user's own spacing from the US end,
            # rather than dividing the reach length into N equal parts
            # (which silently changes the spacing to whatever divides
            # evenly, e.g. 1.01 m when the user asked for 1.00 m). The
            # last full step is followed by one extra, shorter step
            # landing exactly on the DS end, so that end is always shown
            # even when the reach length isn't an exact multiple of the
            # spacing.
            chs = []
            ch = ch0
            while ch < ch1 - 1e-9:
                chs.append(ch)
                ch += spacing
            if chs and (ch1 - chs[-1]) <= 1e-6:
                chs[-1] = ch1
            else:
                chs.append(ch1)
            self._step_chainages = chs
        self._draw_current_step()

    def _on_zfactor_changed(self):
        """This window's OWN vertical exaggeration — redraws just this
        window, never the embedded preview or any other Check Cross-
        Section window (see self._z_factor's own docstring)."""
        self._z_factor = self.spin_zfactor.value()
        self._draw_current_step()

    def _step(self, delta):
        if not self._step_chainages:
            return
        self._step_idx = max(0, min(len(self._step_chainages) - 1, self._step_idx + delta))
        self._draw_current_step()

    def current_chainage(self):
        """The chainage this window is CURRENTLY showing, or None if no
        reach is picked yet — used by the dock to draw a 'ghost' marker
        on the main ground-profile chart showing where along the
        alignment this popped-out view is actually looking (see
        _draw_terrain_vertical_decorations)."""
        if not self._step_chainages:
            return None
        return self._step_chainages[self._step_idx]

    def _draw_current_step(self):
        if self.canvas is None:
            return
        if not self._step_chainages:
            self.ax.clear()
            self.ax.text(0.5, 0.5, 'No reach available.', ha='center', va='center',
                          fontsize=9, color='gray', transform=self.ax.transAxes)
            self.ax.set_xticks([]); self.ax.set_yticks([])
            self.lbl_step.setText('')
            self.canvas.draw_idle()
            self.dock._draw_terrain_vertical_decorations()
            return

        ch0, ch1 = self._reach
        ch = self._step_chainages[self._step_idx]
        last_idx = len(self._step_chainages) - 1

        # _terrain_xs_interp_params gives the numeric params (used here
        # only to size the ground sampling extent) — the actual design
        # shape comes from _terrain_xs_build_interpolated_shape, which
        # blends the US/DS options too when they differ (see its own
        # docstring for why an abrupt option switch isn't acceptable for
        # a channel meant to be genuinely constructible).
        params = self.dock._terrain_xs_interp_params(ch)
        invert_level = self.dock._terrain_invert_level_at(ch)
        ground = self.dock._terrain_xs_sample_ground(ch, params)
        poly_x, poly_y = self.dock._terrain_xs_build_interpolated_shape(ch, invert_level, ground)
        on_control = any(abs(cs['chainage'] - ch) < 1e-6 for cs in self.dock._terrain_control_sections)
        title = f'Interpolated Cross-Section — Chainage {ch:.2f} m'
        if on_control:
            title += '  (control section)'
        self.dock._terrain_draw_xs_axes(self.ax, poly_x, poly_y, ground, title=title,
                                          z_factor=self._z_factor)
        self.canvas.draw_idle()

        end_tag = '  (DS end)' if self._step_idx == last_idx else ''
        self.lbl_step.setText(
            f'Step {self._step_idx} of {last_idx}   —   '
            f'Chainage {ch:.2f} m (reach {ch0:.2f}–{ch1:.2f} m){end_tag}')
        self.btn_prev.setEnabled(self._step_idx > 0)
        self.btn_next.setEnabled(self._step_idx < last_idx)
        # Moves this window's ghost marker on the main ground-profile
        # chart to the new chainage.
        self.dock._draw_terrain_vertical_decorations()

    def _on_canvas_resize(self, event):
        """Re-flow margins on window resize so the x-axis label doesn't
        stay clipped between resizes."""
        try:
            self.figure.tight_layout(pad=1.0)
        except Exception:
            pass
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ zoom/pan

    def _on_scroll_zoom(self, event):
        """Scroll-to-zoom, same convention used everywhere else in this
        plugin (the main chart, the embedded Modify Terrain preview, and
        the Layer Profile Cross-Section window)."""
        if event.inaxes is not self.ax or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y = event.xdata, event.ydata
        ax = event.inaxes
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        ax.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        self.canvas.draw_idle()

    def _on_pan_press(self, event):
        """Middle-mouse-drag pan — same gesture as everywhere else."""
        if event.button == 2 and event.inaxes is self.ax:
            self._pan_press_px  = (event.x, event.y)
            self._pan_xlim0     = self.ax.get_xlim()
            self._pan_ylim0     = self.ax.get_ylim()
            self._pan_transform = self.ax.transData.inverted().frozen()

    def _on_pan_motion(self, event):
        if self._pan_press_px is None or self._pan_transform is None:
            return
        if event.x is None or event.y is None:
            return
        x0d, y0d = self._pan_transform.transform(self._pan_press_px)
        x1d, y1d = self._pan_transform.transform((event.x, event.y))
        dx, dy = x0d - x1d, y0d - y1d
        xl0, xr0 = self._pan_xlim0
        yb0, yt0 = self._pan_ylim0
        self.ax.set_xlim([xl0 + dx, xr0 + dx])
        self.ax.set_ylim([yb0 + dy, yt0 + dy])
        self.canvas.draw_idle()

    def _on_pan_release(self, event):
        if event.button == 2:
            self._pan_press_px = self._pan_xlim0 = self._pan_ylim0 = self._pan_transform = None


class EmbankCheckXSDialog(QDialog):
    """Modify Embank's 'Check Cross-Section' window — steps through the
    cross-section INTERPOLATED between two break points along a chosen
    reach, at a user-set spacing, so the interpolation can be sanity-
    checked visually before it's trusted for eventual raster/TIFF
    generation (same interpolation helper, dock._embank_xs_interp_params,
    that a raster step would later reuse at dense per-cell spacing).

    Deliberately a separate popped-out window (like the Layer Profile
    tab's own XSectionDialog) rather than embedded in the dock — a reach
    can be long, and stepping through it wants more room than the docked
    per-station preview has, plus the two operate on different concepts
    (a single station vs a whole reach) so they don't need to share a
    canvas."""

    def __init__(self, dock):
        super().__init__(dock.iface.mainWindow())
        self.dock = dock
        self._reach = None      # (ch0, ch1) of the currently picked reach
        self._step_idx = 0      # which sample step along the reach we're on
        self._step_chainages = []
        # Scroll-zoom / middle-drag pan state — same convention as the
        # embedded Modify Embank per-station preview's own
        # _on_embank_xs_scroll_zoom/_on_embank_xs_press.
        self._pan_press_px   = None
        self._pan_xlim0      = None
        self._pan_ylim0      = None
        self._pan_transform  = None
        # Vertical exaggeration — THIS window's own value, independent of
        # the embedded Cross-Section Preview's (dock._embank_xs_z_factor)
        # and of any other open Check Cross-Section window — per explicit
        # request, after trying a shared value, that Z factor only affect
        # the one plot it's actually changed in.
        self._z_factor = 1.0
        # 'Elev' click-to-read-elevation tool — THIS window's own, same
        # convention as the embedded Cross-Section Preview's (snaps onto
        # either the design line or the ground profile, see
        # _snap_point, rather than just reading the raw cursor position)
        # per explicit request. self._last_poly_x/_poly_y/_ground cache
        # whatever _draw_current_step most recently built/sampled, since
        # the snap needs the SAME shape that's actually on screen, not a
        # freshly rebuilt one (which would be wasteful work on every
        # click and could drift from what's drawn if anything changed).
        self._elev_mode     = False
        self._elev_markers  = []
        self._last_poly_x   = None
        self._last_poly_y   = None
        self._last_ground   = None

        self.setWindowTitle('Check Cross-Section')
        self.resize(640, 460)
        self._setup_ui()
        self._on_reach_changed()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel('Reach:'))
        self.combo_reach = QComboBox()
        self.combo_reach.setMinimumWidth(200)
        for label, ch0, ch1 in self.dock._embank_xs_reach_list():
            self.combo_reach.addItem(label, (ch0, ch1))
        self.combo_reach.currentIndexChanged.connect(self._on_reach_changed)
        top.addWidget(self.combo_reach)
        top.addSpacing(12)
        top.addWidget(QLabel('Spacing:'))
        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.01, 10000.0)
        self.spin_spacing.setDecimals(2)
        self.spin_spacing.setValue(1.0)
        self.spin_spacing.setSuffix(' m')
        self.spin_spacing.setFixedWidth(90)
        self.spin_spacing.valueChanged.connect(self._on_reach_changed)
        top.addWidget(self.spin_spacing)
        top.addSpacing(12)
        top.addWidget(QLabel('Z factor:'))
        self.spin_zfactor = QDoubleSpinBox()
        self.spin_zfactor.setRange(0.1, 100.0)
        self.spin_zfactor.setValue(self._z_factor)
        self.spin_zfactor.setSingleStep(0.5)
        self.spin_zfactor.setDecimals(1)
        self.spin_zfactor.setFixedWidth(60)
        self.spin_zfactor.setToolTip(
            "Vertical exaggeration — same idea as QGIS's own embankment Z\n"
            'factor. 1 = true scale; 2 = vertical scale doubled, and so\n'
            "on. This window's own setting — independent of the embedded\n"
            "Cross-Section Preview's own Z factor."
        )
        self.spin_zfactor.valueChanged.connect(self._on_zfactor_changed)
        top.addWidget(self.spin_zfactor)
        top.addStretch()

        self.btn_elev = QPushButton('Elev')
        self.btn_elev.setCheckable(True)
        self.btn_elev.setToolTip(
            'Click the chart to read its elevation, snapped onto either\n'
            'the design line or the ground profile (whichever is nearer)\n'
            'so the reading is accurate to that line, not just wherever\n'
            'the cursor happens to be. Click an existing marker to remove\n'
            'it, or this button again to clear all of them.'
        )
        self.btn_elev.clicked.connect(self._toggle_elev_tool)
        top.addWidget(self.btn_elev)
        outer.addLayout(top)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(5, 3.2), dpi=100)
            self.ax = self.figure.add_subplot(111)
            self.canvas = FigureCanvas(self.figure)
            self.canvas.setSizePolicy(_EXPAND, _EXPAND)
            self.canvas.mpl_connect('resize_event', self._on_canvas_resize)
            self.canvas.mpl_connect('scroll_event',         self._on_scroll_zoom)
            self.canvas.mpl_connect('button_press_event',   self._on_pan_press)
            self.canvas.mpl_connect('motion_notify_event',  self._on_pan_motion)
            self.canvas.mpl_connect('button_release_event', self._on_pan_release)
            outer.addWidget(self.canvas, 1)
        else:
            self.canvas = None
            outer.addWidget(QLabel('matplotlib is not available — cannot preview.'))

        nav = QHBoxLayout()
        self.btn_prev = QPushButton('◀  Prev (US)')
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        nav.addWidget(self.btn_prev)
        self.lbl_step = QLabel('')
        self.lbl_step.setAlignment(Qt.AlignmentFlag.AlignCenter
                                    if hasattr(Qt, 'AlignmentFlag')
                                    else Qt.AlignCenter)  # type: ignore
        self.lbl_step.setStyleSheet('font-size:10px;color:#455A64;')
        nav.addWidget(self.lbl_step, 1)
        self.btn_next = QPushButton('Next (DS)  ▶')
        self.btn_next.clicked.connect(lambda: self._step(1))
        nav.addWidget(self.btn_next)
        outer.addLayout(nav)

    def _on_reach_changed(self):
        data = self.combo_reach.currentData()
        self._step_idx = 0
        if data is None:
            self._reach = None
            self._step_chainages = []
        else:
            self._reach = data
            ch0, ch1 = data
            spacing = max(0.01, self.spin_spacing.value())
            # Step at exactly the user's own spacing from the US end,
            # rather than dividing the reach length into N equal parts
            # (which silently changes the spacing to whatever divides
            # evenly, e.g. 1.01 m when the user asked for 1.00 m). The
            # last full step is followed by one extra, shorter step
            # landing exactly on the DS end, so that end is always shown
            # even when the reach length isn't an exact multiple of the
            # spacing.
            chs = []
            ch = ch0
            while ch < ch1 - 1e-9:
                chs.append(ch)
                ch += spacing
            if chs and (ch1 - chs[-1]) <= 1e-6:
                chs[-1] = ch1
            else:
                chs.append(ch1)
            self._step_chainages = chs
        self._draw_current_step()

    def _on_zfactor_changed(self):
        """This window's OWN vertical exaggeration — redraws just this
        window, never the embedded preview or any other Check Cross-
        Section window (see self._z_factor's own docstring)."""
        self._z_factor = self.spin_zfactor.value()
        self._draw_current_step()

    def _step(self, delta):
        if not self._step_chainages:
            return
        self._step_idx = max(0, min(len(self._step_chainages) - 1, self._step_idx + delta))
        self._draw_current_step()

    def current_chainage(self):
        """The chainage this window is CURRENTLY showing, or None if no
        reach is picked yet — used by the dock to draw a 'ghost' marker
        on the main ground-profile chart showing where along the
        alignment this popped-out view is actually looking (see
        _draw_embank_vertical_decorations)."""
        if not self._step_chainages:
            return None
        return self._step_chainages[self._step_idx]

    def _draw_current_step(self):
        if self.canvas is None:
            return
        self._elev_markers = []   # wiped by ax.clear() below; drop the stale references
        if not self._step_chainages:
            self.ax.clear()
            self.ax.text(0.5, 0.5, 'No reach available.', ha='center', va='center',
                          fontsize=9, color='gray', transform=self.ax.transAxes)
            self.ax.set_xticks([]); self.ax.set_yticks([])
            self.lbl_step.setText('')
            self.canvas.draw_idle()
            self.dock._draw_embank_vertical_decorations()
            return

        ch0, ch1 = self._reach
        ch = self._step_chainages[self._step_idx]
        last_idx = len(self._step_chainages) - 1

        # _embank_xs_interp_params gives the numeric params (used here
        # only to size the ground sampling extent) — the actual design
        # shape comes from _embank_xs_build_interpolated_shape, which
        # blends the US/DS options too when they differ (see its own
        # docstring for why an abrupt option switch isn't acceptable for
        # a channel meant to be genuinely constructible).
        params = self.dock._embank_xs_interp_params(ch)
        invert_level = self.dock._embank_invert_level_at(ch)
        ground = self.dock._embank_xs_sample_ground(ch, params)
        poly_x, poly_y = self.dock._embank_xs_build_interpolated_shape(ch, invert_level, ground)
        # Cached for _snap_point, which needs the SAME shape/ground that's
        # actually on screen right now rather than rebuilding it per click.
        self._last_poly_x, self._last_poly_y, self._last_ground = poly_x, poly_y, ground
        on_control = any(abs(cs['chainage'] - ch) < 1e-6 for cs in self.dock._embank_control_sections)
        title = f'Interpolated Cross-Section — Chainage {ch:.2f} m'
        if on_control:
            title += '  (control section)'
        self.dock._embank_draw_xs_axes(self.ax, poly_x, poly_y, ground, title=title,
                                          z_factor=self._z_factor)
        self.canvas.draw_idle()

        end_tag = '  (DS end)' if self._step_idx == last_idx else ''
        self.lbl_step.setText(
            f'Step {self._step_idx} of {last_idx}   —   '
            f'Chainage {ch:.2f} m (reach {ch0:.2f}–{ch1:.2f} m){end_tag}')
        self.btn_prev.setEnabled(self._step_idx > 0)
        self.btn_next.setEnabled(self._step_idx < last_idx)
        # Moves this window's ghost marker on the main ground-profile
        # chart to the new chainage.
        self.dock._draw_embank_vertical_decorations()

    def _on_canvas_resize(self, event):
        """Re-flow margins on window resize so the x-axis label doesn't
        stay clipped between resizes."""
        try:
            self.figure.tight_layout(pad=1.0)
        except Exception:
            pass
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ zoom/pan

    def _on_scroll_zoom(self, event):
        """Scroll-to-zoom, same convention used everywhere else in this
        plugin (the main chart, the embedded Modify Embank preview, and
        the Layer Profile Cross-Section window)."""
        if event.inaxes is not self.ax or event.xdata is None:
            return
        f = 0.8 if event.button == 'up' else 1.25
        x, y = event.xdata, event.ydata
        ax = event.inaxes
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        ax.set_xlim([x - (x - xl) * f, x + (xr - x) * f])
        ax.set_ylim([y - (y - yb) * f, y + (yt - y) * f])
        self.canvas.draw_idle()

    def _on_pan_press(self, event):
        """Middle-mouse-drag pan — same gesture as everywhere else. Left-
        click, while the Elev tool is on, reads/places a spot elevation
        instead (see _snap_point)."""
        if (event.button == 1 and self._elev_mode and event.inaxes is self.ax
                and event.xdata is not None and event.ydata is not None):
            idx = self._find_elev_marker_near(event.xdata, event.ydata)
            if idx is not None:
                self._remove_elev_marker(idx)
            else:
                x, y = self._snap_point(event.xdata, event.ydata) or (event.xdata, event.ydata)
                self._add_elev_marker(x, y)
            return
        if event.button == 2 and event.inaxes is self.ax:
            self._pan_press_px  = (event.x, event.y)
            self._pan_xlim0     = self.ax.get_xlim()
            self._pan_ylim0     = self.ax.get_ylim()
            self._pan_transform = self.ax.transData.inverted().frozen()

    def _on_pan_motion(self, event):
        if self._pan_press_px is None or self._pan_transform is None:
            return
        if event.x is None or event.y is None:
            return
        x0d, y0d = self._pan_transform.transform(self._pan_press_px)
        x1d, y1d = self._pan_transform.transform((event.x, event.y))
        dx, dy = x0d - x1d, y0d - y1d
        xl0, xr0 = self._pan_xlim0
        yb0, yt0 = self._pan_ylim0
        self.ax.set_xlim([xl0 + dx, xr0 + dx])
        self.ax.set_ylim([yb0 + dy, yt0 + dy])
        self.canvas.draw_idle()

    def _on_pan_release(self, event):
        if event.button == 2:
            self._pan_press_px = self._pan_xlim0 = self._pan_ylim0 = self._pan_transform = None

    # ------------------------------------------------------------------ spot elevation

    def _snap_point(self, xdata, ydata, tol_px=28):
        """Nearest point to the click on either the interpolated design
        line or the ground profile line — same technique as the dock's
        own _embank_xs_snap_point (vertices AND any point along a
        segment, via perpendicular pixel-space projection), but against
        THIS window's own currently-drawn shape (self._last_poly_x/
        _poly_y/_ground), since it shows an INTERPOLATED chainage along a
        reach, not a real station's own stored params. Returns (x, y) in
        data coordinates, or None if nothing is within tol_px screen
        pixels (so a click far from both lines still free-places)."""
        click_px = self.ax.transData.transform((xdata, ydata))
        best = None  # (dist_px, x, y)

        def _consider(xs, ys):
            nonlocal best
            n = len(xs)
            for i in range(n):
                if ys[i] is None:
                    continue
                px, py = self.ax.transData.transform((xs[i], ys[i]))
                d = math.hypot(px - click_px[0], py - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i], ys[i])
            for i in range(n - 1):
                y0, y1 = ys[i], ys[i + 1]
                if y0 is None or y1 is None:
                    continue
                p0x, p0y = self.ax.transData.transform((xs[i], y0))
                p1x, p1y = self.ax.transData.transform((xs[i + 1], y1))
                sx, sy = p1x - p0x, p1y - p0y
                seg_len2 = sx * sx + sy * sy
                if seg_len2 < 1e-9:
                    continue
                t = ((click_px[0] - p0x) * sx + (click_px[1] - p0y) * sy) / seg_len2
                t = max(0.0, min(1.0, t))
                proj_x, proj_y = p0x + t * sx, p0y + t * sy
                d = math.hypot(proj_x - click_px[0], proj_y - click_px[1])
                if best is None or d < best[0]:
                    best = (d, xs[i] + t * (xs[i + 1] - xs[i]), y0 + t * (y1 - y0))

        if self._last_poly_x is not None:
            _consider(self._last_poly_x, self._last_poly_y)
        ground = self._last_ground
        col = self.dock._embank_ground_col
        if ground is not None and col and col in ground['data']:
            _consider(ground['xs'], ground['data'][col])

        if best is not None and best[0] <= tol_px:
            return best[1], best[2]
        return None

    def _find_elev_marker_near(self, xdata, ydata, tol_px=10):
        if not self._elev_markers:
            return None
        click_px = self.ax.transData.transform((xdata, ydata))
        best_i, best_d = None, tol_px
        for i, m in enumerate(self._elev_markers):
            pt_px = self.ax.transData.transform((m['x'], m['y']))
            d = math.hypot(pt_px[0] - click_px[0], pt_px[1] - click_px[1])
            if d <= best_d:
                best_d, best_i = d, i
        return best_i

    def _remove_elev_marker(self, idx):
        if not (0 <= idx < len(self._elev_markers)):
            return
        m = self._elev_markers.pop(idx)
        for art in (m['marker'], m['text']):
            try: art.remove()
            except Exception: pass
        self.canvas.draw_idle()

    def _add_elev_marker(self, x, y):
        """Inverted-triangle spot elevation marker — same convention as
        the embedded Cross-Section Preview's own Elev tool."""
        mk, = self.ax.plot([x], [y], marker='v', markersize=12, markeredgewidth=1.3,
                            markerfacecolor='#D32F2F', markeredgecolor='#7A0000', zorder=30)
        txt = self.ax.annotate(
            f'{y:.3f} masl', xy=(x, y), xytext=(6, -12), textcoords='offset points',
            fontsize=8, fontweight='bold', color='#D32F2F',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.9, edgecolor='none'),
            zorder=30,
        )
        self._elev_markers.append({'x': x, 'y': y, 'marker': mk, 'text': txt})
        self.canvas.draw_idle()

    def _toggle_elev_tool(self, checked):
        self._elev_mode = checked
        if not checked:
            for m in self._elev_markers:
                for art in (m['marker'], m['text']):
                    try: art.remove()
                    except Exception: pass
            self._elev_markers = []
            if self.canvas is not None:
                self.canvas.draw_idle()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

class AdvancedProfilePlugin:

    def __init__(self, iface):
        self.iface   = iface
        self.toolbar = None
        self.action  = None
        self.dock    = None

    def initGui(self):
        self.toolbar = self.iface.addToolBar('Advanced Profile Tool')
        self.toolbar.setObjectName('AdvancedProfileToolToolbar')
        import os as _os
        _icon_path = _os.path.join(_os.path.dirname(__file__), 'icon.svg')
        _icon = QIcon(_icon_path) if _os.path.exists(_icon_path) else QIcon()
        self.action = QAction(_icon, 'Advanced Profile Tool', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Advanced Profile Tool')
        self.action.triggered.connect(self._toggle_dock)
        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu('&Advanced Profile Tool', self.action)
        self.dock = NormalProfileDock(self.iface, self.iface.mainWindow())
        self.iface.addDockWidget(_RIGHT_DOCK, self.dock)
        self.dock.hide()

        # Chart dock — registered here (after iface is ready) so Qt properly
        # docks it into the main window. Canvas/toolbar are wired in next line.
        self._chart_dock = QDockWidget('Advanced Profile Tool — Chart', self.iface.mainWindow())
        self._chart_dock.setObjectName('AdvancedProfileToolChart')
        self._chart_dock.setMinimumWidth(500)
        self._chart_dock.setMinimumHeight(300)
        self.iface.addDockWidget(_BOTTOM_DOCK, self._chart_dock)
        self._chart_dock.hide()
        self.dock._setup_chart_dock(self._chart_dock)

        self.dock.visibilityChanged.connect(self.action.setChecked)

    def unload(self):
        if self.dock:
            # Close all open cross-section dialogs and remove their map canvas bands
            for _dlg in list(getattr(self.dock, '_xs_dialogs', [])):
                try:
                    _dlg._clear_map_band()
                    _dlg._clear_xs_hover_band()
                except Exception:
                    pass
                try:
                    _dlg.close()
                except Exception:
                    pass
            try:
                self.dock._hover_band.reset(_POINT_GEOM)
            except Exception:
                pass
            try:
                self.dock._clear_cursor_map_points()
            except Exception:
                pass
            try:
                self.dock._clear_terrain_chainage_marks()
            except Exception:
                pass
            try:
                self.dock._clear_terrain_vertex_markers()
            except Exception:
                pass
            try:
                self.dock._clear_terrain_break_map_bands()
            except Exception:
                pass
            try:
                self.dock._clear_terrain_xs_map_marker()
            except Exception:
                pass
            try:
                self.dock._close_terrain_inline_editor(commit=False)
            except Exception:
                pass
            try:
                self.dock._clear_embank_chainage_marks()
            except Exception:
                pass
            try:
                self.dock._clear_embank_vertex_markers()
            except Exception:
                pass
            try:
                self.dock._clear_embank_break_map_bands()
            except Exception:
                pass
            try:
                self.dock._clear_embank_xs_map_marker()
            except Exception:
                pass
            try:
                self.dock._close_embank_inline_editor(commit=False)
            except Exception:
                pass
            # Profile tab's drawn/selected line and Channel/Embankment
            # Surface's own alignment bands are their own separate
            # QgsRubberBand instances on the SAME map canvas — none of
            # the cleanups above touch them. Without this, every plugin
            # reload orphaned whichever of these happened to be on screen
            # at the time, permanently — the dock instance holding the
            # only Python reference able to remove them was about to be
            # replaced, but the graphics items themselves don't go away
            # on their own.
            for _band_attr in ('_perm_band', '_terrain_align_band', '_embank_align_band',
                               '_embank_extend_band_us', '_embank_extend_band_ds',
                               '_shapes_band', '_wetland_band', '_wetland_vertex_selected_band'):
                _band = getattr(self.dock, _band_attr, None)
                if _band is not None:
                    try:
                        self.dock.canvas.scene().removeItem(_band)
                    except Exception:
                        pass
            for _band in getattr(self.dock, '_wetland_vertex_bands', []) or []:
                try:
                    self.dock.canvas.scene().removeItem(_band)
                except Exception:
                    pass
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if hasattr(self, '_chart_dock') and self._chart_dock:
            try:
                self.iface.removeDockWidget(self._chart_dock)
                self._chart_dock.deleteLater()
            except Exception:
                pass
            self._chart_dock = None
        if self.action:
            self.iface.removePluginMenu('&Advanced Profile Tool', self.action)
            self.action = None
        if self.toolbar:
            self.toolbar.deleteLater()
            self.toolbar = None

    def _toggle_dock(self, checked):
        if checked:
            self.dock.show(); self.dock.raise_()
        else:
            self.dock.hide()
            if hasattr(self, '_chart_dock') and self._chart_dock:
                self._chart_dock.hide()
