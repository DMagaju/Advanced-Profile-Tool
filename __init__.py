def classFactory(iface):
    from .plugin import FTAProfilePlugin
    return FTAProfilePlugin(iface)
