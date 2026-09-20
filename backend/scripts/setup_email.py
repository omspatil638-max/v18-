"""
Connect ContractLens to your email so alerts and reminders reach your inbox.

Run:  python scripts/setup_email.py

For Gmail you need an APP PASSWORD (not your normal password):
  1. Turn on 2-Step Verification: https://myaccount.google.com/security
  2. Create an app password:      https://myaccount.google.com/apppasswords
  3. Paste the 16 letters here when asked (the input is hidden).

The password is saved only to backend/.env (never committed, never shown again). The script logs in
once to check it works, and sends you a test email.
"""

import getpass
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from envfile import ENV_PATH, set_values  # noqa: E402

PRESETS = {
    "gmail.com": ("smtp.gmail.com", 587), "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587), "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587), "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
}


def main() -> int:
    print("ContractLens email setup\n")
    address = input("Your email address (alerts are sent from and to it): ").strip()
    if "@" not in address:
        print("That does not look like an email address.")
        return 1
    domain = address.split("@", 1)[1].lower()
    host, port = PRESETS.get(domain, (None, 587))
    if host is None:
        host = input(f"SMTP server for {domain} (for example smtp.{domain}): ").strip()
        port = int(input("SMTP port [587]: ").strip() or "587")
    password = getpass.getpass("App password (typing is hidden): ").replace(" ", "")
    if not password:
        print("No password entered.")
        return 1

    print(f"\nChecking the login with {host}:{port} ...")
    try:
        with smtplib.SMTP(host, port, timeout=25) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(address, password)
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = address, address, "ContractLens email is connected"
            msg.set_content("Email alerts are working. ContractLens will send deadline alerts here, and remind you "
                            "again until you mark them as read.\n\nAI-assisted, not legal advice.")
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print("The server rejected the login. For Gmail use an APP PASSWORD, not your normal password. Nothing was saved.")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect ({exc.__class__.__name__}). Check the server name and your internet. Nothing was saved.")
        return 2

    set_values({"SMTP_HOST": host, "SMTP_PORT": str(port), "SMTP_USER": address, "SMTP_PASSWORD": password,
                "SMTP_FROM": address, "SMTP_STARTTLS": "true"})
    print(f"\nWorks. A test email was sent to {address}, and the settings were saved to {ENV_PATH.name}.")
    print("Restart the backend, then open Reminder settings and turn on email alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
