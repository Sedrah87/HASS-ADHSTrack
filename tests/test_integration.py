"""Webhook-Abgleich, Befehle, Ereignisse und Config-Flow."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.adhstrack.const import (
    CONF_URL_EXTERN,
    CONF_URL_INTERN,
    CONF_WEBHOOK_ID,
    DOMAIN,
    EVENT_EINGENOMMEN,
    EVENT_ERINNERUNG,
    EVENT_ZONE,
)

WEBHOOK_ID = "test_webhook"


def iso(zeit) -> str:
    return dt_util.as_utc(zeit).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
async def eintrag(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={CONF_WEBHOOK_ID: WEBHOOK_ID, CONF_URL_INTERN: "http://ha.local:8123", CONF_URL_EXTERN: ""},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def sende(hass_client_no_auth, nachricht: dict[str, Any]) -> dict[str, Any]:
    client = await hass_client_no_auth()
    antwort = await client.post(f"/api/webhook/{WEBHOOK_ID}", json=nachricht)
    assert antwort.status == 200
    return await antwort.json()


def sync(**teile) -> dict[str, Any]:
    return {"typ": "sync", "v": 1, "gesendet": iso(dt_util.utcnow()), "geraet": "iPhone", "bestaetigt": [], **teile}


def aufgabe(uid_id: str, datum: str, erledigt: bool = False) -> dict[str, Any]:
    return {
        "uid": f"{uid_id}|{datum}", "id": uid_id, "datum": datum, "titel": f"Aufgabe {uid_id}",
        "start": iso(dt_util.now().replace(hour=9, minute=0)), "erledigt": erledigt, "ueberfaellig": False,
        "prioritaet": "mittel", "wiederkehrend": False,
    }


def medikament(erinnerung, status: str = "offen") -> dict[str, Any]:
    return {
        "id": "MED1", "name": "Elvanse", "dosis": 30, "einheit": "mg", "farbe": "#4A90D9", "kritisch": False,
        "plan": [{
            "datum": dt_util.as_local(erinnerung).date().isoformat(), "einnahme_um": iso(erinnerung),
            "erinnerung_um": iso(erinnerung), "app_erinnerung": True, "status": status,
        }],
    }


async def test_koerper_sensoren(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    await sende(hass_client_no_auth, sync(
        koerper={"recovery": 72, "schlaf_prozent": 88, "schlaf_dauer_min": 431, "strain": 9.4, "ruhepuls": 52.0, "hrv": 61.5},
        journal={"tag": "2026-11-03", "koffein": 2, "alkohol": 0, "fluessigkeit_ml": 1500, "stimmung": 4, "energie": None},
    ))
    await hass.async_block_till_done()
    werte = {s.attributes.get("friendly_name"): s.state for s in hass.states.async_all("sensor")}
    assert "72" in werte.values()
    assert "431" in werte.values()
    assert "1500" in werte.values()


async def test_aufgabe_abhaken_und_bestaetigen(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date().isoformat()
    await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute)]))
    await hass.async_block_till_done()

    todo_id = next(s.entity_id for s in hass.states.async_all("todo"))
    assert hass.states.get(todo_id).state == "1"
    await hass.services.async_call(
        "todo", "update_item", {"item": f"A1|{heute}", "status": "completed"}, target={"entity_id": todo_id}, blocking=True
    )
    # Optimistisch sofort erledigt
    assert hass.states.get(todo_id).state == "0"

    antwort = await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute)]))
    befehl = antwort["befehle"][0]
    assert befehl["art"] == "aufgabe_status" and befehl["aufgabe_id"] == "A1" and befehl["erledigt"] is True
    # App hat noch alten Stand gemeldet → Anzeige bleibt erledigt
    await hass.async_block_till_done()
    assert hass.states.get(todo_id).state == "0"

    antwort = await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute, erledigt=True)], bestaetigt=[befehl["id"]]))
    assert antwort["befehle"] == []


async def test_einnahme_dienst_und_erinnerung_unterdrueckt(
    hass: HomeAssistant, eintrag, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    erinnerungen = async_capture_events(hass, EVENT_ERINNERUNG)
    erinnerung = dt_util.utcnow() + timedelta(minutes=10)
    await sende(hass_client_no_auth, sync(medikamente=[medikament(erinnerung)], einnahmen=[]))

    await hass.services.async_call(DOMAIN, "einnahme_erfassen", {"medikament": "elvanse"}, blocking=True)
    antwort = await sende(hass_client_no_auth, sync(medikamente=[medikament(erinnerung)], einnahmen=[]))
    assert [b["art"] for b in antwort["befehle"]] == ["einnahme"]
    assert antwort["befehle"][0]["med_id"] == "MED1"
    assert antwort["befehle"][0]["zeitpunkt"].endswith("Z") and "." not in antwort["befehle"][0]["zeitpunkt"]

    freezer.move_to(erinnerung + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert erinnerungen == []


async def test_erinnerung_feuert_wenn_offen(
    hass: HomeAssistant, eintrag, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    erinnerungen = async_capture_events(hass, EVENT_ERINNERUNG)
    erinnerung = dt_util.utcnow() + timedelta(minutes=5)
    await sende(hass_client_no_auth, sync(medikamente=[medikament(erinnerung)]))
    freezer.move_to(erinnerung + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(erinnerungen) == 1
    assert erinnerungen[0].data["medikament"] == "Elvanse"

    # Erneuter Abgleich plant dieselbe Erinnerung nicht noch einmal.
    await sende(hass_client_no_auth, sync(medikamente=[medikament(erinnerung)]))
    freezer.move_to(erinnerung + timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(erinnerungen) == 1


async def test_eingenommen_nur_fuer_neue_einnahmen(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    ereignisse = async_capture_events(hass, EVENT_EINGENOMMEN)
    alt = {"id": "D1", "med_id": "MED1", "name": "Elvanse", "zeit": iso(dt_util.utcnow()), "eingenommen": True}
    await sende(hass_client_no_auth, sync(einnahmen=[alt]))
    await hass.async_block_till_done()
    assert ereignisse == []  # erster Abgleich: nur merken

    neu = {**alt, "id": "D2"}
    await sende(hass_client_no_auth, sync(einnahmen=[alt, neu]))
    await sende(hass_client_no_auth, sync(einnahmen=[alt, neu]))
    await hass.async_block_till_done()
    assert len(ereignisse) == 1
    assert ereignisse[0].data["medikament"] == "Elvanse"


async def test_trainingszone(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    zonen = async_capture_events(hass, EVENT_ZONE)
    basis = {"typ": "training", "v": 1, "aktiv": True, "aktivitaet": "rudern", "zeit": iso(dt_util.utcnow())}
    await sende(hass_client_no_auth, {**basis, "puls": 120, "zone": 2, "zonen_name": "Zone 2"})
    await sende(hass_client_no_auth, {**basis, "puls": 125, "zone": 2})
    await sende(hass_client_no_auth, {**basis, "puls": 150, "zone": 3})
    await hass.async_block_till_done()
    assert [e.data["zone"] for e in zonen] == [2, 3]
    aktiv = next(s for s in hass.states.async_all("binary_sensor") if s.attributes.get("device_class") == "running")
    assert aktiv.state == "on"

    await sende(hass_client_no_auth, {**basis, "aktiv": False})
    await hass.async_block_till_done()
    assert hass.states.get(aktiv.entity_id).state == "off"


async def test_falscher_typ_und_json(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    assert (await client.post(f"/api/webhook/{WEBHOOK_ID}", json={"typ": "quatsch"})).status == 400
    assert (await client.post(f"/api/webhook/{WEBHOOK_ID}", data="kein json")).status == 400


async def test_kopplungslink(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    antwort = await client.get(f"/api/adhstrack/koppeln/{WEBHOOK_ID}", allow_redirects=False)
    assert antwort.status == 302
    assert antwort.headers["Location"].startswith("adhstrack://homeassistant")
    assert "id=test_webhook" in antwort.headers["Location"]
    assert (await client.get("/api/adhstrack/koppeln/falsch", allow_redirects=False)).status == 404


async def test_config_flow(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "http", {})
    ergebnis = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert ergebnis["type"] is FlowResultType.FORM
    ergebnis = await hass.config_entries.flow.async_configure(
        ergebnis["flow_id"], {CONF_URL_INTERN: "http://192.168.1.5:8123/", CONF_URL_EXTERN: "http://unsicher.example"}
    )
    assert ergebnis["errors"] == {CONF_URL_EXTERN: "extern_https"}
    ergebnis = await hass.config_entries.flow.async_configure(
        ergebnis["flow_id"], {CONF_URL_INTERN: "http://192.168.1.5:8123/"}
    )
    assert ergebnis["step_id"] == "kopplung"
    assert "/api/adhstrack/koppeln/" in ergebnis["description_placeholders"]["kopplungs_link"]
    ergebnis = await hass.config_entries.flow.async_configure(ergebnis["flow_id"], {})
    assert ergebnis["type"] is FlowResultType.CREATE_ENTRY
    assert ergebnis["data"][CONF_URL_INTERN] == "http://192.168.1.5:8123"
    assert len(ergebnis["data"][CONF_WEBHOOK_ID]) > 20


async def test_dashboard_karten_werden_ausgeliefert(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    urls = list(hass.data[DATA_EXTRA_MODULE_URL].urls)
    assert any(u.startswith("/adhstrack_static/adhstrack-cards.js") for u in urls)
    client = await hass_client_no_auth()
    antwort = await client.get("/adhstrack_static/adhstrack-cards.js")
    assert antwort.status == 200
    text = await antwort.text()
    for karte in ("adhstrack-agenda-card", "adhstrack-kalender-card", "adhstrack-tagesplanung-card", "adhstrack-uhr-card", "adhstrack-medikamente-card"):
        assert karte in text
