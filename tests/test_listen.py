"""Listen-Abgleich mit fremden To-do-Listen (Local To-do als Gegenstelle)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

# Local To-do (Gegenstelle der Tests) braucht das Paket „ical“; ohne es werden diese Tests übersprungen.
pytest.importorskip("ical")

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.adhstrack.listen_sync import aufgaben_id

from .test_integration import eintrag, iso, sende, sync  # noqa: F401 – Fixture

LISTE = "11111111-1111-1111-1111-111111111111"
ZIEL = "todo.einkauf"


@pytest.fixture(autouse=True)
def ablage(hass: HomeAssistant, tmp_path) -> None:
    """Local To-do schreibt seine .ics-Dateien nach <config>/.storage – im Test in ein leeres Verzeichnis."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / ".storage").mkdir()


@pytest.fixture
async def einkauf(hass: HomeAssistant, eintrag) -> MockConfigEntry:  # noqa: F811
    entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Einkauf", "storage_key": "einkauf"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ZIEL) is not None
    return entry


async def eintraege(hass: HomeAssistant) -> list[dict[str, Any]]:
    antwort = await hass.services.async_call("todo", "get_items", {"entity_id": ZIEL}, blocking=True, return_response=True)
    return antwort[ZIEL]["items"]


async def ha_hinzufuegen(hass: HomeAssistant, titel: str) -> None:
    await hass.services.async_call("todo", "add_item", {"entity_id": ZIEL, "item": titel}, blocking=True)


def aufgabe(aid: str, titel: str, geaendert, erledigt: bool = False, notiz: str = "", serie: bool = False,
            vorkommen: str | None = None) -> dict[str, Any]:
    return {"id": aid, "titel": titel, "notiz": notiz, "erledigt": erledigt, "faellig_tag": vorkommen,
            "serie": serie, "vorkommen": vorkommen, "geaendert_am": iso(geaendert), "geaendert_von": "iPhone"}


def liste(*aufgaben: dict[str, Any], ids: list[str] | None = None) -> dict[str, Any]:
    return {"liste_id": LISTE, "name": "Einkauf",
            "aufgaben": list(aufgaben), "ids": ids if ids is not None else [a["id"] for a in aufgaben]}


def zuordnen(zeit=None) -> dict[str, Any]:
    return {"id": "Z1", "liste_id": LISTE, "entity_id": ZIEL, "zeit": iso(zeit or dt_util.utcnow())}


def befehle(antwort: dict[str, Any], art: str) -> list[dict[str, Any]]:
    return [b for b in antwort["befehle"] if b["art"] == art]


async def test_zuordnung_und_anlage_in_ha(hass: HomeAssistant, einkauf, hass_client_no_auth) -> None:
    jetzt = dt_util.utcnow()
    antwort = await sende(hass_client_no_auth, sync(
        zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "Milch", jetzt))]))
    assert antwort["erledigt"] == ["Z1"]
    assert antwort["zuordnung"] == [{"liste_id": LISTE, "entity_id": ZIEL}]
    assert ZIEL in [t["entity_id"] for t in antwort["todo_listen"]]
    assert "todo.adhs_track_aufgaben" not in [t["entity_id"] for t in antwort["todo_listen"]]
    assert [e["summary"] for e in await eintraege(hass)] == ["Milch"]

    # Erneut gesendet (Antwort verloren): kein zweiter Eintrag.
    antwort = await sende(hass_client_no_auth, sync(
        zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "Milch", jetzt))]))
    assert antwort["erledigt"] == ["Z1"]
    assert len(await eintraege(hass)) == 1
    assert befehle(antwort, "aufgabe_anlegen") == []


async def test_gleicher_titel_wird_verknuepft_statt_verdoppelt(hass: HomeAssistant, einkauf, hass_client_no_auth) -> None:
    await ha_hinzufuegen(hass, "Brot")
    antwort = await sende(hass_client_no_auth, sync(
        zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "brot ", dt_util.utcnow()))]))
    assert befehle(antwort, "aufgabe_anlegen") == []
    assert [e["summary"] for e in await eintraege(hass)] == ["brot"]  # App-Fassung gewinnt beim ersten Verknüpfen


async def test_neuer_eintrag_in_ha_wird_aufgabe_mit_fester_id(hass: HomeAssistant, einkauf, hass_client_no_auth) -> None:
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()], listen=[liste()]))
    await ha_hinzufuegen(hass, "Käse")
    antwort = await sende(hass_client_no_auth, sync(listen=[liste()]))
    neu = befehle(antwort, "aufgabe_anlegen")
    assert len(neu) == 1
    uid = (await eintraege(hass))[0]["uid"]
    assert neu[0]["aufgabe_id"] == aufgaben_id(ZIEL, uid)
    assert neu[0]["liste_id"] == LISTE and neu[0]["titel"] == "Käse"

    # Nachzügler meldet die Aufgabe noch nicht: Befehl bleibt derselbe, Verknüpfung bleibt.
    antwort = await sende(hass_client_no_auth, sync(listen=[liste()]))
    assert [b["id"] for b in befehle(antwort, "aufgabe_anlegen")] == [neu[0]["id"]]

    # App hat angelegt und bestätigt: kein neuer Befehl, kein Doppel in HA.
    antwort = await sende(hass_client_no_auth, sync(
        bestaetigt=[neu[0]["id"]], listen=[liste(aufgabe(neu[0]["aufgabe_id"], "Käse", dt_util.utcnow()))]))
    assert befehle(antwort, "aufgabe_anlegen") == []
    assert len(await eintraege(hass)) == 1


async def test_letzte_aenderung_gewinnt(hass: HomeAssistant, einkauf, hass_client_no_auth, freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "Milch", t0))]))
    uid = (await eintraege(hass))[0]["uid"]

    # App ändert (jünger) → HA übernimmt.
    freezer.tick(timedelta(minutes=1))
    await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Hafermilch", dt_util.utcnow()))]))
    assert (await eintraege(hass))[0]["summary"] == "Hafermilch"

    # HA ändert danach → Befehl an die App mit HA-Zeitpunkt.
    freezer.tick(timedelta(minutes=1))
    await hass.services.async_call("todo", "update_item", {"entity_id": ZIEL, "item": uid, "rename": "Sojamilch"},
                                   blocking=True)
    antwort = await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Hafermilch", t0 + timedelta(minutes=1)))]))
    aendern = befehle(antwort, "aufgabe_aendern")
    assert len(aendern) == 1 and aendern[0]["titel"] == "Sojamilch"
    assert (await eintraege(hass))[0]["summary"] == "Sojamilch"

    # Danach ändert die App erneut (jünger als die HA-Änderung) → App gewinnt, Befehl wird verworfen.
    freezer.tick(timedelta(minutes=1))
    antwort = await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Mandelmilch", dt_util.utcnow()))]))
    assert befehle(antwort, "aufgabe_aendern") == []
    assert (await eintraege(hass))[0]["summary"] == "Mandelmilch"


async def test_veraltetes_geraet_ueberschreibt_nichts(hass: HomeAssistant, einkauf, hass_client_no_auth, freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "Milch", t0))]))
    freezer.tick(timedelta(minutes=5))
    await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Hafermilch", dt_util.utcnow()))]))
    # Mac mit altem iCloud-Stand meldet noch „Milch“ mit dem alten Stempel.
    antwort = await sende(hass_client_no_auth, sync(geraet="Mac", listen=[liste(aufgabe("A1", "Milch", t0))]))
    assert (await eintraege(hass))[0]["summary"] == "Hafermilch"
    assert befehle(antwort, "aufgabe_aendern") == []


async def test_loeschen_in_der_app(hass: HomeAssistant, einkauf, hass_client_no_auth, freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()],
                                          listen=[liste(aufgabe("A1", "Milch", t0), aufgabe("A2", "Brot", t0))]))
    freezer.tick(timedelta(minutes=1))
    # A1 mit Grabstein gelöscht, A2 ohne Grabstein verschwunden (z. B. ältere App) → nur entkoppelt.
    await sende(hass_client_no_auth, sync(
        listen=[liste()], grabsteine=[{"aufgabe_id": "A1", "liste_id": LISTE, "geloescht_am": iso(dt_util.utcnow()),
                                       "geloescht_von": "iPhone"}]))
    assert [e["summary"] for e in await eintraege(hass)] == ["Brot"]

    # Nachzügler meldet A1 noch → wird nicht neu angelegt.
    await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Milch", t0))]))
    assert [e["summary"] for e in await eintraege(hass)] == ["Brot"]


async def test_aenderung_in_ha_gewinnt_gegen_frueheres_loeschen(hass: HomeAssistant, einkauf, hass_client_no_auth,
                                                                freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()], listen=[liste(aufgabe("A1", "Milch", t0))]))
    uid = (await eintraege(hass))[0]["uid"]
    geloescht = t0 + timedelta(minutes=1)
    freezer.tick(timedelta(minutes=2))
    await hass.services.async_call("todo", "update_item", {"entity_id": ZIEL, "item": uid, "rename": "Milch 2 l"},
                                   blocking=True)
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow())  # Minuten-Takt stempelt die HA-Änderung
    await hass.async_block_till_done()
    antwort = await sende(hass_client_no_auth, sync(
        listen=[liste()], grabsteine=[{"aufgabe_id": "A1", "liste_id": LISTE, "geloescht_am": iso(geloescht),
                                       "geloescht_von": "iPhone"}]))
    wieder = befehle(antwort, "aufgabe_anlegen")
    assert len(wieder) == 1 and wieder[0]["aufgabe_id"] == "A1" and wieder[0]["titel"] == "Milch 2 l"
    assert len(await eintraege(hass)) == 1


async def test_loeschen_in_ha(hass: HomeAssistant, einkauf, hass_client_no_auth, freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(
        zuordnung_setzen=[zuordnen()],
        listen=[liste(aufgabe("A1", "Milch", t0), aufgabe("A2", "Brot", t0))]))
    # A2 in der App erledigt
    freezer.tick(timedelta(minutes=1))
    await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Milch", t0),
                                                        aufgabe("A2", "Brot", dt_util.utcnow(), erledigt=True))]))
    uids = {e["summary"]: e["uid"] for e in await eintraege(hass)}
    await hass.services.async_call("todo", "remove_item", {"entity_id": ZIEL, "item": list(uids.values())}, blocking=True)
    freezer.tick(timedelta(minutes=1))
    antwort = await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("A1", "Milch", t0),
                                                                  aufgabe("A2", "Brot", t0, erledigt=True))]))
    loeschen = befehle(antwort, "aufgabe_loeschen")
    # Nur die offene Aufgabe wird gelöscht, die erledigte bleibt in der App.
    assert [b["aufgabe_id"] for b in loeschen] == ["A1"]
    assert await eintraege(hass) == []


async def test_aus_dem_fenster_ohne_befehl(hass: HomeAssistant, einkauf, hass_client_no_auth, freezer) -> None:
    t0 = dt_util.utcnow()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()],
                                          listen=[liste(aufgabe("A1", "Milch", t0, erledigt=False))]))
    freezer.tick(timedelta(minutes=1))
    antwort = await sende(hass_client_no_auth, sync(listen=[liste(ids=["A1"])]))
    assert await eintraege(hass) == []
    assert antwort["befehle"] == []


async def test_serie_abhaken_erledigt_vorkommen(hass: HomeAssistant, einkauf, hass_client_no_auth) -> None:
    t0 = dt_util.utcnow()
    heute = dt_util.now().date().isoformat()
    morgen = (dt_util.now() + timedelta(days=1)).date().isoformat()
    await sende(hass_client_no_auth, sync(zuordnung_setzen=[zuordnen()],
                                          listen=[liste(aufgabe("S1", "Müll", t0, serie=True, vorkommen=heute))]))
    uid = (await eintraege(hass))[0]["uid"]
    await hass.services.async_call("todo", "update_item", {"entity_id": ZIEL, "item": uid, "status": "completed"},
                                   blocking=True)
    antwort = await sende(hass_client_no_auth, sync(listen=[liste(aufgabe("S1", "Müll", t0, serie=True, vorkommen=heute))]))
    status = befehle(antwort, "aufgabe_status")
    assert len(status) == 1 and status[0]["datum"] == heute and status[0]["erledigt"] is True
    assert (await eintraege(hass))[0]["status"] == "completed"  # bleibt abgehakt, bis die App bestätigt

    # App meldet das nächste Vorkommen → HA zeigt es offen, keine Löschung.
    antwort = await sende(hass_client_no_auth, sync(
        bestaetigt=[status[0]["id"]], listen=[liste(aufgabe("S1", "Müll", t0, serie=True, vorkommen=morgen))]))
    neu = (await eintraege(hass))[0]
    assert neu["status"] == "needs_action" and neu["due"] == morgen
    assert antwort["befehle"] == []


async def test_neue_liste_anlegen(hass: HomeAssistant, eintrag, hass_client_no_auth) -> None:  # noqa: F811
    antwort = await sende(hass_client_no_auth, sync(
        liste_anlegen=[{"id": "N1", "liste_id": LISTE, "name": "Baumarkt"}], listen=[]))
    await hass.async_block_till_done()
    if "N1" not in antwort["erledigt"]:
        antwort = await sende(hass_client_no_auth, sync(liste_anlegen=[{"id": "N1", "liste_id": LISTE, "name": "Baumarkt"}]))
    assert antwort["erledigt"] == ["N1"]
    assert antwort["zuordnung"] == [{"liste_id": LISTE, "entity_id": "todo.baumarkt"}]
    # Erneut gesendet: keine zweite Liste.
    await sende(hass_client_no_auth, sync(liste_anlegen=[{"id": "N1", "liste_id": LISTE, "name": "Baumarkt"}]))
    assert len(hass.config_entries.async_entries("local_todo")) == 1
