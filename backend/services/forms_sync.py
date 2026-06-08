"""Google Forms sync via the linked Google Sheet.

A Google Form writes responses to a Google Sheet; we read that sheet (Sheets
API v4, service-account auth) and map each row to an applicant.

Setup (real mode):
  1. Create a service account, download its JSON key.
  2. Share the responses Google Sheet with the service account's client_email
     (Viewer is enough).
  3. Set GOOGLE_SHEETS_SA_FILE to the JSON path and GOOGLE_FORMS_SHEET_ID to the
     sheet id (the long id in the sheet URL).

If GOOGLE_SHEETS_SA_FILE is unset, built-in mock responses are returned so the
channel works end-to-end today.

Column mapping is header-based and forgiving: we match header names
case-insensitively against known aliases (e.g. "Email Address" -> email).
"""

from __future__ import annotations

import logging
import re

from models.candidate import ApplicantSubmission
from services.config import settings

logger = logging.getLogger("ta_agent.forms_sync")

# header alias -> applicant field
_HEADER_ALIASES = {
    "name": "full_name",
    "full name": "full_name",
    "your name": "full_name",
    "email": "email",
    "email address": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile": "phone",
    "linkedin": "linkedin_url",
    "linkedin url": "linkedin_url",
    "linkedin profile": "linkedin_url",
    "headline": "headline",
    "current title": "headline",
    "current role": "headline",
    "location": "location",
    "city": "location",
    "skills": "skills",
    "key skills": "skills",
    "experience": "experience_years",
    "years of experience": "experience_years",
    "experience (years)": "experience_years",
    "resume": "resume_url",
    "resume link": "resume_url",
    "cv": "resume_url",
}

_MOCK_RESPONSES = [
    {
        "Full Name": "Vikram Singh",
        "Email Address": "vikram.singh@example.com",
        "Phone": "+91-98200-11223",
        "LinkedIn URL": "https://linkedin.com/in/vikram-singh-form",
        "Current Title": "Senior Backend Engineer",
        "Location": "Mumbai, India",
        "Key Skills": "Python, FastAPI, PostgreSQL, AWS, Redis",
        "Years of Experience": "8",
        "Resume Link": "https://example.com/resumes/vikram.pdf",
    },
    {
        "Full Name": "Aisha Khan",
        "Email Address": "aisha.khan@example.com",
        "Phone": "+91-99300-44556",
        "LinkedIn URL": "https://linkedin.com/in/aisha-khan-form",
        "Current Title": "Backend Engineer",
        "Location": "Remote, India",
        "Key Skills": "Python, Django, PostgreSQL, Docker",
        "Years of Experience": "5",
        "Resume Link": "https://example.com/resumes/aisha.pdf",
    },
    {
        "Full Name": "Tom Becker",
        "Email Address": "tom.becker@example.com",
        "Phone": "+49-151-0000",
        "LinkedIn URL": "https://linkedin.com/in/tom-becker-form",
        "Current Title": "Junior Developer",
        "Location": "Munich, Germany",
        "Key Skills": "Java, Spring, MySQL",
        "Years of Experience": "2",
        "Resume Link": "",
    },
]


def _use_mock() -> bool:
    return not settings.google_sheets_sa_file


class FormsRefError(ValueError):
    """The pasted form reference is a response/share link, not the edit-link ID."""


_RESPONDER_HINT = (
    "That's the form's public *response* link (it contains /forms/d/e/… or starts "
    "with 1FAIpQLS), which the Google Forms API can't use. Open the form in edit "
    "mode and copy the URL that looks like "
    "https://docs.google.com/forms/d/<FORM_ID>/edit, then paste that."
)


def extract_form_id(value: str | None) -> str | None:
    """Accept a Google Form **edit** URL or a bare form ID; return the ID.

    Rejects the public responder link (/forms/d/e/<id>/viewform), because that
    `1FAIpQLS…` id is NOT the form id and the API returns 404 for it.
    """
    if not value:
        return None
    value = value.strip()

    # Public responder link → not API-usable.
    if "/forms/d/e/" in value:
        raise FormsRefError(_RESPONDER_HINT)

    m = re.search(r"/forms/d/([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)

    # Bare ID (no slashes / spaces)
    if "/" not in value and " " not in value:
        if value.startswith("1FAIpQLS"):
            raise FormsRefError(_RESPONDER_HINT)
        return value
    return None


def _split_skills(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in re.split(r"[,\n;/]", raw) if s.strip()]


def _to_int(raw) -> int | None:
    if raw is None:
        return None
    m = re.search(r"\d+", str(raw))
    return int(m.group()) if m else None


def _row_to_applicant(row: dict) -> ApplicantSubmission | None:
    """Map a header->value dict (one response) to an ApplicantSubmission."""
    mapped: dict = {}
    extra: dict = {}
    for header, value in row.items():
        field = _HEADER_ALIASES.get(str(header).strip().lower())
        if field == "skills":
            mapped["skills"] = _split_skills(value)
        elif field == "experience_years":
            mapped["experience_years"] = _to_int(value)
        elif field:
            mapped[field] = value or None
        else:
            extra[header] = value
    if not mapped.get("full_name"):
        return None
    mapped["extra"] = extra
    return ApplicantSubmission.model_validate(mapped)


def _google_creds(scopes: list[str]):
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_file(
        settings.google_sheets_sa_file, scopes=scopes
    )


def _fetch_rows_forms_api(form_id: str) -> list[dict]:
    """Read responses directly from a Google Form via the Forms API.

    Maps each response to a {question_title: answer} dict. Share the form with
    the service-account client_email (Viewer) and enable the Forms API.
    """
    from googleapiclient.discovery import build

    creds = _google_creds(
        [
            "https://www.googleapis.com/auth/forms.body.readonly",
            "https://www.googleapis.com/auth/forms.responses.readonly",
        ]
    )
    service = build("forms", "v1", credentials=creds, cache_discovery=False)

    form = service.forms().get(formId=form_id).execute()
    # questionId -> question title
    titles: dict[str, str] = {}
    for item in form.get("items", []):
        q = item.get("questionItem", {}).get("question")
        if q and q.get("questionId"):
            titles[q["questionId"]] = item.get("title", q["questionId"])

    result = service.forms().responses().list(formId=form_id).execute()
    rows: list[dict] = []
    for r in result.get("responses", []):
        row: dict = {}
        for qid, ans in r.get("answers", {}).items():
            title = titles.get(qid, qid)
            # File-upload questions (e.g. a resume) return Drive file ids — turn
            # them into shareable Drive view links so the recruiter can open them.
            file_ans = ans.get("fileUploadAnswers", {}).get("answers", [])
            if file_ans:
                links = [
                    f"https://drive.google.com/file/d/{f.get('fileId')}/view"
                    for f in file_ans
                    if f.get("fileId")
                ]
                row[title] = ", ".join(links)
            else:
                vals = ans.get("textAnswers", {}).get("answers", [])
                row[title] = ", ".join(a.get("value", "") for a in vals)
        rows.append(row)
    return rows


def _fetch_rows_live(sheet_id: str, sheet_range: str, tab: str | None) -> list[dict]:
    from googleapiclient.discovery import build

    creds = _google_creds(["https://www.googleapis.com/auth/spreadsheets.readonly"])
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    rng = f"{tab}!{sheet_range}" if tab else sheet_range
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=rng)
        .execute()
    )
    values = resp.get("values", [])
    if not values:
        return []
    headers = values[0]
    return [dict(zip(headers, row)) for row in values[1:]]


def fetch_form_responses(
    form_id: str | None = None,
    sheet_id: str | None = None,
    sheet_range: str = "A1:Z1000",
    tab: str | None = None,
) -> list[ApplicantSubmission]:
    """Return applicants parsed from a Google Form (Forms API) or its Sheet.

    Precedence: explicit form_id/sheet_id args → env defaults. With no service
    account configured, returns built-in mock responses.
    """
    if _use_mock():
        logger.info("Forms sync (mock): %d canned responses", len(_MOCK_RESPONSES))
        rows = _MOCK_RESPONSES
    else:
        fid = extract_form_id(form_id) or extract_form_id(settings.google_form_id)
        sid = sheet_id or settings.google_forms_sheet_id
        if fid:
            rows = _fetch_rows_forms_api(fid)
            logger.info("Forms sync (Forms API): %d responses from form %s", len(rows), fid)
        elif sid:
            rows = _fetch_rows_live(sid, sheet_range, tab)
            logger.info("Forms sync (Sheets): %d rows from sheet %s", len(rows), sid)
        else:
            raise RuntimeError(
                "Set GOOGLE_FORM_ID (Forms API) or GOOGLE_FORMS_SHEET_ID (Sheets), "
                "or pass form_id/sheet_id."
            )

    applicants = [a for a in (_row_to_applicant(r) for r in rows) if a]
    return applicants
