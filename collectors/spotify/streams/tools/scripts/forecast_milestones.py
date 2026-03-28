from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[4]
ROOT        = _REPO_ROOT / "website"
SITE_DATA_DIR = ROOT / "site" / "data"
HISTORY_DIR = ROOT / "site" / "history"
HISTORY_INDEX_PATH = HISTORY_DIR / "index.json"

SONGS_PATH = SITE_DATA_DIR / "songs.json"
OUTPUT_PATH = SITE_DATA_DIR / "expected_milestones.json"

DEFAULT_MILESTONES = [
    100_000_000,
    200_000_000,
    300_000_000,
    400_000_000,
    500_000_000,
    600_000_000,
    700_000_000,
    800_000_000,
    900_000_000,
    1_000_000_000,
    1_500_000_000,
    2_000_000_000,
    2_500_000_000,
    3_000_000_000,
    3_500_000_000,
    4_000_000_000,
]

MAX_FORECAST_DAYS = 5 * 365
RECENT_WINDOW = 90          # 3 mois d'historique pour une tendance stable
MIN_REQUIRED_HISTORY_POINTS = 5
SPIKE_IQR_MULTIPLIER = 2.0  # seuil IQR au-delà duquel un jour est considéré comme pic
EWMA_ALPHA = 0.10           # lissage exponentiel (poids fort sur les jours récents)


def format_milestone_label(value: int) -> str:
    if value >= 1_000_000_000:
        billions = value / 1_000_000_000
        if abs(billions - round(billions)) < 1e-9:
            return f"{int(round(billions))}B"
        return f"{billions:.1f}B"
    return f"{int(value / 1_000_000)}M"


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_history_bundle() -> dict:
    if not HISTORY_INDEX_PATH.exists():
        return {"dates": [], "by_date": {}}

    index_data = load_json(HISTORY_INDEX_PATH)
    dates = index_data.get("dates", [])
    by_date = {}

    for d in dates:
        day_path = HISTORY_DIR / f"{d}.json"
        if not day_path.exists():
            continue
        by_date[d] = load_json(day_path) or {}

    return {
        "dates": dates,
        "by_date": by_date,
    }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def remove_spikes(values: list[float]) -> list[float]:
    """
    Supprime les pics statistiques via IQR.
    Conserve les valeurs <= Q3 + SPIKE_IQR_MULTIPLIER * IQR.
    Retourne la liste originale si trop peu de points.
    """
    if len(values) < 4:
        return values
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    upper = q3 + SPIKE_IQR_MULTIPLIER * iqr
    cleaned = [v for v in values if v <= upper]
    return cleaned if cleaned else values


def ewma(values: list[float], alpha: float = EWMA_ALPHA) -> float:
    """
    Exponential Weighted Moving Average.
    Les valeurs récentes ont plus de poids.
    alpha proche de 0 = lissage fort (mémoire longue).
    """
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def estimate_decay_factor(daily_values: list[float]) -> float:
    """
    Calcule le facteur de décroissance/croissance quotidien en comparant
    la moyenne des 7 derniers jours à celle des 7 jours précédents.
    Retourne un ratio quotidien (ex: 0.993 = -0.7%/jour, 1.002 = +0.2%/jour).
    Clampé à [0.965, 1.020] pour éviter les extrapolations absurdes.
    """
    n = len(daily_values)
    if n < 7:
        return 1.0

    if n >= 14:
        last_7  = daily_values[-7:]
        prev_7  = daily_values[-14:-7]
        avg_last = sum(last_7) / len(last_7)
        avg_prev = sum(prev_7) / len(prev_7)
        span = 7
    else:
        # Moins de 14 jours : comparaison première/deuxième moitié
        half = n // 2
        last_7  = daily_values[-half:]
        prev_7  = daily_values[:-half]
        avg_last = sum(last_7) / len(last_7)
        avg_prev = sum(prev_7) / len(prev_7)
        span = half

    if avg_prev <= 0:
        return 1.0

    weekly_ratio = avg_last / avg_prev
    # Convertir le ratio sur `span` jours en ratio quotidien
    daily_decay = weekly_ratio ** (1.0 / span)
    return clamp(daily_decay, 0.965, 1.020)


def get_track_history_series(track_id: str, history_by_date: dict, dates: list[str]) -> list[dict]:
    series = []

    for d in dates:
        row = history_by_date.get(d, {}).get(track_id)
        if not row:
            continue

        streams = safe_int(row.get("s") or row.get("streams"))
        daily_streams = row.get("d") if row.get("d") is not None else row.get("daily_streams")

        if daily_streams in (None, ""):
            continue

        daily_streams = safe_int(daily_streams)

        if streams <= 0:
            continue

        # on ignore les daily <= 0 pour éviter les faux zéros
        if daily_streams <= 0:
            continue

        series.append(
            {
                "date": d,
                "streams": streams,
                "daily_streams": daily_streams,
            }
        )

    return series


def estimate_future_daily_streams(series: list[dict]) -> dict:
    """
    Estime les streams quotidiens futurs à partir de l'historique récent.

    Approche :
    1. Fenêtre glissante de RECENT_WINDOW jours (90 jours par défaut)
    2. Suppression des pics (IQR) pour isoler la tendance de fond
    3. EWMA sur les valeurs nettoyées → base quotidienne stable
    4. Facteur de décroissance multiplicatif calculé sur semaine / semaine précédente
    5. Projection : daily *= decay_factor à chaque jour (courbe exponentielle)
       → bien plus réaliste qu'une tendance linéaire sur des horizons longs (>100 jours)
    """
    if not series:
        return {"base_daily": 0, "decay_factor": 1.0, "projected_next_daily": 0}

    recent = series[-RECENT_WINDOW:]
    all_daily = [
        safe_float(row["daily_streams"])
        for row in recent
        if safe_float(row["daily_streams"]) > 0
    ]

    if not all_daily:
        return {"base_daily": 0, "decay_factor": 1.0, "projected_next_daily": 0}

    # Supprimer les pics pour une baseline propre
    cleaned = remove_spikes(all_daily)

    # EWMA sur les valeurs nettoyées (les jours récents ont plus de poids)
    base = ewma(cleaned)

    # Facteur de décroissance calculé aussi sur données nettoyées
    decay = estimate_decay_factor(cleaned)

    projected_next = max(1.0, base * decay)

    return {
        "base_daily": int(round(base)),
        "decay_factor": decay,
        "projected_next_daily": int(round(projected_next)),
    }


def next_milestone(current_streams: int, milestones: list[int]) -> int | None:
    for milestone in milestones:
        if current_streams < milestone:
            return milestone
    return None


def project_milestone_date(
    current_streams: int,
    last_date: str,
    start_daily: int,
    decay_factor: float,
    milestone: int,
) -> dict | None:
    """
    Projette la date d'atteinte du milestone avec décroissance multiplicative :
        daily_j = start_daily * decay_factor^j

    Somme géométrique convergente si decay_factor < 1.
    Si la somme totale possible < remaining → retourne None (jamais atteint).
    """
    if current_streams >= milestone:
        return None

    remaining = milestone - current_streams
    if remaining <= 0 or start_daily <= 0:
        return None

    # Vérification rapide : si decay < 1, la somme max est start_daily / (1 - decay_factor)
    # Si cette somme est inférieure au remaining, le milestone ne sera jamais atteint
    if decay_factor < 1.0:
        max_possible = start_daily / (1.0 - decay_factor)
        if max_possible < remaining:
            return None

    current_date = parse_iso_date(last_date)
    projected_streams = float(current_streams)
    daily = float(start_daily)

    for day_index in range(1, MAX_FORECAST_DAYS + 1):
        projected_streams += daily

        if projected_streams >= milestone:
            eta_date = current_date + timedelta(days=day_index)
            return {
                "expected_date": eta_date.isoformat(),
                "days_left": day_index,
                "projected_streams_on_hit": int(round(projected_streams)),
            }

        daily *= decay_factor

        if daily < 1.0:
            return None

    return None


def compute_progress(current_streams: int, target: int) -> dict:
    target = max(1, target)
    progress_ratio = clamp(current_streams / target, 0.0, 1.0)

    return {
        "previous_reference": 0,
        "target": target,
        "remaining": max(0, target - current_streams),
        "progress_ratio": progress_ratio,
        "progress_percent": round(progress_ratio * 100, 2),
    }


def build_forecasts() -> dict:
    songs_data = load_json(SONGS_PATH)
    history_data = load_history_bundle()

    songs = songs_data.get("songs", [])
    dates = history_data.get("dates", [])
    history_by_date = history_data.get("by_date", {})

    if not songs or not dates:
        return {
            "generated_at": datetime.now().isoformat(),
            "latest_history_date": None,
            "forecasts": [],
        }

    latest_history_date = dates[-1]
    forecasts = []

    for song in songs:
        track_id = song.get("track_id")
        if not track_id:
            continue

        series = get_track_history_series(track_id, history_by_date, dates)
        if not series:
            continue

        last_row = series[-1]
        current_streams = safe_int(last_row["streams"])
        if current_streams <= 0:
            continue

        next_target = next_milestone(current_streams, DEFAULT_MILESTONES)
        if next_target is None:
            continue

        projection_inputs = estimate_future_daily_streams(series)

        if len(series) < MIN_REQUIRED_HISTORY_POINTS:
            projected_daily = max(safe_int(last_row["daily_streams"]), 1)
            decay_factor = 1.0
            base_daily = projected_daily
        else:
            projected_daily = max(projection_inputs["projected_next_daily"], 1)
            decay_factor = projection_inputs["decay_factor"]
            base_daily = max(projection_inputs["base_daily"], 1)

        projection = project_milestone_date(
            current_streams=current_streams,
            last_date=latest_history_date,
            start_daily=projected_daily,
            decay_factor=decay_factor,
            milestone=next_target,
        )

        progress = compute_progress(current_streams, next_target)

        forecasts.append(
            {
                "track_id": track_id,
                "title": song.get("title"),
                "title_clean": song.get("title_clean") or song.get("title"),
                "image_url": song.get("image_url"),
                "primary_album": song.get("primary_album"),
                "primary_artist": song.get("primary_artist"),
                "spotify_url": song.get("spotify_url"),
                "current_streams": current_streams,
                "latest_daily_streams": safe_int(last_row["daily_streams"]),
                "estimated_base_daily": base_daily,
                "estimated_next_daily": projected_daily,
                "estimated_decay_factor": round(decay_factor, 6),
                "estimated_trend_per_day": round((decay_factor - 1.0) * base_daily, 2),
                "next_milestone": next_target,
                "next_milestone_label": format_milestone_label(next_target),
                "progress": progress,
                "forecast": projection,
            }
        )

    sortable = []
    unsortable = []

    for item in forecasts:
        if item["forecast"] and item["forecast"].get("expected_date"):
            sortable.append(item)
        else:
            unsortable.append(item)

    sortable.sort(
        key=lambda x: (
            x["forecast"]["expected_date"],
            x["forecast"]["days_left"],
            -(x["current_streams"] or 0),
        )
    )

    unsortable.sort(
        key=lambda x: (
            -(x["progress"]["progress_percent"] or 0),
            -(x["current_streams"] or 0),
        )
    )

    return {
        "generated_at": datetime.now().isoformat(),
        "latest_history_date": latest_history_date,
        "forecasts": sortable + unsortable,
    }


def main() -> None:
    output = build_forecasts()
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Written: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()