"""To-do-Liste: heutige, überfällige und Aufgaben ohne Datum, abhakbar. Der Status ändert sich erst, wenn das iPhone die Änderung bestätigt hat."""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AdhsConfigEntry
from .const import DOMAIN
from .daten import AdhsDaten, parse_zeit
from .entity import AdhsEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AdhsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AdhsAufgabenListe(entry.runtime_data)])


_WARTET_HINWEIS = "⏳ Wird an das iPhone übertragen …"


def _beschreibung(aufgabe: dict) -> str | None:
    """Notiz der App, bei unbestätigter Änderung mit Hinweis davor."""
    if aufgabe.get("wartet") is None:
        return aufgabe.get("notiz")
    return "\n".join(t for t in (_WARTET_HINWEIS, aufgabe.get("notiz")) if t)


class AdhsAufgabenListe(AdhsEntity, TodoListEntity):
    # Nur der Status ist änderbar; Titel, Termin und Notiz pflegt die App.
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, daten: AdhsDaten) -> None:
        super().__init__(daten, "aufgaben")

    @property
    def todo_items(self) -> list[TodoItem]:
        heute = self.daten.heute()
        eintraege = []
        for a in self.daten.aufgaben():
            # Heute plus Überfälliges aus früheren Tagen; die Folgetage stehen im Kalender.
            if a.get("datum") != heute and not (a.get("ueberfaellig") and not a.get("erledigt")):
                continue
            start = parse_zeit(a.get("start"))
            # Ohne Uhrzeit nur das Datum als Fälligkeit.
            due = (dt_util.as_local(start).date() if a.get("ohne_zeit") else dt_util.as_local(start)) if start else None
            eintraege.append(
                TodoItem(
                    uid=a["uid"],
                    summary=a.get("titel") or "",
                    status=TodoItemStatus.COMPLETED if a.get("erledigt") else TodoItemStatus.NEEDS_ACTION,
                    due=due,
                    description=_beschreibung(a),
                )
            )
        # Aufgaben ohne Datum am Ende, ohne Fälligkeit.
        for a in self.daten.ohne_datum():
            eintraege.append(
                TodoItem(
                    uid=a["uid"],
                    summary=a.get("titel") or "",
                    status=TodoItemStatus.COMPLETED if a.get("erledigt") else TodoItemStatus.NEEDS_ACTION,
                    description=_beschreibung(a),
                )
            )
        return eintraege

    async def async_update_todo_item(self, item: TodoItem) -> None:
        aktuell = self.daten.aufgabe(item.uid or "")
        if aktuell is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="aufgabe_unbekannt", translation_placeholders={"uid": item.uid or ""}
            )
        if item.summary not in (None, aktuell.get("titel")) or item.description not in (None, aktuell.get("notiz"), _beschreibung(aktuell)):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nur_status")
        if item.status is not None:
            self.daten.aufgabe_setzen(item.uid or "", item.status == TodoItemStatus.COMPLETED)
