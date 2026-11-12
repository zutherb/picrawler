# Dienste

Jeder Dienst ist ein eigener Prozess, gestartet mit
`python -m services.<name> --config <datei>.toml`. Auf dem Pi übernimmt das
systemd (Units aus `deploy/ansible`). Hintergrund: `docs/target-architecture.md`.

| Dienst | Aufgabe | MQTT-Benutzer |
|---|---|---|
| `services.body` | einziger Prozess mit Zugriff auf die Servos: Befehle prüfen, `MotionExecutor`, Watchdog | `body` |
| `services.arbiter` | Sensorwerte → Befehle nach Regeln, Heartbeat, optional „Herumlaufen“ | `arbiter` |
| `services.sensor_ultrasonic` | Abstand messen und veröffentlichen | `sensor-ultrasonic` |
| `services.common` | Konfiguration, MQTT-Anbindung, Prüfung der Nachrichten | – |

Die Logik steckt jeweils in einer Klasse ohne Netzwerk (`BodyAdapter`,
`Arbiter`), die `__main__.py` verbindet sie nur mit MQTT. Deshalb laufen alle
Tests ohne Broker und ohne Hardware: `python3 -m unittest discover -s tests -t .`

## Erster Test auf dem Pi (von Hand)

Voraussetzung: Ansible ist einmal durchgelaufen (`picrawler_services_start: false`).

```bash
sudo -u picrawler -s
cd /opt/picrawler/src
V=/opt/picrawler/venv/bin/python

# 1. Body allein: Servos fahren in die Startposition, danach keine Bewegung
$V -m services.body --config /etc/picrawler/body.toml

# 2. In einem zweiten Terminal: Arbiter und Sensor
$V -m services.arbiter --config /etc/picrawler/arbiter.toml
$V -m services.sensor_ultrasonic --config /etc/picrawler/sensor-ultrasonic.toml
```

Mitlesen (Passwort aus `/etc/picrawler/mqtt/arbiter.pass`):

```bash
mosquitto_sub -h 127.0.0.1 -u arbiter -P "$(cat /etc/picrawler/mqtt/arbiter.pass)" -t 'crawler/#' -v
```

Hand vor den Sensor halten: `cmd/stop` mit `"reason": "obstacle"` erscheint.

Herumlaufen ausprobieren: in `/etc/picrawler/arbiter.toml` unter `[wander]`
`enabled = true` setzen und den Arbiter neu starten (dauerhaft über die
Ansible-Variable `picrawler_wander_enabled`). Der Crawler läuft vorwärts,
dreht bei weniger als `clear_cm` ab und stoppt unter 15 cm. **Beim ersten
Mal mit der Hand am Not-Aus:** `Strg+C` im Body-Terminal setzt ihn hin.

Klappt alles, in Ansible `picrawler_services_start: true` setzen.

## Am Mac ohne Pi

Mit einem lokalen Broker (z. B. `brew install mosquitto`, dann
`mosquitto -p 1883`) und Konfigurationen ohne Benutzer:

```bash
printf 'robot_id = "1"\n[mqtt]\nhost = "127.0.0.1"\nport = 1883\n' > /tmp/dev.toml
python3 -m services.body --config /tmp/dev.toml --fake
python3 -m services.sensor_ultrasonic --config /tmp/dev.toml --fake
python3 -m services.arbiter --config arbiter-dev.toml   # mit [[rule]] und [wander]
```

`--fake` ersetzt die Servos durch eine Attrappe bzw. liefert einen künstlichen
Abstand, der zwischen 10 und 70 cm schwankt. Benötigt `paho-mqtt>=2`.

## Sicherheitsverhalten (Kurzfassung)

- Body startet ohne Bewegung und nimmt GOAL-Befehle nur an, solange ein
  Heartbeat (`cmd/heartbeat`, vom Arbiter mit 4 Hz) da ist. Fällt er
  > 1 s aus, stoppt eine laufende GOAL-Bewegung.
- `cmd/stop` wird immer ausgeführt, auch mit falscher Uhrzeit oder
  kaputtem Inhalt. Danach sind niedrigere Prioritäten 1 s gesperrt.
- Verbindung zum Broker weg → Stopp, nach 5 s hinsetzen.
- `SIGTERM`/`Strg+C` → hinsetzen, `status` = `offline`.
