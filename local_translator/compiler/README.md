# compiler/ — Standalone-Build

Baut `local_translator` als `--onedir`-PyInstaller-Paket (EXE + Programmordner),
optional zusammen mit einer gepackten Terminologie-Engine.

> **⚠️ Nutzung auf eigene Gefahr.** Keine Garantie, dass der Build auf jeder Maschine
> funktioniert oder die weitergegebene EXE fehlerfrei läuft — vor Weitergabe selbst testen.
> Bereitgestellt ohne jede Gewährleistung — siehe [LICENSE](../../LICENSE).

## Vor dem Bauen: Version hochzählen

`local_translator/version.py` (`APP_VERSION`) wird nie automatisch gesetzt — vor einem
Build, den du weitergibst, von Hand anheben, wenn sich seit dem letzten Build etwas
geändert hat. Die Version steht danach rechts in der Statusleiste der laufenden App —
so lässt sich unterscheiden, welchen Stand ein Freund gerade tatsächlich nutzt (siehe
`docs/MAINTENANCE_translator.md`, Abschnitt "Version").

## Voraussetzungen

- Python auf der Baumaschine (irgendeine Version, die `venv` kann).
- `pip install pyinstaller` ist **nicht** nötig — `build.py` legt sich beim ersten
  Lauf eine eigene, projektlokale venv unter `local_translator/.venv-build/` an und
  installiert `requirements.txt` + PyInstaller dort hinein. Das hält alles, was
  sonst noch auf der Maschine global installiert ist, aus dem Build heraus.
- Für `compiler/build_gui.py`: Tkinter (bei den meisten Python-Installationen unter
  Windows schon dabei).

## Drei Modi

```
python compiler/build.py                                    # nur App
python compiler/build.py --term-engine-dir <pfad>            # App + Terminologie
python compiler/build.py --only-term-engine --term-engine-dir <pfad> --out <pfad>
                                                               # nur Terminologie packen
python compiler/build.py --with-tester                       # + Batch-Tester-EXE (zu jedem der obigen Modi 1/2 kombinierbar)
```

Oder grafisch: `python compiler/build_gui.py` — drei Radio-Buttons statt der drei
Flags oben, plus eine Checkbox "Batch-Tester mitbauen".

## Batch-Tester mitbauen (`--with-tester`)

Baut zusätzlich `test/test_gui.py` (den Batch-Qualitätstest-Runner, siehe
dessen eigenen Docstring) als eigenständige EXE:
`dist/LocalTranslate/test/LocalTranslate-Tester.exe`. Läuft unabhängig von
der Haupt-App, braucht aber einen laufenden LocalTranslate-Server (egal ob
aus dem Quellcode oder der gebauten EXE) unter `http://127.0.0.1:8000`.

Anders als die App wird der Tester als `--onefile` statt `--onedir` gebaut
— kein `_internal`-Unterordner, der die relative Pfadauflösung von
`test_gui.py` (Quelltexte/Ergebnisse neben sich selbst, `logs/` eine Ebene
höher — dieselbe Struktur wie im Repo-Checkout) durcheinanderbringen würde.
Der einmalige Neuentpack-Overhead von `--onefile` bei jedem Start fällt hier
nicht ins Gewicht — anders als bei der App wartet hier nichts aktiv auf
einen Server-Start.

Braucht keine eigene venv-Vorbereitung — der Tester hat keine Abhängigkeiten
über Python-Standardbibliothek + Tkinter hinaus, beides schon in der
Build-venv der App vorhanden.

**Wichtig:** `<pfad>` bei `--term-engine-dir` ist der kompilierte `terminology/`-Ordner
(die `mindset/sprache.json`-Dateien, gebaut von `Terminologie-Engine/build_terminology.py`
+ `filter_terminology.py`) — der liegt bei dir lokal, nie in diesem Repo. Der Pfad wird
nirgends gespeichert oder vorausgefüllt — bei jedem Lauf neu angeben, weil er nur auf der
jeweiligen Maschine gültig ist.

## Warum `--onedir` statt `--onefile`

Die App wird interaktiv gestartet und wartet aktiv auf den Server-Start, bevor der
Browser aufgeht. `--onefile` würde sich bei jedem einzelnen Start neu in einen
Temp-Ordner entpacken — spürbar langsamer bei jedem Start. `--onedir` entpackt sich
einmalig beim Bauen; jeder Start danach ist so schnell wie ein normales Skript.

## Was wohin kommt

| Ort | Inhalt | Warum |
|---|---|---|
| Eingebettet (`--add-data`) | `index.html`, `static/`, `pipeline/mindsets.json` | Read-only, gehört fest zur App |
| Extern, neben der EXE (`dist/LocalTranslate/`) | `config.yaml` | Editierbar — Ollama-Modell, Sprachen etc. |
| Extern, neben der EXE, optional | `terminology.data` | Von `--term-engine-dir` gepackt, unabhängig von der App aktualisierbar |
| Extern, `test/`-Unterordner, optional | `LocalTranslate-Tester.exe` | Von `--with-tester` gebaut, unabhängig von der App startbar |
| Nie mitgeliefert | `.env` | Persönliche API-Keys (DeepL/Lara) — wird nie kopiert oder gezippt |

`dist/LocalTranslate/` selbst ist reiner Build-Output, keine dauerhafte Installation —
er wird bei jedem App-Build (Modi 1 und 2) komplett geleert, bevor PyInstaller läuft.
Die eigentliche Weitergabe-Einheit ist das Release-ZIP (`dist/LocalTranslate.zip`,
außer bei `--no-zip`); wer die App tatsächlich benutzt, tut das aus einer entpackten
Kopie des ZIPs, nicht direkt aus `dist/` im Repo.

## Nach dem Build

Ein unsigniertes EXE wird von Windows SmartScreen/Defender beim ersten Start eines
Fremden vermutlich angemeckert ("Weitere Informationen" → "Trotzdem ausführen") — ganz
normal bei privater Weitergabe ohne teures Code-Signing-Zertifikat, kein Bug.

Wer die App bekommt, braucht zusätzlich eine eigene Ollama-Installation mit dem in
`config.yaml` hinterlegten Modell (Standard: `translategemma:12b`) — das EXE bringt
kein Ollama mit.

**Warum `terminology.data` und nicht `terminology.pack.gz`:** Auf einer echten
Windows-Maschine hat ein installiertes Archiv-Tool die `.gz`-Endung übernommen und
den Explorer beim Draufklicken in die Datei hineinnavigieren lassen statt sie als
eine Datei anzuzeigen — mit einer irreführenden "0 KB"-Anzeige, obwohl der Inhalt
vollständig da war. `.data` ist nirgends als Archiv-Endung registriert, deshalb
zeigt der Explorer die Datei einfach mit ihrer echten Größe an. Am Dateiinhalt
ändert das nichts — weiterhin stinknormales gzip, `gzip.open()` interessiert sich
nicht für die Dateiendung.
