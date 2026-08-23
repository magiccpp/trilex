"""Forgetting curve and scheduling (FSRS-4.5).

SM-2, which this replaces, only ever asked "how many days until next time?".
It had no notion of how much you still remember *right now*, so it could not
answer the question that actually matters: which words are closest to being
forgotten. This module models that directly.

Each card carries two latent variables:

    stability  S - days until recall probability falls to 90%
    difficulty D - 1..10, how much harder this card is than average

and retention decays along a power-law forgetting curve:

    R(t) = (1 + FACTOR * t / S) ^ DECAY

with FACTOR and DECAY chosen so that R(S) = 0.90 exactly. A review is due when
R drops to the target retention, and the review queue is ordered by *lowest R
first* - the words you are closest to losing come back first.

Weights are the published FSRS-4.5 defaults, fitted on a large public review
corpus. They are a good prior for a new user; `optimise_difficulty_prior` below
nudges them toward this particular user's observed accuracy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Grades, shared with srs.py
AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3
# FSRS speaks in 1..4; our buttons are 0..3.
_G = lambda grade: grade + 1

DECAY = -0.5
FACTOR = 19.0 / 81.0          # makes R(S) == 0.9

W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.0310, 1.6474,
     0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.5870, 0.2272, 2.8755)

MIN_STABILITY = 0.1
MAX_STABILITY = 365.0 * 10
DEFAULT_RETENTION = 0.90


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# --------------------------------------------------------------- the curve
def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recalling the card right now. 1.0 = just reviewed."""
    if stability <= 0:
        return 0.0
    return (1.0 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def interval_for(stability: float, retention: float = DEFAULT_RETENTION) -> float:
    """Days until retention falls to `retention`. Equals S when retention=0.9."""
    retention = clamp(retention, 0.70, 0.99)
    return (stability / FACTOR) * (retention ** (1.0 / DECAY) - 1.0)


def half_life(stability: float) -> float:
    """Days until you have a 50% chance of recall - the intuitive reading."""
    return interval_for(stability, 0.5)


def curve(stability: float, days: int = 60, step: int = 1):
    """Sample points for plotting the forgetting curve."""
    return [(d, retrievability(d, stability)) for d in range(0, days + 1, step)]


# ------------------------------------------------------- state transitions
def initial_stability(grade: int) -> float:
    return clamp(W[_G(grade) - 1], MIN_STABILITY, MAX_STABILITY)


def initial_difficulty(grade: int) -> float:
    return clamp(W[4] - (_G(grade) - 3) * W[5], 1.0, 10.0)


def next_difficulty(difficulty: float, grade: int) -> float:
    d = difficulty - W[6] * (_G(grade) - 3)
    # Mean reversion pulls difficulty back toward the "easy first answer"
    # baseline, so one bad day does not permanently mark a card as hard.
    d = W[7] * initial_difficulty(EASY) + (1.0 - W[7]) * d
    return clamp(d, 1.0, 10.0)


def stability_after_recall(difficulty, stability, r, grade) -> float:
    hard_penalty = W[15] if grade == HARD else 1.0
    easy_bonus = W[16] if grade == EASY else 1.0
    gain = (math.exp(W[8])
            * (11.0 - difficulty)
            * (stability ** -W[9])
            * (math.exp((1.0 - r) * W[10]) - 1.0)
            * hard_penalty * easy_bonus)
    return clamp(stability * (1.0 + gain), MIN_STABILITY, MAX_STABILITY)


def stability_after_lapse(difficulty, stability, r) -> float:
    s = (W[11]
         * (difficulty ** -W[12])
         * (((stability + 1.0) ** W[13]) - 1.0)
         * math.exp((1.0 - r) * W[14]))
    return clamp(min(s, stability), MIN_STABILITY, MAX_STABILITY)


@dataclass
class Memory:
    """The learned state of one card."""
    stability: float = 0.0
    difficulty: float = 0.0
    reps: int = 0
    lapses: int = 0

    @property
    def is_new(self) -> bool:
        return self.reps == 0 or self.stability <= 0

    def recall(self, elapsed_days: float) -> float:
        return 1.0 if self.is_new else retrievability(elapsed_days, self.stability)

    def review(self, grade: int, elapsed_days: float) -> "Memory":
        """Apply a grade and return the new memory state."""
        if self.is_new:
            return Memory(initial_stability(grade), initial_difficulty(grade),
                          1, 1 if grade == AGAIN else 0)
        r = self.recall(elapsed_days)
        d = next_difficulty(self.difficulty, grade)
        if grade == AGAIN:
            s = stability_after_lapse(self.difficulty, self.stability, r)
            return Memory(s, d, self.reps + 1, self.lapses + 1)
        s = stability_after_recall(self.difficulty, self.stability, r, grade)
        return Memory(s, d, self.reps + 1, self.lapses)

    def next_interval(self, retention=DEFAULT_RETENTION) -> float:
        return max(1.0, round(interval_for(self.stability, retention), 2))


def preview(mem: Memory, elapsed_days: float, retention=DEFAULT_RETENTION) -> dict:
    """Interval each button would produce, for the button labels."""
    return {g: mem.review(g, elapsed_days).next_interval(retention)
            for g in (AGAIN, HARD, GOOD, EASY)}


def optimise_difficulty_prior(reviews) -> float:
    """Crude personalisation: shift the starting difficulty toward this user's
    observed first-try accuracy. Full FSRS weight optimisation needs thousands
    of reviews and a gradient fit; this is the useful 5% of it that works on a
    few dozen, and it degrades to the published default when data is thin.

    `reviews` is an iterable of grades (0..3). Returns a difficulty offset.
    """
    grades = [g for g in reviews]
    if len(grades) < 20:
        return 0.0
    accuracy = sum(1 for g in grades if g != AGAIN) / len(grades)
    # 0.9 accuracy is the expected baseline; below that, start cards harder.
    return clamp((0.90 - accuracy) * 6.0, -2.0, 2.0)


def fmt_interval(days: float) -> str:
    if days < 1:
        return "today"
    if days < 30:
        return f"{days:.0f}d"
    if days < 365:
        return f"{days/30.44:.1f}mo"
    return f"{days/365.25:.1f}y"


def fmt_retention(r: float) -> str:
    return f"{100.0 * r:.0f}%"
