"""Konstanten der Integration ADHS Track."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "adhstrack"
NAME: Final = "ADHS Track"
HERSTELLER: Final = "Sedrah87"

PROTOKOLL_VERSION: Final = 1

CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_URL_INTERN: Final = "url_intern"
CONF_URL_EXTERN: Final = "url_extern"

# Eigene Farben (Optionen): je Art ein Schalter und eine Farbe
FARB_ARTEN: Final = ("medikamente", "aufgaben", "termine")

# Ereignisse auf dem HA-Bus (für Automationen)
EVENT_ERINNERUNG: Final = "adhstrack_medikament_erinnerung"
EVENT_EINGENOMMEN: Final = "adhstrack_medikament_eingenommen"
EVENT_ZONE: Final = "adhstrack_trainingszone_geaendert"
EVENT_TRAINING: Final = "adhstrack_training"

# Dienste
SERVICE_EINNAHME: Final = "einnahme_erfassen"
SERVICE_AUFGABE: Final = "aufgabe_erledigen"

# Befehle an die App (Antwort auf "sync")
BEFEHL_AUFGABE: Final = "aufgabe_status"
BEFEHL_EINNAHME: Final = "einnahme"

# Frontend (Dashboard-Karten)
FRONTEND_URL: Final = "/adhstrack_static"
FRONTEND_DATEI: Final = "adhstrack-cards.js"

# Ohne Trainingsmeldung so lange gilt eine Einheit als beendet (App abgestürzt, Netz weg).
TRAINING_TIMEOUT_SEKUNDEN: Final = 120

SPEICHER_VERSION: Final = 1
