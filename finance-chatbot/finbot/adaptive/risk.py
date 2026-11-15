"""Session risk score: catches a jailbreak spread over several turns.

Each message alone may look harmless; the pattern across them does not.

    blocked attack       +2.0   (built-in prompt-attack screen or a learned input rule)
    probing language     +1.0   ("your instructions", "pretend", "bypass", ...)
    clean turn           -0.5   (decays, never below zero)

At GuardPolicy.risk_threshold the session is handed to a person and stays
locked: a new session, after a human looked, is the way back. The score lives
in SessionMemory, so it survives worker takeover with the rest of the state.
"""
from __future__ import annotations

import re

BLOCK_WEIGHT = 2.0
PROBE_WEIGHT = 1.0
DECAY = 0.5

PROBE = re.compile(r"\b(system prompt|your (instructions|rules|guidelines|configuration)|pretend|role-?play|"
                   r"jailbreak|developer mode|override|bypass|no restrictions|hidden (rules|prompt))\b", re.I)


def next_score(score: float, *, attack: bool, text: str) -> float:
    if attack:
        return score + BLOCK_WEIGHT
    if PROBE.search(text):
        return score + PROBE_WEIGHT
    return max(0.0, score - DECAY)
