"""
plugin.py — FTA Profile Tool dock plugin.

Raster layers auto-populated from QGIS project via QListWidget (checkable).
Cross-Section Analysis: cut/fill shading + hydraulic area engine (Riemann sum).
c-c water-level spinbox; d-d auto-set to Zmin in window.
Stage-Area side subplot with proper Elevation (m AD) axis; no duplicate legend.
Live chart preview; hover sync to map canvas; scroll zoom; middle-mouse pan.
"""

import csv
from collections import defaultdict

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QDoubleSpinBox, QPushButton, QLineEdit,
    QFileDialog, QGroupBox, QFrame, QSizePolicy, QMessageBox,
    QApplication, QCheckBox, QComboBox, QColorDialog, QScrollArea,
    QListWidget, QListWidgetItem, QTabWidget,
)
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap, QPainter, QPen, QStandardItem, QStandardItemModel

from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsRubberBand
from qgis.core import (
    Qgis, QgsProject, QgsWkbTypes, QgsGeometry, QgsPointXY,
    QgsSpatialIndex, QgsFieldProxyModel, QgsMapLayerProxyModel,
    QgsRasterLayer, QgsMapLayer, QgsRectangle, QgsFeatureRequest,
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
except AttributeError:
    _ICON_CIRCLE = QgsRubberBand.ICON_CIRCLE  # type: ignore[attr-defined]

try:
    _VECTOR_FILTER = QgsMapLayerProxyModel.Filter.VectorLayer
except AttributeError:
    _VECTOR_FILTER = QgsMapLayerProxyModel.VectorLayer  # type: ignore[attr-defined]

try:
    _RASTER_FILTER = QgsMapLayerProxyModel.Filter.RasterLayer
except AttributeError:
    _RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer  # type: ignore[attr-defined]

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

# ---------------------------------------------------------------------------
# Matplotlib
# ---------------------------------------------------------------------------
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle as _MplRect
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
        super().__init__('Normal Profile V01', parent)
        self.iface  = iface
        self.canvas = iface.mapCanvas()

        # Profile state
        self.profile_geom        = None
        self._perm_band          = None
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

        rm = _rm_btn('Remove raster layer')
        rm.clicked.connect(lambda: self._remove_raster_row(row))

        fl.addWidget(tog); fl.addWidget(badge); fl.addWidget(lc, 1)
        fl.addWidget(ls_combo); fl.addWidget(c_btn); fl.addWidget(rm)

        row.update({'widget': frame, 'toggle': tog,
                    'layer_combo': lc, 'ls_combo': ls_combo, 'color_btn': c_btn})
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
        self.lbl_line.setText('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')
        self.lbl_status.setText('')
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

        self.lbl_line = QLabel('Profile line: not drawn')
        self.lbl_line.setStyleSheet('color:gray;font-style:italic;font-size:11px;')

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
        outer.addWidget(self.lbl_line)
        outer.addLayout(iv)

        # ---- Fixed bottom section ----------------------------------------
        outer.addWidget(_sep())

        cr = QHBoxLayout()
        cr.addWidget(QLabel('CSV (optional):'))
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText('Output CSV path — leave blank to skip')
        btn_csv = QPushButton('...')
        btn_csv.setFixedWidth(28)
        btn_csv.clicked.connect(self._browse_csv)
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
            self.btn_save_png = QPushButton('Save PNG')
            self.btn_save_png.setFixedWidth(80)
            self.btn_save_png.setEnabled(False)
            self.btn_save_png.clicked.connect(self._save_png)
            top_bar.addWidget(self.btn_save_png)
            plot_outer.addLayout(top_bar)
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
                ax_i.set_xlabel('Chainage (m)', fontsize=9)
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
        self.figure.clear()
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
                    }
        else:
            for vec in self._vector_rows:
                for zf in vec['z_fields']:
                    if zf.get('_col'):
                        meta[zf['_col']] = {
                            'color':     zf['color'],
                            'visible':   zf['toggle'].isChecked(),
                            'linestyle': zf['ls_combo'].currentData() if 'ls_combo' in zf else '-',
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
        self._perm_band.setColor(QColor(33, 150, 243, 200))
        self._perm_band.setWidth(2)
        self._perm_band.setToGeometry(geom, None)
        self.lbl_line.setText(f'Profile line: {geom.length():.2f} m — ready.')
        self.lbl_line.setStyleSheet('color:#43A047;font-style:italic;font-size:11px;')
        self.btn_draw.setChecked(False)
        self._toggle_digitizing(False)
        self._on_live_update(geom)  # live for both Raster and Vector tabs

    def _clear_line(self):
        self.profile_geom        = None
        self._profile_chainages  = []
        self._profile_data_store = {}
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

    def _on_mouse_release(self, event):
        if event.button == 2:
            self._pan_press_px = self._pan_xlim0 = \
                self._pan_ylim0 = self._pan_transform = None

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
        r_btn = QPushButton('−'); r_btn.setFixedSize(22, 22)
        r_btn.setStyleSheet('color:#E53935;font-weight:bold;font-size:16px;')
        r_btn.clicked.connect(lambda: self._remove_zfield_row(vec, zf))
        h.addWidget(tog); h.addWidget(badge)
        h.addWidget(QLabel('Z:')); h.addWidget(fc, 1)
        h.addWidget(ls_combo); h.addWidget(c_btn); h.addWidget(r_btn)
        zf.update({'widget': w, 'toggle': tog, 'combo': fc,
                   'ls_combo': ls_combo, 'color_btn': c_btn})
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
            _style = f'background-color:{c.name()};border:1px solid #888;border-radius:2px;'
            for _z in vec['z_fields']:
                _z['color'] = c
                _z['color_btn'].setStyleSheet(_style)
            self._refresh_plot()

    # ------------------------------------------------------------------ CSV / PNG / run

    def _browse_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save Profile CSV', '', 'CSV files (*.csv)')
        if path:
            if not path.lower().endswith('.csv'): path += '.csv'
            self.csv_edit.setText(path)

    def _save_png(self):
        if not MATPLOTLIB_AVAILABLE: return
        path, _ = QFileDialog.getSaveFileName(self, 'Save Chart PNG', '', 'PNG files (*.png)')
        if path:
            if not path.lower().endswith('.png'): path += '.png'
            self.figure.savefig(path, dpi=150, bbox_inches='tight')
            self.lbl_status.setText(f'Chart saved: {path}')

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
            self.lbl_status.setText('Failed — see error dialog.')
            return

        csv_path = self.csv_edit.text().strip()
        if csv_path:
            try:
                cols = list(profile_data.keys())
                with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f)
                    w.writerow(['Chainage_m'] + cols)
                    for i, ch in enumerate(chainages):
                        row = [ch]
                        for col in cols:
                            v = profile_data[col][i]
                            row.append('' if v is None else round(v, 4))
                        w.writerow(row)
                self.lbl_status.setText(
                    f'Done — {len(chainages)} pts, {len(profile_data)} layer(s).\n'
                    f'CSV: {csv_path}'
                )
            except Exception as exc:
                QMessageBox.critical(self, 'FTA Profile — CSV Error', str(exc))
                self.lbl_status.setText('Extraction OK — CSV write failed.')
        else:
            self.lbl_status.setText(
                f'Done — {len(chainages)} pts, {len(profile_data)} layer(s). (No CSV)'
            )
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
                ys    = np.array([v if v is not None else np.nan for v in vals], dtype=float)
                ax_j.plot(xs, ys, label=_prune_mid(col), color=color.name(),
                          linewidth=1.5, linestyle=ls)
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
        self.canvas_plot.draw()


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
        self.action.setToolTip('FTA — Normal Profile V01')
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
            try:
                self.dock._hover_band.reset(_POINT_GEOM)
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
