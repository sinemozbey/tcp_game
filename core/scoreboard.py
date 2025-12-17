# core/scoreboard.py

from dataclasses import dataclass


@dataclass
class Scoreboard:
    my_score: int = 0
    opponent_score: int = 0

    def detected_error(self):
        self.my_score += 1

    def my_invalid_undetected(self):
        self.my_score += 1

    def opponent_timeout(self):
        # Opponent fails to respond within 30s → they lose 1 point (we record it as +1 to us or -1 to them?)
        # Spec says: "the side who must send a response will lose 1 point."
        # Here we treat it as: my_score += 1 (easier to reason in one place)
        self.opponent_score -= 1

    def my_timeout(self):
        # Our local timeout – we lose 1 point
        self.my_score -= 1

    def snapshot(self) -> str:
        return f"Score: ME={self.my_score}, OPP={self.opponent_score}"
