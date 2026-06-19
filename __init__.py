# __author__  = "Dipendra Magaju"
# __licence__ = "GNU General Public License v2 or later (GPLv2+)"

def classFactory(iface):
    from .plugin import FTAProfilePlugin
    return FTAProfilePlugin(iface)
