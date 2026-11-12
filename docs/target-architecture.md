# Zielarchitektur: Sensorgesteuerter Körper über MQTT

Status: Entwurf · Stand: 2026-11-11 · Branch: `motion-framework`

## 1. Ziel

Sensoren, die auf demselben Pi oder auf anderen Geräten laufen (z. B. ESP32),
darunter eine Kamera mit Coral Edge TPU für Bilderkennung,
sollen das Verhalten des PiCrawlers beeinflussen können, ohne mit der
Servo-Ansteuerung gekoppelt zu sein. Neue Sensoren und externe Steuerungen
(Node-RED, Home Assistant, LLM) sollen sich anschließen lassen, ohne dass der
Motorik-Kern geändert werden muss.

### Grundsätze

1. **Sensoren melden Fakten, keine Befehle.** Ein Sensor veröffentlicht
   „Abstand = 12 cm“, nicht „stopp“.
2. **Eine Stelle entscheidet.** Der Arbiter setzt Sensorfakten nach
   Prioritäten in Befehle um.
3. **Ein einziger Prozess spricht mit den Servos.** Nur der `MotionExecutor`
   ruft `Picrawler` auf.
4. **Der Kern bleibt frei von MQTT.** `picrawler/` hat keine Abhängigkeit zu
   MQTT und bleibt ohne Hardware testbar.
5. **Im Zweifel anhalten.** Fällt eine Verbindung aus, kommt eine ungültige
   Nachricht oder läuft eine Zeit ab, geht der Körper in einen sicheren Zustand.

### Nicht-Ziele

- Regelkreise über 20 Hz über MQTT (z. B. Balancieren mit IMU). Solche
  Regelkreise laufen lokal im Executor.
- Einzelne Gait-Frames als Echtzeit-Stream über das Netz.
- Navigation, Kartierung, mehrere Roboter (dafür später ROS 2 prüfen).

## 2. Komponenten

```
 Sensor-Knoten                Arbiter                     Body-Service (Pi)
┌──────────────┐ sensor/*  ┌──────────────┐  cmd/*   ┌──────────────────────────┐
│ ultrasonic   │ ────────► │ Regeln       │ ───────► │ picrawler_mqtt.adapter   │
│ imu          │           │ Prioritäten  │          │   Validierung, Dedupe    │
│ vision+Coral │           │              │ ◄─────── │   Watchdog               │
│ remote       │           └──────────────┘ state/*  │ picrawler.executor       │
└──────────────┘                                     │   MotionExecutor-Thread  │
                                                     │ picrawler.Picrawler      │
                                                     └──────────────────────────┘
```

| Komponente | Ort | Verantwortung | Darf nicht |
|---|---|---|---|
| `picrawler` (Kern) | dieses Repo | Kinematik, Gaits, Servos | MQTT importieren, Threads starten |
| `picrawler.executor` | dieses Repo | Befehle nacheinander ausführen, unterbrechbar zwischen Frames | Netzwerkzugriff |
| `picrawler_mqtt.adapter` | dieses Repo, eigenes Paket | MQTT ↔ Executor, Validierung, Watchdog, Status | Servos direkt ansteuern |
| `picrawler_mqtt.arbiter` | anfangs im selben Prozess, später eigenständig | Sensorfakten → Befehle nach Priorität | `cmd/*` von Sensoren durchreichen |
| Sensor-Knoten | beliebig | Messwerte veröffentlichen | unter `cmd/*` veröffentlichen |
| `vision-service` | Pi mit Kamera + Coral | Kamera, Videostream (nur lokal), Inferenz auf der TPU, Erkennungen veröffentlichen | Verhalten entscheiden, Befehle senden |
| `web-service` | Pi | Login, Stream-Proxy, Objekt-Log, Not-Aus, Admin (Bluetooth, Vision-Modus, Dienste) | Servos oder Kamera direkt ansteuern |
| `gamepad-service` | Pi | Bluetooth-Controller lesen, Eingaben veröffentlichen | unter `cmd/*` veröffentlichen |

Paketstruktur:

```
picrawler/
  kinematics.py
  gaits.py
  picrawler.py        # + iter_action()
  executor.py         # neu
picrawler_mqtt/       # neu, Extra: pip install .[mqtt]  (paho-mqtt)
  __main__.py         # python -m picrawler_mqtt --config body.toml
  adapter.py
  arbiter.py
  schema.py           # Payload-Validierung
  config.py
sensors/              # Beispiel-Knoten, keine Bibliothek
  ultrasonic_node.py
services/vision/      # Kamera + Coral, siehe Abschnitt 7
  __main__.py
  camera.py           # picamera2: Haupt-Stream + lores-Stream
  stream.py           # MJPEG über HTTP
  inference.py        # TPU-Modelle, nur das jeweils neueste Bild
  imprint.py          # eigene Objekte lernen
services/web/         # Weboberfläche, siehe Abschnitt 8
  __main__.py
  app.py              # aiohttp: Routen, Login, SSE, Stream-Proxy
  sightings.py        # Erkennungen → Sichtungen (Objekt-Log)
  store.py            # SQLite
  bluetooth.py        # BlueZ über D-Bus
  system.py           # Dienste/System über polkit
  static/             # HTML, CSS, JS, ohne CDN
services/gamepad/     # Bluetooth-Controller über evdev
tests/
```

## 3. Kern-Erweiterungen

### 3.1 `Picrawler.iter_action(name, steps=1)`

Generator, der die Frames einer Aktion einzeln liefert. Die Logik für die
abwechselnden Beinpaare (`stand_position`) wird dabei genauso fortgeschrieben
wie in `do_action`. `do_action` wird intern darauf umgestellt und behält sein
Verhalten:

```python
def do_action(self, motion_name, step=1, speed=50):
    for frame in self.iter_action(motion_name, step):
        self.do_step(frame, speed=speed)
```

Unbekannte Namen lösen `KeyError` aus, statt etwas auszugeben. `do_action`
fängt das ab und gibt aus Kompatibilitätsgründen weiterhin „No such action“ aus.

### 3.2 `MotionExecutor`

```python
class Priority(IntEnum):
    GOAL = 0      # Fernsteuerung, Missionen, LLM
    REFLEX = 1    # Sensorreaktionen, z. B. Ausweichen
    SAFETY = 2    # Stopp, Watchdog, Not-Aus

@dataclass(frozen=True)
class Command:
    kind: Literal["action", "frame", "stop", "sit"]
    name: str | None = None
    steps: int = 1
    speed: int = 50
    frame: list[list[float]] | None = None
    priority: Priority = Priority.GOAL
    id: str | None = None

class MotionExecutor:
    def __init__(self, crawler, on_state=None): ...
    def start(self) -> None: ...
    def submit(self, cmd: Command) -> bool: ...   # False = abgelehnt
    def stop(self, priority=Priority.SAFETY) -> None: ...
    def shutdown(self, sit=True) -> None: ...
    @property
    def state(self) -> ExecutorState: ...
```

Regeln:

- Genau ein Worker-Thread besitzt `crawler`. Kein anderer Code ruft ihn auf.
- Zwischen zwei Frames wird geprüft, ob abgebrochen werden soll. Die
  Reaktionszeit ist damit höchstens die Dauer eines Frames (abhängig von
  `speed`, am Gerät messen).
- Ein Befehl mit **höherer** Priorität bricht den laufenden ab und leert alle
  wartenden Befehle mit niedrigerer Priorität.
- Ein Befehl mit **gleicher** Priorität wird hinten angestellt. Die Warteschlange
  hat eine feste Obergrenze (Standard 8), danach wird abgelehnt (`submit` → False).
- Ein Befehl mit **niedrigerer** Priorität wird abgelehnt, solange die Sperrzeit
  nach einem höher priorisierten Befehl läuft (Standard 1 s). So kann
  „vorwärts“ einen gerade ausgelösten Stopp nicht sofort aufheben.
- Fehler im Worker beenden den Thread nicht. Sie werden als Zustand `error`
  gemeldet, danach folgt ein Stopp.

Zustände:

```
idle ──submit──► running ──fertig──► idle
  ▲                 │
  │            preempt/stop
  │                 ▼
  └──────────── stopping ──► idle
            error ──► (stop) ──► idle
```

`on_state(ExecutorState)` wird bei jedem Übergang aufgerufen und liefert
`status`, `command_id`, `name`, `priority`, `queue_len` und `error`.

## 4. MQTT-Schnittstelle

Präfix: `crawler/<robot_id>/` (Standard `robot_id = 1`). Alle Payloads sind
JSON in UTF-8 und enthalten `ts` (Unix-Zeit in Sekunden, float).

### 4.1 Topics

| Topic | Richtung | QoS | Retain |
|---|---|---|---|
| `sensor/<sensor>/<messwert>` | Sensor → Arbiter | 0 | nein |
| `sensor/<sensor>/status` | Sensor → | 1 | ja (LWT `offline`) |
| `cmd/action` | Arbiter → Body | 1 | **nie** |
| `cmd/frame` | Arbiter → Body | 1 | **nie** |
| `cmd/stop` | jeder Berechtigte → Body | 1 | **nie** |
| `cmd/heartbeat` | Arbiter → Body | 0 | nein |
| `state/motion` | Body → | 1 | ja |
| `state/legs` | Body → | 0 | ja |
| `status` | Body → | 1 | ja (LWT `offline`) |
| `event/rejected` | Body → | 1 | nein |

Der Body-Service ignoriert retained Nachrichten auf `cmd/*` (`msg.retain == True`)
und meldet sie über `event/rejected`.

### 4.2 Payloads

`cmd/action`
```json
{"id": "b1f3…", "name": "forward", "steps": 2, "speed": 70, "priority": "goal", "ts": 1762900000.1}
```

`cmd/frame`
```json
{"id": "…", "frame": [[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]], "speed": 60, "priority": "goal", "ts": …}
```

`cmd/stop`
```json
{"id": "…", "sit": false, "reason": "obstacle", "ts": …}
```

`state/motion`
```json
{"status": "running", "command_id": "b1f3…", "name": "forward", "priority": "goal", "queue_len": 0, "ts": …}
```

`sensor/ultrasonic/distance`
```json
{"cm": 12.4, "ts": …}
```

### 4.3 Validierung (`schema.py`)

Nachrichten, die eine der folgenden Regeln verletzen, werden verworfen und über
`event/rejected` mit Grund gemeldet. Ausgeführt wird dann nichts.

- `name` ∈ fest konfigurierte Liste (Standard: alle eingebauten Gaits).
- `steps` ∈ 1–10, `speed` ∈ 1–100.
- `frame`: genau 4 × 3 Zahlen, jede Koordinate im Bereich x 0–120,
  y −120–150, z −100–130 mm (konfigurierbar).
- `priority` ∈ {`goal`, `reflex`}. `safety` gibt es nur intern bzw. über `cmd/stop`.
- `ts` darf höchstens 2 s alt sein (Schutz gegen veraltete Nachrichten).
  Voraussetzung: Die Uhren sind per NTP synchronisiert.
- `id` darf in den letzten 60 s noch nicht vorgekommen sein (Schutz gegen
  doppelte Zustellung bei QoS 1).

## 5. Sicherheit

| Fall | Reaktion |
|---|---|
| Verbindung zum Broker verloren | `stop(SAFETY)`, nach 5 s ohne Verbindung hinsetzen |
| `cmd/heartbeat` bleibt länger als 1 s aus, während eine GOAL-Bewegung läuft | `stop(SAFETY)` |
| Retained Befehl empfangen | ignorieren und melden |
| Ungültige Nachricht | ignorieren und melden, Bewegung läuft weiter |
| Ausnahme im Executor | `error` melden, dann stoppen |
| Prozessende (SIGINT/SIGTERM) | `shutdown(sit=True)`, `status` = `offline` |
| Start | `status` = `online`, Zustand `idle`, **keine** automatische Bewegung |

Broker:

- Benutzer und Passwort pflicht, TLS empfohlen, sobald andere Geräte als
  localhost beteiligt sind.
- ACLs: Sensor-Benutzer dürfen nur `crawler/+/sensor/<eigener Sensor>/#`
  schreiben. Nur Arbiter und Bedienoberfläche dürfen `crawler/+/cmd/#`
  schreiben. Nur der Body-Service darf `state/#`, `status` und `event/#`
  schreiben.

## 6. Arbiter

Regeln werden deklarativ in der Konfiguration angelegt und nach Priorität
ausgewertet. Erste Regel:

```toml
[[rule]]
name = "obstacle_stop"
when = "sensor/ultrasonic/distance"
condition = "cm < 15"
then = { kind = "stop", reason = "obstacle" }
priority = "safety"
cooldown_s = 1.0
```

Messwerte werden entprellt, z. B. durch einen Median über die letzten 3 Werte,
damit ein einzelner Ausreißer keinen Stopp auslöst. Ist ein Sensor `offline`,
gelten seine Regeln als „unbekannt“, und GOAL-Befehle, die von ihm abhängen,
werden abgelehnt.

## 7. Vision-Service (Kamera + Coral TPU)

### 7.1 Aufgaben

- Besitzt **als einziger Prozess** die Kamera und die Coral TPU.
- Nimmt mit `picamera2` zwei Streams gleichzeitig auf: einen **Haupt-Stream**
  für das Video (z. B. 1280×720) und einen **lores-Stream** in der
  Eingabegröße des Modells (z. B. 300×300 oder 320×320) für die TPU.
- Liefert das Video als **MJPEG über HTTP**, nur auf `127.0.0.1`. Login,
  eingezeichnete Boxen und alles für den Browser übernimmt der web-service
  (Abschnitt 8).
- Veröffentlicht Erkennungen als **Fakten** unter `sensor/vision/*`. Was der
  Roboter daraufhin tut, entscheidet der Arbiter (Abschnitt 6).

### 7.2 Pipeline

```
picamera2 ─┬─ main  ─► MJPEG-Encoder ─► HTTP /stream.mjpg
           └─ lores ─► [neuestes Bild] ─► TPU ─► Nachverarbeitung ─► MQTT
```

- Stream und Inferenz laufen in **getrennten Threads**. Ist die Inferenz
  langsam oder fällt sie aus, läuft der Stream trotzdem weiter.
- Die Inferenz holt sich immer nur das **neueste** Bild. Es gibt keine
  Warteschlange, in der sich alte Bilder stauen, daher keine wachsende
  Verzögerung.
- Erkennungen werden auf höchstens 10 Hz begrenzt veröffentlicht. Der Crawler
  kann mit seinen Schrittzyklen ohnehin nicht schneller reagieren.

### 7.3 Modelle (für die Edge TPU kompiliert, INT8)

| Modus | Modell (Startpunkt) | Ergebnis |
|---|---|---|
| `detect` | SSD MobileNet v2 oder EfficientDet-Lite0 (COCO) | Boxen + Klassen (person, cat, sports ball, …) |
| `pose` | MoveNet Lightning | 17 Körperpunkte einer Person |
| `imprint` | MobileNet-Embedding + Weight Imprinting | eigene, selbst beigebrachte Objektklassen |
| `off` | – | nur Stream, TPU unbenutzt |

Es ist immer ein Modus aktiv. Ein Wechsel lädt das Modell neu (Dauer am Gerät
messen). Der Start-Modus steht in `vision.toml`, gewechselt wird über
`vision/mode`.

### 7.4 Topics

| Topic | Richtung | QoS | Retain | Payload |
|---|---|---|---|---|
| `sensor/vision/detections` | vision → | 0 | nein | `{"frame_ts": …, "objects": [{"label": "person", "score": 0.87, "box": [x0, y0, x1, y1]}]}` (Box normiert auf 0–1) |
| `sensor/vision/pose` | vision → | 0 | nein | `{"frame_ts": …, "score": 0.7, "keypoints": [[x, y, score], …]}` (17 Punkte, COCO-Reihenfolge) |
| `sensor/vision/status` | vision → | 1 | ja (LWT `offline`) | `{"status": "online", "tpu": true, "mode": "detect", "fps": 18.2, "inference_ms": 9.5}` |
| `vision/mode` | Arbiter/UI → vision | 1 | nie | `{"id": "…", "mode": "pose", "ts": …}` |

Imprinting (Objekt zeigen, Namen vergeben, lernen) wird über die
Admin-Seite des web-service bedient (Abschnitt 8.4). Die gelernten Klassen liegen unter
`/var/lib/picrawler/vision/`.

### 7.5 Verhalten im Arbiter

Neue Regeln, alle mit Priorität `goal`, außer wo anders angegeben:

- **Folge mir** (`detect`, Label `person`, höchster Score):
  - Box-Mitte weiter als 0,15 von der Bildmitte entfernt → `turn left` bzw.
    `turn right`
  - sonst, solange die Boxhöhe < 0,5 ist → `forward`
  - sonst → stehen bleiben
  - Person länger als 2 s nicht gesehen → `stop`
  - Neuer Befehl erst, wenn `state/motion` wieder `idle` meldet. So wird
    die Warteschlange nicht mit Befehlen geflutet.
- **Gesten** (`pose`): Handgelenk über dem Kopf → `stand`, Winken (seitliche
  Bewegung des Handgelenks über 1 s) → `wave`. Jede Geste muss 3 Bilder in
  Folge erkannt werden, um Fehlauslösungen zu vermeiden.
- **Suchen** (`imprint`): langsam drehen, bis das gelernte Objekt erkannt
  wird, dann wie „Folge mir“ annähern.
- Der Ultraschall-Stopp (`safety`) überstimmt immer alle Vision-Regeln.
- `sensor/vision/status` ist `offline` oder `tpu: false` → alle
  Vision-Regeln gelten als „unbekannt“ und lösen nichts aus.

### 7.6 Randbedingungen

- **Software-Risiko:** Die offizielle `pycoral` unterstützt nur bis
  Python 3.9, Raspberry Pi OS Bookworm hat 3.11. Welcher Weg genommen wird,
  entscheidet Spike M-V0 (Abschnitt 11, `spikes/coral/`):
  1. Community-Build (z. B. `pycoral-pi5`)
  2. `tflite-runtime` bzw. LiteRT mit dem Edge-TPU-Delegate (`libedgetpu`),
     ggf. mit separatem Python über `uv`
  3. Inferenz in einem Container mit älterem Debian. Die Kamera bleibt dabei
     außerhalb, und die Bilder werden lokal übergeben (zusätzlicher Service).
- **Ohne TPU:** Der Service startet trotzdem, liefert den Stream und meldet
  `tpu: false`. Keine Inferenz auf der CPU als stiller Ersatz, damit die CPU
  für Motorik und Stream frei bleibt.
- **Strom:** `libedgetpu` mit Standard-Takt statt Maximal-Takt. Verbrauch
  und Spannungseinbrüche unter Last am Akku messen (Servos + TPU +
  Kamera + Stream gleichzeitig).
- **Datenschutz:** standardmäßig keine Aufzeichnung, Stream nur im LAN mit
  Login, keine Identifikation von Personen.

## 8. Weboberfläche (web-service)

Eigener Dienst `services/web/`, getrennt vom vision-service. Grund: Der
vision-service soll nur Kamera und TPU besitzen, und Admin-Funktionen wie
Bluetooth brauchen andere Rechte als die Kamera. Stürzt die Oberfläche ab,
laufen Stream-Erzeugung und Erkennung weiter, und umgekehrt.

### 8.1 Aufbau

```
Browser ──HTTP──► web-service :8080 ─┬─ /stream.mjpg ──► vision-service 127.0.0.1:8081 (Proxy)
   ▲                                 ├─ MQTT (Benutzer ui): sensor/#, state/#, status, event/#
   └──── Server-Sent Events ─────────┤                    cmd/stop, cmd/action, vision/mode
                                     └─ BlueZ (D-Bus)  ──► Bluetooth-Controller
```

- Der vision-service lauscht nur noch auf `127.0.0.1`. Von außen ist der
  Stream ausschließlich über den web-service (mit Login) erreichbar.
- Live-Daten (Log, Status, Bluetooth-Suche) gehen per **Server-Sent Events**
  an den Browser. Das ist einfacher als WebSockets und reicht, weil
  Befehle vom Browser als normale POST-Anfragen kommen.
- Technik: `aiohttp` (ein Paket für HTTP, SSE und den Stream-Proxy, auch als
  `python3-aiohttp` per apt), BlueZ über `dbus-fast`, Frontend als
  statisches HTML/CSS/JS **ohne Build-Schritt und ohne CDN**. Die Seite muss
  ohne Internet funktionieren.

### 8.2 Seiten

| Seite | Rolle | Inhalt |
|---|---|---|
| **Live** | viewer | Video, eingezeichnete Boxen (umschaltbar), Status-Leiste (vision online/TPU/FPS/Inferenzzeit, Body-Status, Gamepad verbunden), **Not-Aus-Knopf** (immer sichtbar), Live-Log der letzten Ereignisse |
| **Log** | viewer | Objekt-Log mit Filter (Label, Zeitraum, Mindest-Score), CSV-Export |
| **Admin** | admin | Bluetooth-Controller, Vision-Modus, Dienste, System |

Die Boxen zeichnet der Browser in ein Canvas über dem Video, mit den Daten
aus `sensor/vision/detections`. So bleibt der Stream unverändert, und es
wird kein zweiter, bearbeiteter Videostrom auf dem Pi kodiert.

### 8.3 Objekt-Log

Erkennungen kommen mit bis zu 10 Hz. Pro Bild einen Eintrag zu schreiben,
wäre unlesbar. Der web-service fasst sie deshalb zu **Sichtungen** zusammen:

- **Beginn:** Ein Label wird in 3 aufeinanderfolgenden Bildern mit
  Score ≥ `min_score` (Standard 0,5) erkannt.
- **Ende:** Das Label wurde 2 s lang nicht mehr erkannt.
- Mehrere Objekte mit demselben Label zählen als eine Sichtung, die Anzahl
  wird als `max_count` mitgeschrieben. Ein Tracking einzelner Objekte gibt
  es nicht.
- Einträge: `start`, `end`, `label`, `max_score`, `max_count`, `mode`
  (`detect`/`imprint`). Gesten aus `pose` erscheinen als eigene Einträge
  (`gesture: wave`), ebenso Ereignisse des Bodys (`event/rejected`, Stopps
  durch den Ultraschall).
- Speicherung in SQLite unter `/var/lib/picrawler/web/log.db`, Aufbewahrung
  standardmäßig 7 Tage bzw. höchstens 10 000 Einträge.
- **Kein Speichern von Bildern** (passend zu 7.6 Datenschutz). Optional
  einschaltbar: ein kleines Vorschaubild pro Sichtung, standardmäßig aus.

Das Zusammenfassen liegt im web-service und nicht im vision-service, weil
der Arbiter die Rohdaten pro Bild braucht.

### 8.4 Admin-Funktionen

**Bluetooth-Controller**
- Suche starten/stoppen (läuft höchstens 30 s), gefundene Geräte
  live anzeigen, Gamepads anhand der Geräteklasse oben einsortieren.
- Koppeln → vertrauen (`Trusted`, damit sich der Controller später selbst
  wieder verbindet) → verbinden. Für das Koppeln registriert der web-service
  einen BlueZ-Agent mit `NoInputNoOutput`. Das reicht für übliche Gamepads
  (Xbox, PlayStation, 8BitDo).
- Liste gekoppelter Geräte mit Status (verbunden, Akku falls gemeldet),
  Trennen, Entfernen.
- Dazu gehört ein eigener **gamepad-service** (`services/gamepad/`), der den
  verbundenen Controller über `evdev` liest und unter `sensor/gamepad/*`
  veröffentlicht. Wie die Knöpfe auf Befehle abgebildet werden und wie
  Handsteuerung gegenüber Autonomie priorisiert wird, wird in einem
  eigenen Abschnitt festgelegt. Der Ultraschall-Stopp (`safety`) gilt auch
  bei Handsteuerung.

**Vision**: Modus wechseln (`detect`/`pose`/`imprint`/`off`) über
`vision/mode`; Imprinting (Objekt vor die Kamera halten, Namen vergeben,
lernen, gelernte Klassen löschen).

**Dienste**: Status aller `picrawler-*`-Units, Neustart einzelner Dienste,
letzte Log-Zeilen aus dem Journal.

**System**: CPU-Temperatur, Drosselung (`vcgencmd get_throttled`),
Akkuspannung falls verfügbar, Herunterfahren und Neustart (mit
Bestätigungsdialog). Herunterfahren setzt den Crawler vorher hin.

### 8.5 Sicherheit

- **Login** mit Sitzungs-Cookie (`HttpOnly`, `SameSite=Strict`). Passwörter
  als scrypt-Hash in `web.toml`, gesetzt über Ansible Vault.
- **Zwei Rollen:** `viewer` (Video, Log, Not-Aus) und `admin` (zusätzlich
  alles aus 8.4). Der Not-Aus ist für jede angemeldete Person verfügbar.
- Alle ändernden Anfragen nur per POST mit CSRF-Token.
- Nur LAN. HTTPS optional mit eigenem Zertifikat. Ohne HTTPS gehen
  Passwort und Cookie im Klartext durchs WLAN. Das ist im eigenen Netz
  vertretbar, muss aber in der Konfiguration bewusst bestätigt werden (wie
  `mosquitto_allow_plaintext_remote`).
- **Möglichst wenige Rechte:** Der Dienst läuft als eigener Benutzer
  `picrawler-web` und bekommt nur, was er braucht:
  - Gruppe `bluetooth`, damit er BlueZ über D-Bus steuern darf.
  - Eine **polkit-Regel**, die genau Neustart/Status der
    `picrawler-*`-Units sowie Herunterfahren/Neustart des Systems erlaubt.
    Kein `sudo`, keine Shell-Befehle mit Eingaben aus dem Browser.
  - MQTT-Benutzer `ui` (ACL existiert bereits in Ansible).
- Fehlgeschlagene Logins werden gebremst (z. B. 5 Versuche pro Minute
  pro IP).

### 8.6 Konfiguration (`web.toml`)

```toml
[http]
listen = "0.0.0.0:8080"
allow_plaintext = true          # ohne HTTPS bewusst bestätigen
vision_stream = "http://127.0.0.1:8081/stream.mjpg"

[users.admin]
role = "admin"
password_hash = "scrypt$…"      # aus Ansible Vault

[log]
min_score = 0.5
start_frames = 3
end_after_s = 2.0
retention_days = 7
max_entries = 10000
thumbnails = false
```

### 8.7 Tests

- Zusammenfassen zu Sichtungen: Beginn nach 3 Bildern, Ende nach 2 s,
  kurze Aussetzer verlängern die Sichtung, `max_count`/`max_score`.
- Rechte: viewer erreicht keine Admin-Route, POST ohne CSRF-Token wird
  abgelehnt, Not-Aus funktioniert für viewer.
- Bluetooth mit einem Fake-BlueZ-Objekt: Suche, Koppeln, Fehler beim
  Koppeln werden angezeigt statt verschluckt.
- Entwicklung am Mac ohne Pi: `python -m services.web --fake` liefert ein
  Testbild statt des Streams und erzeugt zufällige Erkennungen.

## 9. Konfiguration (`body.toml`)

```toml
robot_id = "1"

[mqtt]
host = "localhost"
port = 1883
username = "body"
password_file = "/etc/picrawler/mqtt.pass"
tls = false

[executor]
queue_max = 8
lockout_s = 1.0

[watchdog]
heartbeat_timeout_s = 1.0
sit_after_disconnect_s = 5.0

[limits]
steps_max = 10
actions = ["stand", "sit", "forward", "backward", "turn left", "turn right", "wave"]
```

## 10. Tests

- **Executor** (ohne Hardware, mit `tests/fake_robot_hat.py`): Reihenfolge,
  Abbruch durch höhere Priorität zwischen Frames, Obergrenze der
  Warteschlange, Sperrzeit, Fehler im Worker, `shutdown` setzt hin.
- **Schema**: alle Ablehnungsgründe aus 4.3.
- **Adapter**: mit einem Fake-MQTT-Client: retained Befehle ignorieren,
  Heartbeat-Timeout, Verbindungsabbruch, Dedupe.
- **Integration** (optional, lokaler Mosquitto): Nachricht an
  `sensor/ultrasonic/distance` mit `cm = 5` → Stopp innerhalb einer
  Frame-Dauer.
- **Am Gerät**: Reaktionszeit eines Stopps bei `speed` 40/70/100 messen und
  hier eintragen.

## 11. Meilensteine

| # | Inhalt | Abnahme |
|---|---|---|
| M1 | `iter_action`, `MotionExecutor` | Bestehende Tests grün, Executor-Tests grün, `do_action` verhält sich unverändert |
| M2 | `picrawler_mqtt` Adapter + Schema + Watchdog | Adapter-Tests grün, Body-Service startet ohne Bewegung, Stopp per MQTT funktioniert am Gerät |
| M3 | Arbiter + Ultraschall-Knoten | Robot läuft vorwärts und stoppt vor einem Hindernis (< 15 cm) |
| M4 | Weitere Sensoren / externe Steuerung | Neuer Sensor ohne Änderung an `picrawler/` angebunden |
| M-V0 | Spike Coral-Software (unabhängig von M1–M3, kann sofort starten) | SSD MobileNet v2 läuft auf dem Ziel-Pi auf der TPU, gemessene Inferenzzeit und gewählter Installationsweg sind hier eingetragen |
| M5 | vision-service: Stream + `detect` + Status | Stream im Browser mit Login, Erkennungen unter `sensor/vision/detections`, Stream läuft weiter, wenn die TPU abgezogen wird |
| M6 | Folge mir | Crawler folgt einer Person, stoppt nach 2 s Verlust und bei Hindernis |
| M7 | `pose`-Gesten + Imprinting | Arm heben → aufstehen, winken → winken, gelerntes Objekt wird gefunden |
| M-W1 | web-service: Login, Live-Seite, Objekt-Log, Not-Aus (mit `--fake` unabhängig von M5 entwickelbar) | Sichtungs-Tests grün, Stream und Log im Browser, Not-Aus stoppt den Crawler, viewer kommt nicht auf Admin-Seiten |
| M-W2 | Admin: Bluetooth, Vision-Modus, Dienste, System + gamepad-service | Controller lässt sich über die Seite koppeln und verbindet sich nach Neustart selbst, Crawler per Gamepad steuerbar, Ultraschall-Stopp greift auch dabei |

## 12. Hardware (entschieden)

- **Raspberry Pi 5, 8 GB.** Kein H.264-Hardware-Encoder, daher MJPEG für
  den Stream (Abschnitt 7).
- **Coral Edge TPU als M.2-Karte über PCIe.** Erfordert 4K-Page-Kernel,
  `dtparam=pciex1`, gasket-Treiber und eine udev-Regel (Gruppe `apex`). Der
  Benutzer `picrawler` braucht die Gruppe `apex`. Einrichtung und Messwerte:
  `spikes/coral/README.md`.
- **Original-Kameramodul des PiCrawler** über das Pi-5-Adapterkabel
  (22-polig auf 15-polig), angesprochen über libcamera/`picamera2`.
- Offen: ob M.2-Board und Robot HAT mechanisch zusammen passen.

## 13. Offene Fragen

- Welche Sensoren laufen auf welchem Gerät? Liegen alle am Pi, reicht anfangs
  ein Event-Bus im Prozess, und MQTT kommt erst mit M2.
- Wo läuft der Broker (Pi selbst oder ein anderer Rechner im Netz)?
- Wie lange dauert ein Frame bei typischer `speed` tatsächlich? Davon hängt
  ab, ob die Reaktionszeit eines Stopps reicht.
- Soll zusätzlich zur eigenen Weboberfläche (Abschnitt 8) Home Assistant
  o. ä. angebunden werden?
- Welcher Controller genau? Davon hängen Tastenbelegung und Kopplung ab.
- Handsteuerung per Gamepad: eigene Priorität `manual` zwischen `goal` und
  `reflex`, oder schaltet sie die Autonomie komplett ab?
- Wie lange dauert ein Modellwechsel (`detect` ↔ `pose`) auf der TPU?
