import os
import re
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment configurations from .env file
load_dotenv()

# Load environment configurations
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", ""))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

def get_verification_html(otp_code: str) -> str:
    """
    Returns a beautifully styled HTML email template for email verification.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Verify Your Email</title>
        <style>
            body {{
                font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #f8fafc;
                margin: 0;
                padding: 0;
                color: #1e293b;
            }}
            .container {{
                max-width: 550px;
                margin: 40px auto;
                background: #ffffff;
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03), 0 10px 30px rgba(0, 0, 0, 0.02);
                border: 1px solid #f1f5f9;
            }}
            .header {{
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 30px;
                border-bottom: 1px solid #f1f5f9;
                padding-bottom: 20px;
            }}
            .logo-text {{
                font-size: 20px;
                font-weight: 900;
                letter-spacing: -0.5px;
                color: #0f172a;
            }}
            .logo-suffix {{
                color: #8ebb96;
            }}
            h2 {{
                font-size: 22px;
                font-weight: 800;
                color: #0f172a;
                margin-top: 0;
                margin-bottom: 12px;
                letter-spacing: -0.3px;
            }}
            p {{
                font-size: 13.5px;
                line-height: 1.6;
                color: #64748b;
                margin-bottom: 24px;
                font-weight: 500;
            }}
            .code-card {{
                background-color: #e5f4e8;
                border: 1px dashed #8ebb96;
                border-radius: 16px;
                padding: 24px;
                text-align: center;
                margin-bottom: 28px;
            }}
            .code-title {{
                font-size: 10px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                color: #30573e;
                margin-bottom: 8px;
            }}
            .otp-code {{
                font-size: 36px;
                font-weight: 900;
                letter-spacing: 6px;
                color: #30573e;
                font-family: monospace;
                margin: 0;
            }}
            .validity {{
                font-size: 11px;
                font-weight: 700;
                color: #64748b;
                margin-top: 10px;
            }}
            .footer {{
                margin-top: 35px;
                border-top: 1px solid #f1f5f9;
                padding-top: 20px;
                text-align: center;
                font-size: 11px;
                color: #94a3b8;
                font-weight: 600;
            }}
            .footer-links {{
                margin-bottom: 8px;
            }}
            .footer-links a {{
                color: #30573e;
                text-decoration: none;
                margin: 0 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <span class="logo-text">Space<span class="logo-suffix">IO</span> CRM</span>
            </div>
            <h2>Verify your email address</h2>
            <p>Thank you for choosing SpaceIO CRM. To complete your registration and activate your account subscription, please use the 6-digit verification code below:</p>
            <div class="code-card">
                <div class="code-title">Verification Code</div>
                <div class="otp-code">{otp_code}</div>
                <div class="validity">Expires in 15 minutes</div>
            </div>
            <p>If you did not initiate this request, you can safely ignore this email. Someone else may have typed your email address by mistake.</p>
            <div class="footer">
                <div class="footer-links">
                    <a href="#">Support</a> &bull; <a href="#">Privacy Policy</a> &bull; <a href="#">Terms of Service</a>
                </div>
                &copy; {1 + 2025} SpaceIO CRM. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

def get_reset_password_html(otp_code: str) -> str:
    """
    Returns a beautifully styled HTML email template for password reset.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Reset Your Password</title>
        <style>
            body {{
                font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #f8fafc;
                margin: 0;
                padding: 0;
                color: #1e293b;
            }}
            .container {{
                max-width: 550px;
                margin: 40px auto;
                background: #ffffff;
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03), 0 10px 30px rgba(0, 0, 0, 0.02);
                border: 1px solid #f1f5f9;
            }}
            .header {{
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 30px;
                border-bottom: 1px solid #f1f5f9;
                padding-bottom: 20px;
            }}
            .logo-text {{
                font-size: 20px;
                font-weight: 900;
                letter-spacing: -0.5px;
                color: #0f172a;
            }}
            .logo-suffix {{
                color: #8ebb96;
            }}
            h2 {{
                font-size: 22px;
                font-weight: 800;
                color: #0f172a;
                margin-top: 0;
                margin-bottom: 12px;
                letter-spacing: -0.3px;
            }}
            p {{
                font-size: 13.5px;
                line-height: 1.6;
                color: #64748b;
                margin-bottom: 24px;
                font-weight: 500;
            }}
            .code-card {{
                background-color: #fef3c7;
                border: 1px dashed #d97706;
                border-radius: 16px;
                padding: 24px;
                text-align: center;
                margin-bottom: 28px;
            }}
            .code-title {{
                font-size: 10px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                color: #78350f;
                margin-bottom: 8px;
            }}
            .otp-code {{
                font-size: 36px;
                font-weight: 900;
                letter-spacing: 6px;
                color: #78350f;
                font-family: monospace;
                margin: 0;
            }}
            .validity {{
                font-size: 11px;
                font-weight: 700;
                color: #64748b;
                margin-top: 10px;
            }}
            .footer {{
                margin-top: 35px;
                border-top: 1px solid #f1f5f9;
                padding-top: 20px;
                text-align: center;
                font-size: 11px;
                color: #94a3b8;
                font-weight: 600;
            }}
            .footer-links {{
                margin-bottom: 8px;
            }}
            .footer-links a {{
                color: #78350f;
                text-decoration: none;
                margin: 0 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <span class="logo-text">Space<span class="logo-suffix">IO</span> CRM</span>
            </div>
            <h2>Password reset request</h2>
            <p>We received a request to reset the password for your SpaceIO CRM account. Please use the 6-digit verification code below to authorize the password reset:</p>
            <div class="code-card">
                <div class="code-title">Reset Code</div>
                <div class="otp-code">{otp_code}</div>
                <div class="validity">Expires in 15 minutes</div>
            </div>
            <p>If you did not request a password reset, you can safely ignore this email. Your password remains secure and will not change unless you finalize this verification step.</p>
            <div class="footer">
                <div class="footer-links">
                    <a href="#">Support</a> &bull; <a href="#">Privacy Policy</a> &bull; <a href="#">Terms of Service</a>
                </div>
                &copy; {1 + 2025} SpaceIO CRM. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

async def send_email(to_email: str, subject: str, body_html: str):
    """
    Sends an email using the configured SMTP server.
    If no SMTP_PASSWORD is set, it outputs the email content to stdout for testing.
    """
    if not SMTP_PASSWORD:
        msg_parts = [
            "=" * 70,
            "✉️ [SMTP SIMULATOR] Outgoing Email Triggered:",
            f"   To:      {to_email}",
            f"   From:    {SMTP_FROM}",
            f"   Subject: {subject}"
        ]
        # Extract code from template if possible for clear visibility
        otp_match = re.search(r'class="otp-code"[^>]*>\s*(\d{6})\s*<', body_html)
        if otp_match:
            msg_parts.append(f"\n   👉 DEVELOPMENT OTP CODE: {otp_match.group(1)} 👈\n")
        msg_parts.append(f"   HTML Body Preview (150 chars): {body_html.strip()[:150]}...")
        msg_parts.append("=" * 70)
        logger.info("\n".join(msg_parts))
        return

    # Create message container
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_FROM
    msg['To'] = to_email

    # Attach HTML content
    msg.attach(MIMEText(body_html, 'html'))

    try:
        # Check port to choose connection method (465 SSL vs 587 StartTLS)
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent email to {to_email} via SMTP.")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email} via SMTP: {e}")
        # Fall back to printing to logs so system does not completely crash
        msg_parts = [
            "=" * 70,
            "✉️ [SMTP FALLBACK] Outgoing Email Details due to Send Error:",
            f"   To:      {to_email}",
            f"   Subject: {subject}"
        ]
        otp_match = re.search(r'class="otp-code"[^>]*>\s*(\d{6})\s*<', body_html)
        if otp_match:
            msg_parts.append(f"\n   👉 DEVELOPMENT OTP CODE: {otp_match.group(1)} 👈\n")
        msg_parts.append("=" * 70)
        logger.warning("\n".join(msg_parts))
