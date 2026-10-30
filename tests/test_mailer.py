"""Emailing a report, against a fake SMTP server. Nothing here touches the network."""

from __future__ import annotations

import smtplib

import pytest

from lab_intake import mailer
from lab_intake.mailer import MailError, MailSettings, load_env_file, send_report


class FakeSMTP:
    """Records what the mailer does instead of talking to a server."""

    instances: list["FakeSMTP"] = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


SETTINGS = MailSettings(host="mail.test", sender="lab@clinic.test", port=2525, starttls=False)


def test_the_report_goes_out_as_plain_text_with_the_headers_set(fake_smtp):
    send_report("nurse@clinic.test", "Lab result check 3: 2 flags", "OUT_OF_RANGE (2)\n", SETTINGS)

    (smtp,) = fake_smtp.instances
    assert (smtp.host, smtp.port) == ("mail.test", 2525)
    (message,) = smtp.sent
    assert message["From"] == "lab@clinic.test"
    assert message["To"] == "nurse@clinic.test"
    assert message["Subject"] == "Lab result check 3: 2 flags"
    assert message.get_content() == "OUT_OF_RANGE (2)\n"
    assert smtp.calls == [("quit",)]  # no TLS, no login asked for


def test_tls_and_login_happen_before_sending_when_configured(fake_smtp):
    settings = MailSettings(host="mail.test", sender="lab@clinic.test", user="lab", password="pw")

    send_report("nurse@clinic.test", "s", "body", settings)

    (smtp,) = fake_smtp.instances
    assert smtp.calls == [("starttls",), ("login", "lab", "pw"), ("quit",)]


def test_a_server_failure_becomes_a_mail_error_naming_the_server(fake_smtp):
    fake_smtp.fail_with = smtplib.SMTPConnectError(421, "busy")

    with pytest.raises(MailError, match="mail.test:2525"):
        send_report("nurse@clinic.test", "s", "body", SETTINGS)


def test_settings_come_from_the_environment():
    env = {
        "LAB_SMTP_HOST": "mail.test",
        "LAB_SMTP_PORT": "2525",
        "LAB_SMTP_USER": "lab",
        "LAB_SMTP_PASSWORD": "pw",
        "LAB_SMTP_STARTTLS": "0",
        "LAB_MAIL_FROM": "lab@clinic.test",
    }

    settings = MailSettings.from_env(env)

    assert settings == MailSettings("mail.test", "lab@clinic.test", 2525, "lab", "pw", starttls=False)


def test_missing_host_or_sender_is_an_error_not_a_default():
    with pytest.raises(MailError, match="LAB_SMTP_HOST and LAB_MAIL_FROM"):
        MailSettings.from_env({"LAB_SMTP_HOST": "mail.test"})


def test_env_file_fills_in_only_what_the_environment_lacks(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# mail settings\n"
        "LAB_SMTP_HOST=mail.test\n"
        'LAB_MAIL_FROM="lab@clinic.test"\n'
        "LAB_SMTP_PORT = 2525\n"
        "\n"
        "not a setting\n"
    )
    env = {"LAB_SMTP_HOST": "already.set"}

    load_env_file(env_file, env)

    assert env == {
        "LAB_SMTP_HOST": "already.set",
        "LAB_MAIL_FROM": "lab@clinic.test",
        "LAB_SMTP_PORT": "2525",
    }


def test_a_missing_env_file_is_fine(tmp_path):
    env: dict[str, str] = {}
    load_env_file(tmp_path / "nowhere.env", env)
    assert env == {}
