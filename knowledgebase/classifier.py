"""Memory type classification using keyword markers. No LLM needed.

Classifies text into: decision, pattern, bug, preference, milestone, finding.
Based on MemPalace's general_extractor pattern.
"""

import re

MARKERS = {
    "decision": [
        r"\blet'?s (use|go with|try|pick|choose|switch to)\b",
        r"\bwe (should|decided|chose|went with|picked|settled on)\b",
        r"\b(decided|choosing|chose) to\b",
        r"\bbecause (the|this|it|we)\b",
        r"\btrade-?off\b",
        r"\binstead of\b",
        r"\bby design\b",
        r"\bintentional(?:ly)?\b",
    ],
    "pattern": [
        r"\bconvention is\b",
        r"\balways use\b",
        r"\bnever use\b",
        r"\bpattern\b.*\b(is|we use)\b",
        r"\bstandard (?:approach|way|practice)\b",
        r"\bconsistent(?:ly)?\b",
    ],
    "bug": [
        r"\broot cause\b",
        r"\bbug\b.*\b(was|is|in)\b",
        r"\bfix(?:ed)?\b.*\b(by|the|this)\b",
        r"\bbreaking change\b",
        r"\bworkaround\b",
        r"\bregression\b",
        r"\bcrash(?:es|ed|ing)?\b",
    ],
    "preference": [
        r"\bi prefer\b",
        r"\bdon'?t (?:like|want|use)\b",
        r"\bavoid\b.*\b(using|this)\b",
        r"\bbetter to\b",
        r"\bprefer(?:s|red|ence)?\b.*\bover\b",
    ],
    "milestone": [
        r"\bit works\b",
        r"\bfixed\b",
        r"\bbreakthrough\b",
        r"\bfinally\b",
        r"\bsolved\b",
        r"\bcompleted?\b",
        r"\bshipping\b",
    ],
    "architecture": [
        r"\barchitect(?:ure|ural)?\b",
        r"\bstructur(?:e|al|ed)\b",
        r"\bdesign(?:ed)?\b.*\b(to|for|so)\b",
        r"\blayer(?:s|ed)?\b",
        r"\bmodule\b.*\b(for|that|which)\b",
        r"\bseparation of\b",
    ],
}

_COMPILED = {
    mtype: [re.compile(p, re.IGNORECASE) for p in patterns]
    for mtype, patterns in MARKERS.items()
}


def classify(text: str) -> tuple[str, float]:
    """Classify text into a memory type.

    Returns (type, confidence) where confidence is 0-1.
    """
    scores = {}
    for mtype, patterns in _COMPILED.items():
        score = sum(1 for p in patterns if p.search(text))
        if score > 0:
            scores[mtype] = score

    if not scores:
        return "finding", 0.1

    # Length bonus — longer = more likely to be meaningful
    length_bonus = 2 if len(text) > 500 else (1 if len(text) > 200 else 0)

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type] + length_bonus

    # Disambiguate: resolved bugs are milestones
    if best_type == "bug" and scores.get("milestone", 0) > 0:
        if any(w in text.lower() for w in ["fixed", "solved", "resolved", "works now"]):
            best_type = "milestone"

    confidence = min(1.0, best_score / 5.0)
    return best_type, confidence
