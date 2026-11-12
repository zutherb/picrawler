# Provisioning the Pi with Ansible

Sets up a Raspberry Pi (Raspberry Pi OS Bookworm) for the PiCrawler:

| Role | What it does |
|---|---|
| `base` | apt packages, enables I2C (reboots if needed), system user `picrawler` in `i2c`/`gpio` |
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
| `mosquitto_users` | body, arbiter, sensors, ui | MQTT accounts with role → ACL |
| `mosquitto_listen_address` | `127.0.0.1` | non-local only with `mosquitto_allow_plaintext_remote: true` (no TLS yet) |
| `picrawler_robot_hat_version` | `2.5.x` | pin to a commit SHA for reproducible installs |
| `base_reboot_allowed` | `true` | automatic reboot after enabling I2C |

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
