# Auf GitHub veröffentlichen

Dieses Paket enthält den Quellcode, beide Anleitungen, den vollständigen GPLv3-Lizenztext und die Tests. Der vorgeschlagene Repository-Name ist `voltcraft-live`; die Lizenzangabe ist `GPL-3.0-or-later`.

## Über die GitHub-Webseite

1. Das ZIP entpacken. Die Dateien gehören direkt in die Wurzel des neuen Repositorys.
2. Im GitHub-Dashboard über das Plus-Menü **New repository** wählen.
3. Dein Konto als Eigentümer, `voltcraft-live` als Namen und **Public** als Sichtbarkeit wählen. Beschreibung: `Linux waveform viewer and remote control for the Voltcraft DSO-1084F`.
4. README, Gitignore und Lizenz nicht automatisch erzeugen lassen; diese Dateien sind bereits im Paket. **Create repository** anklicken.
5. Den Link zum Hochladen vorhandener Dateien bzw. **Add file → Upload files** wählen. Den Inhalt des entpackten Projektordners samt Unterordnern hochladen. Unter Linux bei Bedarf mit Strg+H versteckte Dateien einblenden, damit auch die Test-Konfiguration und Gitignore mitkommen. Nicht den ZIP-Container hochladen.
6. Mit der Commit-Nachricht `Initial release 0.1.0` auf den Hauptzweig übernehmen. Anschließend prüfen, dass README, `voltcraft_live.py` und LICENSE direkt auf der Repository-Startseite sichtbar sind.
7. Unter **Actions** die Testläufe ansehen. Ein erfolgreicher Lauf bestätigt die simulierten Tests und keine Gerätekompatibilität.

## Mit Git und vorhandener Anmeldung

Nach dem Anlegen eines leeren öffentlichen Repositorys kann das Paket auch mit Git hochgeladen werden. Die Kopier-URL aus deinem Repository verwenden; Platzhalter vorher ersetzen. GitHub akzeptiert für Git-HTTPS keine Kontopasswörter. Eine vorhandene GitHub-CLI-Anmeldung oder eingerichtete SSH-Verbindung verwenden.

```bash
git init -b main
git add .
git commit -m "Initial release 0.1.0"
git remote add origin DEINE-REPOSITORY-URL
git push -u origin main
```

Bei der ersten Git-Nutzung zunächst einen gewünschten Commit-Namen und eine passende, gegebenenfalls von GitHub bereitgestellte private Commit-E-Mail konfigurieren. Zugangsdaten gehören weder in die Repository-URL noch in Projektdateien.

Offizielle Anleitungen: [Repository anlegen](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository) und [Dateien hinzufügen](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).
