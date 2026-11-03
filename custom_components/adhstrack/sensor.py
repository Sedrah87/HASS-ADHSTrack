"""Sensoren: Körperwerte, Journal, Aufgaben, Medikamente, Training."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AdhsConfigEntry
from .daten import AdhsDaten, parse_zeit
from .entity import AdhsEntity

BPM = "bpm"


def _k(feld: str) -> Callable[[AdhsDaten], Any]:
    return lambda d: (d.stand.get("koerper") or {}).get(feld)


def _j(feld: str) -> Callable[[AdhsDaten], Any]:
    return lambda d: (d.stand.get("journal") or {}).get(feld)


def _t(feld: str) -> Callable[[AdhsDaten], Any]:
    return lambda d: d.training.get(feld) if d.training.get("aktiv") else None


def _heute(d: AdhsDaten) -> list[dict[str, Any]]:
    heute = dt_util.now().date().isoformat()
    return [a for a in d.aufgaben() if a.get("datum") == heute]


def _offen_heute(d: AdhsDaten) -> list[dict[str, Any]]:
    return [a for a in _heute(d) if not a.get("erledigt")]


def _ueberfaellig(d: AdhsDaten) -> list[dict[str, Any]]:
    return [a for a in d.aufgaben() if a.get("ueberfaellig") and not a.get("erledigt")]


def _kurz(aufgabe: dict[str, Any]) -> dict[str, Any]:
    return {k: aufgabe.get(k) for k in ("uid", "titel", "start", "erledigt", "ueberfaellig", "liste")}


def _naechste_einnahme(d: AdhsDaten) -> datetime | None:
    termin = d.naechster_termin()
    return parse_zeit(termin[1].get("einnahme_um")) if termin else None


def _naechste_einnahme_attrs(d: AdhsDaten) -> dict[str, Any]:
    termin = d.naechster_termin()
    if not termin:
        return {}
    med, t = termin
    return {
        "medikament": med.get("name"),
        "med_id": med.get("id"),
        "dosis": med.get("dosis"),
        "einheit": med.get("einheit"),
        "erinnerung_um": t.get("erinnerung_um"),
        "kritisch": med.get("kritisch"),
    }


@dataclass(frozen=True, kw_only=True)
class AdhsSensorBeschreibung(SensorEntityDescription):
    wert: Callable[[AdhsDaten], Any]
    attribute: Callable[[AdhsDaten], dict[str, Any]] | None = None


SENSOREN: tuple[AdhsSensorBeschreibung, ...] = (
    # Körper
    AdhsSensorBeschreibung(
        key="recovery", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        wert=_k("recovery"),
        attribute=lambda d: {
            "kategorie": _k("kategorie")(d),
            "empfehlung_titel": _k("empfehlung_titel")(d),
            "empfehlung_text": _k("empfehlung_text")(d),
            "aktualisiert": _k("aktualisiert")(d),
        },
    ),
    AdhsSensorBeschreibung(
        key="schlaf", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        wert=_k("schlaf_prozent"),
    ),
    AdhsSensorBeschreibung(
        key="schlafdauer", device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT, wert=_k("schlaf_dauer_min"),
    ),
    AdhsSensorBeschreibung(
        key="strain", state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1,
        wert=_k("strain"),
        attribute=lambda d: {"ziel_min": _k("ziel_min")(d), "ziel_max": _k("ziel_max")(d)},
    ),
    AdhsSensorBeschreibung(
        key="ruhepuls", native_unit_of_measurement=BPM, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, wert=_k("ruhepuls"),
    ),
    AdhsSensorBeschreibung(
        key="hrv", native_unit_of_measurement=UnitOfTime.MILLISECONDS, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, wert=_k("hrv"),
    ),
    AdhsSensorBeschreibung(
        key="atemfrequenz", native_unit_of_measurement="/min", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, wert=_k("atemfrequenz"),
    ),
    AdhsSensorBeschreibung(
        key="letzte_herzfrequenz", native_unit_of_measurement=BPM, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, wert=_k("letzte_hf"),
        attribute=lambda d: {"gemessen": _k("letzte_hf_zeit")(d)},
    ),
    # Journal
    AdhsSensorBeschreibung(key="koffein", native_unit_of_measurement="Tassen", state_class=SensorStateClass.MEASUREMENT, wert=_j("koffein")),
    AdhsSensorBeschreibung(key="alkohol", native_unit_of_measurement="Getränke", state_class=SensorStateClass.MEASUREMENT, wert=_j("alkohol")),
    AdhsSensorBeschreibung(
        key="fluessigkeit", device_class=SensorDeviceClass.VOLUME, native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.MEASUREMENT, wert=_j("fluessigkeit_ml"),
    ),
    AdhsSensorBeschreibung(key="stimmung", state_class=SensorStateClass.MEASUREMENT, wert=_j("stimmung")),
    AdhsSensorBeschreibung(key="energie", state_class=SensorStateClass.MEASUREMENT, wert=_j("energie")),
    # Aufgaben
    AdhsSensorBeschreibung(
        key="aufgaben_offen", state_class=SensorStateClass.MEASUREMENT,
        wert=lambda d: len(_offen_heute(d)) if d.stand.get("aufgaben") is not None else None,
        attribute=lambda d: {"aufgaben": [_kurz(a) for a in _offen_heute(d)], "gesamt_heute": len(_heute(d))},
    ),
    AdhsSensorBeschreibung(
        key="aufgaben_ueberfaellig", state_class=SensorStateClass.MEASUREMENT,
        wert=lambda d: len(_ueberfaellig(d)) if d.stand.get("aufgaben") is not None else None,
        attribute=lambda d: {"aufgaben": [_kurz(a) for a in _ueberfaellig(d)]},
    ),
    AdhsSensorBeschreibung(
        key="agenda",
        wert=lambda d: len(d.aufgaben()) if d.stand.get("aufgaben") is not None else None,
        attribute=lambda d: {"aufgaben": d.aufgaben()},
    ),
    AdhsSensorBeschreibung(
        key="inbox", state_class=SensorStateClass.MEASUREMENT,
        wert=lambda d: (d.stand.get("inbox") or {}).get("anzahl"),
        attribute=lambda d: {"eintraege": (d.stand.get("inbox") or {}).get("eintraege", [])},
    ),
    # Medikamente
    AdhsSensorBeschreibung(
        key="medikamente",
        wert=lambda d: sum(1 for _, t in d.termine_heute() if t["status"] == "offen")
        if d.stand.get("medikamente") is not None else None,
        attribute=lambda d: {"medikamente": d.medikamente(), "faellig": [m["id"] for m in d.faellige_medikamente()]},
    ),
    AdhsSensorBeschreibung(
        key="naechste_einnahme", device_class=SensorDeviceClass.TIMESTAMP,
        wert=_naechste_einnahme, attribute=_naechste_einnahme_attrs,
    ),
    # Training
    AdhsSensorBeschreibung(
        key="trainingszone", state_class=SensorStateClass.MEASUREMENT, wert=_t("zone"),
        attribute=lambda d: {
            k: d.training.get(k)
            for k in ("zonen_name", "hinweis", "aktivitaet", "ziel_unten", "ziel_oben", "hf_max")
        },
    ),
    AdhsSensorBeschreibung(
        key="trainingspuls", native_unit_of_measurement=BPM, state_class=SensorStateClass.MEASUREMENT, wert=_t("puls"),
    ),
    # Diagnose
    AdhsSensorBeschreibung(
        key="letzter_abgleich", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
        wert=lambda d: d.letzter_sync,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AdhsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(AdhsSensor(entry.runtime_data, b) for b in SENSOREN)


class AdhsSensor(AdhsEntity, SensorEntity):
    # Listen können groß werden (Recorder speichert Attribute über 16 KB ohnehin nicht).
    _unrecorded_attributes = frozenset({"aufgaben", "eintraege", "medikamente", "faellig"})

    entity_description: AdhsSensorBeschreibung

    def __init__(self, daten: AdhsDaten, beschreibung: AdhsSensorBeschreibung) -> None:
        super().__init__(daten, beschreibung.key)
        self.entity_description = beschreibung

    @property
    def native_value(self) -> Any:
        wert = self.entity_description.wert(self.daten)
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP and isinstance(wert, str):
            return parse_zeit(wert)
        return wert

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attribute is None:
            return None
        return self.entity_description.attribute(self.daten)
