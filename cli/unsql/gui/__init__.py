"""
unsql.gui
---------
GUI visualizer package — fixes the 'terminal-only' limitation.
Provides terminal viewer (Rich) + web dashboard (http.server).
No extra deps beyond rich (already in dependencies).
"""
from .visualizer import GUIVisualizer, GUIConfig, get_visualizer, launch_web, launch_terminal

__all__ = ["GUIVisualizer", "GUIConfig", "get_visualizer", "launch_web", "launch_terminal"]
