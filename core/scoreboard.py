# core/scoreboard.py

from dataclasses import dataclass


@dataclass
class Scoreboard:
    my_score: int = 0
    opponent_score: int = 0

    def my_reward(self, points: int = 1) -> None:
        self.my_score += points

    def opponent_reward(self, points: int = 1) -> None:
        self.opponent_score += points

    def my_penalty(self, points: int = 1) -> None:
        self.my_score -= points

    def opponent_penalty(self, points: int = 1) -> None:
        self.opponent_score -= points

    # Backward compatible aliases (old names used in older code paths)
    def detected_error(self) -> None:
        self.my_reward(1)

    def my_invalid_undetected(self) -> None:
        self.my_reward(1)

    def opponent_timeout(self) -> None:
        self.opponent_penalty(1)

    def my_timeout(self) -> None:
        self.my_penalty(1)

    def snapshot(self) -> str:
        return f"Score: ME={self.my_score}, OPP={self.opponent_score}"
