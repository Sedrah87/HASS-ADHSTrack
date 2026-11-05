# Changelog

Alle sichtbaren Änderungen an der Integration „ADHS Track“. Die Version steht in Home Assistant bzw. im Marketplace/HACS; die Release-Notizen auf GitHub werden aus dieser Datei erzeugt.

## [0.6.0] – 2026-11-05

### Neu
- **Eigene Farben:** In den Optionen der Integration (Einstellungen › Geräte & Dienste › ADHS Track › Konfigurieren) lässt sich für Medikamente, Aufgaben und Kalendertermine je eine eigene Farbe wählen. Sie gilt in allen Dashboard-Karten und in den Attributen der Sensoren. Ist die Farbe ausgeschaltet, gilt weiter die Farbe aus der App.

## [0.5.2] – 2026-11-05

### Behoben
- Das Logo in der Beschreibung in HACS/Marketplace wird wieder angezeigt.

## [0.5.1] – 2026-11-05

### Geändert
- **Abhaken erst nach Bestätigung durch das iPhone:** Wird eine Aufgabe in Home Assistant abgehakt, zeigt die Karte sofort eine Sanduhr („wartet auf das iPhone“). Der Haken erscheint erst, wenn die App die Änderung übernommen und bestätigt hat. Geht unterwegs etwas verloren, wird die Änderung beim nächsten Abgleich erneut übertragen.
- Erneutes Antippen der Sanduhr nimmt die Änderung zurück.
- In der To-do-Liste steht bis zur Bestätigung der Hinweis „Wird an das iPhone übertragen …“.

## [0.5.0] – 2026-11-05

### Neu
- **Kalendertermine:** Termine aus den in der App ausgewählten iOS-Kalendern erscheinen im selben Kalender wie Aufgaben und Einnahmen (heute bis +6 Tage), auch ganztägige und mehrtägige Termine. Kein zweiter Kalender mehr nötig.
- Agenda, Tagesplanung, Kalender- und Uhr-Karte zeigen die Termine in der Kalenderfarbe; neuer Schalter „Kalendertermine ausblenden“.

Benötigt eine aktuelle Version der App mit dem Bereich „Kalendertermine“.

## [0.4.1] – 2026-11-05

### Behoben
- Einnahmen und „heute“ richten sich nach der Zeitzone des iPhones. Vorher konnte eine Einnahme nahe Mitternacht keinem Termin zugeordnet werden, wenn Home Assistant in einer anderen Zeitzone lief; die Erinnerung kam dann trotzdem.

## [0.4.0] – 2026-11-05

### Neu
- **Orte-Karte:** alle offenen Aufgaben mit Ort-Erinnerung, gruppiert nach Ort und Auslöser, mit Link zur Karte, Adresse und Umkreis; abhakbar.
- Sensor „Ort-Erinnerungen“ mit der Anzahl offener Aufgaben.

## [0.3.0] – 2026-11-05

### Neu
- Ort-Erinnerungen an Aufgaben werden in Agenda, Tagesplanung, Kalender und Uhr angezeigt; im Kalender als Ort des Termins.
- Neuer Schalter „Ort-Erinnerungen ausblenden“.

## [0.2.0] – 2026-11-03

### Neu
- **Kalender-Karte:** Zeitleiste der nächsten 3 oder 7 Tage mit Aufgaben und Einnahmen.
- Erledigte Aufgaben bzw. eingenommene Medikamente lassen sich in den Karten ausblenden.

### Geändert
- Alle Karten-Schalter heißen „… ausblenden“ und sind standardmäßig aus; ältere Einstellungen werden übernommen.

## [0.1.0] – 2026-11-03

### Neu
- Erste Version: Empfang der Daten aus der iOS-App per Webhook (lokal, ohne Cloud).
- Sensoren für Körperwerte, Journal, Aufgaben, Inbox, Medikamente und Training.
- To-do-Liste (abhakbar) und Kalender mit Aufgaben und Einnahmen.
- Ereignisse für Medikamenten-Erinnerungen, Einnahmen und Trainingszonen.
- Dienste „Einnahme erfassen“ und „Aufgabe erledigen“.
- Einrichtung per Kopplungslink und Dashboard-Karten (Agenda, Tagesplanung, Uhr, Inbox, Recovery, Strain, Medikamente).
