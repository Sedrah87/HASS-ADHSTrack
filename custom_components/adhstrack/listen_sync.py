"""Listen-Abgleich: App-Listen ↔ To-do-Listen in Home Assistant (Local To-do, Einkaufsliste, Todoist, …).

Abgeglichen wird hier, weil nur HA an fremde todo-Entitäten kommt. Regeln (Doku: HOME_ASSISTANT_2026-11-03.md):

* Feste IDs: App-Aufgaben behalten ihre UUID; in HA entstandene Einträge bekommen eine aus Liste und Eintrag
  abgeleitete UUID (uuid5) – ein wiederholter Befehl legt in der App nie ein Doppel an.
* Drei-Wege-Abgleich je Aufgabe: App-Stand, HA-Stand und gemeinsame Basis. Die Basis zeigt, *ob* eine Seite
  geändert hat, die Stempel entscheiden, *wer zuletzt*: App-Stempel kommen aus der App (synchronisiert über iCloud,
  über `gesendet` auf die HA-Uhr umgerechnet), HA-Änderungen werden beim Erkennen gestempelt (jede Minute und bei
  jedem Abgleich). Ein App-Stand mit gleichem oder älterem Stempel als die Basis gilt als veraltet (Nachzügler-Gerät).
* Löschen: In der App gelöscht → nur mit Grabstein; ohne Grabstein wird nur entkoppelt. In HA gelöscht → die App
  löscht nur offene Einmal-Aufgaben, die sie seitdem nicht geändert hat. Eine jüngere Änderung gewinnt immer.
* Offline-fest: Verknüpfungen, Basis und Befehle liegen in der Ablage; alles ist wiederholbar ohne Doppelwirkung.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    BEFEHL_AUFGABE,
    BEFEHL_LISTE_AENDERN,
    BEFEHL_LISTE_ANLEGEN,
    BEFEHL_LISTE_LOESCHEN,
    DOMAIN,
    SPEICHER_VERSION,
)
from .daten import parse_zeit

if TYPE_CHECKING:
    from .daten import AdhsDaten

_LOGGER = logging.getLogger(__name__)

# Namensraum für die aus Liste und Eintrag abgeleiteten Aufgaben-UUIDs (nie ändern).
_NAMENSRAUM = uuid.UUID("6c1f4f4e-1a43-4f43-9a76-0d1f3a5a7b21")
# So lange wartet ein in HA angelegter Eintrag auf seine uid (Cloud-Listen melden ihn verzögert), dann erneut anlegen.
_ANLAGE_WARTEN = timedelta(minutes=10)
# So lange wartet eine in HA entstandene Aufgabe darauf, dass die App sie meldet, bevor die Verknüpfung verfällt.
_AN_APP_WARTEN = timedelta(days=7)
# So lange merkt sich HA gelöschte Aufgaben (wie die Grabsteine der App), damit Nachzügler sie nicht neu anlegen.
_ENTFERNT_MERKEN = timedelta(days=30)
_MERK_GRENZE = 200
_UHR_WARNUNG = timedelta(minutes=2)

_DATUM = TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
_DATUMZEIT = TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
_BESCHREIBUNG = TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM

FEHLER_LISTE_UNBEKANNT = "liste_unbekannt"
FEHLER_ANLEGEN = "anlegen_fehlgeschlagen"


def aufgaben_id(entity_id: str, uid: str) -> str:
    """Feste Aufgaben-UUID für einen in HA entstandenen Eintrag (großgeschrieben wie in der App)."""
    return str(uuid.uuid5(_NAMENSRAUM, f"{entity_id}|{uid}")).upper()


def _iso(zeit: datetime) -> str:
    """ISO 8601 ohne Sekundenbruchteile in UTC – so liest die App Zeitpunkte."""
    return dt_util.as_utc(zeit).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _titel_schluessel(titel: str) -> str:
    return " ".join(titel.split()).casefold()


class ListenAbgleich:
    """Zustand und Abgleich aller zugeordneten Listen eines Konfigurationseintrags."""

    def __init__(self, hass: HomeAssistant, daten: AdhsDaten) -> None:
        self.hass = hass
        self.daten = daten
        self.store: Store = Store(hass, SPEICHER_VERSION, f"{DOMAIN}.{daten.entry_id}.listen")
        self._sperre = asyncio.Lock()
        # liste_id → {"entity_id": str | None, "zeit": iso}
        self.zuordnung: dict[str, dict[str, Any]] = {}
        # liste_id → aufgabe_id → Verknüpfung (s. _verknuepfung)
        self.verknuepfungen: dict[str, dict[str, dict[str, Any]]] = {}
        # anfrage_id → {"liste_id", "name", "eintrag_id"} für neue Local-To-do-Listen
        self.anlagen: dict[str, dict[str, Any]] = {}
        # aufgabe_id → iso: von HA entfernte Einträge gelöschter Aufgaben
        self.entfernt: dict[str, str] = {}
        # uid → iso: erstmals gesehene, noch unverknüpfte HA-Einträge (Zeitpunkt der Anlage in HA)
        self.neu_gesehen: dict[str, str] = {}
        self.erledigte_anfragen: list[str] = []
        self._abmelden: list[Any] = []

    # ------------------------------------------------------------------ Lebenszyklus

    async def async_laden(self) -> None:
        gespeichert = await self.store.async_load() or {}
        self.zuordnung = gespeichert.get("zuordnung", {})
        self.verknuepfungen = gespeichert.get("verknuepfungen", {})
        self.anlagen = gespeichert.get("anlagen", {})
        self.entfernt = gespeichert.get("entfernt", {})
        self.neu_gesehen = gespeichert.get("neu_gesehen", {})
        self.erledigte_anfragen = gespeichert.get("erledigte_anfragen", [])
        # HA-Änderungen auch ohne App erkennen und stempeln (z. B. während das iPhone offline ist).
        self._abmelden.append(async_track_time_interval(self.hass, self._minuten_takt, timedelta(minutes=1)))

    def async_entladen(self) -> None:
        for abmelden in self._abmelden:
            abmelden()
        self._abmelden.clear()

    async def async_entfernen(self) -> None:
        await self.store.async_remove()

    def _speichern(self) -> None:
        self.store.async_delay_save(
            lambda: {
                "zuordnung": self.zuordnung,
                "verknuepfungen": self.verknuepfungen,
                "anlagen": self.anlagen,
                "entfernt": self.entfernt,
                "neu_gesehen": self.neu_gesehen,
                "erledigte_anfragen": self.erledigte_anfragen[-_MERK_GRENZE:],
            },
            1,
        )

    def entity_fuer(self, liste_id: str) -> str | None:
        return (self.zuordnung.get(liste_id) or {}).get("entity_id")

    # ------------------------------------------------------------------ Webhook

    async def verarbeiten(self, nachricht: dict[str, Any]) -> dict[str, Any]:
        """Listen-Teil einer `sync`-Nachricht; liefert die zusätzlichen Antwortfelder."""
        async with self._sperre:
            jetzt = dt_util.utcnow()
            gesendet = parse_zeit(nachricht.get("gesendet"))
            versatz = (jetzt - gesendet) if gesendet else timedelta()
            if abs(versatz) > _UHR_WARNUNG:
                _LOGGER.warning("Uhr von %s weicht %s von HA ab – Zeitstempel werden umgerechnet",
                                nachricht.get("geraet"), versatz)
            erledigt: list[str] = []
            fehler: list[dict[str, str]] = []

            for anfrage in nachricht.get("zuordnung_setzen") or []:
                self._zuordnung_setzen(anfrage, versatz, erledigt, fehler)
            for anfrage in nachricht.get("liste_anlegen") or []:
                await self._liste_anlegen(anfrage, jetzt, erledigt, fehler)

            listen = nachricht.get("listen")
            if isinstance(listen, list):
                grabsteine = {
                    g["aufgabe_id"]: zeit + versatz
                    for g in nachricht.get("grabsteine") or []
                    if isinstance(g, dict) and g.get("aufgabe_id") and (zeit := parse_zeit(g.get("geloescht_am")))
                }
                alle_ids = {i for liste in listen if isinstance(liste, dict) for i in liste.get("ids") or []}
                for liste in listen:
                    if isinstance(liste, dict) and (entity_id := self.entity_fuer(liste.get("liste_id", ""))):
                        try:
                            await self._liste_abgleichen(liste, entity_id, versatz, alle_ids, grabsteine, jetzt)
                        except Exception:  # noqa: BLE001 – eine defekte Fremdliste darf den Abgleich nicht stoppen
                            _LOGGER.exception("Abgleich mit %s fehlgeschlagen", entity_id)

            self._aufraeumen(jetzt)
            self._speichern()
            return {
                "zuordnung": [
                    {"liste_id": lid, "entity_id": z["entity_id"]}
                    for lid, z in sorted(self.zuordnung.items()) if z.get("entity_id")
                ],
                "todo_listen": self._todo_listen(),
                "erledigt": erledigt,
                "fehler": fehler,
            }

    def _zuordnung_setzen(self, anfrage: dict[str, Any], versatz: timedelta, erledigt: list[str],
                          fehler: list[dict[str, str]]) -> None:
        anfrage_id, liste_id = anfrage.get("id"), anfrage.get("liste_id")
        if not anfrage_id or not liste_id:
            return
        if anfrage_id in self.erledigte_anfragen:
            erledigt.append(anfrage_id)
            return
        entity_id = anfrage.get("entity_id")
        if entity_id and entity_id not in {t["entity_id"] for t in self._todo_listen()}:
            fehler.append({"id": anfrage_id, "code": FEHLER_LISTE_UNBEKANNT, "text": entity_id})
            self._anfrage_erledigt(anfrage_id)
            return
        zeit = (parse_zeit(anfrage.get("zeit")) or dt_util.utcnow()) + versatz
        bisher = self.zuordnung.get(liste_id) or {}
        # Jüngste Änderung gilt (zwei Geräte ändern dieselbe Liste).
        if (bisher_zeit := parse_zeit(bisher.get("zeit"))) is None or zeit > bisher_zeit:
            if bisher.get("entity_id") != entity_id:
                # Neue Gegenstelle: Verknüpfungen verwerfen, gelöscht wird nichts.
                self.verknuepfungen.pop(liste_id, None)
                _LOGGER.info("Liste %s → %s", liste_id, entity_id or "getrennt")
            self.zuordnung[liste_id] = {"entity_id": entity_id, "zeit": _iso(zeit)}
        self._anfrage_erledigt(anfrage_id)
        erledigt.append(anfrage_id)

    async def _liste_anlegen(self, anfrage: dict[str, Any], jetzt: datetime, erledigt: list[str],
                             fehler: list[dict[str, str]]) -> None:
        anfrage_id, liste_id, name = anfrage.get("id"), anfrage.get("liste_id"), (anfrage.get("name") or "").strip()
        if not anfrage_id or not liste_id or not name:
            return
        if anfrage_id in self.erledigte_anfragen:
            erledigt.append(anfrage_id)
            return
        laufend = self.anlagen.get(anfrage_id)
        if laufend is None:
            eintrag_id = await self._local_todo_anlegen(name)
            if eintrag_id is None:
                fehler.append({"id": anfrage_id, "code": FEHLER_ANLEGEN, "text": name})
                self._anfrage_erledigt(anfrage_id)
                return
            # Vor dem Auflösen merken: ein erneut gesendeter Sync legt so keine zweite Liste an.
            laufend = self.anlagen[anfrage_id] = {"liste_id": liste_id, "name": name, "eintrag_id": eintrag_id}
            self._speichern()
        entity_id = self._entity_von_eintrag(laufend["eintrag_id"])
        if entity_id is None:
            return  # Liste wird noch eingerichtet – beim nächsten Abgleich erneut.
        self.verknuepfungen.pop(liste_id, None)
        self.zuordnung[liste_id] = {"entity_id": entity_id, "zeit": _iso(jetzt)}
        self.anlagen.pop(anfrage_id, None)
        self._anfrage_erledigt(anfrage_id)
        erledigt.append(anfrage_id)
        _LOGGER.info("Neue Liste „%s“ (%s) für %s angelegt", name, entity_id, liste_id)

    async def _local_todo_anlegen(self, name: str) -> str | None:
        """Neue Local-To-do-Liste; gibt es den Namen schon, wird die vorhandene genommen."""
        schluessel = slugify(name)
        vorhanden = next(
            (e for e in self.hass.config_entries.async_entries("local_todo") if e.data.get("storage_key") == schluessel),
            None,
        )
        if vorhanden is not None:
            return vorhanden.entry_id
        try:
            ergebnis = await self.hass.config_entries.flow.async_init(
                "local_todo", context={"source": "user"}, data={"todo_list_name": name}
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Local-To-do-Liste „%s“ konnte nicht angelegt werden", name)
            return None
        eintrag = ergebnis.get("result")
        return eintrag.entry_id if eintrag is not None else None

    def _entity_von_eintrag(self, eintrag_id: str) -> str | None:
        register = er.async_get(self.hass)
        return next(
            (e.entity_id for e in er.async_entries_for_config_entry(register, eintrag_id) if e.domain == "todo"),
            None,
        )

    def _anfrage_erledigt(self, anfrage_id: str) -> None:
        if anfrage_id not in self.erledigte_anfragen:
            self.erledigte_anfragen = [*self.erledigte_anfragen, anfrage_id][-_MERK_GRENZE:]

    def _todo_listen(self) -> list[dict[str, Any]]:
        """Alle To-do-Listen in HA außer der eigenen Sammel-Liste."""
        register = er.async_get(self.hass)
        listen = []
        for zustand in self.hass.states.async_all("todo"):
            eintrag = register.async_get(zustand.entity_id)
            if eintrag is not None and eintrag.platform == DOMAIN:
                continue
            listen.append({
                "entity_id": zustand.entity_id,
                "name": zustand.attributes.get("friendly_name") or zustand.entity_id,
                "funktionen": int(zustand.attributes.get("supported_features") or 0),
            })
        return sorted(listen, key=lambda t: t["name"].casefold())

    def _aufraeumen(self, jetzt: datetime) -> None:
        self.entfernt = {
            aid: zeit for aid, zeit in self.entfernt.items()
            if (z := parse_zeit(zeit)) is not None and jetzt - z < _ENTFERNT_MERKEN
        }

    # ------------------------------------------------------------------ HA-Liste lesen/schreiben

    def _funktionen(self, entity_id: str) -> int:
        zustand = self.hass.states.get(entity_id)
        return int(zustand.attributes.get("supported_features") or 0) if zustand else 0

    async def _eintraege(self, entity_id: str) -> dict[str, dict[str, Any]] | None:
        """Einträge der HA-Liste nach uid; `None`, wenn die Liste nicht erreichbar ist (dann nichts ableiten)."""
        if self.hass.states.get(entity_id) is None:
            return None
        antwort = await self.hass.services.async_call(
            "todo", "get_items", {"entity_id": entity_id}, blocking=True, return_response=True
        )
        eintraege = ((antwort or {}).get(entity_id) or {}).get("items") or []
        return {e["uid"]: e for e in eintraege if e.get("uid")}

    async def _aufrufen(self, dienst: str, daten: dict[str, Any]) -> None:
        await self.hass.services.async_call("todo", dienst, daten, blocking=True)

    @staticmethod
    def _inhalt_ha(eintrag: dict[str, Any], funktionen: int) -> dict[str, Any]:
        faellig = None
        if funktionen & (_DATUM | _DATUMZEIT) and (due := eintrag.get("due")):
            if isinstance(due, str) and len(due) == 10:
                faellig = due
            elif (zeit := parse_zeit(due if isinstance(due, str) else due.isoformat())) is not None:
                faellig = _iso(zeit)
        return {
            "titel": (eintrag.get("summary") or "").strip(),
            "notiz": (eintrag.get("description") or "") if funktionen & _BESCHREIBUNG else None,
            "faellig": faellig,
            "erledigt": eintrag.get("status") == "completed",
        }

    @staticmethod
    def _inhalt_app(aufgabe: dict[str, Any], funktionen: int) -> dict[str, Any]:
        faellig = None
        if funktionen & _DATUMZEIT and (zeit := parse_zeit(aufgabe.get("faellig_zeit"))) is not None:
            faellig = _iso(zeit)
        elif funktionen & (_DATUM | _DATUMZEIT) and aufgabe.get("faellig_tag"):
            faellig = aufgabe["faellig_tag"]
        return {
            "titel": (aufgabe.get("titel") or "").strip(),
            "notiz": (aufgabe.get("notiz") or "") if funktionen & _BESCHREIBUNG else None,
            "faellig": faellig,
            "erledigt": bool(aufgabe.get("erledigt")),
        }

    @staticmethod
    def _dienstdaten(inhalt: dict[str, Any], funktionen: int, neu: bool) -> dict[str, Any]:
        """Felder für todo.add_item/update_item, nur soweit die Liste sie kennt."""
        daten: dict[str, Any] = {}
        if funktionen & _BESCHREIBUNG and (inhalt["notiz"] or not neu):
            daten["description"] = inhalt["notiz"] or None
        if funktionen & (_DATUM | _DATUMZEIT) and (inhalt["faellig"] or not neu):
            faellig = inhalt["faellig"]
            if faellig is None:
                daten["due_date" if funktionen & _DATUM else "due_datetime"] = None
            elif len(faellig) == 10:
                daten["due_date"] = faellig
            else:
                daten["due_datetime"] = parse_zeit(faellig)
        return daten

    async def _anlegen_in_ha(self, entity_id: str, inhalt: dict[str, Any], funktionen: int,
                             belegt: set[str]) -> str | None:
        """Legt den Eintrag an und sucht seine uid (todo.add_item liefert keine)."""
        await self._aufrufen("add_item", {"entity_id": entity_id, "item": inhalt["titel"],
                                          **self._dienstdaten(inhalt, funktionen, neu=True)})
        uid = self._unbelegt_mit_titel(await self._eintraege(entity_id) or {}, inhalt["titel"], belegt)
        if uid is not None and inhalt["erledigt"]:
            await self._aufrufen("update_item", {"entity_id": entity_id, "item": uid, "status": "completed"})
        return uid

    async def _entfernen_in_ha(self, entity_id: str, uid: str, eintraege: dict[str, dict[str, Any]]) -> None:
        await self._aufrufen("remove_item", {"entity_id": entity_id, "item": [uid]})
        eintraege.pop(uid, None)  # sonst gälte er weiter unten als neuer Eintrag aus HA

    @staticmethod
    def _unbelegt_mit_titel(eintraege: dict[str, dict[str, Any]], titel: str, belegt: set[str]) -> str | None:
        schluessel = _titel_schluessel(titel)
        treffer = [uid for uid, e in eintraege.items()
                   if uid not in belegt and _titel_schluessel(e.get("summary") or "") == schluessel]
        return treffer[-1] if treffer else None

    async def _aktualisieren_in_ha(self, entity_id: str, uid: str, alt: dict[str, Any], neu: dict[str, Any],
                                   funktionen: int) -> None:
        daten: dict[str, Any] = {"entity_id": entity_id, "item": uid}
        if neu["titel"] != alt["titel"]:
            daten["rename"] = neu["titel"]
        if neu["erledigt"] != alt["erledigt"]:
            daten["status"] = "completed" if neu["erledigt"] else "needs_action"
        felder = self._dienstdaten(neu, funktionen, neu=False)
        if neu["notiz"] == alt["notiz"]:
            felder.pop("description", None)
        if neu["faellig"] == alt["faellig"]:
            felder.pop("due_date", None)
            felder.pop("due_datetime", None)
        daten.update(felder)
        if len(daten) > 2:
            await self._aufrufen("update_item", daten)

    # ------------------------------------------------------------------ Abgleich einer Liste

    @staticmethod
    def _verknuepfung(uid: str | None, basis: dict[str, Any] | None, stempel: datetime, **mehr: Any) -> dict[str, Any]:
        return {"uid": uid, "basis": basis, "stempel": _iso(stempel), "ha_geaendert": None, "ha_geloescht": None,
                "anlage_seit": None, "an_app": None, "vorkommen": None, "vorkommen_erledigt": None, **mehr}

    def _ha_aenderungen_stempeln(self, links: dict[str, dict[str, Any]], eintraege: dict[str, dict[str, Any]],
                                 funktionen: int, jetzt: datetime) -> None:
        """Erkannte HA-Änderungen seit der Basis mit dem Zeitpunkt des Erkennens stempeln (nur das erste Mal)."""
        for link in links.values():
            if link["uid"] is None or link["basis"] is None:
                continue
            eintrag = eintraege.get(link["uid"])
            if eintrag is None:
                link["ha_geloescht"] = link["ha_geloescht"] or _iso(jetzt)
            elif self._inhalt_ha(eintrag, funktionen) != link["basis"]:
                link["ha_geaendert"] = link["ha_geaendert"] or _iso(jetzt)
        belegt = {link["uid"] for link in links.values() if link["uid"]}
        for uid in eintraege:
            if uid not in belegt:
                self.neu_gesehen.setdefault(uid, _iso(jetzt))

    async def _minuten_takt(self, _jetzt: datetime) -> None:
        async with self._sperre:
            jetzt = dt_util.utcnow()
            for liste_id, links in self.verknuepfungen.items():
                if not links or not (entity_id := self.entity_fuer(liste_id)):
                    continue
                try:
                    eintraege = await self._eintraege(entity_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("%s nicht lesbar", entity_id, exc_info=True)
                    continue
                if eintraege is not None:
                    self._ha_aenderungen_stempeln(links, eintraege, self._funktionen(entity_id), jetzt)
            self._speichern()

    def _befehl(self, art: str, aufgabe_id: str, **felder: Any) -> None:
        self.daten.befehl_einreihen(art, f"liste|{aufgabe_id}", aufgabe_id=aufgabe_id, **felder)

    async def _liste_abgleichen(self, liste: dict[str, Any], entity_id: str, versatz: timedelta, alle_ids: set[str],
                                grabsteine: dict[str, datetime], jetzt: datetime) -> None:
        eintraege = await self._eintraege(entity_id)
        if eintraege is None:
            return  # Liste gerade nicht erreichbar: nichts ableiten, beim nächsten Mal.
        liste_id = liste["liste_id"]
        funktionen = self._funktionen(entity_id)
        links = self.verknuepfungen.setdefault(liste_id, {})
        self._ha_aenderungen_stempeln(links, eintraege, funktionen, jetzt)
        app = {a["id"]: a for a in liste.get("aufgaben") or [] if isinstance(a, dict) and a.get("id")}
        ids = set(liste.get("ids") or [])
        belegt = {link["uid"] for link in links.values() if link["uid"]}

        def nach_app(zeit: datetime) -> str:
            return _iso(zeit - versatz)

        # 1. Aufgaben der App
        for aid, aufgabe in app.items():
            stempel_app = (parse_zeit(aufgabe.get("geaendert_am")) or datetime.fromtimestamp(0, dt_util.UTC)) + versatz
            soll = self._inhalt_app(aufgabe, funktionen)
            serie = bool(aufgabe.get("serie"))
            link = links.get(aid)

            if link is not None and link["an_app"]:
                link["an_app"] = None  # die App kennt die Aufgabe jetzt

            if link is None or (link["uid"] is None and link["anlage_seit"] is None and link["ha_geloescht"] is None):
                if soll["erledigt"] or not soll["titel"]:
                    continue  # erledigte Aufgaben nicht neu in HA anlegen
                if (geloescht := parse_zeit(self.entfernt.get(aid))) is not None and stempel_app <= geloescht:
                    continue  # Nachzügler-Gerät meldet eine bereits gelöschte Aufgabe
                if (uid := self._unbelegt_mit_titel(eintraege, soll["titel"], belegt)) is not None:
                    # Gleicher Titel schon in HA: verknüpfen statt doppelt anlegen. Basis = HA-Stand; die App gewinnt,
                    # wenn sie abweicht (Basis-Stempel 0).
                    link = links[aid] = self._verknuepfung(uid, self._inhalt_ha(eintraege[uid], funktionen),
                                                           datetime.fromtimestamp(0, dt_util.UTC))
                    belegt.add(uid)
                else:
                    await self._neu_in_ha(links, aid, entity_id, soll, funktionen, belegt, stempel_app, jetzt, aufgabe)
                    continue

            if link["uid"] is None and link["anlage_seit"] is not None:
                # Angelegt, uid noch unbekannt: zuerst suchen, erst nach Ablauf erneut anlegen.
                if (uid := self._unbelegt_mit_titel(eintraege, soll["titel"], belegt)) is not None:
                    link.update(uid=uid, anlage_seit=None, basis=self._inhalt_ha(eintraege[uid], funktionen))
                    belegt.add(uid)
                elif jetzt - (parse_zeit(link["anlage_seit"]) or jetzt) > _ANLAGE_WARTEN:
                    await self._neu_in_ha(links, aid, entity_id, soll, funktionen, belegt, stempel_app, jetzt, aufgabe)
                    continue
                else:
                    continue

            eintrag = eintraege.get(link["uid"]) if link["uid"] else None
            if eintrag is None:
                await self._in_ha_entfernt(links, aid, aufgabe, soll, serie, stempel_app, entity_id, funktionen,
                                           belegt, jetzt, nach_app)
                continue

            ist = self._inhalt_ha(eintrag, funktionen)
            if serie:
                await self._serie_abgleichen(link, aid, aufgabe, soll, ist, stempel_app, entity_id, funktionen, nach_app)
                continue

            basis_stempel = parse_zeit(link["stempel"]) or jetzt
            ha_stempel = parse_zeit(link["ha_geaendert"])
            app_geaendert = soll != link["basis"] and stempel_app > basis_stempel
            ha_geaendert = ist != link["basis"]
            if soll == ist:
                link.update(basis=ist, stempel=_iso(max(stempel_app, ha_stempel or basis_stempel)), ha_geaendert=None)
                self.daten.befehle_verwerfen(f"liste|{aid}")
            elif app_geaendert and (not ha_geaendert or ha_stempel is None or stempel_app >= ha_stempel):
                await self._aktualisieren_in_ha(entity_id, link["uid"], ist, soll, funktionen)
                link.update(basis=soll, stempel=_iso(stempel_app), ha_geaendert=None)
                self.daten.befehle_verwerfen(f"liste|{aid}")
                if ha_geaendert:
                    _LOGGER.info("Konflikt bei „%s“: App-Änderung ist jünger", soll["titel"])
            elif ha_geaendert:
                if app_geaendert:
                    _LOGGER.info("Konflikt bei „%s“: HA-Änderung ist jünger", ist["titel"])
                self._befehl(BEFEHL_LISTE_AENDERN, aid, **self._befehl_inhalt(ist, funktionen),
                             geaendert_am=nach_app(ha_stempel or jetzt))
            # sonst: App-Stand abweichend, aber nicht jünger als die Basis → veraltetes Gerät, nichts tun.

        # 2. Verknüpfte Aufgaben, die die App nicht (mehr) meldet
        for aid in [a for a in links if a not in app]:
            link = links[aid]
            if link["an_app"]:
                if jetzt - (parse_zeit(link["an_app"]) or jetzt) > _AN_APP_WARTEN:
                    links.pop(aid)
                continue
            uid = link["uid"] if link["uid"] in eintraege else None
            if aid in ids or (aid in alle_ids and aid not in ids):
                # Aus dem Fenster gefallen bzw. in eine andere verbundene Liste verschoben: HA-Eintrag entfernen.
                if uid:
                    await self._entfernen_in_ha(entity_id, uid, eintraege)
                links.pop(aid)
                continue
            geloescht = grabsteine.get(aid)
            if geloescht is None:
                links.pop(aid)  # ohne Grabstein (z. B. Liste gewechselt, ältere App): nur entkoppeln
                continue
            ha_stempel = parse_zeit(link["ha_geaendert"])
            if uid and ha_stempel is not None and ha_stempel > geloescht:
                # In HA nach dem Löschen geändert → Änderung gewinnt, Aufgabe in der App wiederherstellen.
                ist = self._inhalt_ha(eintraege[uid], funktionen)
                self._befehl(BEFEHL_LISTE_ANLEGEN, aid, liste_id=liste_id, **self._befehl_inhalt(ist, funktionen),
                             geaendert_am=nach_app(ha_stempel))
                link.update(an_app=_iso(jetzt), basis=ist, stempel=_iso(ha_stempel), ha_geaendert=None)
                continue
            if uid:
                await self._entfernen_in_ha(entity_id, uid, eintraege)
            self.entfernt[aid] = _iso(geloescht)
            links.pop(aid)

        # 3. Neue Einträge in HA → neue Aufgaben in der App (feste UUID)
        belegt = {link["uid"] for link in links.values() if link["uid"]}
        for uid, eintrag in eintraege.items():
            if uid in belegt:
                continue
            ist = self._inhalt_ha(eintrag, funktionen)
            aid = aufgaben_id(entity_id, uid)
            if ist["erledigt"] or not ist["titel"] or aid in links or aid in self.entfernt:
                continue  # erledigte Altbestände nicht übernehmen
            zeit = parse_zeit(self.neu_gesehen.pop(uid, None)) or jetzt
            self._befehl(BEFEHL_LISTE_ANLEGEN, aid, liste_id=liste_id, **self._befehl_inhalt(ist, funktionen),
                         geaendert_am=nach_app(zeit))
            links[aid] = self._verknuepfung(uid, ist, zeit, an_app=_iso(jetzt))
        self.neu_gesehen = {uid: z for uid, z in self.neu_gesehen.items() if uid in eintraege}

    async def _neu_in_ha(self, links: dict[str, dict[str, Any]], aid: str, entity_id: str, soll: dict[str, Any],
                         funktionen: int, belegt: set[str], stempel_app: datetime, jetzt: datetime,
                         aufgabe: dict[str, Any]) -> None:
        # Vor dem Anlegen merken: bricht HA mitten im Anlegen ab, wird beim nächsten Mal zuerst gesucht.
        links[aid] = self._verknuepfung(None, None, stempel_app, anlage_seit=_iso(jetzt),
                                        vorkommen=aufgabe.get("vorkommen"))
        self._speichern()
        uid = await self._anlegen_in_ha(entity_id, soll, funktionen, belegt)
        if uid is not None:
            links[aid].update(uid=uid, basis=soll, anlage_seit=None)
            belegt.add(uid)

    async def _in_ha_entfernt(self, links, aid, aufgabe, soll, serie, stempel_app, entity_id, funktionen, belegt,
                              jetzt, nach_app) -> None:
        link = links[aid]
        geloescht = parse_zeit(link["ha_geloescht"]) or jetzt
        link.update(ha_geloescht=_iso(geloescht), uid=None)
        if serie or stempel_app > geloescht:
            # Serien löscht HA nie; nach dem Löschen in der App geändert → Änderung gewinnt, neu anlegen.
            if not soll["erledigt"]:
                await self._neu_in_ha(links, aid, entity_id, soll, funktionen, belegt, stempel_app, jetzt, aufgabe)
            return
        if soll["erledigt"]:
            return  # erledigte Aufgaben werden nur entkoppelt (z. B. „Erledigte entfernen“ in HA)
        self._befehl(BEFEHL_LISTE_LOESCHEN, aid, geaendert_am=nach_app(geloescht))

    async def _serie_abgleichen(self, link, aid, aufgabe, soll, ist, stempel_app, entity_id, funktionen,
                                nach_app) -> None:
        """Serien: Titel/Notiz wie gewohnt; Datum und Status gehören dem Vorkommen in der App. Abhaken in HA erledigt
        das angezeigte Vorkommen (`aufgabe_status`), danach zeigt HA das nächste."""
        vorkommen = aufgabe.get("vorkommen")
        if ist["erledigt"] and link["vorkommen"] and link["vorkommen_erledigt"] != link["vorkommen"]:
            self.daten.befehl_einreihen(BEFEHL_AUFGABE, f"serie|{aid}", uid=f"{aid}|{link['vorkommen']}",
                                        aufgabe_id=aid, datum=link["vorkommen"], erledigt=True)
            link["vorkommen_erledigt"] = link["vorkommen"]
        wartet = link["vorkommen_erledigt"] is not None and link["vorkommen_erledigt"] == vorkommen
        text_felder = ("titel", "notiz")
        basis_stempel = parse_zeit(link["stempel"]) or stempel_app
        ha_stempel = parse_zeit(link["ha_geaendert"])
        text_soll = {f: soll[f] for f in text_felder}
        text_ist = {f: ist[f] for f in text_felder}
        text_basis = {f: (link["basis"] or {}).get(f) for f in text_felder}
        app_text = text_soll != text_basis and stempel_app > basis_stempel
        ha_text = text_ist != text_basis
        ziel = dict(soll)
        if ha_text and not (app_text and ha_stempel is not None and stempel_app >= ha_stempel) and text_ist != text_soll:
            self._befehl(BEFEHL_LISTE_AENDERN, aid, titel=ist["titel"], notiz=ist["notiz"] or "",
                         felder=["titel", "notiz"] if funktionen & _BESCHREIBUNG else ["titel"],
                         geaendert_am=nach_app(ha_stempel or dt_util.utcnow()))
            ziel.update(text_ist)  # HA-Text bleibt, bis die App ihn übernommen hat
        if wartet:
            ziel["erledigt"] = ist["erledigt"]  # abgehaktes Vorkommen bis zur Bestätigung stehen lassen
            ziel["faellig"] = ist["faellig"]
        elif ist["erledigt"] is False and link["vorkommen_erledigt"] == link["vorkommen"] and link["vorkommen"] == vorkommen:
            # In HA wieder geöffnet, bevor die App das Vorkommen abgehakt meldete.
            self.daten.befehl_einreihen(BEFEHL_AUFGABE, f"serie|{aid}", uid=f"{aid}|{vorkommen}",
                                        aufgabe_id=aid, datum=vorkommen, erledigt=False)
            link["vorkommen_erledigt"] = None
        if ziel != ist:
            await self._aktualisieren_in_ha(entity_id, link["uid"], ist, ziel, funktionen)
        if not wartet:
            link["vorkommen_erledigt"] = None
        link.update(basis=ziel, stempel=_iso(max(stempel_app, basis_stempel)), ha_geaendert=None, vorkommen=vorkommen)

    def _befehl_inhalt(self, inhalt: dict[str, Any], funktionen: int) -> dict[str, Any]:
        """HA-Stand als Befehlsfelder; nicht unterstützte Felder fehlen in `felder` und bleiben in der App."""
        felder = ["titel", "erledigt"]
        daten: dict[str, Any] = {"titel": inhalt["titel"], "erledigt": inhalt["erledigt"]}
        if funktionen & _BESCHREIBUNG:
            felder.append("notiz")
            daten["notiz"] = inhalt["notiz"] or ""
        if funktionen & (_DATUM | _DATUMZEIT):
            felder.append("faellig")
            faellig = inhalt["faellig"]
            if faellig and len(faellig) == 10:
                daten["faellig_tag"] = faellig
            elif faellig and (zeit := parse_zeit(faellig)) is not None:
                # Kalendertag in der Zeitzone der App, wie sie `faellig_tag` bildet.
                daten["faellig_tag"] = self.daten.tag(zeit)
                daten["faellig_zeit"] = _iso(zeit)
        daten["felder"] = felder
        return daten
