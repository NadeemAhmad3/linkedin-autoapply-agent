"""Easy-Apply automation with REVIEW-AND-CONFIRM.

Self-contained Easy-Apply engine. It deliberately **never clicks
"Submit application"**. It fills the form, clicks through the multi-step flow to
the review/final step, then hands off to the human — you review in the real
LinkedIn window and click Submit yourself. We then detect that the application
was sent and report success.

Runs in a background thread (one at a time) using a persistent browser profile
so your LinkedIn login persists between runs.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from .humanize import human_click, random_sleep, wait_for_page_full_load

logger = logging.getLogger(__name__)

# Timeouts (seconds)
LOGIN_TIMEOUT = 300        # 5 min to complete manual login
REVIEW_TIMEOUT = 600       # 10 min to review + click submit
MAX_STEPS = 10             # safety cap on multi-step forms

# Easy-Apply selectors (LinkedIn changes these often — adjust if they break).
EASY_APPLY_BTN = ".jobs-apply-button--top-card #jobs-apply-button-id"
EASY_APPLY_BTN_ALT = "button.jobs-apply-button"
MODAL = "div.jobs-easy-apply-modal"


class ApplySession:
    def __init__(self, session_id: str, job_url: str, profile: dict[str, Any]):
        self.id = session_id
        self.job_url = job_url
        self.profile = profile
        # queued -> launching -> needs_login -> filling -> awaiting_review ->
        # submitted | failed | cancelled | timed_out
        self.status = "queued"
        self.message = ""
        self.created_at = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "status": self.status,
            "message": self.message,
            "job_url": self.job_url,
            "elapsed_sec": round(time.time() - self.created_at, 1),
        }


# In-memory registry (local single-user tool). One active session at a time.
_sessions: dict[str, ApplySession] = {}
_lock = threading.Lock()


def _active_session() -> ApplySession | None:
    """Return the currently non-terminal session, if any."""
    terminal = {"submitted", "failed", "cancelled", "timed_out"}
    with _lock:
        for s in _sessions.values():
            if s.status not in terminal:
                return s
    return None


def start_apply(job_url: str, profile: dict[str, Any], user_data_dir: str) -> ApplySession:
    active = _active_session()
    if active:
        raise RuntimeError(
            f"An apply session is already running ({active.id}, status={active.status}). "
            "Cancel it first."
        )
    sid = uuid.uuid4().hex[:12]
    session = ApplySession(sid, job_url, profile)
    with _lock:
        _sessions[sid] = session
    session._thread = threading.Thread(target=_run, args=(session, user_data_dir), daemon=True)
    session._thread.start()
    return session


def cancel_apply(session_id: str) -> bool:
    with _lock:
        s = _sessions.get(session_id)
    if not s:
        return False
    s._stop.set()
    return True


def get_session(session_id: str) -> ApplySession | None:
    with _lock:
        return _sessions.get(session_id)


# --------------------------------------------------------------------------
# The run loop
# --------------------------------------------------------------------------

def _run(session: ApplySession, user_data_dir: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        session.status, session.message = "failed", f"playwright not installed: {exc}"
        return

    session.status = "launching"
    session.message = "Opening browser…"
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=["--start-maximized"],
                viewport=None,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not _ensure_login(session, page):
                    if not session._stop.is_set():
                        session.status, session.message = "failed", "Login not completed in time."
                    else:
                        session.status = "cancelled"
                    return

                if not _open_easy_apply(session, page):
                    return  # status/message set inside

                if not _fill_and_navigate(session, page, session.profile):
                    return

                # awaiting_review set inside _fill_and_navigate; now wait for human submit.
                _wait_for_submission(session, page)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as exc:
        session.status, session.message = "failed", f"{type(exc).__name__}: {exc}"
        logger.exception("Apply run failed")


def _is_logged_in(page) -> bool:
    url = page.url
    if any(b in url for b in ["/login", "authwall", "checkpoint", "/challenge"]):
        return False
    try:
        return page.query_selector(".global-nav, nav.global-nav") is not None
    except Exception:
        return False


def _ensure_login(session: ApplySession, page) -> bool:
    page.goto(session.job_url)
    wait_for_page_full_load(page)
    if _is_logged_in(page):
        return True

    session.status = "needs_login"
    session.message = "Please log in to LinkedIn in the browser window (you have 5 min)."
    try:
        page.bring_to_front()
    except Exception:
        pass
    page.goto("https://www.linkedin.com/login")

    deadline = time.time() + LOGIN_TIMEOUT
    while not session._stop.is_set() and time.time() < deadline:
        if _is_logged_in(page):
            page.goto(session.job_url)
            wait_for_page_full_load(page)
            return True
        time.sleep(2)
    return False


def _open_easy_apply(session: ApplySession, page) -> bool:
    btn = page.query_selector(EASY_APPLY_BTN) or page.query_selector(EASY_APPLY_BTN_ALT)
    if not btn:
        session.status, session.message = (
            "failed", "Easy Apply button not found — this job may not support Easy Apply."
        )
        return False
    try:
        btn.click()
    except Exception:
        try:
            page.locator(EASY_APPLY_BTN_ALT).click(force=True)
        except Exception as exc:
            session.status, session.message = "failed", f"Could not click Easy Apply: {exc}"
            return False
    random_sleep()
    try:
        page.wait_for_selector(MODAL, timeout=10000)
    except Exception:
        session.status, session.message = "failed", "Easy Apply modal did not open."
        return False
    return True


def _fill_and_navigate(session: ApplySession, page, profile: dict[str, Any]) -> bool:
    session.status = "filling"
    session.message = "Filling the application form…"

    for _step in range(MAX_STEPS):
        if session._stop.is_set():
            session.status = "cancelled"
            return False
        try:
            modal = page.query_selector(MODAL)
            if not modal:
                # modal closed mid-flow -> likely already submitted or error
                if _looks_submitted(page):
                    session.status, session.message = "submitted", "Application appears sent."
                    return True
                break
            _fill_step(page, modal, profile)
        except Exception as exc:
            logger.debug("fill step error: %s", exc)

        random_sleep()
        modal = page.query_selector(MODAL)

        # Reached the final step? Hand off WITHOUT submitting.
        submit = modal.query_selector("button:has-text('Submit application')") if modal else None
        if submit and submit.is_visible():
            session.status = "awaiting_review"
            session.message = "Form filled. REVIEW in the browser, then click 'Submit application' yourself."
            try:
                page.bring_to_front()
            except Exception:
                pass
            return True

        review = modal.query_selector("button:has-text('Review')") if modal else None
        if review and review.is_visible():
            review.click()
            random_sleep()
            continue

        nxt = (modal.query_selector("button:has-text('Next'), button[aria-label='Continue to next step']")
               if modal else None)
        if nxt and nxt.is_visible():
            nxt.click()
            random_sleep()
            continue

        # Nothing else to click — hand off for human review.
        session.status = "awaiting_review"
        session.message = "Reached the end of the form. Review and submit yourself."
        try:
            page.bring_to_front()
        except Exception:
            pass
        return True

    session.status, session.message = "failed", "Could not reach a reviewable state."
    return False


def _fill_step(page, modal, profile: dict[str, Any]) -> None:
    """Fill one step of the Easy Apply form from the profile."""
    phone = profile.get("phone", "")
    email = profile.get("email", "")
    resume_path = profile.get("resume_path", "")

    # Phone
    for sel in [
        "input[name*='phone']", "input[id*='phone']", "input[id*='phoneNumber']",
        "input[inputmode='tel']", "input[type='tel']", "input[aria-label*='phone']",
    ]:
        inp = modal.query_selector(sel)
        if inp and phone:
            try:
                inp.fill(phone)
            except Exception:
                pass
            break

    # Email
    email_input = modal.query_selector("input[name*='email']")
    if email_input and email:
        try:
            email_input.fill(email)
        except Exception:
            pass

    # Resume upload
    if resume_path:
        file_input = modal.query_selector("input[type='file']")
        if file_input:
            try:
                file_input.set_input_files(resume_path)
            except Exception as exc:
                logger.debug("resume upload failed: %s", exc)

    # Free-text questions (experience, notice period, salary, etc.)
    text_inputs = modal.query_selector_all("input[type='text'], input[type='number'], textarea")
    for inp in text_inputs:
        try:
            label = _label_for(page, inp)
            answer = _answer_for(label, profile)
            if answer and inp.is_visible():
                inp.fill(str(answer))
        except Exception:
            continue

    # Radio groups
    for group in modal.query_selector_all("fieldset, div[role='radiogroup']"):
        try:
            label = _label_for(page, group)
            answer = _answer_for(label, profile) or "Yes"
            _select_radio(group, answer)
        except Exception:
            continue

    # Dropdowns
    for sel_el in modal.query_selector_all("select"):
        try:
            label = _label_for(page, sel_el)
            answer = _answer_for(label, profile) or "Yes"
            _select_dropdown(sel_el, answer)
        except Exception:
            continue


def _wait_for_submission(session: ApplySession, page) -> None:
    session.message = (session.message or "") + " Waiting for you to submit…"
    deadline = time.time() + REVIEW_TIMEOUT
    while not session._stop.is_set() and time.time() < deadline:
        if _looks_submitted(page):
            session.status, session.message = "submitted", "Application sent. ✓"
            return
        time.sleep(2)
    if session._stop.is_set():
        session.status = "cancelled"
    else:
        session.status, session.message = "timed_out", "Timed out waiting for you to submit."


def _looks_submitted(page) -> bool:
    try:
        body = (page.query_selector("body").inner_text() if page.query_selector("body") else "").lower()
        if any(t in body for t in ["application has been sent", "application was sent", "applied"]):
            # "applied" alone is weak; require the modal gone too
            if not page.query_selector(MODAL):
                return True
        # Modal closed is the strongest signal.
        if not page.query_selector(MODAL):
            # Distinguish "submitted" from "cancelled/dismissed": check for success toast.
            return any(t in body for t in ["has been sent", "was sent"])
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------
# Form-filling helpers (adapted from the reference project)
# --------------------------------------------------------------------------

def _label_for(page, element) -> str:
    try:
        elem_id = element.get_attribute("id")
        if elem_id:
            label = page.query_selector(f"label[for='{elem_id}']")
            if label:
                return label.inner_text().strip()
        parent = element.evaluate("el => el.closest('label')")
        if parent:
            return element.evaluate("el => el.closest('label').innerText").strip()
        aria = element.get_attribute("aria-label")
        if aria:
            return aria.strip()
        placeholder = element.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
        legend = element.query_selector("legend")
        if legend:
            return legend.inner_text().strip()
    except Exception:
        pass
    return ""


def _answer_for(question: str, profile: dict[str, Any]):
    if not question:
        return None
    q = question.lower()

    def g(key, default=None):
        v = profile.get(key, default)
        return v if v not in (None, "") else default

    if any(k in q for k in ["years of experience", "years experience", "how many years"]):
        return g("years_of_experience", "5")
    if any(k in q for k in ["education", "degree", "qualification", "highest level"]):
        return g("education_level", "Bachelor's Degree")
    if any(k in q for k in ["authorized to work", "legally authorized", "work authorization", "right to work"]):
        return g("work_authorization", "Yes")
    if any(k in q for k in ["visa sponsorship", "require sponsorship", "need sponsorship"]):
        return g("require_sponsorship", "No")
    if any(k in q for k in ["comfortable working", "willing to work", "work onsite", "relocate", "work in"]):
        return g("willing_to_relocate", "Yes")
    if any(k in q for k in ["notice period", "availability", "when can you start", "start date", "join"]):
        return g("notice_period", "15")
    if any(k in q for k in ["current fixed ctc", "current ctc"]):
        return g("current_salary")
    if any(k in q for k in ["salary", "compensation", "expected salary", "expected ctc", "salary expectation"]):
        return g("salary_expectation")
    if any(k in q for k in ["previously worked", "worked for", "former employee"]):
        return g("previously_worked", "No")
    if any(k in q for k in ["contact", "reference", "previous employer"]):
        return g("reference_check", "Yes")
    if any(k in q for k in ["certification", "certified", "certificate"]):
        return g("certifications", "None")
    return None


def _select_radio(group, answer) -> None:
    try:
        a = str(answer).lower()
        if a in ("yes", "true"):
            radio = group.query_selector(
                "input[type='radio'][value='Yes'], input[type='radio'][value='yes'], "
                "input[type='radio'][value='true']"
            )
            if radio:
                radio.click()
                return
        if a in ("no", "false"):
            radio = group.query_selector(
                "input[type='radio'][value='No'], input[type='radio'][value='no'], "
                "input[type='radio'][value='false']"
            )
            if radio:
                radio.click()
                return
        for label in group.query_selector_all("label"):
            if a and a in label.inner_text().lower():
                rid = label.get_attribute("for")
                if rid:
                    radio = group.query_selector(f"input[type='radio']#{rid}")
                    if radio:
                        radio.click()
                        return
    except Exception:
        pass


def _select_dropdown(select_el, answer) -> None:
    try:
        a = str(answer)
        for option in select_el.query_selector_all("option"):
            txt = option.inner_text().strip()
            if a.lower() in txt.lower() or txt.lower() in a.lower():
                select_el.select_option(value=option.get_attribute("value"))
                return
    except Exception:
        pass
