"""
OCR endpoint — uses Claude claude-sonnet-4-5 Vision to extract scores from uploaded images/PDFs.
Replaces OpenAI GPT-4o. Falls back to spreadsheet parsing for Excel/CSV files.
"""
import os, uuid, base64, json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.base import get_db
from app.models.models import (
    User, Student, Class, Subject, OcrJob, OcrRow, AuditLog,
    Result, ResultBatch, ResultStatus, Session as AcSession, Term,
)
from app.api.v1.deps import require_staff, get_current_user
from app.core.config import settings
from app.utils.grading import fuzzy_match_name, calculate_grade, compute_subject_total

router = APIRouter()

ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/webp", "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel", "text/csv",
}

# ── Prompt sent to Claude Vision ────────────────────────────────────────────
AI_EXTRACT_PROMPT = """You are an AI data extraction assistant for a Nigerian school result management system.

Your ONLY job: read this result sheet image and return the data you can LITERALLY SEE written on it.

CRITICAL RULES — follow these exactly:
1. Copy student names CHARACTER FOR CHARACTER as they are written on the sheet. Do NOT correct spelling, do NOT guess full names, do NOT add or remove any part of a name.
2. Copy score numbers EXACTLY as written. If a cell is blank, crossed out, or unreadable → set it to null. NEVER invent or guess a number.
3. Do NOT use any prior knowledge about Nigerian names or typical scores. Only read what is physically on this image.
4. If a row looks like a header, total row, or summary row (not a student name) — skip it.
5. If you are not confident about a value (blurry, smudged, ambiguous) → set it to null and lower confidence.

SCORING COLUMNS to look for (in order of priority):
- "1st Test" or "Test 1" or "T1"  → first_test  (max 20)
- "2nd Test" or "Test 2" or "T2"  → second_test (max 20)
- "Exam" or "Examination"         → exam_score  (max 60)
- "CA" or "Continuous Assessment" → ca_score    (combined, only if no separate tests)
- "Total" or "Score"              → total_score (max 100)

Return ONLY a raw JSON array — no markdown, no explanation, no code fences, just the array.
Each object must have exactly these fields:
{
  "student_name": "exactly as written on sheet",
  "first_test":   number or null,
  "second_test":  number or null,
  "ca_score":     number or null,
  "exam_score":   number or null,
  "total_score":  number or null,
  "confidence":   0.0 to 1.0
}

Example of correct behaviour:
- Sheet shows: "Amaka Obi  | 18 | 15 | 55 | 88"
- You return:  {"student_name": "Amaka Obi", "first_test": 18, "second_test": 15, "exam_score": 55, "total_score": 88, "ca_score": null, "confidence": 0.97}

Example of WRONG behaviour (do NOT do this):
- Sheet shows: "A. Obi" but you return "Amaka Obiageli" — WRONG, copy what you see
- Sheet shows blank cell but you return 0 — WRONG, return null
- You make up a plausible-looking score — WRONG, only read what's there
"""


@router.post("/upload")
async def ocr_upload(
    file: UploadFile = File(...),
    class_name: Optional[str] = Form(None),
    class_id:   Optional[int] = Form(None),
    subject_id: Optional[int] = Form(None),
    session_id: Optional[int] = Form(None),
    term_id:    Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if file.content_type not in ALLOWED_TYPES and not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "File type not supported. Use JPG, PNG, PDF, Excel or CSV.")

    content = await file.read()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(400, f"File too large (max {settings.MAX_FILE_SIZE_MB}MB)")

    # Save uploaded file
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(settings.UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # Resolve class
    cls = None
    if class_id:
        cls = db.query(Class).filter(Class.id == class_id).first()
    elif class_name:
        cls = db.query(Class).filter(Class.name == class_name).first()

    # Create OCR job record
    job = OcrJob(
        uploaded_by=current_user.id,
        class_id=cls.id if cls else None,
        subject_id=subject_id,
        session_id=session_id,
        term_id=term_id,
        filename=file.filename,
        filepath=filepath,
        extraction_status="processing",
    )
    db.add(job); db.flush(); job_id = job.id

    # ── Run extraction ──────────────────────────────────────────────────────
    is_image = (
        file.content_type in {"image/jpeg", "image/png", "image/webp"}
        or ext in ("jpg", "jpeg", "png", "webp")
    )
    try:
        if is_image:
            extracted = await _extract_with_claude(content, file.content_type or "image/jpeg")
        elif ext in ("xlsx", "xls", "csv"):
            extracted = _extract_from_spreadsheet(content, ext)
        else:
            extracted = []
    except Exception as extraction_err:
        job.extraction_status = "failed"
        job.error_message = str(extraction_err)
        db.commit()
        raise HTTPException(422, str(extraction_err))

    if not extracted:
        job.extraction_status = "failed"
        job.error_message = (
            "Claude returned no rows. The image may be low quality, blurry, or the sheet "
            "has an unusual format. Try a clearer photo with better lighting."
        )
        db.commit()
        raise HTTPException(422, job.error_message)

    # ── Match names to registered students ──────────────────────────────────
    students_in_class = []
    if cls:
        students_in_class = db.query(Student).filter(
            Student.class_id == cls.id, Student.is_active == True
        ).all()

    rows_out = []
    total_conf = 0.0

    for ex in extracted:
        name      = ex.get("student_name", "").strip()
        first_t   = ex.get("first_test")
        second_t  = ex.get("second_test")
        ca        = ex.get("ca_score")
        exam      = ex.get("exam_score")
        total     = ex.get("total_score")
        conf      = float(ex.get("confidence", 0.8))

        # Recompute total using Jaasiel formula to ensure consistency
        computed_total = compute_subject_total(first_t, second_t, ca, exam, total)

        # Fuzzy-match name to class register
        best_match = None; best_score = 0.0
        for s in students_in_class:
            score = fuzzy_match_name(name, s.full_name)
            if score > best_score:
                best_score = score; best_match = s

        match_type = "none"
        if best_match and best_score >= 0.85:
            match_type = "full"
        elif best_match and best_score >= 0.60:
            match_type = "fuzzy"
            conf = min(conf, 0.79)

        row = OcrRow(
            job_id=job_id,
            extracted_name=name,
            matched_student_id=(best_match.id if best_match and best_score >= 0.60 else None),
            match_type=match_type,
            confidence=round(conf, 2),
            ca_score=ca,
            exam_score=exam,
        )
        db.add(row)

        rows_out.append({
            "extracted_name":   name,
            "matched_student":  best_match.full_name if best_match and best_score >= 0.60 else None,
            "student_id":       best_match.id        if best_match and best_score >= 0.60 else None,
            "first_test":       first_t,
            "second_test":      second_t,
            "ca_score":         ca,
            "exam_score":       exam,
            "total_score":      computed_total,
            "confidence":       round(conf, 2),
            "match_type":       match_type,
        })
        total_conf += conf

    avg_conf = round(total_conf / len(extracted), 2) if extracted else 0

    job.extraction_status = "completed"
    job.confidence_score  = avg_conf
    job.student_count     = len(extracted)
    job.processed_at      = datetime.now(timezone.utc)

    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name,
        user_role=current_user.role.value, action="ocr_upload",
        entity_type="ocr_job", entity_id=job_id,
        description=f"Claude OCR processed {len(extracted)} rows, avg confidence {avg_conf}",
    ))
    db.commit()

    return {
        "job_id":         job_id,
        "status":         "completed",
        "total":          len(extracted),
        "matched":        len([r for r in rows_out if r["match_type"] != "none"]),
        "avg_confidence": avg_conf,
        "extractions":    rows_out,
    }


# ── Image compression helper ─────────────────────────────────────────────────
def _compress_image(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """
    Resize and compress image so it fits within Claude's limits.
    Max dimension: 1568px. Max size: ~4MB base64 (~3MB raw).
    Returns (compressed_bytes, content_type).
    """
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))

        # Convert RGBA/P to RGB (JPEG doesn't support alpha)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if larger than 1568px on any side
        max_dim = 1568
        w, h = img.size
        if w > max_dim or h > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            print(f"[OCR] Resized image from {w}x{h} to {img.size[0]}x{img.size[1]}")

        # Compress to JPEG
        buf = io.BytesIO()
        quality = 85
        img.save(buf, format="JPEG", quality=quality, optimize=True)

        # If still over 3MB, reduce quality further
        while buf.tell() > 3 * 1024 * 1024 and quality > 40:
            quality -= 15
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)

        compressed = buf.getvalue()
        print(f"[OCR] Image compressed: {len(image_bytes)//1024}KB → {len(compressed)//1024}KB (quality={quality})")
        return compressed, "image/jpeg"

    except Exception as e:
        print(f"[OCR] Compression failed ({e}), sending original")
        return image_bytes, content_type


# ── Claude Vision extraction ─────────────────────────────────────────────────
async def _extract_with_claude(image_bytes: bytes, content_type: str) -> list:
    """Use Claude claude-sonnet-4-5 Vision to extract scores from a result sheet image."""
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None) or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-your"):
        print("ANTHROPIC_API_KEY not configured — skipping Claude extraction")
        return []

    # Compress image before sending to avoid Anthropic 500 errors from oversized payloads
    image_bytes, content_type = _compress_image(image_bytes, content_type)
    b64 = base64.b64encode(image_bytes).decode()
    print(f"[OCR] Base64 size: {len(b64)//1024}KB, content_type: {content_type}")

    try:
        import httpx
        payload = {
            "model": "claude-sonnet-4-5",
            "max_tokens": 2048,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": content_type,
                            "data":       b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": AI_EXTRACT_PROMPT,
                    },
                ],
            }],
        }

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type":      "application/json",
                },
                json=payload,
            )

        resp.raise_for_status()
        data = resp.json()

        # Log full response for debugging
        print(f"[OCR] Claude response status: {resp.status_code}")
        print(f"[OCR] Claude response body keys: {list(data.keys())}")

        # Check for API-level error in response body
        if "error" in data:
            err_msg = data["error"].get("message", str(data["error"]))
            print(f"[OCR] Claude API error: {err_msg}")
            raise Exception(f"Claude API error: {err_msg}")

        # Extract text content from response
        raw = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                raw += block["text"]

        print(f"[OCR] Raw Claude output (first 500 chars): {raw[:500]}")

        if not raw.strip():
            print("[OCR] Claude returned empty text — check model name and image format")
            raise Exception("Claude returned an empty response. The image may be unreadable or the model rejected it.")

        # Strip any accidental markdown fences Claude might add
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove first line (```json or ```) and last line (```)
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = raw.strip()

        print(f"[OCR] Cleaned JSON to parse (first 500 chars): {raw[:500]}")

        result = json.loads(raw)
        print(f"[OCR] Parsed {len(result) if isinstance(result, list) else 'non-list'} rows")
        return result if isinstance(result, list) else []

    except json.JSONDecodeError as e:
        print(f"[OCR] JSON parse error: {e}")
        print(f"[OCR] Raw text that failed: {raw[:600]}")
        raise Exception(f"Claude returned invalid JSON. Raw output: {raw[:300]}")
    except Exception as e:
        print(f"[OCR] Exception: {type(e).__name__}: {e}")
        raise


# ── Spreadsheet extraction ────────────────────────────────────────────────────
def _extract_from_spreadsheet(content: bytes, ext: str) -> list:
    """
    Extract scores from Excel/CSV.
    Expected columns: Name | 1st Test | 2nd Test | Exam Score
    Also accepts:     Name | CA Score | Exam Score
    """
    rows = []
    try:
        if ext in ("xlsx", "xls"):
            import openpyxl, io
            wb = openpyxl.load_workbook(io.BytesIO(content))
            ws = wb.active
            raw_rows = list(ws.iter_rows(values_only=True))
        elif ext == "csv":
            import csv, io
            reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
            raw_rows = list(reader)
        else:
            return []

        # Detect header row
        headers = [str(c).strip().lower() if c else "" for c in (raw_rows[0] if raw_rows else [])]

        def _col(keywords):
            """Return column index matching any keyword, or None."""
            for kw in keywords:
                for i, h in enumerate(headers):
                    if kw in h:
                        return i
            return None

        col_name    = _col(["name", "student", "pupil"]) or 0
        col_first   = _col(["1st", "first", "test 1", "t1"])
        col_second  = _col(["2nd", "second", "test 2", "t2"])
        col_ca      = _col(["ca", "continuous", "assignment"])
        col_exam    = _col(["exam", "examination", "written"])
        col_total   = _col(["total", "score"])

        for i, row in enumerate(raw_rows[1:], 1):
            if not any(row): continue
            cells = [str(c).strip() if c is not None else "" for c in row]

            def _val(idx):
                if idx is None or idx >= len(cells): return None
                try: return float(cells[idx]) if cells[idx] else None
                except: return None

            name = cells[col_name] if col_name < len(cells) else ""
            if not name: continue

            rows.append({
                "student_name": name,
                "first_test":   _val(col_first),
                "second_test":  _val(col_second),
                "ca_score":     _val(col_ca),
                "exam_score":   _val(col_exam),
                "total_score":  _val(col_total),
                "confidence":   0.97,
            })

    except Exception as e:
        print(f"Spreadsheet extraction error: {e}")

    return rows


# ── Job status & result retrieval ─────────────────────────────────────────────
@router.get("/{job_id}/status")
def ocr_job_status(job_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
    if not job: raise HTTPException(404, "OCR job not found")
    return {
        "job_id":           job.id,
        "status":           job.extraction_status,
        "student_count":    job.student_count,
        "confidence_score": job.confidence_score,
        "error_message":    job.error_message,
        "uploaded_at":      job.uploaded_at.isoformat()  if job.uploaded_at  else None,
        "processed_at":     job.processed_at.isoformat() if job.processed_at else None,
    }


@router.get("/{job_id}/result")
def ocr_job_result(job_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_staff)):
    job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
    if not job: raise HTTPException(404, "OCR job not found")
    if job.extraction_status not in ("completed", "failed"):
        raise HTTPException(400, f"Job is still {job.extraction_status}")
    rows = db.query(OcrRow).filter(OcrRow.job_id == job_id).all()
    return {
        "job_id":       job.id,
        "status":       job.extraction_status,
        "class_name":   job.class_.name   if job.class_   else None,
        "subject_name": job.subject.name  if job.subject  else None,
        "avg_confidence": job.confidence_score,
        "extractions":  [{
            "row_id":          r.id,
            "extracted_name":  r.extracted_name,
            "matched_student": r.student.full_name if r.student else None,
            "student_id":      r.matched_student_id,
            "ca_score":        r.ca_score,
            "exam_score":      r.exam_score,
            "confidence":      r.confidence,
            "match_type":      r.match_type,
            "is_confirmed":    r.is_confirmed,
        } for r in rows],
    }


# ── Confirm / save to results ─────────────────────────────────────────────────
class ConfirmRow(BaseModel):
    row_id:     int
    student_id: Optional[int]   = None
    first_test: Optional[float] = None
    second_test:Optional[float] = None
    ca_score:   Optional[float] = None
    exam_score: Optional[float] = None

class ConfirmRequest(BaseModel):
    rows:       list[ConfirmRow]
    session_id: int
    term_id:    int
    subject_id: Optional[int] = None

@router.post("/{job_id}/confirm")
def ocr_job_confirm(
    job_id: int,
    body: ConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
    if not job: raise HTTPException(404, "OCR job not found")
    if job.extraction_status != "completed":
        raise HTTPException(400, "Job extraction not yet completed")

    session = db.query(AcSession).filter(AcSession.id == body.session_id).first()
    term    = db.query(Term).filter(Term.id == body.term_id).first()
    if not session or not term: raise HTTPException(400, "Valid session and term required")

    subject_id = body.subject_id or job.subject_id
    subject = db.query(Subject).filter(Subject.id == subject_id).first() if subject_id else None
    if not subject: raise HTTPException(400, "Subject is required")

    cls = db.query(Class).filter(Class.id == job.class_id).first() if job.class_id else None
    if not cls: raise HTTPException(400, "Class is required")

    batch = ResultBatch(
        uploaded_by=current_user.id, class_id=cls.id, subject_id=subject.id,
        session_id=session.id, term_id=term.id, upload_type="ocr",
        status=ResultStatus.pending,
    )
    db.add(batch); db.flush()

    saved, skipped = 0, 0
    for item in body.rows:
        row = db.query(OcrRow).filter(OcrRow.id == item.row_id, OcrRow.job_id == job_id).first()
        if not row: skipped += 1; continue

        sid     = item.student_id or row.matched_student_id
        first_t = item.first_test  if item.first_test  is not None else None
        second_t= item.second_test if item.second_test is not None else None
        ca      = item.ca_score    if item.ca_score    is not None else (row.ca_score or 0)
        exam    = item.exam_score  if item.exam_score  is not None else (row.exam_score or 0)

        if not sid: skipped += 1; continue

        # Use Jaasiel scoring formula
        total = compute_subject_total(first_t, second_t, ca, exam, None)
        if total is None: total = ca + exam
        grade, remark = calculate_grade(total)

        existing = db.query(Result).filter(
            Result.student_id == sid,
            Result.subject_id == subject.id,
            Result.term_id    == term.id,
        ).first()
        if existing:
            existing.first_test=first_t; existing.second_test=second_t
            existing.ca_score=ca; existing.exam_score=exam; existing.total_score=total
            existing.grade=grade; existing.remark=remark
            existing.batch_id=batch.id; existing.status=ResultStatus.pending
        else:
            db.add(Result(
                student_id=sid, class_id=cls.id, subject_id=subject.id,
                session_id=session.id, term_id=term.id, batch_id=batch.id,
                first_test=first_t, second_test=second_t,
                ca_score=ca, exam_score=exam, total_score=total,
                grade=grade, remark=remark, status=ResultStatus.pending,
            ))
        row.is_confirmed = True
        saved += 1

    db.add(AuditLog(
        user_id=current_user.id, user_name=current_user.full_name,
        user_role=current_user.role.value, action="ocr_confirm",
        entity_type="ocr_job", entity_id=job_id,
        description=f"Confirmed Claude OCR job {job_id}: {saved} saved, {skipped} skipped",
    ))
    db.commit()
    return {"message": "Results submitted for approval", "batch_id": batch.id, "saved": saved, "skipped": skipped}


@router.get("/history")
def ocr_history(per_page: int = 20, db: Session = Depends(get_db),
                current_user: User = Depends(require_staff)):
    q = db.query(OcrJob).filter(OcrJob.uploaded_by == current_user.id)
    items = q.order_by(OcrJob.uploaded_at.desc()).limit(per_page).all()
    return {"items": [{
        "id": j.id, "filename": j.filename,
        "class_name":    j.class_.name   if j.class_   else "—",
        "subject_name":  j.subject.name  if j.subject  else "—",
        "extraction_status": j.extraction_status,
        "confidence_score":  j.confidence_score,
        "student_count":     j.student_count,
        "error_message":     j.error_message,
        "uploaded_at": j.uploaded_at.isoformat() if j.uploaded_at else None,
        "uploader":    j.uploader.full_name       if j.uploader    else "—",
    } for j in items], "total": q.count()}


@router.get("/analytics")
def ocr_analytics(db: Session = Depends(get_db), current_user: User = Depends(require_staff)):
    total     = db.query(OcrJob).count()
    completed = db.query(OcrJob).filter(OcrJob.extraction_status == "completed").count()
    jobs      = db.query(OcrJob).filter(OcrJob.confidence_score.isnot(None)).all()
    avg_conf  = round(sum(j.confidence_score for j in jobs) / len(jobs), 1) if jobs else 0
    auto_matched = db.query(OcrRow).filter(OcrRow.match_type == "full").count()
    total_rows   = db.query(OcrRow).count()
    return {
        "total_jobs": total, "completed": completed,
        "accuracy": avg_conf, "ocr_accuracy": avg_conf,
        "auto_matched": round(auto_matched / total_rows * 100, 1) if total_rows else 0,
        "avg_time": 2.3,
    }