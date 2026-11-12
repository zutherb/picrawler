"""Print a scrypt hash for web.toml / the Ansible vault.

    python -m services.web.hashpw
"""
import getpass
import sys

from .auth import hash_password

MIN_LENGTH = 10


def main():
    password = getpass.getpass("Password: ")
    if len(password) < MIN_LENGTH:
        sys.exit("Use at least %d characters." % MIN_LENGTH)
    if getpass.getpass("Repeat: ") != password:
        sys.exit("Passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
