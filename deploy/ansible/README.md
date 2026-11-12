# Pi provisionieren mit Ansible

Richtet einen Raspberry Pi (Raspberry Pi OS Bookworm) für den PiCrawler ein:

| Rolle | Was sie macht |
|---|---|
| `base` | apt-Pakete, I2C aktivieren (ggf. Reboot), Systembenutzer `picrawler` in `i2c`/`gpio` |
| `mosquitto` | Broker nur mit Login, Passwörter gehasht, ACLs nach `docs/target-architecture.md` |
| `picrawler` | Code nach `/opt/picrawler/src`, venv, `robot_hat`, Configs in `/etc/picrawler`, systemd-Units + `picrawler.target` |

## Einmalig

```bash
cd deploy/ansible
ansible-galaxy collection install -r requirements.yml
cp inventory/hosts.example.yml inventory/hosts.yml        # Host/IP eintragen
cp inventory/group_vars/picrawler/vault.example.yml inventory/group_vars/picrawler/vault.yml
# lange Zufallspasswörter setzen, z. B.: openssl rand -base64 24
ansible-vault encrypt inventory/group_vars/picrawler/vault.yml
```

`hosts.yml` und `vault.yml` sind per `.gitignore` ausgeschlossen.

## Ausführen

```bash
ansible-playbook site.yml --ask-vault-pass --check --diff   # Trockenlauf
ansible-playbook site.yml --ask-vault-pass
```

Standardmäßig wird der lokale Checkout per rsync übertragen. Alternativ
`picrawler_source_mode: git` und `picrawler_git_repo` setzen.

## Services starten

Die Unit-Dateien werden installiert, aber erst gestartet, wenn
`picrawler_services_start: true` gesetzt ist. Den Service-Code
(`services/body`, `services/arbiter`, `services/sensor_<name>`) gibt es noch
nicht (Meilensteine M1–M3).

```bash
systemctl status 'picrawler-*'
journalctl -u picrawler-body -f
sudo systemctl stop picrawler.target
```

## Wichtige Variablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `picrawler_services_start` | `false` | Units aktivieren und starten |
| `picrawler_sensors` | `[ultrasonic]` | Sensor-Instanzen `picrawler-sensor@<name>` |
| `mosquitto_users` | body, arbiter, sensor-ultrasonic | MQTT-Konten mit Rolle → ACL |
| `mosquitto_listen_address` | `127.0.0.1` | Nicht-lokal nur mit `mosquitto_allow_plaintext_remote: true` (TLS fehlt noch) |
| `picrawler_robot_hat_version` | `2.5.x` | Für reproduzierbare Installationen auf einen Commit-SHA pinnen |
| `base_reboot_allowed` | `true` | Automatischer Reboot nach dem Aktivieren von I2C |

## Hinweise

- `robot_hat` 2.5.x lässt sich nur importieren, wenn `smbus2`, `lgpio`,
  `pyaudio` und `pygame` vorhanden sind, auch ohne Audio-Nutzung. Die
  kompilierten Pakete kommen per apt und werden über `--system-site-packages`
  in das venv durchgereicht.
- Die Services laufen als `picrawler`, nicht als root. Ob `robot_hat`
  (v. a. `reset_mcu()`) ohne root funktioniert, ist auf dem Gerät noch nicht
  geprüft.
- Kalibrierwerte liegen pro Benutzer in `~/.config/.picrawler.config`. Für die
  Services also unter `/var/lib/picrawler/.config/`. Kalibrierung deshalb als
  dieser Benutzer ausführen:
  `sudo -u picrawler /opt/picrawler/venv/bin/python /opt/picrawler/src/examples/0_calibration.py`
