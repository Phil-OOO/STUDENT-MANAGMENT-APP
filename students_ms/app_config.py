import sys
import os

def get_base_path():
    """Get the correct base path whether running as script or PyInstaller exe."""
    if getattr(sys, 'frozen', False):
        # Running as compiled exe
        return sys._MEIPASS
    return os.path.abspath(os.path.dirname(__file__))

BASE_PATH = get_base_path()
TEMPLATE_FOLDER = os.path.join(BASE_PATH, 'templates')
STATIC_FOLDER = os.path.join(BASE_PATH, 'static')