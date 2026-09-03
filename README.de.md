# Kehrwoche

**Wer putzt diese Woche das Bad? Wer schuldet wem was? Was fehlt im Kühlschrank?**
Kehrwoche ist eine freie, selbstgehostete Webanwendung für alles, was in einem gemeinsamen
Haushalt organisiert werden will — Aufgaben mit fairem Wechsel, eine gemeinsame
Einkaufsliste, geteilte Ausgaben mit minimalem Ausgleich und eine Pinnwand, die alles
zusammenhält.

Keine Konten in fremden Clouds, kein Tracking, keine Bezahlfunktionen. Ein Container,
deine Daten.

*In English: [README.md](README.md)*

## Woher der Name kommt

Die *Kehrwoche* ist eine jahrhundertealte schwäbische Tradition: Die Pflicht, die
gemeinsamen Bereiche eines Hauses zu reinigen, wandert Woche für Woche von Haushalt zu
Haushalt. Jede*r ist mal dran, niemand trägt es allein. Genau dieses Prinzip macht die
Software für die Menschen, mit denen du zusammenlebst — daher der Name.

## Bildschirmfotos

Das Handy ist das Leitgerät; die Desktop-Darstellung ist eine vollwertige, aber
nachgelagerte Anpassung.

| Putzplan | Einkaufsliste | Kasse |
|---|---|---|
| ![Die Aufgabenliste am Handy](docs/images/mobile-chores.png) | ![Die Einkaufsliste am Handy](docs/images/mobile-shopping.png) | ![Salden am Handy](docs/images/mobile-expenses.png) |

![Die Pinnwand am Desktop](docs/images/desktop-feed.png)

## Was drin ist

**Putzplan.** Wiederkehrende Aufgaben, die im Haushalt reihum wandern, oder feste, die
immer derselben Person gehören. Termine, Punkte, eine Rangliste und eine Erinnerung, die
sich an die zuständige Person schicken lässt. Abhaken ist ein Tipp und lässt sich fünf
Minuten lang zurücknehmen — für etwas, das man täglich tut, gibt es keinen
Bestätigungsdialog.

**Einkaufsliste.** Eintragen, abhaken, alles Gekaufte in einem Rutsch aufräumen. Die
Vorschläge kommen aus einer Liste in deiner Sprache und aus dem, was dieser Haushalt
zuletzt gekauft hat — die meisten Artikel sind damit ein Tipp statt einer Eingabe.

**Haushaltskasse.** Eintragen, was du ausgelegt hast und wer sich beteiligt; aufgeteilt
wird standardmäßig gleichmäßig, bis auf den letzten Cent. Dazu laufende Salden je Person
und ein Ausgleich, der alle mit möglichst wenigen Zahlungen auf null bringt. Abgerechnete
Perioden werden archiviert, nicht gelöscht.

**Pinnwand.** Alles, was in den anderen drei Modulen passiert, landet hier — vermischt mit
Beiträgen, die Leute selbst schreiben, samt „Gefällt mir" und Kommentaren. Sie ist
zugleich das Protokoll: Systemeinträge kann niemand ändern oder löschen.

**Und drumherum:** Benachrichtigungen in der App, zwei Sprachen (Deutsch und Englisch, zur
Laufzeit erweiterbar), Profilbilder, Rollen, Beitrittscodes und ein Verwaltungswerkzeug
für die Kommandozeile.

**Aktualisieren ist ein Pull und ein Neustart.** Der Container prüft sich selbst, bevor er
irgendetwas ausliefert: Konfiguration, Datenbank, Schreibrechte und ob das Schema zum
Image passt. Bei SQLite kopiert er die Datenbank vor der Migration und spielt die Kopie
zurück, wenn die Migration scheitert. Was er nicht in Ordnung bringen kann, hält ihn an —
mit dem Grund in ganzen Sätzen. Der Server startet zuletzt, ein gescheitertes Update kann
also weder eine Aufgabe noch eine Ausgabe noch ein Passwort verändert haben.

## Schnellstart

Du brauchst Docker mit dem Compose-Plugin. Drei Befehle:

```bash
curl -O https://raw.githubusercontent.com/ChrissWalters/Kehrwoche/main/docker-compose.yml
docker compose up -d
docker compose logs -f kehrwoche
```

Dann `https://<Adresse des Rechners>:8443` aufrufen. Der Container erzeugt beim ersten
Start sein eigenes Zertifikat, deshalb warnt der Browser einmal — das ist so gewollt und
[in der FAQ erklärt](docs/faq.md#why-does-my-browser-warn-me-about-the-certificate).

Registrieren, Haushalt gründen, den Beitrittscode an die Mitbewohner*innen weitergeben.
Wer gründet, ist Administration.

Weitere Betriebsarten — PostgreSQL, Reverse Proxy, eigenes Zertifikat — stehen in
[docs/installation.md](docs/installation.md).

## Dokumentation

Die ausführliche Dokumentation ist englisch:

| | |
|---|---|
| [Installation](docs/installation.md) | Compose-Dateien, Datenbanken, Aktualisieren, Entfernen |
| [Konfiguration](docs/configuration.md) | Alle Umgebungsvariablen, TLS-Modi, Ports |
| [Reverse Proxy](docs/reverse-proxy.md) | Geprüftes Caddy-Beispiel und was ein Proxy durchreichen muss |
| [Sicherung und Wiederherstellung](docs/backup-restore.md) | Was zu sichern ist und wie es zurückkommt |
| [FAQ](docs/faq.md) | Zertifikate, Funktionen nur mit HTTPS, Passwörter, Sprachen |
| [Sicherheitsrichtlinie](SECURITY.md) | Wie sich die Anwendung schützt und wie man ein Problem meldet |

## Betrieb mit Verstand

Kehrwoche ist für das Heimnetz oder ein VPN gebaut. Sie ist so geschrieben, dass sie im
offenen Internet bestehen kann — Anmeldung, TLS, CSRF-Schutz, Ratenbegrenzung, strikte
Content-Security-Policy —, aber sie ist **nicht auditiert**, und der Betrieb dort erfolgt
vollständig auf eigenes Risiko.

Wenn du sie doch veröffentlichst, ist das das Minimum:

1. TLS davor beenden, mit echtem Zertifikat, und den Container mit `TLS_MODE=off`
   dahinter betreiben. Niemals unverschlüsselt nach außen.
2. Die Registrierung mit `REGISTRATION_OPEN=false` schließen, sobald alle ein Konto haben.
3. Das Image aktuell halten — ein Update ist ein Pull und ein Neustart.
4. Sichern, und einmal ausprobieren, ob sich eine Sicherung auch zurückspielen lässt.
5. Den Proxy die echte Client-Adresse durchreichen lassen **und** dem Container erlauben,
   sie zu glauben (`FORWARDED_ALLOW_IPS`) — sonst zählt die Ratenbegrenzung das ganze
   Internet als eine Person.

Ausführlich, samt dem was für eine Sicherheitsmeldung im Rahmen liegt, in
[SECURITY.md](SECURITY.md).

## Die Schnittstelle

Kehrwoche ist eine API mit einem Browser-Client davor, und die API beschreibt sich selbst:
`GET /api/v1/openapi.json` liefert das vollständige OpenAPI-Dokument — jeder Endpunkt,
jedes Feld, jeder Fehler —, erzeugt aus dem Code und damit nie abweichend von dem, was der
Server tatsächlich tut. Abrufbar für angemeldete Administrationen eines Haushalts.

Eine eingebaute interaktive Doku-Seite gibt es bewusst nicht: Sie müsste ihre eigenen
Dateien aus dem Internet nachladen, und das tut dieses Projekt nicht. Lade das Dokument
stattdessen in ein Werkzeug deiner Wahl, etwa

```bash
curl -b cookies.txt https://kehrwoche.local/api/v1/openapi.json > kehrwoche-api.json
```

und öffne die Datei in Bruno, Insomnia, Postman, Swagger Editor oder einem Generator
deiner Wahl.

## Issues ja, Pull Requests nein

**Fehlermeldungen, Fragen und Ideen sind sehr willkommen — bitte als Issue.** Das ist der
eine Kanal, und er wird gelesen: Woran Leute hängenbleiben, wird behoben.

**Pull Requests werden nicht angenommen**, und das gehört vorher gesagt, nicht hinterher.
Dies ist ein Ein-Personen-Projekt; fremden Code so gründlich zu prüfen, dass ich für das
geradestehen kann, was in ein Image wandert, das andere Haushalte betreiben, ist eine
Fähigkeit und ein Zeitaufwand, die ich derzeit nicht aufbringen kann. Eine fertige Arbeit
abzulehnen wäre unhöflicher als dieser Absatz.

Was du mit der Software machen darfst, schränkt das nicht ein. Sie steht unter AGPL-3.0:
abspalten, ändern, die geänderte Fassung betreiben, weitergeben. Die Lizenz verlangt nur,
dass deine Fassung unter denselben Bedingungen bleibt und die Vermerke erhalten bleiben.

Wenn du in deiner Abspaltung etwas repariert hast, ist ein Issue mit Beschreibung — oder
mit Link auf deinen Commit — wirklich hilfreich. Es nimmt nur den Weg über mich statt über
einen Merge-Knopf.

### An einer eigenen Kopie arbeiten

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

Die Testsuite läuft gegen SQLite im Arbeitsspeicher; die CI zusätzlich gegen PostgreSQL,
weil das Schema dialektunabhängig bleiben muss.

Ein paar Konventionen, falls sie dir Zeit sparen:

* **Code, Bezeichner, Kommentare und Commit-Messages sind englisch.** Oberflächentexte
  sind deutsch und englisch und nie hart codiert — sie liegen in `app/locales/`.
* **Kein Build-Schritt, kein npm.** Der Browser-Client besteht aus ES-Modulen, Vue 3 wird
  als Datei mitgeliefert. Komponenten sind Render-Funktionen statt Templates: Der
  Template-Compiler bräuchte `unsafe-eval`, und die Content-Security-Policy erlaubt das
  nicht.
* **Geld sind ganze Cent, Zeiten sind UTC mit Zeitzone, IDs sind Ganzzahlen.**
* **Routen fassen die Datenbank nicht an.** Die Logik liegt in `app/services/`, und jede
  fachliche Route geht über `require_member()` oder `require_admin()`.

### Übersetzen

Jeder Text liegt in einer flachen JSON-Datei unter `app/locales/`, mit Schlüsseln wie
`chores.form.title`. Eine Sprache hinzuzufügen heißt: `en.json` kopieren und die Werte
übersetzen.

1. `app/locales/en.json` nach `app/locales/<code>.json` kopieren (zweibuchstabiger Code,
   z. B. `fr`).
2. Die Werte übersetzen. Die Schlüssel bleiben, wie sie sind, und jeder `{Platzhalter}`
   bleibt exakt erhalten — aus `{name}` darf kein `{naem}` werden, sonst stehen die
   Klammern wörtlich auf dem Bildschirm.
3. `"language.name"` auf den Namen der Sprache setzen, so wie ihre Sprecher*innen ihn
   schreiben.
4. Zwei Einträge sind Listen statt Sätzen: `chore_templates` und `shopping_suggestions`.
   Auch sie sind Inhalt — übersetzen, und ruhig ersetzen, was im Sprachraum keinen Sinn
   ergibt.

Zum Ausprobieren muss nichts neu gebaut werden: Einen Ordner als `/app/locales-extra`
einhängen, der Server verschmilzt ihn beim Start über die mitgelieferten Dateien und
bietet die neue Sprache sofort an. Was fehlt, fällt auf Englisch zurück. Siehe
[docs/configuration.md](docs/configuration.md#extra-language-files).

**Damit eine Sprache mit der nächsten Veröffentlichung mitgeliefert wird, öffne ein Issue
und häng die fertige `<code>.json` an** — ein Link auf eine Datei in deinem eigenen
Repository geht genauso. Übersetzungen sind der eine Beitrag, der keine Code-Prüfung
braucht, deshalb sind sie leicht zu übernehmen. Sag bitte dazu, wie du genannt werden
möchtest — oder dass du lieber nicht genannt wirst.

Die Testsuite prüft, dass alle mitgelieferten Sprachen dieselben Schlüssel und dieselben
Platzhalter tragen; eine Datei mit vertipptem Schlüssel kommt also nicht weit.

## Das Projekt unterstützen

Kehrwoche ist kostenlos und bleibt es: keine Bezahlstufe, kein Lizenzschlüssel, keine
zurückgehaltene Funktion. Wenn es deinem Haushalt hin und wieder eine Diskussion erspart
und du danke sagen möchtest:

* [Ko-fi](https://ko-fi.com/chrisswalters)

Vollkommen freiwillig, und nichts an der Software hängt davon ab — eine Unterstützer-
Ausgabe gibt es nicht und wird es nicht geben.

## Versionierung

[Semantische Versionierung](https://semver.org). Die laufende Version steht in
`GET /api/v1/meta`; was sich zwischen Veröffentlichungen geändert hat, im
[CHANGELOG.md](CHANGELOG.md).

Container-Images tragen die Marken `1`, `1.0`, `1.0.0` und `latest` — wer in der
Compose-Datei die Hauptversion festlegt, bekommt Korrekturen ohne Überraschungen.

## Fremdkomponenten

Kehrwoche liefert einige Dateien mit, die nicht aus diesem Projekt stammen. Jede steht
hier mit Herkunft und Lizenz; beides steht zusätzlich in der Datei selbst.

| Komponente | Wofür | Lizenz |
|---|---|---|
| [Vue 3](https://vuejs.org) 3.5.40 — `app/static/vendor/vue.esm-browser.prod.js` | Der Browser-Client; als Datei mitgeliefert, damit nichts aus einem CDN nachgeladen wird | MIT |
| [SecLists](https://github.com/danielmiessler/SecLists) — `Pwdb_top-1000.txt`, abgelegt als `app/data/common_passwords.txt` | Die 1000 häufigsten Passwörter bei der Registrierung ablehnen | MIT |

Mitgelieferte Fremddateien sind byte-identisch mit ihrer veröffentlichten Fassung.
Herkunft, Prüfsumme und Prüfweg stehen in `app/static/vendor/README.md`; die Testsuite
prüft die Summen bei jedem Lauf nach.

Alles Übrige kommt von PyPI und steht in `pyproject.toml` (FastAPI, SQLAlchemy, Alembic,
Pydantic, argon2-cffi, Pillow, uvicorn, Typer und die Datenbanktreiber), jedes unter
seiner eigenen freizügigen Lizenz. Zur Laufzeit wird nichts aus dem Netz nachgeladen.

## Entwicklung

Zur Unterstützung der Entwicklung von Kehrwoche wurden und werden KI-Werkzeuge eingesetzt.

## Lizenz

[GNU Affero General Public License v3.0 oder später](LICENSE). Bereitgestellt **ohne
jegliche Gewährleistung**. Der Betrieb außerhalb eines abgeschlossenen Heimnetzes —
insbesondere im öffentlichen Internet — erfolgt vollständig auf eigenes Risiko der
Betreiber*innen.
