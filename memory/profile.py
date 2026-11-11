"""Learned profile: habits, routines, preferences gathered over time."""
from .store import MemoryStore


class Profile:
    def __init__(self, store: MemoryStore):
        self.store = store

    def learn(self, fact: str, category: str = "general", confirmed: bool = False) -> None:
        self.store.add_fact(fact, category=category, confirmed=confirmed)

    def confirm(self, fact: str) -> None:
        """Promote an unconfirmed observation to a confirmed fact."""
        rows = self.store.facts()
        for row in rows:
            if not row.get("confirmed") and row["fact"] == fact:
                self.store._conn.execute(
                    "UPDATE profile_facts SET confirmed = 1 WHERE fact = ?", (fact,),
                )
                self.store._conn.commit()
                return

    def summary(self) -> str:
        facts = self.store.facts()
        if not facts:
            return "I have not learned much about you yet."
        lines = [f"- {f['fact']}" for f in facts]
        return "What I know about you:\n" + "\n".join(lines)

    def pending_observations(self) -> list[str]:
        rows = self.store._conn.execute(
            "SELECT fact FROM profile_facts WHERE confirmed = 0",
        ).fetchall()
        return [r["fact"] for r in rows]

    def nightly_summary(self) -> None:
        """Consolidate today's unconfirmed observations into confirmed facts
        (runs at a low-activity hour)."""
        rows = self.store._conn.execute(
            "SELECT id, fact FROM profile_facts WHERE confirmed = 0",
        ).fetchall()
        for r in rows:
            self.store._conn.execute(
                "UPDATE profile_facts SET confirmed = 1 WHERE id = ?", (r["id"],),
            )
        self.store._conn.commit()

