"""Session state: where the user left off, for greeting resume."""
import datetime

from .store import MemoryStore


class SessionState:
    def __init__(self, store: MemoryStore):
        self.store = store

    def record(self, app: str = "", file: str = "", task: str = "") -> None:
        self.store.update_session(last_app=app, last_file=file, last_task=task)

    def resume_point(self) -> dict | None:
        return self.store.latest_session()

    def greeting_message(self) -> str:
        """Natural resume line for the smart greeting."""
        session = self.resume_point()
        if not session or not (session.get("last_task") or session.get("last_app")):
            return ""
        hour = datetime.datetime.now().hour
        when = "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")
        if session.get("last_task"):
            return (f"Good {when}. Last time you were working on {session['last_task']} "
                    f"around {self._time_str(session.get('last_seen'))}. Shall we continue?")
        if session.get("last_app"):
            return (f"Good {when}. The last app you had open was {session['last_app']} "
                    f"around {self._time_str(session.get('last_seen'))}.")
        return f"Good {when}."

    @staticmethod
    def _time_str(iso: str | None) -> str:
        if not iso:
            return "recently"
        try:
            dt = datetime.datetime.fromisoformat(iso)
            return dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            return "recently"

