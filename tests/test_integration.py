"""Webhook-Abgleich, Befehle, Ereignisse und Config-Flow."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
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


async def test_koffein_spiegel_baut_ohne_app_ab(
    hass: HomeAssistant, eintrag, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    jetzt = dt_util.utcnow().replace(microsecond=0)
    freezer.move_to(jetzt)
    journal = {"tag": "2026-11-08", "koffein": 2, "alkohol": 0, "fluessigkeit_ml": 0}
    # Ältere App ohne die neuen Felder: Sensoren bleiben unbekannt.
    await sende(hass_client_no_auth, sync(journal=journal))
    await hass.async_block_till_done()
    register = er.async_get(hass)
    spiegel = register.async_get_entity_id("sensor", DOMAIN, f"{eintrag.entry_id}_koffein_spiegel")
    heute = register.async_get_entity_id("sensor", DOMAIN, f"{eintrag.entry_id}_koffein_mg")
    assert hass.states.get(spiegel).state == "unknown"

    await sende(hass_client_no_auth, sync(journal={
        **journal, "koffein_mg": 160, "koffein_spiegel_mg": 120, "koffein_spiegel_zeit": iso(jetzt),
    }))
    await hass.async_block_till_done()
    assert hass.states.get(spiegel).state == "120"
    assert hass.states.get(heute).state == "160"

    # 5 h ohne Abgleich: der Sensor rechnet selbst weiter (minütlicher Takt).
    freezer.move_to(jetzt + timedelta(hours=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(spiegel).state == "60"
    assert hass.states.get(heute).state == "160"

    # Abgleich ohne Koffein-Basis (Health gesperrt): letzte Basis bleibt, keine Lücke im Verlauf.
    await sende(hass_client_no_auth, sync(journal=journal))
    await hass.async_block_till_done()
    assert hass.states.get(spiegel).state == "60"
    freezer.move_to(jetzt + timedelta(hours=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(spiegel).state == "30"


async def test_aufgabe_abhaken_und_bestaetigen(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date().isoformat()
    await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute)]))
    await hass.async_block_till_done()

    todo_id = next(s.entity_id for s in hass.states.async_all("todo"))
    assert hass.states.get(todo_id).state == "1"
    await hass.services.async_call(
        "todo", "update_item", {"item": f"A1|{heute}", "status": "completed"}, target={"entity_id": todo_id}, blocking=True
    )
    # Nicht optimistisch: Stand der App bleibt, die Änderung wartet auf das iPhone.
    agenda = next(s for s in hass.states.async_all("sensor") if s.attributes.get("termine") is not None)
    assert hass.states.get(todo_id).state == "1"
    assert hass.states.get(agenda.entity_id).attributes["aufgaben"][0]["wartet"] is True
    assert hass.states.get(agenda.entity_id).attributes["aufgaben"][0]["erledigt"] is False

    antwort = await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute)]))
    befehl = antwort["befehle"][0]
    assert befehl["art"] == "aufgabe_status" and befehl["aufgabe_id"] == "A1" and befehl["erledigt"] is True
    # Antwort mit dem Befehl ist unterwegs, App meldet noch den alten Stand → wartet weiter
    await hass.async_block_till_done()
    assert hass.states.get(todo_id).state == "1"

    # Antwort verloren: der nächste Abgleich ohne Bestätigung stellt denselben Befehl erneut zu
    antwort = await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute)]))
    assert [b["id"] for b in antwort["befehle"]] == [befehl["id"]]

    # iPhone hat verarbeitet und bestätigt → erst jetzt erledigt
    antwort = await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute, erledigt=True)], bestaetigt=[befehl["id"]]))
    assert antwort["befehle"] == []
    await hass.async_block_till_done()
    assert hass.states.get(todo_id).state == "0"
    assert "wartet" not in hass.states.get(agenda.entity_id).attributes["aufgaben"][0]


async def test_kalendertermine_im_kalender_und_agenda(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date()
    start = dt_util.start_of_local_day() + timedelta(hours=14)
    termine = [
        {"uid": "E1|1", "datum": heute.isoformat(), "bis_datum": heute.isoformat(), "titel": "Zahnarzt",
         "start": iso(start), "ende": iso(start + timedelta(hours=1)), "ganztaegig": False, "ort": "Praxis",
         "kalender": "Privat", "farbe": "#FF0000", "wiederkehrend": False},
        {"uid": "E2|2", "datum": heute.isoformat(), "bis_datum": (heute + timedelta(days=1)).isoformat(), "titel": "Urlaub",
         "start": iso(dt_util.start_of_local_day()), "ende": iso(dt_util.start_of_local_day() + timedelta(days=2)),
         "ganztaegig": True, "kalender": "Privat", "wiederkehrend": False},
    ]
    await sende(hass_client_no_auth, sync(aufgaben=[aufgabe("A1", heute.isoformat())], termine=termine))
    await hass.async_block_till_done()

    kalender_id = next(s.entity_id for s in hass.states.async_all("calendar"))
    antwort = await hass.services.async_call(
        "calendar", "get_events",
        {"start_date_time": dt_util.start_of_local_day(), "end_date_time": dt_util.start_of_local_day() + timedelta(days=3)},
        target={"entity_id": kalender_id}, blocking=True, return_response=True,
    )
    ereignisse = {e["summary"]: e for e in antwort[kalender_id]["events"]}
    assert set(ereignisse) == {"Aufgabe A1", "Zahnarzt", "Urlaub"}
    assert ereignisse["Zahnarzt"]["location"] == "Praxis"
    assert ereignisse["Urlaub"]["start"] == heute.isoformat()
    assert ereignisse["Urlaub"]["end"] == (heute + timedelta(days=2)).isoformat()

    agenda = next(s for s in hass.states.async_all("sensor") if s.attributes.get("termine"))
    assert [t["titel"] for t in agenda.attributes["termine"]] == ["Zahnarzt", "Urlaub"]

    # Leere Liste aus der App (z. B. Kalenderzugriff entzogen) leert den Stand.
    await sende(hass_client_no_auth, sync(termine=[]))
    await hass.async_block_till_done()
    assert hass.states.get(agenda.entity_id).attributes["termine"] == []


async def test_ort_erinnerung_im_kalender_und_sensor(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date().isoformat()
    mit_ort = {**aufgabe("A2", heute), "ort": {
        "ausloeser": "ankommen", "bezeichnung": "Supermarkt", "name": "Supermarkt", "adresse": "Hauptstraße 1, Berlin",
        "breite": 52.52, "laenge": 13.40, "umkreis": 150,
    }}
    await sende(hass_client_no_auth, sync(aufgaben=[mit_ort]))
    await hass.async_block_till_done()

    kalender_id = next(s.entity_id for s in hass.states.async_all("calendar"))
    antwort = await hass.services.async_call(
        "calendar", "get_events",
        {"start_date_time": dt_util.start_of_local_day(), "end_date_time": dt_util.start_of_local_day() + timedelta(days=1)},
        target={"entity_id": kalender_id}, blocking=True, return_response=True,
    )
    termine = antwort[kalender_id]["events"]
    assert termine[0]["location"] == "Hauptstraße 1, Berlin"

    agenda = next(s for s in hass.states.async_all("sensor") if "aufgaben" in s.attributes and isinstance(s.attributes["aufgaben"], list)
                  and any("ort" in a for a in s.attributes["aufgaben"]))
    assert agenda.attributes["aufgaben"][0]["ort"]["ausloeser"] == "ankommen"


async def test_orte_sensor_und_abhaken_ohne_datum(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date().isoformat()
    ortsaufgabe = {
        "uid": f"O1|{heute}", "id": "O1", "datum": heute, "titel": "Milch kaufen", "erledigt": False,
        "wiederkehrend": False, "ort": {"ausloeser": "ankommen", "bezeichnung": "Supermarkt", "breite": 52.5, "laenge": 13.4},
    }
    await sende(hass_client_no_auth, sync(aufgaben=[], orte=[ortsaufgabe]))
    await hass.async_block_till_done()
    orte = next(s for s in hass.states.async_all("sensor") if s.attributes.get("aufgaben") and s.attributes["aufgaben"][0].get("id") == "O1")
    assert orte.state == "1"

    # Abhaken über den bestehenden Dienst, obwohl die Aufgabe nicht in `aufgaben` steht
    await hass.services.async_call(DOMAIN, "aufgabe_erledigen", {"uid": f"O1|{heute}", "erledigt": True}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(orte.entity_id).state == "1"
    assert hass.states.get(orte.entity_id).attributes["aufgaben"][0]["wartet"] is True
    antwort = await sende(hass_client_no_auth, sync(aufgaben=[], orte=[ortsaufgabe]))
    befehl = antwort["befehle"][0]
    assert befehl["aufgabe_id"] == "O1" and befehl["datum"] == heute and befehl["erledigt"] is True


async def test_aufgaben_ohne_datum_und_ohne_uhrzeit(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date()
    ohne_zeit = {**aufgabe("A2", heute.isoformat()), "start": iso(dt_util.start_of_local_day()), "ohne_zeit": True, "im_kalender": True}
    undatiert = {"uid": f"U1|{heute.isoformat()}", "id": "U1", "datum": heute.isoformat(), "titel": "Irgendwann",
                 "erledigt": False, "prioritaet": "mittel"}
    await sende(hass_client_no_auth, sync(aufgaben=[ohne_zeit], ohne_datum=[undatiert]))
    await hass.async_block_till_done()

    agenda = next(s for s in hass.states.async_all("sensor") if s.attributes.get("ohne_datum"))
    assert agenda.attributes["ohne_datum"][0]["titel"] == "Irgendwann"
    assert agenda.attributes["aufgaben"][0]["im_kalender"] is True

    # To-do-Liste: Aufgabe ohne Uhrzeit mit reinem Datum, Aufgabe ohne Datum ohne Fälligkeit.
    todo_id = next(s.entity_id for s in hass.states.async_all("todo"))
    assert hass.states.get(todo_id).state == "2"
    antwort = await hass.services.async_call(
        "todo", "get_items", {}, target={"entity_id": todo_id}, blocking=True, return_response=True
    )
    eintraege = {e["summary"]: e for e in antwort[todo_id]["items"]}
    assert eintraege["Aufgabe A2"]["due"] == heute.isoformat()
    assert "due" not in eintraege["Irgendwann"] or eintraege["Irgendwann"]["due"] is None

    # Kalender: ohne Uhrzeit ganztägig, ohne Datum gar nicht.
    kalender_id = next(s.entity_id for s in hass.states.async_all("calendar"))
    antwort = await hass.services.async_call(
        "calendar", "get_events",
        {"start_date_time": dt_util.start_of_local_day(), "end_date_time": dt_util.start_of_local_day() + timedelta(days=2)},
        target={"entity_id": kalender_id}, blocking=True, return_response=True,
    )
    ereignisse = {e["summary"]: e for e in antwort[kalender_id]["events"]}
    assert set(ereignisse) == {"Aufgabe A2"}
    assert ereignisse["Aufgabe A2"]["start"] == heute.isoformat()

    # Abhaken einer Aufgabe ohne Datum über den bestehenden Dienst.
    await hass.services.async_call(DOMAIN, "aufgabe_erledigen", {"uid": undatiert["uid"], "erledigt": True}, blocking=True)
    antwort = await sende(hass_client_no_auth, sync(aufgaben=[ohne_zeit], ohne_datum=[undatiert]))
    befehl = antwort["befehle"][0]
    assert befehl["aufgabe_id"] == "U1" and befehl["datum"] == heute.isoformat() and befehl["erledigt"] is True


async def test_einnahme_dienst_und_erinnerung_unterdrueckt(
    hass: HomeAssistant, eintrag, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    # Lokaler Mittag: Einnahme und Termin liegen sicher am selben Kalendertag (sonst scheitert der Test kurz vor Mitternacht).
    freezer.move_to(dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0))
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


async def test_tag_in_zeitzone_der_app(
    hass: HomeAssistant, eintrag, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    """HA (Testzone US/Pacific) und App (Europe/Berlin) liegen an verschiedenen Kalendertagen.

    07:00 UTC = 08:00 Berlin (5.11.) = 23:00 Pacific (4.11.). Die App meldet `datum` in ihrer Zone;
    HA muss die Einnahme demselben Tag zuordnen, sonst bleibt der Termin offen und die Erinnerung kommt.
    """
    await hass.config.async_set_time_zone("US/Pacific")
    freezer.move_to("2026-11-05T07:00:00+00:00")
    erinnerungen = async_capture_events(hass, EVENT_ERINNERUNG)
    erinnerung = dt_util.utcnow() + timedelta(hours=1)
    plan = {**medikament(erinnerung), "plan": [{**medikament(erinnerung)["plan"][0], "datum": "2026-11-05"}]}
    await sende(hass_client_no_auth, sync(zeitzone="Europe/Berlin", medikamente=[plan], einnahmen=[]))
    await hass.async_block_till_done()

    offene = next(s for s in hass.states.async_all("sensor") if "faellig" in s.attributes)
    assert offene.state == "1"  # „heute“ = 5.11. (Berlin), nicht 4.11. (Pacific)

    await hass.services.async_call(DOMAIN, "einnahme_erfassen", {"medikament": "elvanse"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(offene.entity_id).state == "0"

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
    for karte in ("adhstrack-agenda-card", "adhstrack-kalender-card", "adhstrack-tagesplanung-card", "adhstrack-uhr-card", "adhstrack-medikamente-card", "adhstrack-orte-card"):
        assert karte in text


async def test_eigene_farben(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:
    heute = dt_util.now().date().isoformat()
    termin = {"uid": "E1|1", "datum": heute, "bis_datum": heute, "titel": "Zahnarzt", "ganztaegig": True,
              "kalender": "Privat", "farbe": "#FF0000", "wiederkehrend": False}
    a = {**aufgabe("A1", heute), "farbe": "#00FF00"}
    stand = sync(aufgaben=[a], termine=[termin], medikamente=[medikament(dt_util.now() + timedelta(hours=1))])

    ergebnis = await hass.config_entries.options.async_init(eintrag.entry_id)
    abschluss = await hass.config_entries.options.async_configure(
        ergebnis["flow_id"],
        {
            "url_intern": "http://ha.local:8123",
            "eigene_farbe_medikamente": True, "farbe_medikamente": "#111111",
            "eigene_farbe_aufgaben": True, "farbe_aufgaben": "#222222",
            "eigene_farbe_termine": False, "farbe_termine": "#333333",
        },
    )
    await hass.async_block_till_done()
    assert abschluss["type"] == "create_entry", abschluss
    await sende(hass_client_no_auth, stand)
    await hass.async_block_till_done()

    agenda = next(s for s in hass.states.async_all("sensor") if s.attributes.get("termine"))
    assert agenda.attributes["aufgaben"][0]["farbe"] == "#222222"
    assert agenda.attributes["termine"][0]["farbe"] == "#FF0000"
    meds = next(s for s in hass.states.async_all("sensor") if s.attributes.get("medikamente"))
    assert meds.attributes["medikamente"][0]["farbe"] == "#111111"
