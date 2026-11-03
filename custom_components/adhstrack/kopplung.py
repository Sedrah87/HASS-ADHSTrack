"""Kopplungslink für die App.

Die HA-Oberfläche entfernt Links mit eigenem Schema (adhstrack://) aus Beschreibungen. Deshalb zeigt der
Config-Flow einen normalen http(s)-Link auf diese Ansicht, die auf adhstrack://homeassistant?… weiterleitet.
Auf dem iPhone antippen → Safari → ADHS Track öffnet sich und übernimmt URL und Webhook-ID.
Die Ansicht verrät nichts Neues: Wer den Link kennt, kennt die Webhook-ID ohnehin.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util.hass_dict import HassKey

from .const import CONF_URL_EXTERN, CONF_URL_INTERN, CONF_WEBHOOK_ID, DOMAIN

PFAD = "/api/adhstrack/koppeln"

# Kopplungsdaten laufender Config-Flows (Link funktioniert schon, bevor der Eintrag angelegt ist).
AUSSTEHEND: HassKey[dict[str, dict[str, Any]]] = HassKey(f"{DOMAIN}_kopplung_ausstehend")


def app_link(daten: dict[str, Any]) -> str:
    parameter = {"url": daten.get(CONF_URL_INTERN, ""), "id": daten[CONF_WEBHOOK_ID]}
    if daten.get(CONF_URL_EXTERN):
        parameter["extern"] = daten[CONF_URL_EXTERN]
    return "adhstrack://homeassistant?" + urlencode(parameter)


def kopplungs_links(daten: dict[str, Any]) -> dict[str, str]:
    """Platzhalter für die Beschreibung im Config-/Options-Flow."""
    basis = daten.get(CONF_URL_EXTERN) or daten.get(CONF_URL_INTERN, "")
    return {
        "kopplungs_link": f"{basis}{PFAD}/{daten[CONF_WEBHOOK_ID]}",
        "url_intern": daten.get(CONF_URL_INTERN, ""),
        "url_extern": daten.get(CONF_URL_EXTERN) or "–",
        "webhook_id": daten[CONF_WEBHOOK_ID],
    }


class KopplungsView(HomeAssistantView):
    """GET /api/adhstrack/koppeln/{webhook_id} → Weiterleitung in die App."""

    url = PFAD + "/{webhook_id}"
    name = "api:adhstrack:koppeln"
    requires_auth = False

    async def get(self, request: web.Request, webhook_id: str) -> web.Response:
        hass: HomeAssistant = request.app[KEY_HASS]
        for eintrag in hass.config_entries.async_entries(DOMAIN):
            if eintrag.data.get(CONF_WEBHOOK_ID) == webhook_id:
                raise web.HTTPFound(app_link(eintrag.data))
        if (daten := hass.data.get(AUSSTEHEND, {}).get(webhook_id)) is not None:
            raise web.HTTPFound(app_link(daten))
        return web.Response(status=404)
