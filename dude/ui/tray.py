"""System tray + status indicator using PyQt5.

Provides a minimal, always-available UI: tray icon with start/stop/mute,
and a small status overlay showing listening/speaking state.
"""
import sys


class TrayApp:
    """Thin wrapper that launches a PyQt5 tray app on a worker thread.

    The Qt event loop must run on the main thread on most platforms; call
    run() from the main thread of your program.
    """

    def __init__(self, agent):
        self.agent = agent
        self._app = None
        self._tray = None

    def run(self) -> int:
        try:
            from PyQt5 import QtWidgets
            from PyQt5.QtGui import QIcon
        except ImportError:
            print("PyQt5 not available; running headless.")
            return self._run_headless()

        self._app = QtWidgets.QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._tray = self._build_tray(QIcon)
        self.agent.set_event_callback(self._on_event)
        self.agent.start(greet=True)
        return self._app.exec_()

    def _build_tray(self, QIcon):
        from PyQt5 import QtWidgets
        tray = QtWidgets.QSystemTrayIcon(self._app)
        tray.setIcon(QIcon())  # placeholder icon
        tray.setToolTip("DUDE - Personal Assistant")

        menu = QtWidgets.QMenu()
        act_status = menu.addAction("Listening...")
        act_status.setEnabled(False)
        menu.addSeparator()
        act_stop = menu.addAction("Pause / Sleep")
        act_stop.triggered.connect(self._toggle_sleep)
        act_quit = menu.addAction("Quit")
        act_quit.triggered.connect(self._quit)
        tray.setContextMenu(menu)
        tray.show()
        self._status_action = act_status
        return tray

    def _on_event(self, event: str, data: dict) -> None:
        if not self._status_action:
            return
        labels = {
            "listening": "Listening...",
            "speaking": "Speaking...",
            "thinking": "Thinking...",
            "greeted": "Active",
            "idle": "Idle",
        }
        if event in labels:
            self._status_action.setText(labels[event])

    def _toggle_sleep(self) -> None:
        if self.agent.running:
            self.agent.stop()
            if self._status_action:
                self._status_action.setText("Sleeping (wake word)"
                                            if self.agent._wake_word else "Sleeping")
        else:
            self.agent.start(greet=False)

    def _quit(self) -> None:
        self.agent.stop()
        if self._app:
            self._app.quit()

    def _run_headless(self) -> int:
        self.agent.start(greet=True)
        return 0

