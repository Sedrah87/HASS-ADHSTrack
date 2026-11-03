"""Kalender: Aufgaben und geplante Medikamenteneinnahmen (heute bis +6 Tage)."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AdhsConfigEntry
from .daten import AdhsDaten, parse_zeit
from .entity import AdhsEntity

# Aufgaben ohne Endzeit und Einnahmen erhalten eine kurze Dauer, damit sie im Kalender sichtbar sind.
_STANDARD_DAUER = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant, entry: AdhsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AdhsKalender(entry.runtime_data)])


class AdhsKalender(AdhsEntity, CalendarEntity):
    def __init__(self, daten: AdhsDaten) -> None:
        super().__init__(daten, "kalender")

    def _termine(self) -> list[CalendarEvent]:
        termine: list[CalendarEvent] = []
        for a in self.daten.aufgaben():
            start = parse_zeit(a.get("start"))
            if start is None:
                continue
            ende = parse_zeit(a.get("ende")) or start + _STANDARD_DAUER
            haken = "✓ " if a.get("erledigt") else ""
            termine.append(
                CalendarEvent(
                    start=dt_util.as_local(start),
                    end=dt_util.as_local(ende),
                    summary=f"{haken}{a.get('titel') or ''}",
                    description=a.get("notiz"),
                    uid=f"aufgabe_{a['uid']}",
                )
            )
        for med in self.daten.medikamente():
            for t in med["plan"]:
                start = parse_zeit(t.get("einnahme_um"))
                if start is None:
                    continue
                zeichen = {"eingenommen": "✓ ", "uebersprungen": "– "}.get(t["status"], "💊 ")
                termine.append(
                    CalendarEvent(
                        start=dt_util.as_local(start),
                        end=dt_util.as_local(start + _STANDARD_DAUER),
                        summary=f"{zeichen}{med.get('name')} {med.get('dosis', '')} {med.get('einheit', '')}".strip(),
                        uid=f"einnahme_{med['id']}_{t.get('datum')}",
                    )
                )
        return sorted(termine, key=lambda e: e.start_datetime_local)

    @property
    def event(self) -> CalendarEvent | None:
        jetzt = dt_util.now()
        return next((e for e in self._termine() if e.end_datetime_local > jetzt), None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        return [
            e for e in self._termine() if e.end_datetime_local > start_date and e.start_datetime_local < end_date
        ]
