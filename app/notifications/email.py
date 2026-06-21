"""Simple SMTP email sender for password reset links.

Configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD in .env to enable real emails.
Without SMTP config the reset token is logged to console (dev-mode only).
"""
import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_reset_email(to_email: str, reset_link: str, settings) -> bool:
    """Send a password reset email. Returns True on success.

    Falls back to logging the link when SMTP is not configured — useful
    in development and when testing the flow without an email provider.
    """
    if not settings.smtp_host or not settings.smtp_user:
        logger.info(
            "SMTP not configured — password reset link for %s: %s",
            to_email, reset_link,
        )
        return False

    try:
        body = (
            f"Hi,\n\n"
            f"Click the link below to reset your AlphaFunds password.\n"
            f"This link expires in 1 hour.\n\n"
            f"{reset_link}\n\n"
            f"If you didn't request this, ignore this email.\n\n"
            f"— AlphaFunds"
        )
        msg = MIMEText(body)
        msg["Subject"] = "Reset your AlphaFunds password"
        msg["From"] = settings.smtp_from_email or settings.smtp_user
        msg["To"] = to_email

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

        logger.info("Password reset email sent to %s", to_email)
        return True
    except Exception as e:
        logger.error("Failed to send reset email to %s: %s", to_email, e)
        return False
