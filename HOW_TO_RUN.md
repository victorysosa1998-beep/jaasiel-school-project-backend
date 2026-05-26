# 🏫 Jaasiel Education Centre — AI RMS
## How to Run — Complete Guide

---

## ✅ What's Inside

```
jaasiel-rms-final/
├── main.py              ← FastAPI server entry point
├── seed.py              ← Creates admin accounts + sample data
├── requirements.txt     ← Python dependencies
├── .env                 ← Configuration (database, keys, school info)
├── app/
│   ├── api/v1/endpoints/
│   │   ├── auth.py      ← Login, logout, refresh, change-password
│   │   ├── students.py  ← Student CRUD, bulk upload, student portal
│   │   ├── results.py   ← Upload scores, approve/reject, lock
│   │   ├── ocr.py       ← AI OCR with GPT-4o Vision
│   │   └── other.py     ← Dashboard, sessions, analytics, settings, audit
│   ├── models/models.py ← All 15 database tables
│   ├── core/            ← JWT, bcrypt, settings
│   └── db/base.py       ← SQLite / PostgreSQL connection
├── frontend/            ← 21 HTML pages (all roles)
│   ├── css/style.css    ← Design system
│   ├── js/core.js       ← API client + auth utilities
│   ├── js/ui.js         ← Sidebar builders
│   ├── login.html
│   ├── admin-dashboard.html
│   ├── subadmin-dashboard.html
│   ├── student-dashboard.html
│   └── ... (18 more pages)
└── uploads/             ← Uploaded OCR files (auto-created)
```

---

## 🚀 QUICKSTART (5 Minutes)

### Step 1 — Install Python (if needed)

Requires **Python 3.10 or higher**. Check with:
```bash
python --version
```
Download from https://python.org if needed.

---

### Step 2 — Install dependencies

Open a terminal in the project folder, then:

```bash
pip install -r requirements.txt
```

This installs FastAPI, SQLAlchemy, JWT, bcrypt, OpenAI, and everything else.

---

### Step 3 — Seed the database

```bash
python seed.py
```

This creates the SQLite database and inserts:
- ✅ Super Admin account
- ✅ Sub Admin account
- ✅ All 17 classes
- ✅ 24 subjects
- ✅ Session 2024/2025 (Second Term active)
- ✅ 8 sample students

Output will show all credentials.

---

### Step 4 — Start the server

```bash
uvicorn main:app --reload --port 8000
```

Open your browser: **http://localhost:8000**

You'll see the login page immediately.

---

## 🔑 Login Credentials

| Role | Username | Password |
|------|----------|----------|
| Super Admin | `superadmin` | `Admin@123` |
| Sub Admin | `subadmin` | `SubAdmin@123` |
| Student (example) | `eghosavictoraisosa12` | `200412` |

> **Student password format:** DDMMYY of date of birth  
> e.g. Born 20 April 2012 → password is `200412`

---

## 🤖 Enable AI OCR (Optional)

To use the real AI OCR that reads handwritten/scanned result sheets:

1. Get a free API key at https://platform.openai.com/api-keys
2. Open `.env` and change:
   ```
   OPENAI_API_KEY=
   ```
3. Restart the server

Without this key, OCR uploads still work — the frontend shows extracted data from spreadsheets (Excel/CSV), and image OCR falls back gracefully.

---

## 🗄️ Database

**Default:** SQLite — no setup needed. Database file is `jaasiel_rms.db` (auto-created).

**Switch to PostgreSQL for production:**

1. Create a database:
   ```sql
   CREATE USER jaasiel_user WITH PASSWORD 'YourPassword';
   CREATE DATABASE jaasiel_rms OWNER jaasiel_user;
   ```

2. Update `.env`:
   ```
   DATABASE_URL=postgresql://jaasiel_user:YourPassword@localhost:5432/jaasiel_rms
   ```

3. Run `python seed.py` again.

---

## 📱 Access From Other Devices (Same WiFi)

To use on your phone or tablet on the same network:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Find your computer's IP address (e.g. 192.168.1.5), then open:  
`http://192.168.1.5:8000` on any device.

---

## 📋 All Pages

| Page | URL | Role |
|------|-----|------|
| Login | `/login.html` | All |
| Admin Dashboard | `/admin-dashboard.html` | Super Admin |
| Result Approvals | `/results-approval.html` | Super Admin |
| Students | `/students.html` | Admin + Sub Admin |
| Add Student | `/add-student.html` | Sub Admin |
| Bulk Upload | `/bulk-upload.html` | Sub Admin |
| AI OCR Upload | `/ocr-upload.html` | Sub Admin |
| Manual Entry | `/manual-entry.html` | Sub Admin |
| Sub Admin Dashboard | `/subadmin-dashboard.html` | Sub Admin |
| My Uploads | `/upload-history.html` | Sub Admin |
| Analytics | `/analytics.html` | Super Admin |
| Sessions & Terms | `/sessions.html` | Super Admin |
| Classes & Subjects | `/classes.html` | Super Admin |
| Audit Logs | `/audit-logs.html` | Super Admin |
| OCR Monitor | `/ocr-monitor.html` | Super Admin |
| Settings | `/settings.html` | All Staff |
| Student Dashboard | `/student-dashboard.html` | Student |
| My Results | `/my-results.html` | Student |
| Report Card | `/report-card.html` | Student |
| Change Password | `/student-settings.html` | Student |
| API Docs | `/api/docs` | Dev |

---

## 🔄 Complete Workflow

### 1. Sub Admin uploads results
1. Login as `subadmin`
2. Go to **AI OCR Upload** → Select class, subject, session, term
3. Upload a result sheet image or Excel file
4. Review extracted scores → click **Submit for Approval**

### 2. Super Admin approves
1. Login as `superadmin`
2. Go to **Result Approvals** → See pending batch
3. Click **Approve & Publish**
4. Results are now visible to students

### 3. Student views results
1. Login with student username/password
2. See results on dashboard
3. Download report card as PDF

---

## ⚙️ .env Configuration

```env
# Database (SQLite default — no setup needed)
DATABASE_URL=sqlite:///./jaasiel_rms.db

# Security (CHANGE THIS IN PRODUCTION!)
SECRET_KEY=change-this-secret-key-in-production

# AI OCR (optional)
OPENAI_API_KEY=
# School Info (appears on report cards)
SCHOOL_NAME=Jaasiel Education Centre
SCHOOL_ADDRESS=Oxygen Street, Benin City, Edo State
SCHOOL_PHONE=+234 703 630 4408
SCHOOL_EMAIL=admin@jaasiel.edu.ng
PRINCIPAL_NAME=The Principal
```

---

## ❓ Troubleshooting

**Port already in use:**
```bash
uvicorn main:app --reload --port 8001
# Then open http://localhost:8001
```

**Module not found:**
```bash
pip install -r requirements.txt --upgrade
```

**Database error — run seed again:**
```bash
python seed.py
```

**Forgot admin password:**
```bash
python -c "
from app.db.base import SessionLocal
from app.models.models import User
from app.core.security import hash_password
db = SessionLocal()
u = db.query(User).filter(User.username=='superadmin').first()
u.password_hash = hash_password('Admin@123')
db.commit()
print('Password reset to Admin@123')
"
```
