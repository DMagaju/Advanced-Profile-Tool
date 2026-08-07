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

__version__    = '0.5'
TOOL_ID        = 'fta_profile_tool'
DISPLAY_NAME   = 'Advanced Profile Tool'
GROUP_NAME     = 'Advanced Flood & Terrain Auditor'
_LINKED_PROMPT = 'FTA_Normal_Profile_V01_GM.txt'

import csv
import math
import os
from collections import defaultdict
from datetime import datetime

from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer, QSizeF, QPointF, QObject, QEvent
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QDoubleSpinBox, QPushButton, QLineEdit,
    QFileDialog, QGroupBox, QFrame, QSizePolicy, QMessageBox,
    QApplication, QCheckBox, QComboBox, QColorDialog, QScrollArea,
    QListWidget, QListWidgetItem, QTabWidget, QInputDialog, QProgressBar,
    QSpinBox, QDialog, QMenu,
)
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap, QPainter, QPen, QStandardItem, QStandardItemModel, QTextDocument, QCursor

from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsRubberBand
from qgis.core import (
    Qgis, QgsProject, QgsWkbTypes, QgsGeometry, QgsPointXY,
    QgsSpatialIndex, QgsFieldProxyModel, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsMapLayer, QgsRectangle, QgsFeatureRequest,
    QgsVectorFileWriter, QgsFields, QgsFeature,
)

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
except AttributeError:
    _ICON_CIRCLE = QgsRubberBand.ICON_CIRCLE  # type: ignore[attr-defined]
    _ICON_X      = QgsRubberBand.ICON_X       # type: ignore[attr-defined]

try:
    _VECTOR_FILTER = QgsMapLayerProxyModel.Filter.VectorLayer
except AttributeError:
    _VECTOR_FILTER = QgsMapLayerProxyModel.VectorLayer  # type: ignore[attr-defined]

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer  # type: ignore[attr-defined]

try:
    _LINE_FILTER = QgsMapLayerProxyModel.Filter.LineLayer
except AttributeError:
    _LINE_FILTER = QgsMapLayerProxyModel.LineLayer  # type: ignore[attr-defined]

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

from .profile_line_tool import ProfileLineTool

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
        self._xs_dialogs          = []   # open XSectionDialog instances for cut/fill sync
        self._color_idx          = 0
        self._has_xsec_plot      = False
        self._xsec_dd            = None

        self._active_tab = 0   # 0 = Raster, 1 = Vector

        # Raster rows (manual, like vector rows)
        self._raster_rows = []

        # Vector rows
        self._vector_rows = []

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

        self._build_ui()
        self.visibilityChanged.connect(self._on_visibility_changed)

    # ------------------------------------------------------------------ colour

    def _next_color(self) -> str:
        c = _CHART_COLORS[self._color_idx % len(_CHART_COLORS)]
        self._color_idx += 1
        return c

    # ------------------------------------------------------------------ raster rows

    def _add_raster_row(self):
        row = {'_col': None}
        frame = QFrame()
        frame.setStyleSheet(
            'QFrame{border:1px solid #BDBDBD;border-radius:3px;margin-top:2px;}'
        )
        fl = QHBoxLayout(frame)
        fl.setContentsMargins(4, 3, 4, 3); fl.setSpacing(3)

        tog = QCheckBox(); tog.setChecked(True)
        tog.stateChanged.connect(self._refresh_plot)

        badge = QLabel()
        badge.setPixmap(_raster_type_pix(14))
        badge.setFixedSize(16, 16)
        badge.setToolTip('Raster layer')

        lc = QgsMapLayerComboBox()
        lc.setFilters(_RASTER_FILTER)
        lc.setAllowEmptyLayer(True)
        lc.setCurrentIndex(0)
        lc.layerChanged.connect(lambda _: self._trigger_update())
        lc.wheelEvent = lambda e: e.ignore()

        ls_combo = QComboBox()
        ls_combo.setFixedWidth(88)
        for code, label in _LINESTYLES:
            ls_combo.addItem(label, code)
        ls_combo.currentIndexChanged.connect(self._refresh_plot)
        ls_combo.wheelEvent = lambda e: e.ignore()

        hex_c = self._next_color()
        row['color'] = QColor(hex_c)
        c_btn = _color_btn(hex_c)
        c_btn.clicked.connect(lambda: self._pick_raster_color(row))

        lw_spin = QDoubleSpinBox()
        lw_spin.setRange(0.5, 5.0); lw_spin.setValue(1.5); lw_spin.setSingleStep(0.5)
        lw_spin.setDecimals(1); lw_spin.setFixedWidth(48)
        lw_spin.setToolTip('Line width')
        lw_spin.valueChanged.connect(self._refresh_plot)

        al_spin = QSpinBox()
        al_spin.setRange(10, 100); al_spin.setValue(100); al_spin.setSuffix('%')
        al_spin.setFixedWidth(52)
        al_spin.setToolTip('Opacity')
        al_spin.valueChanged.connect(self._refresh_plot)

        rm = _rm_btn('Remove raster layer')
        rm.clicked.connect(lambda: self._remove_raster_row(row))

        fl.addWidget(tog); fl.addWidget(badge); fl.addWidget(lc, 1)
        fl.addWidget(ls_combo); fl.addWidget(lw_spin); fl.addWidget(al_spin)
        fl.addWidget(c_btn); fl.addWidget(rm)

        row.update({'widget': frame, 'toggle': tog,
                    'layer_combo': lc, 'ls_combo': ls_combo, 'color_btn': c_btn,
                    'lw_spin': lw_spin, 'al_spin': al_spin})
        self._raster_layout.insertWidget(self._raster_layout.count() - 1, frame)
        self._raster_rows.append(row)

    def _remove_raster_row(self, row):
        self._raster_rows.remove(row)
        row['widget'].setParent(None); row['widget'].deleteLater()
        self._trigger_update()

    def _pick_raster_color(self, row):
        c = QColorDialog.getColor(row['color'], self)
        if c.isValid():
            row['color'] = c
            row['color_btn'].setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;'
            )
            self._refresh_plot()

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

    def _on_tab_changed(self, idx):
        self._active_tab = idx
        # Clear all profile state — each tab starts fresh
        self.profile_geom = None
        if self._perm_band:
            try:
                self.canvas.scene().removeItem(self._perm_band)
            except Exception:
                pass
            self._perm_band = None
        self._map_tool.reset()
        self.btn_draw.setChecked(False)
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
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
        is_xsec = (idx == 2)
        if hasattr(self, 'pw'):
            self.pw.setVisible(not is_xsec)
        for combo in (self.cutfill_y1, self.cutfill_y2):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        root = QWidget()
        root.setMinimumWidth(320)
        self.setWidget(root)

        outer = QVBoxLayout(root)
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

        # ---- Layer tabs (Raster / Vector) --------------------------------
        tabs = QTabWidget()
        tabs.setStyleSheet(
            'QTabWidget::pane{border:1px solid #BDBDBD;border-top:none;}'
            'QTabBar::tab{padding:4px 12px;font-size:11px;}'
            'QTabBar::tab:selected{font-weight:bold;}'
        )

        # Raster tab — scrollable layer list
        raster_tab  = QWidget()
        rt_outer_l  = QVBoxLayout(raster_tab)
        rt_outer_l.setContentsMargins(0, 0, 0, 0)
        rt_sc = QScrollArea(); rt_sc.setWidgetResizable(True)
        rt_sc.setFrameShape(_NO_FRAME)
        rt_inner = QWidget()
        rt_l = QVBoxLayout(rt_inner)
        rt_l.setContentsMargins(4, 6, 4, 4); rt_l.setSpacing(3)
        self._raster_layout = QVBoxLayout()
        self._raster_layout.setSpacing(3)
        add_r = QPushButton('+ Add Raster Layer')
        add_r.setStyleSheet('color:#2196F3;font-size:11px;border:none;padding:2px;')
        add_r.clicked.connect(self._add_raster_row)
        self._raster_layout.addWidget(add_r)
        rt_l.addLayout(self._raster_layout)
        rt_l.addStretch()
        rt_sc.setWidget(rt_inner)
        rt_outer_l.addWidget(rt_sc)
        tabs.addTab(raster_tab,
                    QIcon(QPixmap(_raster_type_pix(12))), 'Raster')

        # Vector tab — scrollable layer list
        vector_tab  = QWidget()
        vt_outer_l  = QVBoxLayout(vector_tab)
        vt_outer_l.setContentsMargins(0, 0, 0, 0)
        vt_sc = QScrollArea(); vt_sc.setWidgetResizable(True)
        vt_sc.setFrameShape(_NO_FRAME)
        vt_inner = QWidget()
        vt_l = QVBoxLayout(vt_inner)
        vt_l.setContentsMargins(4, 6, 4, 4); vt_l.setSpacing(3)
        self._vector_layout = QVBoxLayout()
        self._vector_layout.setSpacing(4)
        add_v = QPushButton('+ Add Vector Layer')
        add_v.setStyleSheet('color:#43A047;font-size:11px;border:none;padding:2px;')
        add_v.clicked.connect(self._add_vector_row)
        self._vector_layout.addWidget(add_v)
        vt_l.addLayout(self._vector_layout)
        vt_l.addStretch()
        vt_sc.setWidget(vt_inner)
        vt_outer_l.addWidget(vt_sc)
        tabs.addTab(vector_tab,
                    QIcon(QPixmap(_vector_type_pix(12))), 'Vector')

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

        tabs.setMinimumHeight(200)
        self._tabs = tabs
        tabs.currentChanged.connect(self._on_tab_changed)
        sc.addWidget(tabs)

        # ---- Profile Windows group (shown only for Raster and Vector tabs)
        self.pw = pw = QGroupBox('Profile Windows (split Y-range)')
        pw_l = QVBoxLayout(pw)
        pw_l.setContentsMargins(4, 10, 4, 4)
        pw_l.setSpacing(3)

        _default_win_names = ['Elevation [masl]', 'Depth [m]', 'Velocity [m/s]']

        self._win_cfgs = []
        for i in range(3):
            row = QHBoxLayout()
            en_cb = QCheckBox(f'Win {i + 1}')
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

            # Row 2: window name (used as Y-axis label)
            row_name = QHBoxLayout()
            row_name.addSpacing(20)
            row_name.addWidget(QLabel('Name:'))
            name_edit = QLineEdit(_default_win_names[i])
            name_edit.setPlaceholderText('Y-axis label')
            name_edit.setFixedHeight(22)
            name_edit.textChanged.connect(self._refresh_plot)
            row_name.addWidget(name_edit, 1)
            pw_l.addLayout(row_name)

            # Row 3: data/layer assignment for this window
            row2 = QHBoxLayout()
            row2.addSpacing(20)
            row2.addWidget(QLabel('Data:'))
            col_combo = _CheckCombo()
            col_combo.setToolTip('Check columns to show in this window; nothing checked = All')
            col_combo.selectionChanged.connect(lambda _idx=i: self._on_win_col_changed(_idx))
            row2.addWidget(col_combo, 1)
            row2.addStretch()
            pw_l.addLayout(row2)

            # Row 4: per-window cut/fill shading
            row_cf = QHBoxLayout()
            row_cf.addSpacing(20)
            cf_cb = QCheckBox('Cut/fill')
            cf_cb.stateChanged.connect(self._refresh_plot)
            row_cf.addWidget(cf_cb)
            row_cf.addWidget(QLabel('Y1:'))
            cf_y1 = QComboBox(); cf_y1.wheelEvent = lambda e: e.ignore()
            cf_y1.currentIndexChanged.connect(self._refresh_plot)
            row_cf.addWidget(cf_y1, 1)
            row_cf.addWidget(QLabel('Y2:'))
            cf_y2 = QComboBox(); cf_y2.wheelEvent = lambda e: e.ignore()
            cf_y2.currentIndexChanged.connect(self._refresh_plot)
            row_cf.addWidget(cf_y2, 1)
            pw_l.addLayout(row_cf)

            self._win_cfgs.append({
                'enabled_cb': en_cb, 'auto_cb': auto_cb,
                'ymin': ymin_sp,    'ymax': ymax_sp,
                'name_edit': name_edit,
                'col_combo': col_combo,
                'cutfill_cb': cf_cb,
                'cf_y1':      cf_y1,
                'cf_y2':      cf_y2,
            })

        sc.addWidget(pw)

        # ---- Draw controls -----------------------------------------------
        dr = QHBoxLayout()
        self.btn_draw = QPushButton('Draw Profile Line')
        self.btn_draw.setCheckable(True)
        self.btn_draw.setToolTip(
            'Left-click to add vertices.\nRight-click or double-click to finish.'
        )
        self.btn_draw.clicked.connect(self._toggle_digitizing)
        self.btn_clear = QPushButton('Clear')
        self.btn_clear.setFixedWidth(55)
        self.btn_clear.clicked.connect(self._clear_line)
        dr.addWidget(self.btn_draw); dr.addWidget(self.btn_clear)

        # ---- Layer-based profile line selection --------------------------
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
        btn_use_layer.clicked.connect(self._use_line_layer)
        ll.addWidget(btn_use_layer)

        self.lbl_line = QLabel('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')

        # Map profile line style (width + opacity)
        mls = QHBoxLayout()
        mls.setSpacing(4)
        mls.addWidget(QLabel('Map line:'))
        self._map_lw_spin = QDoubleSpinBox()
        self._map_lw_spin.setRange(0.5, 10.0)
        self._map_lw_spin.setValue(3.0)
        self._map_lw_spin.setSingleStep(0.5)
        self._map_lw_spin.setDecimals(1)
        self._map_lw_spin.setFixedWidth(60)
        self._map_lw_spin.setToolTip('Profile line width on map canvas (px)')
        self._map_lw_spin.valueChanged.connect(self._apply_map_line_style)
        mls.addWidget(self._map_lw_spin)
        mls.addWidget(QLabel('px'))
        mls.addSpacing(8)
        mls.addWidget(QLabel('Opacity:'))
        self._map_op_spin = QSpinBox()
        self._map_op_spin.setRange(5, 100)
        self._map_op_spin.setValue(100)
        self._map_op_spin.setSuffix('%')
        self._map_op_spin.setFixedWidth(60)
        self._map_op_spin.setToolTip('Profile line opacity on map canvas')
        self._map_op_spin.valueChanged.connect(self._apply_map_line_style)
        mls.addWidget(self._map_op_spin)
        mls.addSpacing(8)
        self._map_line_color_btn = _color_btn('#39FF14', 'Map line colour')
        self._map_line_color_btn.clicked.connect(self._pick_map_line_color)
        mls.addWidget(self._map_line_color_btn)
        mls.addStretch()

        # ---- Sampling interval (OUTSIDE scroll — always visible) ----------
        iv = QHBoxLayout()
        iv.addWidget(QLabel('Sampling Interval:'))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.001, 100000.0)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setDecimals(3)
        self.interval_spin.setSuffix(' m')
        self.interval_spin.setFixedWidth(110)
        iv.addWidget(self.interval_spin); iv.addStretch()

        # ---- Always-visible draw controls (outside scroll) ---------------
        outer.addLayout(dr)
        outer.addLayout(ll)
        outer.addWidget(self.lbl_line)
        outer.addLayout(mls)
        outer.addLayout(iv)

        # ---- Fixed bottom section ----------------------------------------
        outer.addWidget(_sep())

        cr = QHBoxLayout()
        cr.addWidget(QLabel('Result Folder:'))
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText('Leave blank to auto-save to Downloads/ProfilePlot_<Date>')
        btn_csv = QPushButton('...')
        btn_csv.setFixedWidth(28)
        btn_csv.clicked.connect(self._browse_result_folder)
        cr.addWidget(self.csv_edit); cr.addWidget(btn_csv)
        outer.addLayout(cr)

        br = QHBoxLayout()
        self.btn_run = QPushButton('Run / Refresh Profile')
        self.btn_run.setStyleSheet('font-weight:bold;padding:5px;')
        self.btn_run.clicked.connect(self._run)
        br.addWidget(self.btn_run)
        outer.addLayout(br)
        self.btn_save_png = None  # created in _setup_chart_dock

        self.btn_open_chart = QPushButton('Show / Hide Chart')
        self.btn_open_chart.setStyleSheet('padding:4px;')
        self.btn_open_chart.clicked.connect(self._on_open_chart)
        outer.addWidget(self.btn_open_chart)

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
                ('pen',    'Pen',    'Freehand pen — click and drag'),
                ('line',   'Line',   'Straight line — click and drag'),
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
        self.ax.set_title('Normal Profile', fontsize=10, fontweight='bold')

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

    def _collect_entries(self):
        raster_entries, vector_entries, col_meta = [], [], {}
        used = set()

        def uniq(name):
            if name not in used:
                used.add(name); return name
            i = 1
            while f'{name} ({i})' in used:
                i += 1
            n = f'{name} ({i})'; used.add(n); return n

        if self._active_tab == 0:
            # Raster tab: only raster rows
            for row in self._raster_rows:
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
        else:
            # Vector tab: only vector rows
            for vec in self._vector_rows:
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

    def _collect_plot_meta(self):
        meta = {}
        if self._active_tab == 0:
            for row in self._raster_rows:
                if row.get('_col'):
                    meta[row['_col']] = {
                        'color':     row['color'],
                        'visible':   row['toggle'].isChecked(),
                        'linestyle': row['ls_combo'].currentData(),
                        'linewidth': row['lw_spin'].value() if 'lw_spin' in row else 1.5,
                        'alpha':     row['al_spin'].value() / 100.0 if 'al_spin' in row else 1.0,
                    }
        else:
            for vec in self._vector_rows:
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
        if self._active_tab in (0, 1) and self.profile_geom is not None:
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
        if not self._profile_chainages or not MATPLOTLIB_AVAILABLE:
            return
        self._plot(self._profile_chainages, self._profile_data_store,
                   self._collect_plot_meta())

    # ------------------------------------------------------------------ map tool

    def _toggle_digitizing(self, checked):
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
        self.profile_geom        = None
        self._profile_chainages  = []
        self._profile_data_store = {}
        self._ch_cursors         = []
        self._ch_cursor_artists  = []
        self._clear_cursor_map_points()
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
            self._hide_hover()

    # ------------------------------------------------------------------ live preview

    def _on_live_update(self, partial_geom):
        if self._active_tab not in (0, 1):
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
        if (event.button == 1 and self._sketch_mode is not None
                and event.inaxes is not None
                and event.xdata is not None and event.ydata is not None):
            self._sketch_on_press(event)

    def _on_mouse_release(self, event):
        if event.button == 2:
            self._pan_press_px = self._pan_xlim0 = \
                self._pan_ylim0 = self._pan_transform = None
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

    def _on_chart_hover(self, event):
        if not MATPLOTLIB_AVAILABLE:
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

        if _hi is not None:
            # Per-window ΔY annotation (shown inside the plot axes, not in a bottom bar)
            _all_p2 = [self.ax] + self._extra_axes
            for _j2, _ax2 in enumerate(_all_p2):
                _ann = self._cf_ann_texts.get(_j2)
                _cfg2 = self._win_cfgs[_j2] if _j2 < len(self._win_cfgs) else None
                if _ann is None or _cfg2 is None:
                    continue
                if (self._active_tab != 2 and _cfg2['cutfill_cb'].isChecked()
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
        dlg.finished.connect(
            lambda _r, d=dlg: self._xs_dialogs.remove(d) if d in self._xs_dialogs else None)
        dlg.show()
        dlg.raise_()

    def _make_perp_line_geom(self, chainage, left_m, right_m):
        """Return a QgsGeometry line perpendicular to the profile at the given chainage.

        'Left' is the CCW direction from the tangent (left when walking along profile).
        The returned line runs from the left end to the right end.
        """
        if self.profile_geom is None:
            return None
        total  = self.profile_geom.length()
        delta  = min(0.5, chainage, total - chainage)
        delta  = max(delta, 0.001)
        ch1    = max(0.0, chainage - delta)
        ch2    = min(total, chainage + delta)
        pt1    = self.profile_geom.interpolate(ch1).asPoint()
        pt2    = self.profile_geom.interpolate(ch2).asPoint()
        dx, dy = pt2.x() - pt1.x(), pt2.y() - pt1.y()
        dist   = math.sqrt(dx * dx + dy * dy)
        if dist < 1e-10:
            return None
        tx, ty = dx / dist, dy / dist   # tangent unit vector
        px, py = -ty, tx                # perpendicular CCW = "left"
        ctr    = self.profile_geom.interpolate(chainage).asPoint()
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

        for cursor in self._ch_cursors:
            ch      = cursor['chainage']
            ax_idx  = cursor['ax_idx']
            if ax_idx >= len(all_p):
                ax_idx = 0
            ax_c = all_p[ax_idx]

            # Cut/fill ΔY at this chainage
            cf_val = None
            if xs is not None and len(xs) > 1:
                cfg_j = self._win_cfgs[ax_idx] if ax_idx < len(self._win_cfgs) else None
                if (cfg_j is not None and self._active_tab != 2
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

            lbl_top = ax_c.text(
                ch, 0.97, top_text,
                transform=blend, rotation=90, rotation_mode='anchor',
                fontsize=7, color=top_color,
                ha='right', va='top', zorder=11,
                bbox=top_bbox)

            # Bottom-right: XS name
            lbl_name = ax_c.text(
                ch, 0.03, cursor.get('name', ''),
                transform=blend, rotation=90, rotation_mode='anchor',
                fontsize=7, color='#212121',
                ha='left', va='bottom', zorder=11,
                bbox=_bbox_kw)

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

    def _sketch_on_press(self, event):
        self._sketch_pressed = True
        ax = event.inaxes
        fx, fy = event.xdata, event.ydata
        self._sketch_press_data = (fx, fy)

        if self._sketch_mode == 'pen':
            ctrl = self._ctrl_tracker.ctrl_held
            if not ctrl:
                try:
                    ctrl = bool(QApplication.queryKeyboardModifiers() & _CTRL_MOD)
                except Exception:
                    ctrl = False

            if ctrl:
                self._sketch_pressed = False
                if not self._pen_poly_mode:
                    self._pen_poly_mode = True
                    self._pen_poly_pts  = ([fx], [fy])
                    line, = ax.plot(
                        [fx, fx], [fy, fy],
                        color=self._sketch_color, linewidth=self._sketch_lw,
                        linestyle=self._sketch_ls,
                        solid_capstyle='round', solid_joinstyle='round', zorder=10
                    )
                    self._pen_poly_art = line
                    self._sketch_objects.append(line)
                else:
                    self._pen_poly_pts[0].append(fx)
                    self._pen_poly_pts[1].append(fy)
                    self._pen_poly_art.set_data(*self._pen_poly_pts)
                self.canvas_plot.draw_idle()
                return
            else:
                if self._pen_poly_mode:
                    self._pen_poly_pts[0].append(fx)
                    self._pen_poly_pts[1].append(fy)
                    self._pen_poly_art.set_data(*self._pen_poly_pts)
                    self._pen_poly_mode = False
                    self._pen_poly_pts  = ([], [])
                    self._pen_poly_art  = None
                    self._sketch_pressed = False
                    self.canvas_plot.draw_idle()
                    return
                # Normal freehand pen
                self._sketch_pen_pts = ([fx], [fy])
                line, = ax.plot(
                    self._sketch_pen_pts[0], self._sketch_pen_pts[1],
                    color=self._sketch_color, linewidth=self._sketch_lw,
                    linestyle=self._sketch_ls,
                    solid_capstyle='round', solid_joinstyle='round', zorder=10
                )
                self._sketch_current = (line, ax)
                self._sketch_objects.append(line)

        elif self._sketch_mode == 'line':
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

    def _add_vector_row(self):
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
        lc.layerChanged.connect(lambda _: (self._refresh_col_previews(), self._trigger_update()))
        lc.wheelEvent = lambda e: e.ignore()
        rm = _rm_btn('Remove vector layer')
        rm.clicked.connect(lambda: self._remove_vector_row(vec))
        hdr.addWidget(lc, 1); hdr.addWidget(rm)
        fl.addLayout(hdr)

        zf_widget = QWidget()
        zf_layout = QVBoxLayout(zf_widget)
        zf_layout.setContentsMargins(0, 0, 0, 0); zf_layout.setSpacing(2)
        fl.addWidget(zf_widget)

        add_z = QPushButton('+ Add Z-field')
        add_z.setStyleSheet('color:#43A047;font-size:10px;border:none;')
        add_z.clicked.connect(lambda: self._add_zfield_row(vec))
        zf_layout.addWidget(add_z)

        vec.update({'widget': frame, 'layer_combo': lc, 'zf_layout': zf_layout})
        self._vector_layout.insertWidget(self._vector_layout.count() - 1, frame)
        self._vector_rows.append(vec)
        self._add_zfield_row(vec)

    def _remove_vector_row(self, vec):
        self._vector_rows.remove(vec)
        vec['widget'].setParent(None); vec['widget'].deleteLater()
        self._trigger_update()

    def _add_zfield_row(self, vec):
        _auto_ls = ['-', '--', ':', '-.']
        _pos  = len(vec['z_fields'])
        hex_c = vec['z_fields'][0]['color'].name() if _pos > 0 else self._next_color()
        zf = {'color': QColor(hex_c), '_col': None}
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(3)
        tog = QCheckBox(); tog.setChecked(True)
        tog.stateChanged.connect(self._refresh_plot)
        badge = QLabel()
        badge.setPixmap(_vector_type_pix(14))
        badge.setFixedSize(16, 16)
        badge.setToolTip('Vector layer')
        fc = QgsFieldComboBox()
        try: fc.setFilters(_NUMERIC_FILTER)
        except Exception: pass
        fc.setLayer(vec['layer_combo'].currentLayer())
        vec['layer_combo'].layerChanged.connect(fc.setLayer)
        fc.fieldChanged.connect(lambda _: self._on_zfield_changed())
        fc.wheelEvent = lambda e: e.ignore()
        ls_combo = QComboBox()
        ls_combo.setFixedWidth(88)
        for code, label in _LINESTYLES:
            ls_combo.addItem(label, code)
        _default_ls = _auto_ls[_pos] if _pos < len(_auto_ls) else '-'
        _ls_idx = next((i for i, (c, _) in enumerate(_LINESTYLES) if c == _default_ls), 0)
        ls_combo.setCurrentIndex(_ls_idx)
        ls_combo.currentIndexChanged.connect(self._refresh_plot)
        ls_combo.wheelEvent = lambda e: e.ignore()
        c_btn = _color_btn(hex_c)
        c_btn.clicked.connect(lambda: self._pick_zfield_color(vec, zf))

        _lw = QDoubleSpinBox()
        _lw.setRange(0.5, 5.0); _lw.setValue(1.5); _lw.setSingleStep(0.5)
        _lw.setDecimals(1); _lw.setFixedWidth(48)
        _lw.setToolTip('Line width')
        _lw.valueChanged.connect(self._refresh_plot)

        _al = QSpinBox()
        _al.setRange(10, 100); _al.setValue(100); _al.setSuffix('%')
        _al.setFixedWidth(52)
        _al.setToolTip('Opacity')
        _al.valueChanged.connect(self._refresh_plot)

        r_btn = QPushButton('−'); r_btn.setFixedSize(22, 22)
        r_btn.setStyleSheet('color:#E53935;font-weight:bold;font-size:16px;')
        r_btn.clicked.connect(lambda: self._remove_zfield_row(vec, zf))
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
        self._refresh_col_previews()

    def _on_zfield_changed(self):
        self._refresh_col_previews()
        if self._active_tab == 1 and self.profile_geom is not None:
            self._run()
        else:
            self._trigger_update()

    def _refresh_col_previews(self):
        """Update Profile Window dropdowns from current field config without running extraction."""
        raster_entries, vector_entries, _ = self._collect_entries()
        col_names = [col for _, col in raster_entries] + [col for _, _, col in vector_entries]
        if col_names:
            self._update_win_col_combos(col_names)

    def _remove_zfield_row(self, vec, zf):
        if len(vec['z_fields']) <= 1: return
        vec['z_fields'].remove(zf)
        zf['widget'].setParent(None); zf['widget'].deleteLater()
        self._refresh_col_previews()
        self._trigger_update()

    def _pick_zfield_color(self, vec, zf):
        c = QColorDialog.getColor(zf['color'], self)
        if c.isValid():
            zf['color'] = c
            zf['color_btn'].setStyleSheet(
                f'background-color:{c.name()};border:1px solid #888;border-radius:2px;')
            self._refresh_plot()

    # ------------------------------------------------------------------ Save results / run

    def _default_output_folder(self):
        """Return ~/Downloads/ProfilePlot_YYYY-MM-DD, creating it if needed."""
        downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
        folder = os.path.join(downloads, 'ProfilePlot_' + datetime.now().strftime('%Y-%m-%d'))
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
        self.lbl_status.setText(f'Results saved to: {folder}')
        msg = '\n'.join(saved)
        if errors:
            msg += '\n\nErrors:\n' + '\n'.join(errors)
        QMessageBox.information(self, 'Results Saved', msg)

    def _run(self):
        if self.profile_geom is None:
            QMessageBox.warning(self, 'FTA Profile', 'Draw a profile line first.')
            return
        raster_entries, vector_entries, col_meta = self._collect_entries()
        if not raster_entries and not vector_entries:
            QMessageBox.warning(self, 'FTA Profile',
                'No layers selected. Check at least one raster or add a vector Z-field.')
            return
        if QgsProject.instance().crs().isGeographic():
            QMessageBox.critical(self, 'FTA Profile — CRS Error',
                'Project CRS is Geographic (degrees).\n'
                'Switch to a Projected Metric CRS first.')
            return
        self.btn_run.setEnabled(False)
        self.progress.setRange(0, 0)  # indeterminate while running
        self.lbl_status.setText('Running…')
        QApplication.processEvents()
        try:
            profile_data, chainages = self._extract(
                self.profile_geom, raster_entries, vector_entries,
                self.interval_spin.value()
            )
        except Exception as exc:
            QMessageBox.critical(self, 'FTA Profile Error', str(exc))
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

            # Per-window cut/fill shading (not applicable in X-Section tab)
            if (self._active_tab != 2 and cfg_j is not None
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
                                       color='#F44336', alpha=0.20, interpolate=True)
                    ax_j.fill_between(xs, _cf1, _cf2, where=_cfv & (_cf2 < _cf1),
                                       color='#1565C0', alpha=0.20, interpolate=True)

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
                ('pen',    'Pen',  'Freehand pen — drag'),
                ('line',   'Line', 'Straight line — drag'),
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
            outer.addWidget(self.canvas_xs, 1)
        else:
            outer.addWidget(QLabel('Matplotlib is not available.'))

    # ------------------------------------------------------------------ extent

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
                if (cfg is not None and pdock._active_tab != 2
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

        if self._xs_sketch_mode == 'pen':
            ctrl = False
            try:
                ctrl = self.parent_dock._ctrl_tracker.ctrl_held
                if not ctrl:
                    ctrl = bool(QApplication.queryKeyboardModifiers() & _CTRL_MOD)
            except Exception:
                ctrl = False
            if ctrl:
                self._xs_sketch_pressed = False
                if not self._xs_pen_poly_mode:
                    self._xs_pen_poly_mode = True
                    self._xs_pen_poly_pts  = ([fx], [fy])
                    line, = ax.plot(
                        [fx, fx], [fy, fy],
                        color=self._xs_sketch_color, linewidth=self._xs_sketch_lw,
                        linestyle=self._xs_sketch_ls,
                        solid_capstyle='round', solid_joinstyle='round', zorder=10
                    )
                    self._xs_pen_poly_art = line
                    self._xs_sketch_objects.append(line)
                else:
                    self._xs_pen_poly_pts[0].append(fx)
                    self._xs_pen_poly_pts[1].append(fy)
                    self._xs_pen_poly_art.set_data(*self._xs_pen_poly_pts)
                self.canvas_xs.draw_idle()
                return
            else:
                if self._xs_pen_poly_mode:
                    self._xs_pen_poly_pts[0].append(fx)
                    self._xs_pen_poly_pts[1].append(fy)
                    self._xs_pen_poly_art.set_data(*self._xs_pen_poly_pts)
                    self._xs_pen_poly_mode = False
                    self._xs_pen_poly_pts  = ([], [])
                    self._xs_pen_poly_art  = None
                    self._xs_sketch_pressed = False
                    self.canvas_xs.draw_idle()
                    return
                # Normal freehand pen
                self._xs_sketch_pen_pts = ([fx], [fy])
                line, = ax.plot([fx], [fy],
                                color=self._xs_sketch_color, linewidth=self._xs_sketch_lw,
                                linestyle=self._xs_sketch_ls,
                                solid_capstyle='round', solid_joinstyle='round', zorder=10)
                self._xs_sketch_current = (line,)
                self._xs_sketch_objects.append(line)

        elif self._xs_sketch_mode == 'line':
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


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

class FTAProfilePlugin:

    def __init__(self, iface):
        self.iface   = iface
        self.toolbar = None
        self.action  = None
        self.dock    = None

    def initGui(self):
        self.toolbar = self.iface.addToolBar('FTA Tools')
        self.toolbar.setObjectName('FTAToolsToolbar')
        import os as _os
        _icon_path = _os.path.join(_os.path.dirname(__file__), 'icon.svg')
        _icon = QIcon(_icon_path) if _os.path.exists(_icon_path) else QIcon()
        self.action = QAction(_icon, 'Normal Profile', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Advanced Profile Tool')
        self.action.triggered.connect(self._toggle_dock)
        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu('&FTA Tools', self.action)
        self.dock = NormalProfileDock(self.iface, self.iface.mainWindow())
        self.iface.addDockWidget(_RIGHT_DOCK, self.dock)
        self.dock.hide()

        # Chart dock — registered here (after iface is ready) so Qt properly
        # docks it into the main window. Canvas/toolbar are wired in next line.
        self._chart_dock = QDockWidget('FTA Profile — Chart', self.iface.mainWindow())
        self._chart_dock.setObjectName('FTANormalProfileChart')
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
            self.iface.removePluginMenu('&FTA Tools', self.action)
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
