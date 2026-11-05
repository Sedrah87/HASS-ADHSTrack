"""Zustand der Integration: letzter Stand aus der App, Befehls-Warteschlange, Medikamenten-Ereignisse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
import logging
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BEFEHL_AUFGABE,
    BEFEHL_EINNAHME,
    DOMAIN,
    EVENT_EINGENOMMEN,
    EVENT_ERINNERUNG,
    EVENT_TRAINING,
    EVENT_ZONE,
    SPEICHER_VERSION,
    TRAINING_TIMEOUT_SEKUNDEN,
)

_LOGGER = logging.getLogger(__name__)

# So viele IDs bereits gemeldeter Einnahmen / gefeuerter Erinnerungen werden gemerkt.
_MERK_GRENZE = 500


def signal_update(entry_id: str) -> str:
    """Dispatcher-Signal: Entitäten neu zeichnen."""
    return f"{DOMAIN}_{entry_id}_update"


def parse_zeit(wert: Any) -> datetime | None:
    """ISO-Zeit aus der App (UTC) in eine zeitzonenbehaftete datetime wandeln."""
    if not isinstance(wert, str):
        return None
    zeit = dt_util.parse_datetime(wert)
    if zeit is None:
        return None
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=dt_util.UTC)
    return zeit


def tag_text(zeit: datetime, zeitzone: tzinfo | None = None) -> str:
    """Kalendertag als yyyy-MM-dd (Standard: lokale HA-Zeitzone)."""
    return zeit.astimezone(zeitzone).date().isoformat() if zeitzone else dt_util.as_local(zeit).date().isoformat()


@dataclass
class AdhsDaten:
    """Laufzeitdaten eines Konfigurationseintrags."""

    hass: HomeAssistant
    entry_id: str
    store: Store
    stand: dict[str, Any] = field(default_factory=dict)
    training: dict[str, Any] = field(default_factory=dict)
    training_zuletzt: datetime | None = None
    letzter_sync: datetime | None = None
    warteschlange: list[dict[str, Any]] = field(default_factory=list)
    gesehene_einnahmen: list[str] = field(default_factory=list)
    gefeuerte_erinnerungen: list[str] = field(default_factory=list)
    einnahmen_initialisiert: bool = False
    _timer: dict[str, Callable[[], None]] = field(default_factory=dict)
    _abmelden: list[Callable[[], None]] = field(default_factory=list)

    # ------------------------------------------------------------------ Lebenszyklus

    async def async_laden(self) -> None:
        gespeichert = await self.store.async_load() or {}
        self.stand = gespeichert.get("stand", {})
        self.warteschlange = gespeichert.get("warteschlange", [])
        self.gesehene_einnahmen = gespeichert.get("gesehene_einnahmen", [])
        self.gefeuerte_erinnerungen = gespeichert.get("gefeuerte_erinnerungen", [])
        self.einnahmen_initialisiert = gespeichert.get("einnahmen_initialisiert", False)
        self.letzter_sync = parse_zeit(gespeichert.get("letzter_sync"))
        self._erinnerungen_planen()
        # Zeitabhängige Zustände (überfällig, ausstehend, Training-Timeout) jede Minute auffrischen.
        self._abmelden.append(
            async_track_time_interval(self.hass, self._minuten_takt, timedelta(minutes=1))
        )

    @callback
    def async_entladen(self) -> None:
        for abbrechen in self._timer.values():
            abbrechen()
        self._timer.clear()
        for abmelden in self._abmelden:
            abmelden()
        self._abmelden.clear()

    def _speichern(self) -> None:
        self.store.async_delay_save(
            lambda: {
                "stand": self.stand,
                "warteschlange": self.warteschlange,
                "gesehene_einnahmen": self.gesehene_einnahmen[-_MERK_GRENZE:],
                "gefeuerte_erinnerungen": self.gefeuerte_erinnerungen[-_MERK_GRENZE:],
                "einnahmen_initialisiert": self.einnahmen_initialisiert,
                "letzter_sync": self.letzter_sync.isoformat() if self.letzter_sync else None,
            },
            2,
        )

    @callback
    def _benachrichtigen(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry_id))

    @callback
    def _minuten_takt(self, _jetzt: datetime) -> None:
        if (
            self.training.get("aktiv")
            and self.training_zuletzt is not None
            and (dt_util.utcnow() - self.training_zuletzt).total_seconds() > TRAINING_TIMEOUT_SEKUNDEN
        ):
            _LOGGER.debug("Training ohne Meldung seit %ss – gilt als beendet", TRAINING_TIMEOUT_SEKUNDEN)
            self.training = {**self.training, "aktiv": False}
        self._benachrichtigen()

    # ------------------------------------------------------------------ Webhook

    @callback
    def verarbeite_sync(self, daten: dict[str, Any]) -> dict[str, Any]:
        """Stand aus der App übernehmen; liefert die Antwort mit offenen Befehlen."""
        bestaetigt = set(daten.get("bestaetigt") or [])
        if bestaetigt:
            vorher = len(self.warteschlange)
            self.warteschlange = [b for b in self.warteschlange if b["id"] not in bestaetigt]
            if len(self.warteschlange) != vorher:
                _LOGGER.debug("%s Befehl(e) von der App bestätigt", vorher - len(self.warteschlange))

        # Zeitzone des iPhones: die App bildet `datum` (Termine, Aufgaben) in dieser Zone.
        if isinstance(daten.get("zeitzone"), str) and dt_util.get_time_zone(daten["zeitzone"]) is not None:
            self.stand["zeitzone"] = daten["zeitzone"]

        # Abgeschaltete Bereiche fehlen in der Nachricht: dann den letzten Stand behalten.
        for bereich in ("koerper", "journal", "aufgaben", "orte", "inbox", "termine", "medikamente", "einnahmen"):
            if bereich in daten and daten[bereich] is not None:
                self.stand[bereich] = daten[bereich]

        if "einnahmen" in daten and daten["einnahmen"] is not None:
            self._neue_einnahmen_melden(daten["einnahmen"])

        self.letzter_sync = dt_util.utcnow()
        self._erinnerungen_planen()
        self._speichern()
        self._benachrichtigen()
        return {"befehle": list(self.warteschlange)}

    @callback
    def verarbeite_training(self, daten: dict[str, Any]) -> None:
        alte_zone = self.training.get("zone") if self.training.get("aktiv") else None
        war_aktiv = bool(self.training.get("aktiv"))
        self.training = {
            "aktiv": bool(daten.get("aktiv")),
            "aktivitaet": daten.get("aktivitaet"),
            "puls": daten.get("puls"),
            "zone": daten.get("zone"),
            "zonen_name": daten.get("zonen_name"),
            "ziel_unten": daten.get("ziel_unten"),
            "ziel_oben": daten.get("ziel_oben"),
            "hf_max": daten.get("hf_max"),
            "hinweis": daten.get("hinweis"),
        }
        self.training_zuletzt = dt_util.utcnow()
        aktiv = self.training["aktiv"]
        if aktiv != war_aktiv:
            self.hass.bus.async_fire(
                EVENT_TRAINING,
                {"aktiv": aktiv, "aktivitaet": self.training["aktivitaet"]},
            )
        zone = self.training["zone"]
        if aktiv and zone is not None and zone != alte_zone:
            self.hass.bus.async_fire(
                EVENT_ZONE,
                {
                    "zone": zone,
                    "vorherige_zone": alte_zone,
                    "zonen_name": self.training["zonen_name"],
                    "puls": self.training["puls"],
                    "hinweis": self.training["hinweis"],
                    "aktivitaet": self.training["aktivitaet"],
                },
            )
        self._benachrichtigen()

    # ------------------------------------------------------------------ Einnahmen

    def _neue_einnahmen_melden(self, einnahmen: list[dict[str, Any]]) -> None:
        bekannt = set(self.gesehene_einnahmen)
        for einnahme in einnahmen:
            eid = einnahme.get("id")
            if not eid or eid in bekannt:
                continue
            self.gesehene_einnahmen.append(eid)
            bekannt.add(eid)
            # Beim allerersten Abgleich nur merken, sonst feuern alte Einnahmen als neue Ereignisse.
            if self.einnahmen_initialisiert and einnahme.get("eingenommen"):
                self.hass.bus.async_fire(
                    EVENT_EINGENOMMEN,
                    {
                        "medikament": einnahme.get("name"),
                        "med_id": einnahme.get("med_id"),
                        "zeit": einnahme.get("zeit"),
                        "quelle": einnahme.get("quelle"),
                    },
                )
        self.einnahmen_initialisiert = True
        self.gesehene_einnahmen = self.gesehene_einnahmen[-_MERK_GRENZE:]

    def zeitzone(self) -> tzinfo | None:
        """Zeitzone der App; ohne Angabe (ältere App) die von HA (None)."""
        name = self.stand.get("zeitzone")
        return dt_util.get_time_zone(name) if name else None

    def tag(self, zeit: datetime) -> str:
        """Kalendertag wie in der App – nur so passt er zu `datum` aus der App."""
        return tag_text(zeit, self.zeitzone())

    def heute(self) -> str:
        return self.tag(dt_util.utcnow())

    def _wartende_einnahme(self, med_id: str, tag: str) -> bool:
        """Einnahme aus HA, die die App noch nicht übernommen hat (optimistische Anzeige)."""
        return any(
            b["art"] == BEFEHL_EINNAHME
            and b.get("med_id") == med_id
            and (zeit := parse_zeit(b.get("zeitpunkt"))) is not None
            and self.tag(zeit) == tag
            for b in self.warteschlange
        )

    def termin_status(self, med_id: str, termin: dict[str, Any]) -> str:
        if termin.get("status") == "offen" and self._wartende_einnahme(med_id, termin.get("datum", "")):
            return "eingenommen"
        return termin.get("status", "offen")

    def medikamente(self) -> list[dict[str, Any]]:
        """Medikamente mit Plan, Status inklusive noch nicht übernommener Einnahmen aus HA."""
        ergebnis = []
        for med in self.stand.get("medikamente") or []:
            plan = [
                {**t, "status": self.termin_status(med["id"], t)} for t in med.get("plan") or []
            ]
            ergebnis.append({**med, "plan": plan})
        return ergebnis

    def termine_heute(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        heute = self.heute()
        return [
            (med, termin)
            for med in self.medikamente()
            for termin in med["plan"]
            if termin.get("datum") == heute
        ]

    def faellige_medikamente(self) -> list[dict[str, Any]]:
        """Heute offen und Erinnerungszeit erreicht."""
        jetzt = dt_util.utcnow()
        return [
            med
            for med, termin in self.termine_heute()
            if termin["status"] == "offen"
            and (zeit := parse_zeit(termin.get("erinnerung_um"))) is not None
            and zeit <= jetzt
        ]

    def naechster_termin(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        jetzt = dt_util.utcnow()
        kandidaten = [
            (med, termin, zeit)
            for med in self.medikamente()
            for termin in med["plan"]
            if termin["status"] == "offen"
            and (zeit := parse_zeit(termin.get("einnahme_um"))) is not None
            and zeit >= jetzt - timedelta(hours=2)
        ]
        if not kandidaten:
            return None
        med, termin, _ = min(kandidaten, key=lambda k: k[2])
        return med, termin

    # ------------------------------------------------------------------ Erinnerungen

    def _erinnerungen_planen(self) -> None:
        for abbrechen in self._timer.values():
            abbrechen()
        self._timer.clear()
        jetzt = dt_util.utcnow()
        for med in self.stand.get("medikamente") or []:
            for termin in med.get("plan") or []:
                zeit = parse_zeit(termin.get("erinnerung_um"))
                if zeit is None or zeit <= jetzt or termin.get("status") != "offen":
                    continue
                schluessel = f"{med['id']}|{termin.get('datum')}"
                if schluessel in self.gefeuerte_erinnerungen:
                    continue
                self._timer[schluessel] = async_track_point_in_time(
                    self.hass, self._erinnerung_callback(med["id"], termin.get("datum")), zeit
                )

    def _erinnerung_callback(self, med_id: str, datum: str):
        @callback
        def _ausloesen(_zeit: datetime) -> None:
            schluessel = f"{med_id}|{datum}"
            self._timer.pop(schluessel, None)
            # Aktuellen Stand prüfen: inzwischen eingenommen (App oder HA) → kein Ereignis.
            for med in self.medikamente():
                if med["id"] != med_id:
                    continue
                for termin in med["plan"]:
                    if termin.get("datum") != datum or termin["status"] != "offen":
                        continue
                    self.gefeuerte_erinnerungen.append(schluessel)
                    self._speichern()
                    self.hass.bus.async_fire(
                        EVENT_ERINNERUNG,
                        {
                            "medikament": med.get("name"),
                            "med_id": med_id,
                            "dosis": med.get("dosis"),
                            "einheit": med.get("einheit"),
                            "kritisch": med.get("kritisch", False),
                            "einnahme_um": termin.get("einnahme_um"),
                            "erinnerung_um": termin.get("erinnerung_um"),
                        },
                    )
                    self._benachrichtigen()
                    return

        return _ausloesen

    # ------------------------------------------------------------------ Aufgaben

    def _wartender_status(self, uid: str) -> bool | None:
        for befehl in reversed(self.warteschlange):
            if befehl["art"] == BEFEHL_AUFGABE and befehl.get("uid") == uid:
                return befehl.get("erledigt")
        return None

    def aufgaben(self) -> list[dict[str, Any]]:
        """Aufgaben mit optimistisch übernommenem Status aus HA."""
        ergebnis = []
        for aufgabe in self.stand.get("aufgaben") or []:
            wartend = self._wartender_status(aufgabe.get("uid", ""))
            if wartend is not None:
                aufgabe = {**aufgabe, "erledigt": wartend, "ueberfaellig": aufgabe.get("ueberfaellig") and not wartend}
            ergebnis.append(aufgabe)
        return ergebnis

    def orte(self) -> list[dict[str, Any]]:
        """Offene Aufgaben mit Ort-Erinnerung (auch ohne Datum), Status optimistisch wie bei `aufgaben`."""
        ergebnis = []
        for aufgabe in self.stand.get("orte") or []:
            wartend = self._wartender_status(aufgabe.get("uid", ""))
            ergebnis.append({**aufgabe, "erledigt": wartend} if wartend is not None else aufgabe)
        return ergebnis

    def termine(self) -> list[dict[str, Any]]:
        """Termine aus den iOS-Kalendern der App (nur lesend)."""
        return self.stand.get("termine") or []

    def aufgabe(self, uid: str) -> dict[str, Any] | None:
        return next((a for a in [*self.aufgaben(), *self.orte()] if a.get("uid") == uid), None)

    # ------------------------------------------------------------------ Befehle aus HA

    @callback
    def aufgabe_setzen(self, uid: str, erledigt: bool) -> bool:
        aufgabe = self.aufgabe(uid)
        if aufgabe is None:
            return False
        # Ältere, noch nicht abgeholte Befehle zur selben Aufgabe ersetzen.
        self.warteschlange = [
            b for b in self.warteschlange if not (b["art"] == BEFEHL_AUFGABE and b.get("uid") == uid)
        ]
        self.warteschlange.append(
            {
                "id": uuid.uuid4().hex,
                "art": BEFEHL_AUFGABE,
                "uid": uid,
                "aufgabe_id": aufgabe["id"],
                "datum": aufgabe["datum"],
                "erledigt": erledigt,
            }
        )
        self._speichern()
        self._benachrichtigen()
        return True

    @callback
    def einnahme_erfassen(self, med_id: str, zeitpunkt: datetime | None = None) -> bool:
        if not any(m.get("id") == med_id for m in self.stand.get("medikamente") or []):
            return False
        zeit = zeitpunkt or dt_util.utcnow()
        if self._wartende_einnahme(med_id, self.tag(zeit)):
            return True
        self.warteschlange.append(
            {
                "id": uuid.uuid4().hex,
                "art": BEFEHL_EINNAHME,
                "med_id": med_id,
                # Ohne Sekundenbruchteile: die App liest reines ISO 8601 („…T07:15:00Z“).
                "zeitpunkt": dt_util.as_utc(zeit).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        self._speichern()
        self._benachrichtigen()
        return True

    def finde_medikament(self, suche: str) -> dict[str, Any] | None:
        suche_klein = suche.strip().lower()
        for med in self.stand.get("medikamente") or []:
            if med.get("id") == suche or (med.get("name") or "").strip().lower() == suche_klein:
                return med
        return None
