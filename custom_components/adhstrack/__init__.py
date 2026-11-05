"""ADHS Track: Körperwerte, Aufgaben, Medikamente und Training aus der iOS-App."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_WEBHOOK_ID,
    DOMAIN,
    FARB_ARTEN,
    FRONTEND_DATEI,
    FRONTEND_URL,
    NAME,
    PROTOKOLL_VERSION,
    SERVICE_AUFGABE,
    SERVICE_EINNAHME,
    SPEICHER_VERSION,
)
from .daten import AdhsDaten
from .kopplung import KopplungsView

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AdhsConfigEntry = ConfigEntry[AdhsDaten]

SCHEMA_EINNAHME = vol.Schema(
    {
        vol.Optional("medikament"): cv.string,
        vol.Optional("zeitpunkt"): cv.datetime,
    }
)
SCHEMA_AUFGABE = vol.Schema(
    {
        vol.Required("uid"): cv.string,
        vol.Optional("erledigt", default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dashboard-Karten bereitstellen und Dienste registrieren (einmal pro HA-Start)."""
    hass.http.register_view(KopplungsView())
    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend"), True)]
    )
    # Versionsparameter: neue Integrationsversion lädt die Karten ohne Browser-Cache neu.
    add_extra_js_url(hass, f"{FRONTEND_URL}/{FRONTEND_DATEI}?v={integration.version}")

    async def einnahme_erfassen(call: ServiceCall) -> None:
        daten = _einziger_eintrag(hass)
        suche = call.data.get("medikament")
        zeitpunkt = call.data.get("zeitpunkt")
        if suche:
            med = daten.finde_medikament(suche)
            if med is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="medikament_unbekannt",
                    translation_placeholders={"medikament": suche},
                )
            daten.einnahme_erfassen(med["id"], zeitpunkt)
            return
        # Ohne Angabe: alle heute fälligen, noch offenen Einnahmen.
        for med in daten.faellige_medikamente():
            daten.einnahme_erfassen(med["id"], zeitpunkt)

    async def aufgabe_erledigen(call: ServiceCall) -> None:
        daten = _einziger_eintrag(hass)
        if not daten.aufgabe_setzen(call.data["uid"], call.data["erledigt"]):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="aufgabe_unbekannt",
                translation_placeholders={"uid": call.data["uid"]},
            )

    hass.services.async_register(DOMAIN, SERVICE_EINNAHME, einnahme_erfassen, schema=SCHEMA_EINNAHME)
    hass.services.async_register(DOMAIN, SERVICE_AUFGABE, aufgabe_erledigen, schema=SCHEMA_AUFGABE)
    return True


def _einziger_eintrag(hass: HomeAssistant) -> AdhsDaten:
    eintraege = hass.config_entries.async_loaded_entries(DOMAIN)
    if not eintraege:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nicht_eingerichtet")
    return eintraege[0].runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: AdhsConfigEntry) -> bool:
    daten = AdhsDaten(
        hass=hass,
        entry_id=entry.entry_id,
        store=Store(hass, SPEICHER_VERSION, f"{DOMAIN}.{entry.entry_id}"),
    )
    await daten.async_laden()
    daten.farben = {
        art: entry.options[f"farbe_{art}"]
        for art in FARB_ARTEN
        if entry.options.get(f"eigene_farbe_{art}") and entry.options.get(f"farbe_{art}")
    }
    entry.runtime_data = daten
    # Neu laden bei geänderten Daten (URLs, Webhook) oder Optionen (Farben), nachdem beides gespeichert ist.
    entry.async_on_unload(entry.add_update_listener(_neu_laden))

    webhook.async_register(
        hass,
        DOMAIN,
        NAME,
        entry.data[CONF_WEBHOOK_ID],
        _webhook_handler,
        local_only=False,
        allowed_methods=["POST"],
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _neu_laden(hass: HomeAssistant, entry: AdhsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AdhsConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    entry.runtime_data.async_entladen()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: AdhsConfigEntry) -> None:
    await Store(hass, SPEICHER_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _webhook_handler(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
    eintrag = next(
        (e for e in hass.config_entries.async_loaded_entries(DOMAIN) if e.data[CONF_WEBHOOK_ID] == webhook_id),
        None,
    )
    if eintrag is None:
        return web.Response(status=404)
    try:
        nachricht: dict[str, Any] = await request.json()
    except ValueError:
        return web.Response(status=400, text="Kein gültiges JSON")
    if not isinstance(nachricht, dict):
        return web.Response(status=400, text="Objekt erwartet")
    if nachricht.get("v", 1) > PROTOKOLL_VERSION:
        _LOGGER.warning("Neuere Protokollversion %s von der App – bitte Integration aktualisieren", nachricht.get("v"))

    daten: AdhsDaten = eintrag.runtime_data
    match nachricht.get("typ"):
        case "sync":
            return web.json_response(daten.verarbeite_sync(nachricht))
        case "training":
            daten.verarbeite_training(nachricht)
            return web.json_response({})
        case _:
            return web.Response(status=400, text="Unbekannter Nachrichtentyp")
