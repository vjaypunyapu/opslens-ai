"""
OpsLens AI — Email Service (Resend)
=====================================
Handles all transactional email via Resend's REST API.

Configured via:
    RESEND_API_KEY   — Resend API key (get one at resend.com)
    APP_URL          — Full frontend URL, e.g. https://app.opslensai.com
    INVITE_FROM_EMAIL — Sender address, e.g. hello@opslensai.com

If RESEND_API_KEY is not set, email calls are logged and silently skipped
so development environments don't break.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_RESEND_SEND_URL = "https://api.resend.com/emails"


async def _send(*, to: str, subject: str, html: str) -> bool:
    """
    Send a single transactional email via Resend.
    Returns True on success, False on failure (never raises).
    """
    api_key = settings.RESEND_API_KEY
    if not api_key:
        logger.warning(
            "RESEND_API_KEY not set — skipping email to %s (subject: %s)", to, subject
        )
        return False

    payload: dict[str, Any] = {
        "from": settings.INVITE_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "html": html,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _RESEND_SEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info("Email sent to %s — id=%s", to, data.get("id"))
                return True
            else:
                logger.error(
                    "Resend error %s sending to %s: %s",
                    resp.status_code, to, resp.text[:300],
                )
                return False
    except Exception as exc:
        logger.error("Email send failed for %s: %s", to, exc)
        return False


# ── Email templates ───────────────────────────────────────────────────────────

def _base_template(content: str) -> str:
    """Wrap content in the OpsLens branded email shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OpsLens AI</title>
</head>
<body style="margin:0;padding:0;background:#0a1628;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a1628;padding:48px 16px;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">

          <!-- Logo / wordmark -->
          <tr>
            <td align="center" style="padding-bottom:32px;">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:rgba(20,184,166,0.15);border:1px solid rgba(20,184,166,0.3);
                             border-radius:12px;padding:10px 20px;">
                    <span style="font-size:18px;font-weight:700;color:#2dd4bf;letter-spacing:-0.3px;">
                      OpsLens
                    </span>
                    <span style="font-size:18px;font-weight:400;color:#94a3b8;margin-left:2px;">AI</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Card -->
          <tr>
            <td style="background:#0f1f38;border:1px solid rgba(255,255,255,0.08);
                       border-radius:16px;padding:40px 40px 32px;box-shadow:0 4px 24px rgba(0,0,0,0.4);">
              {content}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding-top:28px;">
              <p style="margin:0;font-size:12px;color:#334155;line-height:1.6;">
                You're receiving this because someone invited you to OpsLens AI.<br/>
                If you didn't expect this, you can safely ignore it.
              </p>
              <p style="margin:12px 0 0;font-size:11px;color:#1e293b;">
                © OpsLens AI · <a href="https://opslensai.com" style="color:#334155;text-decoration:none;">opslensai.com</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


async def send_invite_email(
    *,
    to_email: str,
    invited_by_name: str,
    workspace_name: str,
    role: str,
    invite_url: str,
    expires_hours: int = 72,
) -> bool:
    """
    Send a workspace invite email to a new user.

    Args:
        to_email:        Recipient email address.
        invited_by_name: Display name / email of the person who sent the invite.
        workspace_name:  The tenant's workspace name shown in the email.
        role:            Role being granted (admin / member / viewer).
        invite_url:      Full sign-up URL with token, e.g.
                         https://app.opslensai.com/sign-up?token=<uuid>
        expires_hours:   How many hours until the link expires (shown in email).
    """
    role_label = role.capitalize()
    expires_label = (
        f"{expires_hours // 24} days" if expires_hours >= 48
        else f"{expires_hours} hours"
    )

    content = f"""
      <!-- Heading -->
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#f1f5f9;line-height:1.3;">
        You're invited to join<br/>
        <span style="color:#2dd4bf;">{workspace_name}</span>
      </h1>
      <p style="margin:0 0 28px;font-size:14px;color:#64748b;line-height:1.6;">
        <strong style="color:#94a3b8;">{invited_by_name}</strong> has invited you to
        collaborate on <strong style="color:#94a3b8;">{workspace_name}</strong>
        on OpsLens AI — your team's operational intelligence copilot.
      </p>

      <!-- Role badge -->
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
        <tr>
          <td style="background:rgba(20,184,166,0.1);border:1px solid rgba(20,184,166,0.25);
                     border-radius:8px;padding:10px 16px;">
            <span style="font-size:12px;color:#64748b;display:block;margin-bottom:2px;">
              YOUR ROLE
            </span>
            <span style="font-size:15px;font-weight:600;color:#2dd4bf;">
              {role_label}
            </span>
          </td>
        </tr>
      </table>

      <!-- CTA button -->
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
        <tr>
          <td align="center" style="background:linear-gradient(135deg,#0d9488,#0891b2);
                                    border-radius:10px;">
            <a href="{invite_url}"
               style="display:inline-block;padding:14px 36px;font-size:15px;font-weight:600;
                      color:#ffffff;text-decoration:none;letter-spacing:0.1px;">
              Accept Invitation →
            </a>
          </td>
        </tr>
      </table>

      <!-- Expiry notice -->
      <p style="margin:0 0 24px;font-size:12px;color:#475569;line-height:1.5;">
        ⏱ This invitation expires in <strong style="color:#64748b;">{expires_label}</strong>.
        After that you'll need to ask {invited_by_name} to send a new one.
      </p>

      <!-- Divider -->
      <hr style="border:none;border-top:1px solid rgba(255,255,255,0.06);margin:0 0 20px;" />

      <!-- Link fallback -->
      <p style="margin:0;font-size:11px;color:#334155;line-height:1.7;">
        If the button doesn't work, copy and paste this link into your browser:<br/>
        <a href="{invite_url}"
           style="color:#0e7490;word-break:break-all;text-decoration:none;">
          {invite_url}
        </a>
      </p>
    """

    return await _send(
        to=to_email,
        subject=f"You've been invited to {workspace_name} on OpsLens AI",
        html=_base_template(content),
    )
