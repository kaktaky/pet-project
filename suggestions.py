"""Подбор из известных порций: без внешних сервисов и выдуманных БЖУ."""
from itertools import combinations
import math

NUTRIENTS = ("cal", "p", "f", "c")


def scale_nutrients(food, portions):
    # Такое же округление использует интерфейс (Math.round).
    return {key: math.floor((food[key] or 0) * portions + 0.5) for key in NUTRIENTS}


def suggest_meals(favorites, remaining, limit=12):
    if remaining <= 0:
        return []
    target = min(remaining, 600)
    foods = [food for food in favorites if food["cal"] > 0]
    # Ограничиваем число сочетаний даже при очень большом избранном.
    foods = sorted(foods, key=lambda food: abs(food["cal"] - target))[:80]
    candidates = []
    groups = [(food,) for food in foods] + list(combinations(foods, 2))
    for group in groups:
        best = None
        for quarter in range(1, 9):
            portions = quarter / 4
            items = [dict(food, portions=portions) for food in group]
            total = {key: sum(scale_nutrients(food, portions)[key] for food in group) for key in NUTRIENTS}
            if not 0 < total["cal"] <= remaining:
                continue
            score = abs(total["cal"] - target) + abs(portions - 1) * 20
            if best is None or score < best[0]:
                best = (score, {"items": items, "total": total})
        if best:
            candidates.append(best)
    candidates.sort(key=lambda candidate: candidate[0])
    return [candidate for _, candidate in candidates[:limit]]
