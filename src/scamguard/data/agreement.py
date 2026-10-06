"""Übereinstimmung zwischen Einstufenden (Inter-Rater-Reliabilität).

Krippendorffs Alpha (nominal) ist das Hauptmaß: Es funktioniert mit zwei oder drei Personen, mit
fehlenden Werten (nicht jede Person stuft jeden Text ein) und korrigiert Zufallstreffer. Dazu
paarweise Cohens Kappa (verbreitetes Vergleichsmaß) und die einfache prozentuale Übereinstimmung.

Lesart nach Krippendorff (2004, Content Analysis, Kap. 11):
  α ≥ 0,800  verlässlich
  α ≥ 0,667  vorläufig verwendbar
  darunter   Kategorien oder Leitfaden schärfen, dann erneut messen
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from itertools import combinations


def krippendorff_alpha(units: Mapping[Hashable, Sequence[Hashable]]) -> float | None:
    """units: Einheit (z. B. Aufgabe) → Werte der Personen; fehlende Werte einfach weglassen.

    None, wenn nicht berechenbar: keine Einheit mit mindestens zwei Werten oder alle Werte gleich
    (dann gibt es keine erwartete Abweichung – Übereinstimmung siehe percent_agreement)."""
    coincidence: Counter[tuple[Hashable, Hashable]] = Counter()
    for values in units.values():
        m = len(values)
        if m < 2:
            continue
        for i, a in enumerate(values):
            for j, b in enumerate(values):
                if i != j:
                    coincidence[(a, b)] += 1 / (m - 1)
    totals: Counter[Hashable] = Counter()
    for (a, _), weight in coincidence.items():
        totals[a] += weight
    n = sum(totals.values())
    expected = n * n - sum(v * v for v in totals.values())  # Σ über c≠k von n_c · n_k
    if n <= 1 or expected <= 1e-12:
        return None
    observed = sum(weight for (a, b), weight in coincidence.items() if a != b)
    return 1 - (n - 1) * observed / expected


def percent_agreement(units: Mapping[Hashable, Sequence[Hashable]]) -> float | None:
    """Anteil der mehrfach eingestuften Einheiten, bei denen alle dasselbe gewählt haben."""
    shared = [values for values in units.values() if len(values) >= 2]
    if not shared:
        return None
    return sum(len(set(values)) == 1 for values in shared) / len(shared)


def shared_units(units: Mapping[Hashable, Sequence[Hashable]]) -> int:
    return sum(len(values) >= 2 for values in units.values())


def cohen_kappa(pairs: Sequence[tuple[Hashable, Hashable]]) -> float | None:
    """Cohens Kappa für zwei Personen; pairs = (Wert A, Wert B) je gemeinsamer Einheit."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[c] * right[c] for c in left) / (n * n)
    if expected >= 1 - 1e-12:
        return None  # beide immer dieselbe eine Kategorie → Kappa nicht definiert
    return (observed - expected) / (1 - expected)


def pairwise_kappa(by_person: Mapping[str, Mapping[Hashable, Hashable]]) -> list[dict]:
    """by_person: Person → (Einheit → Wert). Eine Zeile je Personenpaar mit gemeinsamen Einheiten."""
    rows = []
    for a, b in combinations(sorted(by_person), 2):
        common = sorted(set(by_person[a]) & set(by_person[b]), key=str)
        if common:
            pairs = [(by_person[a][u], by_person[b][u]) for u in common]
            rows.append({"paar": f"{a} – {b}", "n": len(common), "kappa": cohen_kappa(pairs)})
    return rows


def rating(alpha: float | None) -> str:
    if alpha is None:
        return "nicht berechenbar"
    if alpha >= 0.8:
        return "verlässlich"
    if alpha >= 0.667:
        return "vorläufig verwendbar"
    return "zu gering – Leitfaden schärfen"
