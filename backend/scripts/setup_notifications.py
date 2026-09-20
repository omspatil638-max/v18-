"""
Create the VAPID key pair browser notifications need and store it in backend/.env.

Run once:  python scripts/setup_notifications.py        (idempotent: keeps existing keys)
           python scripts/setup_notifications.py --new   (replace the keys; browsers must re-subscribe)

Nothing secret is printed.
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from py_vapid import Vapid  # noqa: E402

from envfile import ENV_PATH, has_value, set_values  # noqa: E402


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> int:
    if has_value("VAPID_PRIVATE_KEY") and has_value("VAPID_PUBLIC_KEY") and "--new" not in sys.argv:
        print("Browser notification keys already exist in backend/.env. Nothing to do.")
        return 0
    v = Vapid()
    v.generate_keys()
    private = b64url(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    public = b64url(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    set_values({"VAPID_PUBLIC_KEY": public, "VAPID_PRIVATE_KEY": private})
    print(f"Notification keys created and saved to {ENV_PATH.name} (the private key is not shown).")
    print("Restart the backend, then turn on 'Browser notifications' in Reminder settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
