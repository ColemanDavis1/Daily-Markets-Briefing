"""
Email delivery module.

Primary delivery via SendGrid. Falls back to SMTP on failure.
Sends an admin alert if both channels fail.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from config import get_config

logger = logging.getLogger(__name__)
cfg = get_config()


class EmailSender:
    def send(self, *, html_content: str, subject: str) -> dict[str, Any]:
        """
        Attempt SendGrid delivery, fall back to SMTP.

        Returns a delivery result dict with keys: method, success, error.
        """
        recipients = cfg.recipient_emails
        if not recipients:
            logger.error("No recipient emails configured.")
            return {"method": None, "success": False, "error": "No recipients configured."}

        # -- Primary: SendGrid --
        if cfg.sendgrid_api_key:
            try:
                result = self._send_sendgrid(subject, html_content, recipients)
                logger.info("Email delivered via SendGrid to %d recipient(s).", len(recipients))
                return result
            except Exception as exc:
                logger.warning("SendGrid delivery failed: %s. Attempting SMTP fallback.", exc)

        # -- Fallback: SMTP --
        if cfg.smtp_host and cfg.smtp_user and cfg.smtp_password:
            try:
                result = self._send_smtp(subject, html_content, recipients)
                logger.info("Email delivered via SMTP to %d recipient(s).", len(recipients))
                return result
            except Exception as exc:
                logger.error("SMTP delivery also failed: %s", exc)
                self._send_admin_alert(str(exc))
                return {"method": "smtp", "success": False, "error": str(exc)}

        # -- Both unavailable --
        msg = "No delivery method available (missing SendGrid key and SMTP credentials)."
        logger.error(msg)
        self._send_admin_alert(msg)
        return {"method": None, "success": False, "error": msg}

    # ------------------------------------------------------------------
    # SendGrid
    # ------------------------------------------------------------------

    def _send_sendgrid(
        self, subject: str, html: str, recipients: list[str]
    ) -> dict[str, Any]:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        sg = SendGridAPIClient(api_key=cfg.sendgrid_api_key)

        message = Mail(
            from_email=Email(cfg.sender_email, _get_sender_name()),
            subject=subject,
        )
        message.content = [Content("text/html", html)]
        message.to = [To(email=r) for r in recipients]

        response = sg.send(message)

        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"SendGrid returned status {response.status_code}: {response.body}"
            )

        return {
            "method": "sendgrid",
            "success": True,
            "status_code": response.status_code,
            "recipients": recipients,
        }

    # ------------------------------------------------------------------
    # SMTP
    # ------------------------------------------------------------------

    def _send_smtp(
        self, subject: str, html: str, recipients: list[str]
    ) -> dict[str, Any]:
        sender_name = _get_sender_name()
        from_addr = f"{sender_name} <{cfg.sender_email}>"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.sendmail(cfg.sender_email, recipients, msg.as_string())

        return {
            "method": "smtp",
            "success": True,
            "recipients": recipients,
        }

    # ------------------------------------------------------------------
    # Admin alert
    # ------------------------------------------------------------------

    def _send_admin_alert(self, error_detail: str) -> None:
        """Send a plaintext alert to the admin email on pipeline failure."""
        admin = cfg.admin_email
        if not admin:
            logger.warning("No ADMIN_EMAIL set — cannot send failure alert.")
            return

        subject = "[Morning Briefing] Pipeline delivery failure"
        body = (
            f"The morning briefing pipeline failed to deliver today's email.\n\n"
            f"Error:\n{error_detail}\n\n"
            f"Check briefing_log.json for full details."
        )

        try:
            if cfg.sendgrid_api_key:
                from sendgrid import SendGridAPIClient
                from sendgrid.helpers.mail import Mail
                sg = SendGridAPIClient(api_key=cfg.sendgrid_api_key)
                message = Mail(
                    from_email=cfg.sender_email,
                    to_emails=admin,
                    subject=subject,
                    plain_text_content=body,
                )
                sg.send(message)
            elif cfg.smtp_host and cfg.smtp_user and cfg.smtp_password:
                msg = MIMEText(body, "plain")
                msg["Subject"] = subject
                msg["From"] = cfg.sender_email
                msg["To"] = admin
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
                    server.starttls()
                    server.login(cfg.smtp_user, cfg.smtp_password)
                    server.sendmail(cfg.sender_email, [admin], msg.as_string())
            logger.info("Admin failure alert sent to %s.", admin)
        except Exception as exc:
            logger.error("Could not send admin failure alert: %s", exc)


def _get_sender_name() -> str:
    import os
    return os.environ.get("SENDER_NAME", "Morning Briefing")
