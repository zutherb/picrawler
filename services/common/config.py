"""Load a service's TOML config (written by Ansible, see deploy/ansible)."""
import argparse
import tomllib


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def read_password(mqtt_cfg):
    path = mqtt_cfg.get("password_file")
    if not path:
        return mqtt_cfg.get("password")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def topic_prefix(cfg):
    return "crawler/%s/" % cfg.get("robot_id", "1")


def arg_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", required=True, help="path to the service's TOML file")
    parser.add_argument("--fake", action="store_true",
                        help="run without hardware (for development on a laptop)")
    return parser
