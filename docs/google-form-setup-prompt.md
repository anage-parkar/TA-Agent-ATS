# Prompt for Claude Web — set up my Google Form → TA Agent sync

Copy everything in the block below and paste it into a new claude.ai chat. It
walks you (in the browser) from creating the Google Form through to the
service-account JSON the TA Agent backend needs.

---

You are helping me connect a Google Form to my local "TA Agent" app so that
candidate applications submitted through the form sync into my backend
(FastAPI + Supabase). The backend reads the form's responses via the **Google
Forms API** using a **service account**. Walk me through every step in the
browser. After each step that needs me to click or copy something, tell me
exactly what to do and **wait for me to confirm** before continuing.

Context you should keep in mind:
- I may already have a form, or we create one. Either way, capture its link
  (the `https://docs.google.com/forms/d/<ID>/edit` URL). I paste that **link**
  into the TA Agent UI per job when I sync — the form ID is **not** hardcoded
  anywhere; the app extracts the ID from the pasted link.
- My backend auto-maps response columns by question title. To get clean mapping,
  the form's questions should be titled **exactly** like this (short-answer text
  unless noted):
  1. `Full Name`            (required)
  2. `Email Address`
  3. `Phone`
  4. `LinkedIn URL`
  5. `Current Title`
  6. `Location`
  7. `Key Skills`           (comma-separated, e.g. "Python, FastAPI, AWS")
  8. `Years of Experience`  (number)
  9. `Resume Link`          (short answer — a Google Drive "anyone with link"
     URL or any public resume URL. Avoid the File-Upload question type: it
     forces Google sign-in and the API returns a file id, not a usable link.)
  Any other questions are fine — they're stored as extra data, not mapped.

Please take me through these phases:

**Phase A — Finalise the Form**
1. Open my existing form at https://docs.google.com/forms — or create a new one.
   Either way, have me copy its edit-URL (I'll paste this link into the TA Agent
   UI later; nothing gets hardcoded).
2. Help me add/rename the 9 questions exactly as titled above.
3. Turn ON "Collect responses" and have me submit one test response so there's
   data to sync.

**Phase B — Google Cloud project + Forms API**
4. Go to https://console.cloud.google.com — create a new project (e.g.
   "ta-agent") or select an existing one. Tell me how to confirm which project
   is selected.
5. Enable the **Google Forms API**: APIs & Services → Library → search
   "Google Forms API" → Enable.

**Phase C — Service account + key**
6. APIs & Services → Credentials → Create credentials → **Service account**.
   Name it `ta-agent-forms`. Skip the optional role grants.
7. Open the new service account → **Keys** tab → Add key → Create new key →
   **JSON** → download. Tell me to save it somewhere safe, e.g.
   `C:\Users\abhishek.nage\TA Agent\secrets\ta-agent-forms.json`.
8. Copy the service account's **client_email** (looks like
   `ta-agent-forms@<project>.iam.gserviceaccount.com`) — I'll need it next.

**Phase D — Share the Form with the service account**
9. Back in the Form (top-right ⋮ / Add collaborators), **share the form with the
   service-account client_email** as a collaborator (Editor/Viewer). This is what
   lets the API read its responses. Confirm it's added.

**Phase E — Point the backend at it**
10. Tell me to set ONLY the service-account path in my repo's root `.env` file
    (`C:\Users\abhishek.nage\TA Agent\.env`):
    ```
    GOOGLE_SHEETS_SA_FILE=C:\Users\abhishek.nage\TA Agent\secrets\ta-agent-forms.json
    ```
    The form ID is NOT set here — I paste the form's link into the TA Agent UI
    each time I sync (the app extracts the ID). `GOOGLE_FORM_ID` stays blank
    (it's only an optional fallback).
11. Remind me the `secrets/` folder must stay out of git (it's gitignored).

When all phases are done, I'll restart my backend, open a job's Candidates page
in the TA Agent UI, paste my Google Form link into the "Microsoft / Google
Forms" section, and click "Sync form". If it errors, I'll paste the error and you
help me debug (common ones: Forms API not enabled, form not shared with the
service account, or the JSON path wrong).
