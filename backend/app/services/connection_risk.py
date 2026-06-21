from app.schemas import LegRequest, TransferRisk


class ConnectionRiskCalculator:
    def calculate(self, legs: list[LegRequest], distributions: list[list[int]]) -> list[TransferRisk]:
        risks: list[TransferRisk] = []
        for index in range(len(legs) - 1):
            current_leg = legs[index]
            next_leg = legs[index + 1]
            buffer_minutes = (
                next_leg.scheduled_departure - current_leg.scheduled_arrival
            ).total_seconds() / 60
            delays = distributions[index] or [0]
            misses = sum(1 for delay in delays if delay > buffer_minutes)
            probability = misses / len(delays)
            level = self._level(probability, buffer_minutes)
            station = current_leg.destination
            risks.append(
                TransferRisk(
                    from_leg=index,
                    to_leg=index + 1,
                    station=station,
                    planned_buffer_minutes=round(buffer_minutes, 1),
                    miss_probability=round(probability, 2),
                    risk_level=level,
                    message_de=self._message_de(level, station, buffer_minutes, probability),
                    message_en=self._message_en(level, station, buffer_minutes, probability),
                )
            )
        return risks

    @staticmethod
    def _level(probability: float, buffer_minutes: float) -> str:
        if buffer_minutes < 0:
            return "invalid"
        if probability >= 0.45:
            return "high"
        if probability >= 0.2:
            return "medium"
        return "low"

    @staticmethod
    def _message_de(level: str, station: str, buffer: float, probability: float) -> str:
        if level == "invalid":
            return f"Der Anschluss in {station} faehrt vor der geplanten Ankunft ab."
        if level == "high":
            return f"Heikel: {buffer:.0f} Minuten Umstieg in {station}, historisch ca. {probability:.0%} Verpass-Risiko."
        if level == "medium":
            return f"Knapp, aber nicht hoffnungslos: {buffer:.0f} Minuten in {station}, ca. {probability:.0%} Risiko."
        return f"Solide geplant: {buffer:.0f} Minuten in {station}, historisch ca. {probability:.0%} Risiko."

    @staticmethod
    def _message_en(level: str, station: str, buffer: float, probability: float) -> str:
        if level == "invalid":
            return f"The connection in {station} leaves before the planned arrival."
        if level == "high":
            return f"Risky: {buffer:.0f} minutes to transfer in {station}, about {probability:.0%} historical miss risk."
        if level == "medium":
            return f"Tight but possible: {buffer:.0f} minutes in {station}, about {probability:.0%} risk."
        return f"Comfortable: {buffer:.0f} minutes in {station}, about {probability:.0%} risk."
