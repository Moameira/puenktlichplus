from datetime import datetime

from app.schemas import LegRequest
from app.services.cache import JsonCache
from app.services.collector import TimetableSnapshotStore
from app.services.connection_risk import ConnectionRiskCalculator
from app.services.data_source import DelayRepository, DbTimetablesClient
from app.services.delay_model import ExplainableDelayModel


def test_prediction_returns_window_for_known_route():
    model = ExplainableDelayModel(DelayRepository())
    leg = LegRequest(
        origin="Koeln Hbf",
        destination="Duesseldorf Hbf",
        line="RE1",
        scheduled_departure=datetime.fromisoformat("2026-06-22T07:30:00"),
        scheduled_arrival=datetime.fromisoformat("2026-06-22T08:05:00"),
    )

    prediction = model.predict_leg(leg)

    assert prediction.sample_size >= 8
    assert prediction.arrival_window.earliest <= prediction.arrival_window.likely
    assert prediction.arrival_window.likely <= prediction.arrival_window.latest
    assert prediction.expected_delay_minutes > 0


def test_prediction_matches_umlaut_station_names():
    model = ExplainableDelayModel(DelayRepository())
    leg = LegRequest(
        origin="Köln Hbf",
        destination="Düsseldorf Hbf",
        line="RE1",
        scheduled_departure=datetime.fromisoformat("2026-06-22T07:30:00"),
        scheduled_arrival=datetime.fromisoformat("2026-06-22T08:05:00"),
    )

    prediction = model.predict_leg(leg)

    assert prediction.sample_size >= 8
    assert prediction.expected_delay_minutes > 0


def test_connection_risk_flags_tight_transfer():
    calculator = ConnectionRiskCalculator()
    legs = [
        LegRequest(
            origin="Koeln Hbf",
            destination="Duesseldorf Hbf",
            line="RE1",
            scheduled_departure=datetime.fromisoformat("2026-06-22T07:30:00"),
            scheduled_arrival=datetime.fromisoformat("2026-06-22T08:05:00"),
        ),
        LegRequest(
            origin="Duesseldorf Hbf",
            destination="Duisburg Hbf",
            line="RE1",
            scheduled_departure=datetime.fromisoformat("2026-06-22T08:14:00"),
            scheduled_arrival=datetime.fromisoformat("2026-06-22T08:32:00"),
        ),
    ]

    risks = calculator.calculate(legs, [[4, 8, 11, 14, 18], [5, 9]])

    assert risks[0].planned_buffer_minutes == 9
    assert risks[0].risk_level in {"medium", "high"}
    assert risks[0].miss_probability == 0.6


def test_db_station_xml_parser():
    xml = '<stations><station name="Koeln Hbf" eva="8000207" ds100="KK" /></stations>'

    stations = DbTimetablesClient.parse_stations(xml)

    assert stations == [{"name": "Koeln Hbf", "eva": "8000207", "ds100": "KK"}]


def test_db_departure_parser_filters_by_destination_path():
    xml = """
    <timetable station="8000207">
      <s id="1">
        <tl c="RE" n="1" />
        <dp pt="2606211035" pp="4" l="RE1" ppth="Leverkusen Mitte|Duesseldorf Hbf|Duisburg Hbf" />
      </s>
      <s id="2">
        <tl c="S" n="11" />
        <dp pt="2606211041" pp="2" l="S11" ppth="Dormagen|Neuss Hbf" />
      </s>
    </timetable>
    """

    departures = DbTimetablesClient.parse_departures(
        xml,
        {"name": "Koeln Hbf", "eva": "8000207"},
        {"name": "Duesseldorf Hbf", "eva": "8000085"},
    )

    assert len(departures) == 1
    assert departures[0]["line"] == "RE1"
    assert departures[0]["platform"] == "4"
    assert departures[0]["destination"] == "Duesseldorf Hbf"


def test_station_board_parser_and_snapshot_store(tmp_path):
    xml = """
    <timetable station="8000207">
      <s id="1">
        <tl c="RE" n="1" />
        <dp pt="2606211035" pp="4" l="RE1" ppth="Leverkusen Mitte|Duesseldorf Hbf" />
      </s>
    </timetable>
    """
    station = {"name": "Koeln Hbf", "eva": "8000207"}

    departures = DbTimetablesClient.parse_station_board_departures(xml, station)
    store = TimetableSnapshotStore(tmp_path / "collector.sqlite")
    saved = store.save_departures(station, departures, datetime.fromisoformat("2026-06-21T10:00:00+02:00"))

    assert departures[0]["line"] == "RE1"
    assert saved == 1
    assert store.summary()["snapshot_count"] == 1


def test_station_query_germanizes_common_city_names():
    assert DbTimetablesClient.germanize_station_query("Koeln Hbf") == "Köln Hbf"
    assert DbTimetablesClient.germanize_station_query("Duesseldorf Hbf") == "Düsseldorf Hbf"


def test_connection_payload_contains_prediction_ready_legs(tmp_path):
    client = DbTimetablesClient(JsonCache(tmp_path / "cache"))
    connection = client._connection_from_departures(
        [
            {
                "origin": "Köln Hbf",
                "destination": "Düsseldorf Hbf",
                "line": "RE1",
                "train": "RE 1",
                "platform": "4",
                "scheduled_departure": "2026-06-22T07:30:00+02:00",
                "path": ["Düsseldorf Hbf"],
            }
        ]
    )

    assert connection["kind"] == "direct"
    assert connection["transfer_count"] == 0
    assert connection["legs"][0]["scheduled_arrival"] == "2026-06-22T08:05:00+02:00"
