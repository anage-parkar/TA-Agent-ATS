"""Modern, responsive HTML wrapper for outbound emails.

The recruiter edits plain message text; at send time we wrap it in a branded,
professional HTML shell (header + typography + footer). All CSS is inline so it
renders consistently across email clients (Gmail, Outlook, Apple Mail).
"""

from __future__ import annotations

import html as _html

from services.config import settings

_ACCENT = "#4f46e5"  # indigo-600
_ACCENT_SOFT = "#eef2ff"


def _to_paragraphs(text: str) -> str:
    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
    out = []
    for b in blocks:
        safe = _html.escape(b).replace("\n", "<br>")
        out.append(f'<p style="margin:0 0 16px;">{safe}</p>')
    return "".join(out) or "<p></p>"


def render_html(body: str, decision: str = "proceed") -> str:
    """Wrap the message body in a branded, responsive HTML email."""
    company = _html.escape(settings.company_name or "Recruiting")
    website = settings.company_website or ""
    paragraphs = _to_paragraphs(body)
    tag = "You're moving forward" if decision == "proceed" else "Application update"

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f1f5f9;-webkit-font-smoothing:antialiased;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:28px 12px;">
      <tr><td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;
                      border:1px solid #e2e8f0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
          <!-- Header -->
          <tr>
            <td style="background:{_ACCENT};padding:22px 32px;">
              <table role="presentation" width="100%"><tr>
                <td style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:.2px;">{company}</td>
                <td align="right" style="color:#c7d2fe;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Talent Acquisition</td>
              </tr></table>
            </td>
          </tr>
          <!-- Tag strip -->
          <tr>
            <td style="background:{_ACCENT_SOFT};padding:10px 32px;color:{_ACCENT};font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">
              {tag}
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:30px 32px 8px;color:#0f172a;font-size:15px;line-height:1.65;">
              {paragraphs}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:8px 32px 26px;">
              <div style="border-top:1px solid #eef2f6;padding-top:16px;color:#94a3b8;font-size:12px;line-height:1.6;">
                Sent by the {company} Talent Acquisition team{f' · <a href="{website}" style="color:#94a3b8;">{_html.escape(website)}</a>' if website else ''}.<br>
                If you'd prefer not to receive these emails, reply with &ldquo;unsubscribe&rdquo;.
              </div>
            </td>
          </tr>
        </table>
        <div style="color:#cbd5e1;font-size:11px;padding-top:14px;">&copy; {company}</div>
      </td></tr>
    </table>
  </body>
</html>"""


def plain_fallback(body: str) -> str:
    """Plain-text version (for clients that don't render HTML) + opt-out line."""
    company = settings.company_name or "Recruiting"
    return f'{body}\n\n—\n{company} Talent Acquisition. Reply "unsubscribe" to opt out.'
