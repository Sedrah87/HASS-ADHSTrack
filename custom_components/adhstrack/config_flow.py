"""Einrichtung: Webhook erzeugen und Kopplungslink für die App anzeigen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import CONF_URL_EXTERN, CONF_URL_INTERN, CONF_WEBHOOK_ID, DOMAIN, FARB_ARTEN, NAME
from .kopplung import AUSSTEHEND, kopplungs_links


def _url_schema(intern: str, extern: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL_INTERN, default=intern): str,
            vol.Optional(CONF_URL_EXTERN, description={"suggested_value": extern}): str,
        }
    )


def _bereinigt(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


class AdhsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config-Flow für ADHS Track."""

    VERSION = 1

    def __init__(self) -> None:
        self._daten: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        fehler: dict[str, str] = {}
        if user_input is not None:
            intern = _bereinigt(user_input.get(CONF_URL_INTERN))
            extern = _bereinigt(user_input.get(CONF_URL_EXTERN))
            if not intern.startswith(("http://", "https://")):
                fehler[CONF_URL_INTERN] = "url_ungueltig"
            elif extern and not extern.startswith("https://"):
                fehler[CONF_URL_EXTERN] = "extern_https"
            else:
                self._daten = {
                    CONF_WEBHOOK_ID: webhook.async_generate_id(),
                    CONF_URL_INTERN: intern,
                    CONF_URL_EXTERN: extern,
                }
                self.hass.data.setdefault(AUSSTEHEND, {})[self._daten[CONF_WEBHOOK_ID]] = self._daten
                return await self.async_step_kopplung()

        try:
            intern_vorschlag = get_url(self.hass, prefer_external=False, allow_cloud=False)
        except NoURLAvailableError:
            intern_vorschlag = "http://homeassistant.local:8123"
        try:
            extern_vorschlag = get_url(self.hass, allow_internal=False, allow_cloud=False, require_ssl=True)
        except NoURLAvailableError:
            extern_vorschlag = ""
        return self.async_show_form(
            step_id="user",
            data_schema=_url_schema(intern_vorschlag, extern_vorschlag),
            errors=fehler,
        )

    async def async_step_kopplung(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.hass.data.get(AUSSTEHEND, {}).pop(self._daten[CONF_WEBHOOK_ID], None)
            return self.async_create_entry(title=NAME, data=self._daten)
        return self.async_show_form(
            step_id="kopplung",
            data_schema=vol.Schema({}),
            description_placeholders=kopplungs_links(self._daten),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AdhsOptionsFlow()


class AdhsOptionsFlow(OptionsFlow):
    """Kopplungslink erneut anzeigen, URLs ändern oder einen neuen Webhook erzeugen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        daten = dict(self.config_entry.data)
        fehler: dict[str, str] = {}
        if user_input is not None:
            intern = _bereinigt(user_input.get(CONF_URL_INTERN))
            extern = _bereinigt(user_input.get(CONF_URL_EXTERN))
            if not intern.startswith(("http://", "https://")):
                fehler[CONF_URL_INTERN] = "url_ungueltig"
            elif extern and not extern.startswith("https://"):
                fehler[CONF_URL_EXTERN] = "extern_https"
            else:
                daten[CONF_URL_INTERN] = intern
                daten[CONF_URL_EXTERN] = extern
                if user_input.get("neuer_webhook"):
                    # Alte ID wird ungültig; die App muss neu gekoppelt werden.
                    daten[CONF_WEBHOOK_ID] = webhook.async_generate_id()
                self.hass.config_entries.async_update_entry(self.config_entry, data=daten)
                optionen = {
                    k: v for k, v in user_input.items() if k.startswith(("eigene_farbe_", "farbe_"))
                }
                return self.async_create_entry(data=optionen)

        schema = _url_schema(daten.get(CONF_URL_INTERN, ""), daten.get(CONF_URL_EXTERN, "")).extend(
            {vol.Optional("neuer_webhook", default=False): bool}
        )
        optionen = self.config_entry.options
        for art in FARB_ARTEN:
            schema = schema.extend(
                {
                    vol.Optional(f"eigene_farbe_{art}", default=optionen.get(f"eigene_farbe_{art}", False)): bool,
                    vol.Optional(
                        f"farbe_{art}", default=optionen.get(f"farbe_{art}", "#4a90d9")
                    ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.COLOR)),
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=fehler,
            description_placeholders=kopplungs_links(daten),
        )
