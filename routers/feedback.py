import os
import re
import base64
import binascii
import logging
from html import escape
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

import models
from deps import get_current_user
from ratelimit import rate_limit
from email_utils import send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["Feedback"])

FEEDBACK_TO_EMAIL = os.getenv("FEEDBACK_TO_EMAIL", "hello@withspaceio.in")
MAX_IMAGE_BYTES = 3 * 1024 * 1024  # screenshots are capped at ~2 MB client-side
_DATA_URL = re.compile(r"^data:image/(jpeg|png);base64,([A-Za-z0-9+/=\s]+)$")


ALLOWED_FILE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf", "text/plain", "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_FILE_BYTES = int(2.5 * 1024 * 1024)


class FeedbackFile(BaseModel):
    name: str = Field(..., max_length=200)
    content_type: str = Field(..., max_length=100)
    data: str = Field(..., max_length=4 * 1024 * 1024)  # base64 (no data: prefix)


class FeedbackIn(BaseModel):
    kind: str = Field("bug", pattern="^(bug|feature)$")
    notes: str = Field("", max_length=5000)
    screenshot: Optional[str] = Field(None, max_length=5 * 1024 * 1024)
    file: Optional[FeedbackFile] = None
    page_url: str = Field("", max_length=1000)
    user_agent: str = Field("", max_length=500)


@router.post("", dependencies=[Depends(rate_limit("feedback", 5, 600))])
async def submit_feedback(
    data: FeedbackIn,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
):
    notes = data.notes.strip()
    if not notes and not data.screenshot and not data.file:
        raise HTTPException(status_code=400, detail="Add a note, screenshot or file.")

    attachments = []
    if data.screenshot:
        m = _DATA_URL.match(data.screenshot)
        if not m:
            raise HTTPException(status_code=400, detail="Invalid screenshot.")
        try:
            raw = base64.b64decode(m.group(2), validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid screenshot.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Screenshot is too large.")
        ext = "jpg" if m.group(1) == "jpeg" else "png"
        attachments.append((raw, f"image/{m.group(1)}", f"screenshot.{ext}"))

    file_name = None
    if data.file:
        if data.file.content_type not in ALLOWED_FILE_TYPES:
            raise HTTPException(status_code=400, detail="This file type is not allowed.")
        try:
            raw = base64.b64decode(data.file.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid file.")
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File is too large (max 2.5 MB).")
        file_name = re.sub(r"[^A-Za-z0-9._ -]", "_", data.file.name)[:100] or "attachment"
        attachments.append((raw, data.file.content_type, file_name))

    # Reporter identity comes from the verified token, never from the request body
    name, email, role = escape(user.name or ""), escape(user.email or ""), escape(user.role or "")
    label = "Bug report" if data.kind == "bug" else "Feature request"
    body = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;color:#1e293b">
      <h2 style="margin:0 0 12px">{label} from {name}</h2>
      <table cellpadding="4" style="font-size:14px">
        <tr><td><b>Type</b></td><td>{label}</td></tr>
        <tr><td><b>Name</b></td><td>{name}</td></tr>
        <tr><td><b>Email</b></td><td>{email}</td></tr>
        <tr><td><b>Role</b></td><td>{role}</td></tr>
        <tr><td><b>Page</b></td><td>{escape(data.page_url)}</td></tr>
        <tr><td><b>Browser</b></td><td>{escape(data.user_agent)}</td></tr>
      </table>
      <h3 style="margin:16px 0 6px">Notes</h3>
      <div style="white-space:pre-wrap">{escape(notes) or "<i>(no notes)</i>"}</div>
      <p style="color:#64748b">{"Screenshot attached. " if data.screenshot else ""}{("File attached: " + escape(file_name)) if file_name else ""}</p>
    </div>
    """
    subject = f"[SpaceIO {label}] {(user.name or user.email or 'User').replace(chr(10), ' ')[:80]}"
    background_tasks.add_task(send_email, FEEDBACK_TO_EMAIL, subject, body, attachments)
    return {"ok": True}
