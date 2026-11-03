"""Gemeinsame Basis der Entitäten."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, HERSTELLER, NAME
from .daten import AdhsDaten, signal_update


class AdhsEntity(Entity):
    """Entität, die bei jedem Abgleich aus der App neu gezeichnet wird."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, daten: AdhsDaten, schluessel: str) -> None:
        self.daten = daten
        self._attr_translation_key = schluessel
        self._attr_unique_id = f"{daten.entry_id}_{schluessel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, daten.entry_id)},
            name=NAME,
            manufacturer=HERSTELLER,
            model="iOS-App",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_update(self.daten.entry_id), self._aktualisiert)
        )

    @callback
    def _aktualisiert(self) -> None:
        self.async_write_ha_state()
