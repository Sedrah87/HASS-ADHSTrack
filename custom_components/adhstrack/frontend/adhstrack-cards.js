/*
 * ADHS Track – Dashboard-Karten für Home Assistant.
 * Wird von der Integration automatisch geladen (keine Ressource nötig).
 * Karten finden ihre Entitäten über das Entitäten-Register (Plattform „adhstrack“ + translation_key),
 * damit sie unabhängig von Sprache und Entitäts-IDs funktionieren. Optional per `entity` überschreibbar.
 */

const VERSION = "0.2.0";
const DOMAIN = "adhstrack";

// ---------------------------------------------------------------- Hilfsfunktionen

function findeEntitaet(hass, schluessel, ueberschrieben) {
  if (ueberschrieben) return ueberschrieben;
  const eintraege = Object.values(hass.entities || {});
  const treffer = eintraege.find((e) => e.platform === DOMAIN && e.translation_key === schluessel);
  return treffer ? treffer.entity_id : undefined;
}

function zustand(hass, schluessel, ueberschrieben) {
  const id = findeEntitaet(hass, schluessel, ueberschrieben);
  return id ? hass.states[id] : undefined;
}

function sprache(hass) {
  return (hass.locale && hass.locale.language) || hass.language || "de";
}

function uhrzeit(hass, iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(sprache(hass), { hour: "2-digit", minute: "2-digit" });
}

function heuteText(datum = new Date()) {
  const j = datum.getFullYear();
  const m = String(datum.getMonth() + 1).padStart(2, "0");
  const t = String(datum.getDate()).padStart(2, "0");
  return `${j}-${m}-${t}`;
}

function plusTage(tage) {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() + tage);
  return heuteText(d);
}

function tagUeberschrift(hass, datum) {
  if (datum === heuteText()) return "Heute";
  if (datum === plusTage(1)) return "Morgen";
  const [j, m, t] = datum.split("-").map(Number);
  return new Date(j, m - 1, t).toLocaleDateString(sprache(hass), { weekday: "long", day: "numeric", month: "long" });
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (z) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[z]);
}

/** Aufgaben und Einnahmen als gemeinsame, nach Zeit sortierte Einträge. */
function eintraege(hass, config, { tage = 1, mitUeberfaelligen = true } = {}) {
  const agenda = zustand(hass, "agenda", config.entity);
  const meds = zustand(hass, "medikamente", config.medikamente_entity);
  const heute = heuteText();
  const bis = plusTage(tage - 1);
  const liste = [];
  for (const a of (agenda && agenda.attributes.aufgaben) || []) {
    const ueberfaellig = a.datum < heute && a.ueberfaellig && !a.erledigt;
    if (!(a.datum >= heute && a.datum <= bis) && !(mitUeberfaelligen && ueberfaellig)) continue;
    if (config.erledigte_ausblenden && a.erledigt) continue;
    liste.push({ art: "aufgabe", datum: ueberfaellig ? "ueberfaellig" : a.datum, start: a.start, ende: a.ende, titel: a.titel,
      erledigt: a.erledigt, ueberfaellig: a.ueberfaellig, uid: a.uid, farbe: a.farbe, liste: a.liste });
  }
  if (!config.medikamente_ausblenden) {
    for (const m of (meds && meds.attributes.medikamente) || []) {
      for (const t of m.plan || []) {
        if (t.datum < heute || t.datum > bis) continue;
        if (config.eingenommene_ausblenden && t.status !== "offen") continue;
        liste.push({ art: "einnahme", datum: t.datum, start: t.einnahme_um, titel: `${m.name} ${m.dosis || ""} ${m.einheit || ""}`.trim(),
          status: t.status, erledigt: t.status !== "offen", med_id: m.id, farbe: m.farbe, kritisch: m.kritisch });
      }
    }
  }
  return liste.sort((x, y) => (x.start || "").localeCompare(y.start || ""));
}

const BASIS_STIL = `
  :host { display: block; }
  ha-card { padding: 16px; box-sizing: border-box; height: 100%; }
  .kopf { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .titel { font-size: 1.15em; font-weight: 600; color: var(--primary-text-color); }
  .neben { color: var(--secondary-text-color); font-size: 0.9em; }
  .leer { color: var(--secondary-text-color); padding: 8px 0; }
  .zeile { display: flex; align-items: center; gap: 10px; padding: 6px 0; min-height: 32px; }
  .zeile + .zeile { border-top: 1px solid var(--divider-color); }
  .zeit { width: 3.2em; flex: none; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; font-size: 0.9em; }
  .text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .erledigt .text { text-decoration: line-through; color: var(--secondary-text-color); }
  .ueberfaellig .zeit { color: var(--error-color); }
  .punkt { width: 10px; height: 10px; border-radius: 50%; flex: none; }
  .haken { width: 22px; height: 22px; flex: none; border-radius: 50%; border: 2px solid var(--primary-color);
           background: none; cursor: pointer; padding: 0; display: grid; place-items: center; color: var(--text-primary-color, #fff); }
  .haken.an { background: var(--primary-color); }
  .knopf { border: none; border-radius: 16px; padding: 4px 12px; cursor: pointer; font: inherit; font-size: 0.85em;
           background: var(--primary-color); color: var(--text-primary-color, #fff); flex: none; }
  .knopf.zweit { background: none; color: var(--primary-color); border: 1px solid var(--primary-color); }
  .status { font-size: 0.85em; color: var(--secondary-text-color); flex: none; }
  .gruppe { margin-top: 10px; font-size: 0.8em; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
  .gruppe.warn { color: var(--error-color); }
  .segment { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 16px; overflow: hidden; }
  .segment button { border: none; background: none; padding: 3px 10px; cursor: pointer; font: inherit; font-size: 0.85em; color: var(--primary-text-color); }
  .segment button.an { background: var(--primary-color); color: var(--text-primary-color, #fff); }
`;

const ALTE_SCHALTER = {
  zeige_medikamente: "medikamente_ausblenden",
  umschalter: "umschalter_ausblenden",
  ohne_plan: "ohne_plan_ausblenden",
  empfehlung: "empfehlung_ausblenden",
};

// Gemeinsame Editor-Felder
const FELD = {
  titel: { name: "titel", selector: { text: {} } },
  medikamente: { name: "medikamente_ausblenden", selector: { boolean: {} } },
  eingenommene: { name: "eingenommene_ausblenden", selector: { boolean: {} } },
  erledigte: { name: "erledigte_ausblenden", selector: { boolean: {} } },
  umschalter: { name: "umschalter_ausblenden", selector: { boolean: {} } },
  von: { name: "von", selector: { number: { min: 0, max: 23, mode: "box" } } },
  bis: { name: "bis", selector: { number: { min: 1, max: 24, mode: "box" } } },
};
const BESCHRIFTUNG = {
  titel: "Titel", zeitraum: "Zeitraum", tage: "Zeitraum", groesse: "Größe", modus: "Darstellung", anzahl: "Einträge anzeigen",
  von: "Zeitleiste von (Uhr)", bis: "Zeitleiste bis (Uhr)",
  medikamente_ausblenden: "Einnahmen ausblenden",
  eingenommene_ausblenden: "Eingenommene Medikamente ausblenden",
  erledigte_ausblenden: "Erledigte Aufgaben ausblenden",
  umschalter_ausblenden: "Umschalter ausblenden",
  ohne_plan_ausblenden: "Medikamente ohne feste Zeit ausblenden",
  empfehlung_ausblenden: "Empfehlungstext ausblenden",
};
const beschriftung = (s) => BESCHRIFTUNG[s.name] || s.name;

class AdhsBasisKarte extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._signatur = "";
  }

  setConfig(config) {
    // Alle Schalter heißen „… ausblenden“ und sind standardmäßig aus – so zeigt der Editor dasselbe wie die Karte.
    // Ältere Konfigurationen mit „… anzeigen: false“ werden übernommen.
    const c = { ...(config || {}) };
    for (const [alt, neu] of Object.entries(ALTE_SCHALTER)) {
      if (c[alt] === false && c[neu] === undefined) c[neu] = true;
      delete c[alt];
    }
    this._config = { ...(this.constructor.standard || {}), ...c };
    this._signatur = "";
    if (this._hass) this._zeichnen();
  }

  set hass(hass) {
    this._hass = hass;
    // Nur neu zeichnen, wenn sich relevante Zustände geändert haben.
    const signatur = (this.constructor.schluessel || [])
      .map((s) => { const z = zustand(hass, s); return z ? z.last_updated : ""; })
      .join("|") + "|" + Math.floor(Date.now() / 60000);
    if (signatur === this._signatur) return;
    this._signatur = signatur;
    this._zeichnen();
  }

  _zeichnen() {
    if (!this._hass || !this._config) return;
    this.shadowRoot.innerHTML = `<style>${BASIS_STIL}${this.constructor.stil || ""}</style><ha-card>${this.inhalt(this._hass, this._config)}</ha-card>`;
    this.verdrahten && this.verdrahten(this.shadowRoot, this._hass, this._config);
  }

  _dienst(dienst, daten) {
    this._hass.callService(DOMAIN, dienst, daten).catch((fehler) => {
      // Fehler als HA-Toast anzeigen
      this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: fehler.message || String(fehler) }, bubbles: true, composed: true }));
    });
  }

  _aktionenVerdrahten(wurzel) {
    wurzel.querySelectorAll("[data-uid]").forEach((el) =>
      el.addEventListener("click", () => this._dienst("aufgabe_erledigen", { uid: el.dataset.uid, erledigt: el.dataset.erledigt !== "true" })));
    wurzel.querySelectorAll("[data-med]").forEach((el) =>
      el.addEventListener("click", () => { el.disabled = true; this._dienst("einnahme_erfassen", { medikament: el.dataset.med }); }));
    wurzel.querySelectorAll("[data-alle]").forEach((el) =>
      el.addEventListener("click", () => { el.disabled = true; this._dienst("einnahme_erfassen", {}); }));
  }

  getCardSize() { return 3; }

  // Neue Karten bekommen alle Standardwerte, damit der Editor sie anzeigt.
  static getStubConfig() { return { ...(this.standard || {}) }; }
}

function zeileHtml(hass, e, { mitAktionen = true, heute = heuteText() } = {}) {
  const klassen = ["zeile", e.erledigt ? "erledigt" : "", e.ueberfaellig && !e.erledigt ? "ueberfaellig" : ""].join(" ");
  if (e.art === "aufgabe") {
    const haken = mitAktionen
      ? `<button class="haken ${e.erledigt ? "an" : ""}" data-uid="${esc(e.uid)}" data-erledigt="${e.erledigt}" aria-label="Abhaken">${e.erledigt ? "✓" : ""}</button>`
      : `<span class="punkt" style="background:${esc(e.farbe || "var(--primary-color)")}"></span>`;
    return `<div class="${klassen}">${haken}<span class="zeit">${uhrzeit(hass, e.start)}</span><span class="text">${esc(e.titel)}</span></div>`;
  }
  const status = { eingenommen: "✓ eingenommen", uebersprungen: "übersprungen" }[e.status] || "";
  const knopf = mitAktionen && e.status === "offen" && e.datum === heute
    ? `<button class="knopf" data-med="${esc(e.med_id)}">Einnehmen</button>` : `<span class="status">${status}</span>`;
  return `<div class="${klassen}"><span class="punkt" style="background:${esc(e.farbe || "var(--primary-color)")}"></span><span class="zeit">${uhrzeit(hass, e.start)}</span><span class="text">💊 ${esc(e.titel)}</span>${knopf}</div>`;
}

// ---------------------------------------------------------------- Agenda (Heute / 3 / 7 Tage)

class AdhsAgendaKarte extends AdhsBasisKarte {
  static schluessel = ["agenda", "medikamente"];
  static standard = { zeitraum: "heute" };

  inhalt(hass, config) {
    const tage = { heute: 1, "3": 3, "7": 7 }[this._zeitraum || config.zeitraum] || 1;
    const liste = eintraege(hass, config, { tage });
    const segmente = !config.umschalter_ausblenden
      ? `<div class="segment">${[["heute", "Heute"], ["3", "3 Tage"], ["7", "7 Tage"]]
          .map(([w, t]) => `<button data-zeitraum="${w}" class="${(this._zeitraum || config.zeitraum) === w ? "an" : ""}">${t}</button>`).join("")}</div>`
      : "";
    let html = `<div class="kopf"><span class="titel">${esc(config.titel || "Agenda")}</span>${segmente}</div>`;
    if (!liste.length) return html + `<div class="leer">Nichts geplant.</div>`;
    const gruppen = new Map();
    for (const e of liste) {
      if (!gruppen.has(e.datum)) gruppen.set(e.datum, []);
      gruppen.get(e.datum).push(e);
    }
    const reihenfolge = [...gruppen.keys()].sort((a, b) => (a === "ueberfaellig" ? -1 : b === "ueberfaellig" ? 1 : a.localeCompare(b)));
    for (const datum of reihenfolge) {
      const titel = datum === "ueberfaellig" ? "Überfällig" : tagUeberschrift(hass, datum);
      html += `<div class="gruppe ${datum === "ueberfaellig" ? "warn" : ""}">${esc(titel)}</div>`;
      html += gruppen.get(datum).map((e) => zeileHtml(hass, e)).join("");
    }
    return html;
  }

  verdrahten(wurzel) {
    this._aktionenVerdrahten(wurzel);
    wurzel.querySelectorAll("[data-zeitraum]").forEach((el) =>
      el.addEventListener("click", () => { this._zeitraum = el.dataset.zeitraum; this._zeichnen(); }));
  }

  getCardSize() { return 6; }

  static getConfigForm() {
    return {
      schema: [
        FELD.titel,
        { name: "zeitraum", selector: { select: { mode: "dropdown", options: [
          { value: "heute", label: "Heute" }, { value: "3", label: "3 Tage" }, { value: "7", label: "7 Tage" }] } } },
        FELD.umschalter, FELD.erledigte, FELD.eingenommene, FELD.medikamente,
      ],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Tagesplanung S / M / L

class AdhsTagesplanungKarte extends AdhsBasisKarte {
  static schluessel = ["agenda", "medikamente"];
  static standard = { groesse: "m", von: 6, bis: 23 };
  static stil = `
    .fortschritt { display: flex; align-items: center; gap: 16px; }
    .zahl { font-size: 2em; font-weight: 600; }
    .raster { position: relative; margin-left: 3em; border-left: 1px solid var(--divider-color); }
    .stunde { position: absolute; left: -3em; width: 2.6em; text-align: right; font-size: 0.75em; color: var(--secondary-text-color); transform: translateY(-50%); }
    .linie { position: absolute; left: 0; right: 0; border-top: 1px dashed var(--divider-color); }
    .block { position: absolute; left: 6px; right: 4px; border-radius: 6px; padding: 2px 6px; font-size: 0.8em; overflow: hidden;
             white-space: nowrap; text-overflow: ellipsis; background: var(--primary-color); color: var(--text-primary-color, #fff); box-sizing: border-box; }
    .block.erledigt { opacity: .45; text-decoration: line-through; }
    .jetzt { position: absolute; left: -4px; right: 0; border-top: 2px solid var(--error-color); }
    .jetzt::before { content: ""; position: absolute; left: -2px; top: -5px; width: 8px; height: 8px; border-radius: 50%; background: var(--error-color); }
  `;

  inhalt(hass, config) {
    const liste = eintraege(hass, config, { tage: 1 });
    const heute = liste.filter((e) => e.datum !== "ueberfaellig");
    const fertig = heute.filter((e) => e.erledigt).length;
    const jetzt = new Date().toISOString();
    const naechste = heute.filter((e) => !e.erledigt && (e.ende || e.start) >= jetzt.slice(0, 16) || (!e.erledigt && e.ueberfaellig));
    const kopf = `<div class="kopf"><span class="titel">${esc(config.titel || "Tagesplanung")}</span><span class="neben">${fertig}/${heute.length} erledigt</span></div>`;

    if (config.groesse === "s") {
      const n = naechste[0];
      return kopf + `<div class="fortschritt">${ring(heute.length ? fertig / heute.length : 0, 56, 6)}
        <div><div class="neben">Als Nächstes</div><div>${n ? `${uhrzeit(hass, n.start)} · ${esc(n.titel)}` : "Alles erledigt"}</div></div></div>`;
    }
    if (config.groesse === "m") {
      const zeilen = (naechste.length ? naechste : heute).slice(0, 6);
      return kopf + (zeilen.length ? zeilen.map((e) => zeileHtml(hass, e)).join("") : `<div class="leer">Heute nichts geplant.</div>`);
    }
    // Groß: Zeitleiste des Tages
    const von = Number(config.von), bis = Number(config.bis), hoehe = 36;
    const stundenAuf = (iso) => { const d = new Date(iso); return d.getHours() + d.getMinutes() / 60; };
    let raster = "";
    for (let h = von; h <= bis; h++) {
      raster += `<div class="stunde" style="top:${(h - von) * hoehe}px">${String(h).padStart(2, "0")}</div><div class="linie" style="top:${(h - von) * hoehe}px"></div>`;
    }
    for (const e of heute) {
      if (!e.start) continue;
      const s = Math.max(von, stundenAuf(e.start));
      const ende = e.ende ? stundenAuf(e.ende) : s + 0.5;
      if (s > bis) continue;
      const farbe = e.art === "einnahme" ? "var(--warning-color, #f4b400)" : esc(e.farbe || "var(--primary-color)");
      raster += `<div class="block ${e.erledigt ? "erledigt" : ""}" style="top:${(s - von) * hoehe}px;height:${Math.max(18, (Math.min(bis, ende) - s) * hoehe - 2)}px;background:${farbe}">${uhrzeit(hass, e.start)} ${e.art === "einnahme" ? "💊 " : ""}${esc(e.titel)}</div>`;
    }
    const n = new Date(), jetztH = n.getHours() + n.getMinutes() / 60;
    if (jetztH >= von && jetztH <= bis) raster += `<div class="jetzt" style="top:${(jetztH - von) * hoehe}px"></div>`;
    return kopf + `<div class="raster" style="height:${(bis - von) * hoehe + 10}px">${raster}</div>`;
  }

  verdrahten(wurzel) { this._aktionenVerdrahten(wurzel); }

  getCardSize() { return { s: 2, m: 4, l: 9 }[this._config?.groesse] || 4; }

  static getConfigForm() {
    return {
      schema: [
        FELD.titel,
        { name: "groesse", selector: { select: { mode: "dropdown", options: [
          { value: "s", label: "Klein" }, { value: "m", label: "Mittel" }, { value: "l", label: "Groß (Zeitleiste)" }] } } },
        FELD.erledigte, FELD.eingenommene, FELD.medikamente, FELD.von, FELD.bis,
      ],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Kalender 3 / 7 Tage (Zeitleiste wie Tagesplanung groß)

class AdhsKalenderKarte extends AdhsBasisKarte {
  static schluessel = ["agenda", "medikamente"];
  static standard = { tage: "3", von: 6, bis: 23 };
  static stil = `
    .kal { display: grid; gap: 0 4px; }
    .tagkopf { text-align: center; font-size: 0.85em; font-weight: 600; padding-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tagkopf.heute { color: var(--primary-color); }
    .achse, .spalte { position: relative; }
    .spalte { border-left: 1px solid var(--divider-color); }
    .stunde { position: absolute; right: 4px; font-size: 0.7em; color: var(--secondary-text-color); transform: translateY(-50%); }
    .linie { position: absolute; left: 0; right: 0; border-top: 1px dashed var(--divider-color); }
    .block { position: absolute; border-radius: 4px; padding: 1px 4px; font-size: 0.72em; line-height: 1.25; overflow: hidden;
             white-space: nowrap; text-overflow: ellipsis; color: var(--text-primary-color, #fff); box-sizing: border-box; }
    .block.erledigt { opacity: .45; text-decoration: line-through; }
    .jetzt { position: absolute; left: 0; right: 0; border-top: 2px solid var(--error-color); z-index: 2; }
  `;

  inhalt(hass, config) {
    const tage = Number(this._tage || config.tage) === 7 ? 7 : 3;
    const von = Number(config.von), bis = Number(config.bis), hoehe = tage === 7 ? 28 : 34;
    const liste = eintraege(hass, config, { tage, mitUeberfaelligen: false });
    const segmente = !config.umschalter_ausblenden
      ? `<div class="segment">${[["3", "3 Tage"], ["7", "7 Tage"]]
          .map(([w, t]) => `<button data-tage="${w}" class="${String(tage) === w ? "an" : ""}">${t}</button>`).join("")}</div>`
      : "";
    const kopf = `<div class="kopf"><span class="titel">${esc(config.titel || "Kalender")}</span>${segmente}</div>`;
    const stundeVon = (iso) => { const d = new Date(iso); return d.getHours() + d.getMinutes() / 60; };
    const gesamt = (bis - von) * hoehe;

    let achse = "";
    for (let h = von; h <= bis; h++) achse += `<div class="stunde" style="top:${(h - von) * hoehe}px">${String(h).padStart(2, "0")}</div>`;
    let linien = "";
    for (let h = von; h <= bis; h++) linien += `<div class="linie" style="top:${(h - von) * hoehe}px"></div>`;

    let koepfe = `<div></div>`, spalten = `<div class="achse" style="height:${gesamt}px">${achse}</div>`;
    for (let i = 0; i < tage; i++) {
      const datum = plusTage(i);
      const [j, m, t] = datum.split("-").map(Number);
      const tagName = i === 0 ? "Heute" : new Date(j, m - 1, t).toLocaleDateString(sprache(hass), { weekday: "short", day: "numeric" });
      koepfe += `<div class="tagkopf ${i === 0 ? "heute" : ""}">${esc(tagName)}</div>`;

      // Überlappende Einträge nebeneinander (Spuren)
      const amTag = liste.filter((e) => e.datum === datum && e.start).map((e) => {
        const s = Math.max(von, stundeVon(e.start));
        const ende = Math.min(bis, e.ende ? stundeVon(e.ende) : s + 0.5);
        return { ...e, s, ende: Math.max(ende, s + 0.4) };
      }).filter((e) => e.s < bis).sort((a, b) => a.s - b.s);
      const spurEnde = [];
      for (const e of amTag) {
        let spur = spurEnde.findIndex((x) => x <= e.s);
        if (spur < 0) { spur = spurEnde.length; spurEnde.push(0); }
        spurEnde[spur] = e.ende;
        e.spur = spur;
      }
      const spuren = Math.max(1, spurEnde.length);
      let bloecke = "";
      for (const e of amTag) {
        const farbe = e.art === "einnahme" ? "var(--warning-color, #f4b400)" : esc(e.farbe || "var(--primary-color)");
        const breite = 100 / spuren;
        bloecke += `<div class="block ${e.erledigt ? "erledigt" : ""}" title="${esc(uhrzeit(hass, e.start) + " " + e.titel)}"
          style="top:${(e.s - von) * hoehe}px;height:${Math.max(14, (e.ende - e.s) * hoehe - 2)}px;left:calc(${e.spur * breite}% + 2px);width:calc(${breite}% - 4px);background:${farbe}">${e.art === "einnahme" ? "💊 " : ""}${esc(e.titel)}</div>`;
      }
      let jetzt = "";
      if (i === 0) {
        const n = new Date(), h = n.getHours() + n.getMinutes() / 60;
        if (h >= von && h <= bis) jetzt = `<div class="jetzt" style="top:${(h - von) * hoehe}px"></div>`;
      }
      spalten += `<div class="spalte" style="height:${gesamt}px">${linien}${bloecke}${jetzt}</div>`;
    }
    return kopf + `<div class="kal" style="grid-template-columns: 2.4em repeat(${tage}, 1fr)">${koepfe}${spalten}</div>`;
  }

  verdrahten(wurzel) {
    wurzel.querySelectorAll("[data-tage]").forEach((el) =>
      el.addEventListener("click", () => { this._tage = el.dataset.tage; this._zeichnen(); }));
  }

  getCardSize() { return 9; }

  static getConfigForm() {
    return {
      schema: [
        FELD.titel,
        { name: "tage", selector: { select: { mode: "dropdown", options: [{ value: "3", label: "3 Tage" }, { value: "7", label: "7 Tage" }] } } },
        FELD.umschalter, FELD.erledigte, FELD.eingenommene, FELD.medikamente, FELD.von, FELD.bis,
      ],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Uhr 6 h / 12 h

class AdhsUhrKarte extends AdhsBasisKarte {
  static schluessel = ["agenda", "medikamente"];
  static standard = { modus: "12h" };
  static stil = `svg { width: 100%; max-width: 320px; display: block; margin: 0 auto; } .legende { text-align: center; margin-top: 8px; }`;

  inhalt(hass, config) {
    const sechs = config.modus === "6h";
    const spanne = sechs ? 6 : 12;
    const jetzt = new Date();
    const bisZeit = new Date(jetzt.getTime() + spanne * 3600000);
    const r = 90, c = 100;
    // Winkel: 12 h = Zifferblatt (Uhrzeit), 6 h = „jetzt“ oben, Uhrzeigersinn bis +6 h.
    const winkel = (d) => sechs
      ? ((d - jetzt) / (6 * 3600000)) * 360
      : ((d.getHours() % 12) + d.getMinutes() / 60) * 30;
    const punkt = (w, radius) => [c + radius * Math.sin((w * Math.PI) / 180), c - radius * Math.cos((w * Math.PI) / 180)];
    const bogen = (w1, w2, radius) => {
      const [x1, y1] = punkt(w1, radius), [x2, y2] = punkt(w2, radius);
      const gross = ((w2 - w1 + 360) % 360) > 180 ? 1 : 0;
      return `M ${x1} ${y1} A ${radius} ${radius} 0 ${gross} 1 ${x2} ${y2}`;
    };
    let svg = `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="var(--divider-color)" stroke-width="2"/>`;
    for (let i = 0; i < (sechs ? 6 : 12); i++) {
      const w = i * (sechs ? 60 : 30);
      const [x1, y1] = punkt(w, r - 2), [x2, y2] = punkt(w, r - 9);
      svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--secondary-text-color)" stroke-width="2"/>`;
      const [tx, ty] = punkt(w, r - 20);
      const beschriftung = sechs ? String((jetzt.getHours() + i) % 24).padStart(2, "0") : String(i === 0 ? 12 : i);
      svg += `<text x="${tx}" y="${ty}" font-size="10" text-anchor="middle" dominant-baseline="middle" fill="var(--secondary-text-color)">${beschriftung}</text>`;
    }
    const liste = eintraege(hass, config, { tage: 2, mitUeberfaelligen: false })
      .filter((e) => e.start && new Date(e.start) < bisZeit && new Date(e.ende || e.start) > (sechs ? jetzt : new Date(jetzt.getTime() - 3600000)));
    for (const e of liste) {
      const start = new Date(Math.max(new Date(e.start), sechs ? jetzt : 0));
      const ende = new Date(Math.min(new Date(e.ende || new Date(e.start).getTime() + 30 * 60000), bisZeit));
      const farbe = e.art === "einnahme" ? "var(--warning-color, #f4b400)" : (e.farbe || "var(--primary-color)");
      const w1 = winkel(start), w2 = Math.max(winkel(ende), w1 + 4);
      svg += `<path d="${bogen(w1, w2, r + 0.5)}" fill="none" stroke="${esc(farbe)}" stroke-width="8" stroke-linecap="round" opacity="${e.erledigt ? 0.35 : 1}"><title>${esc(uhrzeit(hass, e.start) + " " + e.titel)}</title></path>`;
    }
    if (sechs) {
      const [x, y] = punkt(0, r);
      svg += `<circle cx="${x}" cy="${y}" r="4" fill="var(--error-color)"/>`;
    } else {
      const [hx, hy] = punkt(winkel(jetzt), 45), [mx, my] = punkt(jetzt.getMinutes() * 6, 65);
      svg += `<line x1="${c}" y1="${c}" x2="${hx}" y2="${hy}" stroke="var(--primary-text-color)" stroke-width="4" stroke-linecap="round"/>`;
      svg += `<line x1="${c}" y1="${c}" x2="${mx}" y2="${my}" stroke="var(--primary-text-color)" stroke-width="2.5" stroke-linecap="round"/>`;
      svg += `<circle cx="${c}" cy="${c}" r="3.5" fill="var(--primary-text-color)"/>`;
    }
    const naechster = liste.find((e) => !e.erledigt && new Date(e.start) >= jetzt);
    const mitte = sechs
      ? `<text x="${c}" y="${c - 6}" font-size="20" font-weight="600" text-anchor="middle" fill="var(--primary-text-color)">${uhrzeit(hass, jetzt.toISOString())}</text><text x="${c}" y="${c + 14}" font-size="10" text-anchor="middle" fill="var(--secondary-text-color)">nächste 6 h</text>`
      : "";
    return `<div class="kopf"><span class="titel">${esc(config.titel || (sechs ? "6 Stunden" : "12 Stunden"))}</span></div>
      <svg viewBox="0 0 200 200">${svg}${mitte}</svg>
      <div class="legende neben">${naechster ? `Als Nächstes: ${uhrzeit(hass, naechster.start)} · ${esc(naechster.titel)}` : "Nichts Weiteres geplant"}</div>`;
  }

  getCardSize() { return 6; }

  static getConfigForm() {
    return {
      schema: [
        FELD.titel,
        { name: "modus", selector: { select: { mode: "dropdown", options: [{ value: "12h", label: "12 Stunden" }, { value: "6h", label: "6 Stunden" }] } } },
        FELD.erledigte, FELD.eingenommene, FELD.medikamente,
      ],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Inbox

class AdhsInboxKarte extends AdhsBasisKarte {
  static schluessel = ["inbox"];
  static standard = { anzahl: 5 };
  static stil = `.gross { font-size: 2.2em; font-weight: 600; }`;

  inhalt(hass, config) {
    const z = zustand(hass, "inbox", config.entity);
    const anzahl = z ? Number(z.state) || 0 : 0;
    const liste = ((z && z.attributes.eintraege) || []).slice(0, Number(config.anzahl));
    return `<div class="kopf"><span class="titel">${esc(config.titel || "Inbox")}</span><span class="gross">${anzahl}</span></div>` +
      (liste.length ? liste.map((t) => `<div class="zeile"><span class="text">${esc(t)}</span></div>`).join("") : `<div class="leer">Inbox ist leer.</div>`);
  }

  static getConfigForm() {
    return {
      schema: [FELD.titel, { name: "anzahl", selector: { number: { min: 0, max: 10, mode: "box" } } }],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Recovery / Strain

function ring(anteil, groesse = 120, breite = 10, farbe = "var(--primary-color)", innen = "", band = null) {
  const r = (groesse - breite) / 2, u = 2 * Math.PI * r, c = groesse / 2;
  const a = Math.max(0, Math.min(1, anteil || 0));
  let bandSvg = "";
  if (band) {
    const [von, bis] = band.map((x) => Math.max(0, Math.min(1, x)));
    bandSvg = `<circle cx="${c}" cy="${c}" r="${r + breite / 2 + 3}" fill="none" stroke="var(--success-color, #43a047)" stroke-width="3"
      stroke-dasharray="${(bis - von) * 2 * Math.PI * (r + breite / 2 + 3)} 9999" stroke-dashoffset="${-von * 2 * Math.PI * (r + breite / 2 + 3)}"
      transform="rotate(-90 ${c} ${c})" opacity=".8"/>`;
  }
  return `<svg width="${groesse}" height="${groesse}" viewBox="-6 -6 ${groesse + 12} ${groesse + 12}" style="flex:none">
    <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="var(--divider-color)" stroke-width="${breite}"/>
    <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${farbe}" stroke-width="${breite}" stroke-linecap="round"
      stroke-dasharray="${a * u} ${u}" transform="rotate(-90 ${c} ${c})"/>${bandSvg}
    <text x="${c}" y="${c}" text-anchor="middle" dominant-baseline="central" font-size="${groesse / 4.5}" font-weight="600" fill="var(--primary-text-color)">${innen}</text></svg>`;
}

const KOERPER_STIL = `.reihe { display: flex; align-items: center; gap: 16px; } .empf-titel { font-weight: 600; } .klein { font-size: .9em; color: var(--secondary-text-color); }`;

class AdhsRecoveryKarte extends AdhsBasisKarte {
  static schluessel = ["recovery", "schlaf"];
  static stil = KOERPER_STIL;

  inhalt(hass, config) {
    const z = zustand(hass, "recovery", config.entity);
    const schlaf = zustand(hass, "schlaf");
    const wert = z ? Number(z.state) : NaN;
    if (!z || isNaN(wert)) return `<div class="kopf"><span class="titel">Recovery</span></div><div class="leer">Noch keine Werte aus ADHS Track.</div>`;
    const farbe = wert >= 67 ? "var(--success-color, #43a047)" : wert >= 34 ? "var(--warning-color, #f4b400)" : "var(--error-color, #db4437)";
    const a = z.attributes;
    return `<div class="kopf"><span class="titel">${esc(config.titel || "Recovery")}</span><span class="neben">${esc(a.kategorie || "")}</span></div>
      <div class="reihe">${ring(wert / 100, 110, 10, farbe, `${Math.round(wert)}%`)}
        <div>${a.empfehlung_titel ? `<div class="empf-titel">${esc(a.empfehlung_titel)}</div>` : ""}
          ${a.empfehlung_text && !config.empfehlung_ausblenden ? `<div class="klein">${esc(a.empfehlung_text)}</div>` : ""}
          ${schlaf && !isNaN(Number(schlaf.state)) ? `<div class="klein" style="margin-top:6px">Schlaf ${Math.round(Number(schlaf.state))}%</div>` : ""}</div></div>`;
  }

  static getConfigForm() {
    return {
      schema: [FELD.titel, { name: "empfehlung_ausblenden", selector: { boolean: {} } }],
      computeLabel: beschriftung,
    };
  }
}

class AdhsStrainKarte extends AdhsBasisKarte {
  static schluessel = ["strain"];
  static stil = KOERPER_STIL;

  inhalt(hass, config) {
    const z = zustand(hass, "strain", config.entity);
    const wert = z ? Number(z.state) : NaN;
    if (!z || isNaN(wert)) return `<div class="kopf"><span class="titel">Strain</span></div><div class="leer">Noch keine Werte aus ADHS Track.</div>`;
    const { ziel_min: zielMin, ziel_max: zielMax } = z.attributes;
    const max = 21;
    const band = zielMin != null && zielMax != null ? [zielMin / max, zielMax / max] : null;
    let hinweis = "";
    if (band) hinweis = wert < zielMin ? "Unter dem Ziel" : wert > zielMax ? "Über dem Ziel" : "Im Zielbereich";
    return `<div class="kopf"><span class="titel">${esc(config.titel || "Strain heute")}</span></div>
      <div class="reihe">${ring(wert / max, 110, 10, "var(--primary-color)", wert.toFixed(1), band)}
        <div>${band ? `<div class="empf-titel">${hinweis}</div><div class="klein">Ziel ${Number(zielMin).toFixed(1)}–${Number(zielMax).toFixed(1)}</div>` : ""}</div></div>`;
  }

  static getConfigForm() {
    return { schema: [FELD.titel], computeLabel: beschriftung };
  }
}

// ---------------------------------------------------------------- Medikamente

class AdhsMedikamenteKarte extends AdhsBasisKarte {
  static schluessel = ["medikamente"];
  static standard = {};

  inhalt(hass, config) {
    const z = zustand(hass, "medikamente", config.entity);
    const meds = (z && z.attributes.medikamente) || [];
    const faellig = (z && z.attributes.faellig) || [];
    const heute = heuteText();
    let html = `<div class="kopf"><span class="titel">${esc(config.titel || "Medikamente")}</span>
      ${faellig.length ? `<button class="knopf" data-alle="1">Alle fälligen einnehmen</button>` : ""}</div>`;
    let zeilen = "";
    for (const m of meds) {
      const termine = (m.plan || []).filter((t) => t.datum === heute && !(config.eingenommene_ausblenden && t.status !== "offen"));
      const punkt = `<span class="punkt" style="background:${esc(m.farbe || "var(--primary-color)")}"></span>`;
      const titel = `${esc(m.name)} <span class="neben">${esc(`${m.dosis || ""} ${m.einheit || ""}`.trim())}</span>${m.kritisch ? " ⚠️" : ""}`;
      for (const t of termine) {
        const knopf = t.status === "offen"
          ? `<button class="knopf ${faellig.includes(m.id) ? "" : "zweit"}" data-med="${esc(m.id)}">Einnehmen</button>`
          : `<span class="status">${t.status === "eingenommen" ? "✓ eingenommen" : "übersprungen"}</span>`;
        zeilen += `<div class="zeile ${t.status !== "offen" ? "erledigt" : ""}">${punkt}<span class="zeit">${uhrzeit(hass, t.einnahme_um)}</span><span class="text">${titel}</span>${knopf}</div>`;
      }
      if (!termine.length && !(m.plan || []).length && !config.ohne_plan_ausblenden) {
        zeilen += `<div class="zeile">${punkt}<span class="zeit">–</span><span class="text">${titel}</span><button class="knopf zweit" data-med="${esc(m.id)}">Einnehmen</button></div>`;
      }
    }
    return html + (zeilen || `<div class="leer">Heute keine Einnahmen geplant.</div>`);
  }

  verdrahten(wurzel) { this._aktionenVerdrahten(wurzel); }

  static getConfigForm() {
    return {
      schema: [FELD.titel, { name: "eingenommene_ausblenden", selector: { boolean: {} } }, { name: "ohne_plan_ausblenden", selector: { boolean: {} } }],
      computeLabel: beschriftung,
    };
  }
}

// ---------------------------------------------------------------- Registrierung

const KARTEN = [
  ["adhstrack-agenda-card", AdhsAgendaKarte, "ADHS Track: Agenda", "Aufgaben und Einnahmen für heute, 3 oder 7 Tage – abhakbar."],
  ["adhstrack-tagesplanung-card", AdhsTagesplanungKarte, "ADHS Track: Tagesplanung", "Wie das Widget: klein, mittel oder groß mit Zeitleiste."],
  ["adhstrack-kalender-card", AdhsKalenderKarte, "ADHS Track: Kalender", "Zeitleiste der nächsten 3 oder 7 Tage mit Aufgaben und Einnahmen."],
  ["adhstrack-uhr-card", AdhsUhrKarte, "ADHS Track: Uhr", "Zifferblatt mit Terminen der nächsten 6 oder 12 Stunden."],
  ["adhstrack-inbox-card", AdhsInboxKarte, "ADHS Track: Inbox", "Offene Inbox-Einträge."],
  ["adhstrack-recovery-card", AdhsRecoveryKarte, "ADHS Track: Recovery", "Recovery-Ring mit Empfehlung."],
  ["adhstrack-strain-card", AdhsStrainKarte, "ADHS Track: Strain heute", "Belastung mit Zielbereich."],
  ["adhstrack-medikamente-card", AdhsMedikamenteKarte, "ADHS Track: Medikamente", "Heutige Einnahmen mit „Einnehmen“-Knopf."],
];

window.customCards = window.customCards || [];
for (const [name, klasse, titel, beschreibung] of KARTEN) {
  if (!customElements.get(name)) customElements.define(name, klasse);
  if (!window.customCards.some((k) => k.type === name)) {
    window.customCards.push({ type: name, name: titel, description: beschreibung, preview: true });
  }
}

console.info(`%c ADHS Track Karten %c ${VERSION} `, "background:#4A90D9;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
