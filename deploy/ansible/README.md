# Provisioning the Pi with Ansible

Sets up a Raspberry Pi (Raspberry Pi OS Bookworm) for the PiCrawler:

| Role | What it does |
|---|---|
| `base` | apt packages, enables I2C (reboots if needed), system user `picrawler` in `i2c`/`gpio` |
| `network` | Wi-Fi hotspot fallback: own Wi-Fi `PiCrawler-1` (10.42.0.1) when no known network is in range, back to the known one when the hotspot is idle |
| `storage` | NVMe as data disk: code, venv, models, logs, journal, Mosquitto data, apt cache and swap move there; `/tmp` in RAM. The system stays on the SD card (no NVMe boot behind the HatDrive! AI switch) |
| `mosquitto` | broker with login only, hashed passwords, ACLs as in `docs/target-architecture.md` |
| `picrawler` | code to `/opt/picrawler/src`, venv, `robot_hat`, configs in `/etc/picrawler`, systemd units + `picrawler.target`, Coral test model, web TLS certificate, polkit rule for the web user |

## One-time setup

```bash
cd deploy/ansible
ansible-galaxy collection install -r requirements.yml
cp inventory/hosts.example.yml inventory/hosts.yml        # enter host/IP
cp inventory/group_vars/picrawler/vault.example.yml inventory/group_vars/picrawler/vault.yml
# set long random passwords, e.g.: openssl rand -base64 24
ansible-vault encrypt inventory/group_vars/picrawler/vault.yml
```

`hosts.yml` and `vault.yml` are excluded via `.gitignore`.

## First run with an empty NVMe

The storage role refuses to format without permission. The first time:

```bash
ansible-playbook site.yml --ask-vault-pass -e storage_allow_format=true
```

It only formats a disk with no partition table and no filesystem; a disk
with data but without the `picrawler-data` label is left alone. Existing
directories on the SD card are copied to the NVMe once. Without NVMe set
`storage_enabled: false`. `/tmp` in RAM takes effect after the next reboot.

## Running

```bash
ansible-playbook site.yml --ask-vault-pass --check --diff   # dry run
ansible-playbook site.yml --ask-vault-pass
```

By default the local checkout is copied with rsync. Alternatively set
`picrawler_source_mode: git` and `picrawler_git_repo`.

## Starting the services

The unit files are installed but only started once
`picrawler_services_start: true` is set. Starting the body initializes the
servos, so test the services by hand first (see `services/README.md`).

```bash
systemctl status 'picrawler-*'
journalctl -u picrawler-body -f
sudo systemctl stop picrawler.target
```

## Important variables

| Variable | Default | Meaning |
|---|---|---|
| `picrawler_services_start` | `false` | enable and start the units |
| `picrawler_sensors` | `[ultrasonic, gamepad]` | sensor instances `picrawler-sensor@<name>` |
| `picrawler_wander_enabled` | `false` | arbiter demo: walk forward, turn away from obstacles |
| `picrawler_web_users` | admin (hash from vault) | web logins, `python3 -m services.web.hashpw` |
| `picrawler_web_tls` | `true` | self-signed HTTPS for the web UI on port 8080 |
| `picrawler_manual_buttons` | SOUTH stand, EAST sit, NORTH wave | gamepad button → action; names shown on the admin page |
| `picrawler_vision_download_models` | `true` | fetch the Coral test model and COCO labels |
| `picrawler_speech_enabled` | `true` | voice stack: builds whisper.cpp, installs Piper and litert-lm-api, runs whisper/agent/voice and the speech sensor |
| `picrawler_agent_model_url` | `""` | `.litertlm` download URL (Gemma 4 E2B); empty = keyword parser only |
| `picrawler_agent_remote_url` | `""` | optional OpenAI-compatible server in the LAN, tried first |
| `picrawler_whisper_model_url` | ggml-base | `ggml-small.bin` for better German, about 3x slower |
| `mosquitto_users` | body, arbiter, sensors, ui | MQTT accounts with role → ACL |
| `mosquitto_listen_address` | `127.0.0.1` | non-local only with `mosquitto_allow_plaintext_remote: true` (no TLS yet) |
| `picrawler_robot_hat_version` | `2.5.x` | pin to a commit SHA for reproducible installs |
| `base_reboot_allowed` | `true` | automatic reboot after enabling I2C |
| `network_hotspot_enabled` | `true` | hotspot fallback; password in `vault_hotspot_psk` (8-63 characters) |
| `network_hotspot_band` / `_channel` | `bg` / `6` | `a` / `36` for 5 GHz (leaves 2.4 GHz to Bluetooth, not every phone supports it) |
| `network_wifi_networks` | `[]` | optional client networks to add (Imager settings are kept) |
| `picrawler_splash_dir` | `/usr/local/lib/picrawler` | boot splash script and picture (on the SD card) |
| `picrawler_display_enabled` | `true` | OLED status display; `picrawler_display_driver` `ssd1306` (0.96") or `sh1106` (most 1.3"), address `0x3C` |
| `picrawler_battery_low_v` / `_critical_v` | `7.0` / `6.6` | 2S Li-ion: warn / sit down and refuse motion |
| `storage_enabled` | `true` | use the NVMe (`storage_device`, default `/dev/nvme0n1`) |
| `storage_allow_format` | `false` | allow formatting an empty NVMe (erases it) |
| `storage_swap_mb` | `2048` | swap file on the NVMe; 0 leaves swap alone |

## Notes

- `robot_hat` 2.5.x can only be imported if `smbus2`, `lgpio`, `pyaudio`
  and `pygame` are present, even without using audio. The compiled packages
  come from apt and are passed into the venv via `--system-site-packages`.
- The web UI runs as `picrawler-web` (groups `bluetooth`, `systemd-journal`,
  `video`). A polkit rule lets it restart `picrawler-*` units and power off,
  nothing else.
- The services run as `picrawler`, not as root. Whether `robot_hat`
  (especially `reset_mcu()`) works without root has not been checked on the
  device yet.
- Calibration values are stored per user in `~/.config/.picrawler.config`,
  so for the services under `/var/lib/picrawler/.config/`. Run the
  calibration as that user:
  `sudo -u picrawler /opt/picrawler/venv/bin/python /opt/picrawler/src/examples/0_calibration.py`
