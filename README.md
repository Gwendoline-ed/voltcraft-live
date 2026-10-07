# Voltcraft Live

[English instructions](README.en.md) · GNU GPLv3 oder neuer · Version 0.1.0

Messkurven eines **Voltcraft DSO-1084F** unter Linux im Browser anzeigen, das Oszilloskop fernsteuern und eigene Messprofile speichern. Das Programm besteht aus einer Python-Datei und benötigt zum Betrieb keine zusätzlichen Python-Pakete.

## Funktionen

- Fortlaufende USB-Aufnahmen mit Zeitachse und Anzeige der empfangenen Kanäle CH1–CH4.
- Mausrad: Zeitzoom am Mauszeiger; Umschalt + Mausrad: Amplitudenzoom; Ziehen: Ausschnitt verschieben; Doppelklick: zurücksetzen.
- Start, Stop, Einzelaufnahme, Auto Scale und Trigger auslösen.
- Kanalzustand, Kopplung, Tastkopffaktor, V/div, Offset, Bandbreitenbegrenzung und Invertierung einstellen; Zeitbasis und Flankentrigger konfigurieren.
- Geräteeinstellungen einlesen und nach dem Anwenden zurücklesen.
- Eigene Profile anlegen, auswählen, löschen und als JSON importieren oder exportieren.
- Kurvengrafik als PNG und Aufnahme mit Diagnose als JSON speichern.
- Bedienfeld am Gerät entsperren; dabei wird die PC-Übertragung pausiert.
- Demo ohne Gerät und Wiedergabe gespeicherter Aufnahmen.

## Stand und Grenzen

Diese erste Veröffentlichung ist experimentell. Vollständige USB-Aufnahmen mit 4.000 Messpunkten auf CH1 wurden an einem DSO-1084F mit Firmware 1.1.2 und 2.0.0 beobachtet. Weitere Kanalkombinationen und die neuen Fernsteuerungsaktionen brauchen noch Bestätigung an echter Hardware. Protokoll, Steuerungsabläufe und Profile sind automatisiert mit simulierten Geräten getestet; das ersetzt keine Geräteprüfung.

**Die Amplitude der Kurven zeigt vorzeichenbehaftete 8-Bit-Rohwerte, noch keine kalibrierten Volt.** V/div, Offset und Triggerpegel in der Fernsteuerung sind Geräteeinstellungen. Die Zeitachse verwendet die Abtastrate aus dem empfangenen Datenkopf. Die Bildrate hängt von Gerät, Aufnahmelänge und USB-Übertragung ab.

Die Anwendung läuft auf Linux und bindet ihren Webserver ausschließlich an `127.0.0.1`. Die Oberfläche ist deutsch. Dieses Projekt ist eine unabhängige Community-Anwendung.

## Schnellstart

Voraussetzungen: Linux, Python 3.10 oder neuer, ein aktueller Browser und ein DSO-1084F am rückseitigen USB-Geräteanschluss. Andere Programme, die auf das Oszilloskop zugreifen, vorher schließen.

Den Projektordner herunterladen und dort ausführen:

```bash
sudo modprobe usbtmc
ls -l /dev/usbtmc*
python3 voltcraft_live.py
```

Der Browser öffnet `http://127.0.0.1:8765`. Beenden mit **Strg+C im Terminal**.

Falls der Zugriff auf `/dev/usbtmc0` verweigert wird, kann der Eigentümer für die aktuelle Verbindung gesetzt werden:

```bash
sudo chown "$USER" /dev/usbtmc0
python3 voltcraft_live.py
```

Diese Berechtigung kann beim erneuten Anschließen zurückgesetzt werden. Bei mehreren USBTMC-Geräten den richtigen Pfad über `--device` angeben. Das Programm selbst benötigt keine Root-Rechte.

Ohne angeschlossenes Gerät ausprobieren:

```bash
python3 voltcraft_live.py --demo
```

## Bedienung

Die Häkchen über dem Diagramm steuern die Sichtbarkeit bereits empfangener Kanäle. Ein grauer Kanal mit „keine Messdaten“ hat in der aktuellen Aufnahme keine Daten. Kanäle am Gerät über die Fernsteuerungsfelder oder direkt am Oszilloskop einschalten.

„Vom Oszi einlesen“ füllt die Einstellungsfelder. Eingetragene Werte ändern das Gerät erst mit „Einstellungen anwenden“. Leere Felder werden nicht angewendet. Ein ausgewähltes Profil füllt die Felder und stellt den Anzeigezoom wieder her; auch hier erfolgt die Geräteänderung erst beim Anwenden.

Profile liegen standardmäßig unter `~/.config/voltcraft-live/profiles.json`. Ein bereits vorhandener Profilname wird nur mit gesetztem Überschreiben-Häkchen ersetzt. Eine beschädigte Profildatei wird nicht stillschweigend überschrieben.

„Übertragung pausieren“ hält die PC-Abfragen an. „Bedienfeld entsperren“ pausiert die Übertragung und sendet den Entsperrbefehl als letzten Befehl. Danach am Gerät bedienen und bei Bedarf die Übertragung wieder starten. Neue USB-Abfragen können das Bedienfeld durch das Verhalten der Gerätefirmware erneut sperren.

## Optionen

```bash
python3 voltcraft_live.py --help
python3 voltcraft_live.py --device /dev/usbtmc1 --interval 0.5
python3 voltcraft_live.py --command legacy
python3 voltcraft_live.py --no-browser --port 8766
python3 voltcraft_live.py --replay aufnahme.json
python3 voltcraft_live.py --profiles eigene-profile.json
python3 voltcraft_live.py --version
```

`--command auto` ist der Standard. Das Programm versucht die Display-Abfrage und wechselt bei einer gültigen leeren Antwort zur öffentlichen ALL-Abfrage. `legacy` wählt diese ALL-Abfrage direkt; der Name bezeichnet den Übertragungsweg und setzt keine alte Firmware voraus. `--interval` erlaubt 0,1 bis 60 Sekunden Abstand zwischen Aufnahmen. Demo und Wiedergabe greifen nicht auf USB zu.

## Fehler melden und mitarbeiten

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) für Tests und hilfreiche Fehlerberichte sowie [docs/PROTOCOL.md](docs/PROTOCOL.md) für die Protokollannahmen. Diagnose-JSON kann die Seriennummer des Geräts und deine Messdaten enthalten: vor einem öffentlichen Upload prüfen und bei Bedarf entfernen. Die mitgelieferten Tests verwenden künstliche Daten.

## Lizenz und Quellen

Copyright © 2026 Voltcraft Live contributors. Dieses Projekt steht unter der **GNU General Public License, Version 3 oder jeder späteren Version** (`GPL-3.0-or-later`). Es darf verwendet, verändert und unter den Lizenzbedingungen weitergegeben werden. Der vollständige Lizenztext und Gewährleistungsausschluss stehen in [LICENSE](LICENSE).

Protokollrecherche: [smooker/hantek_dso](https://github.com/smooker/hantek_dso) und [WiZZteXX/DSO4xx4c](https://github.com/WiZZteXX/DSO4xx4c). Herstellerinformationen und Firmware: [Voltcraft Download](https://voltcraftdownload.info/). Diese Quellen sind keine Laufzeitabhängigkeiten. Hersteller-Firmware und proprietäre Windows-Software sind nicht Bestandteil dieses Projekts.
