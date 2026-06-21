from datetime import timedelta
from statistics import mean
from typing import Iterable, List

from app.schemas import ArrivalWindow, LegPrediction, LegRequest
from app.services.data_source import DelayObservation, DelayRepository


def percentile(values: List[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class ExplainableDelayModel:
    def __init__(self, repository: DelayRepository) -> None:
        self.repository = repository

    def predict_leg(self, leg: LegRequest) -> LegPrediction:
        rows = self._matching_rows(leg)
        delays = [row.delay_minutes for row in rows]
        p15 = percentile(delays, 0.15)
        p50 = percentile(delays, 0.50)
        p85 = percentile(delays, 0.85)
        p95 = percentile(delays, 0.95)
        expected = mean(delays) if delays else 0

        line = leg.line or self._most_common_line(rows) or "NRW"
        confidence = self._confidence(len(rows))
        explanation = (
            f"Gruppiert nach Linie {line}, Stunde {leg.scheduled_departure.hour}:00 "
            f"und Wochentag {leg.scheduled_departure.weekday()}."
            if len(rows) >= 10
            else "Fallback auf aehnliche NRW-Beobachtungen, weil die genaue Gruppe klein ist."
        )

        return LegPrediction(
            origin=leg.origin,
            destination=leg.destination,
            line=line,
            scheduled_arrival=leg.scheduled_arrival,
            expected_delay_minutes=round(expected, 1),
            arrival_window=ArrivalWindow(
                earliest=leg.scheduled_arrival + timedelta(minutes=round(p15)),
                likely=leg.scheduled_arrival + timedelta(minutes=round(p50)),
                latest=leg.scheduled_arrival + timedelta(minutes=round(p85)),
                pessimistic=leg.scheduled_arrival + timedelta(minutes=round(p95)),
            ),
            confidence=confidence,
            sample_size=len(rows),
            explanation=explanation,
        )

    def delay_distribution(self, leg: LegRequest) -> List[int]:
        return [row.delay_minutes for row in self._matching_rows(leg)]

    def _matching_rows(self, leg: LegRequest) -> List[DelayObservation]:
        observations = self.repository.observations()
        hour = leg.scheduled_departure.hour
        day = leg.scheduled_departure.weekday()

        filters = [
            lambda row: self._same_route(row, leg) and self._same_line(row, leg) and row.dep_hour == hour and row.day_of_week == day,
            lambda row: self._same_route(row, leg) and self._same_line(row, leg) and abs(row.dep_hour - hour) <= 1,
            lambda row: self._same_route(row, leg) and self._same_line(row, leg),
            lambda row: self._same_line(row, leg) and abs(row.dep_hour - hour) <= 2,
            lambda row: abs(row.dep_hour - hour) <= 2,
        ]

        for matcher in filters:
            rows = [row for row in observations if matcher(row)]
            if len(rows) >= 8:
                return rows
        return observations

    @staticmethod
    def _same_route(row: DelayObservation, leg: LegRequest) -> bool:
        return (
            ExplainableDelayModel._normalize_station(row.origin)
            == ExplainableDelayModel._normalize_station(leg.origin)
            and ExplainableDelayModel._normalize_station(row.destination)
            == ExplainableDelayModel._normalize_station(leg.destination)
        )

    @staticmethod
    def _same_line(row: DelayObservation, leg: LegRequest) -> bool:
        return leg.line is None or row.line.lower() == leg.line.lower()

    @staticmethod
    def _normalize_station(value: str) -> str:
        return (
            value.lower()
            .replace("ö", "oe")
            .replace("ü", "ue")
            .replace("ä", "ae")
            .replace("ß", "ss")
        )

    @staticmethod
    def _most_common_line(rows: Iterable[DelayObservation]) -> str:
        counts = {}
        for row in rows:
            counts[row.line] = counts.get(row.line, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    @staticmethod
    def _confidence(sample_size: int) -> str:
        if sample_size >= 40:
            return "hoch"
        if sample_size >= 18:
            return "mittel"
        return "vorsichtig"
