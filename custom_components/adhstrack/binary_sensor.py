"""Binärsensoren: Training aktiv, Medikament fällig."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AdhsConfigEntry
from .daten import AdhsDaten
from .entity import AdhsEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AdhsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    daten = entry.runtime_data
    async_add_entities([TrainingAktiv(daten), MedikamentFaellig(daten)])


class TrainingAktiv(AdhsEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, daten: AdhsDaten) -> None:
        super().__init__(daten, "training_aktiv")

    @property
    def is_on(self) -> bool:
        return bool(self.daten.training.get("aktiv"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"aktivitaet": self.daten.training.get("aktivitaet")}


class MedikamentFaellig(AdhsEntity, BinarySensorEntity):
    """An, solange heute eine Einnahme fällig (Erinnerungszeit erreicht) und noch offen ist."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, daten: AdhsDaten) -> None:
        super().__init__(daten, "medikament_faellig")

    @property
    def is_on(self) -> bool:
        return bool(self.daten.faellige_medikamente())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        faellig = self.daten.faellige_medikamente()
        return {
            "medikamente": [m.get("name") for m in faellig],
            "kritisch": any(m.get("kritisch") for m in faellig),
        }
