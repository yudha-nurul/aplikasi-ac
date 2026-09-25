import sqlite3
import os
import base64
import hashlib
import hmac
import qrcode
import shutil
import urllib.request
import urllib.error
from urllib.parse import quote
from datetime import date, datetime
from io import BytesIO
from typing import List, Optional
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def image_url(path: Optional[str]) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"/uploads/{path}"

templates.env.globals["image_url"] = image_url
templates.env.filters["image_url"] = image_url

RUNTIME_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(RUNTIME_DIR, "uploads")
STATIC_DIR = os.path.join(RUNTIME_DIR, "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DB_NAME = os.path.join(RUNTIME_DIR, "aplikasi_ac.db")

def load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

load_local_env()

# Deteksi apakah aplikasi berjalan di lingkungan produksi (mis. Render).
IS_PRODUCTION = bool(
    os.getenv("RENDER")
    or os.getenv("RENDER_SERVICE_ID")
    or os.getenv("PRODUCTION")
    or os.getenv("ENV", "").lower() == "production"
)

DATABASE_URL = os.getenv("DATABASE_URL")

# Di production SESSION_SECRET WAJIB diisi agar tidak memakai secret default
# yang bisa dipalsukan. Aplikasi sengaja gagal start bila kosong di production.
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError(
            "SESSION_SECRET wajib diisi di environment produksi. "
            "Set variabel ini sebelum menjalankan aplikasi."
        )
    SESSION_SECRET = "ganti-secret-aplikasi-ac-dev"
    print("[WARN] SESSION_SECRET belum diisi, memakai secret default khusus development.")

if IS_PRODUCTION and len(SESSION_SECRET) < 32:
    raise RuntimeError(
        "SESSION_SECRET terlalu pendek (minimal 32 karakter) untuk production."
    )

TECHNICIAN_USERNAME = os.getenv("TECHNICIAN_USERNAME", "teknisi").strip() or "teknisi"
TECHNICIAN_PASSWORD = (os.getenv("TECHNICIAN_PASSWORD") or "").strip()

# Di production, password default teknisi WAJIB diisi dan tidak boleh lemah.
if IS_PRODUCTION:
    if not TECHNICIAN_PASSWORD:
        raise RuntimeError(
            "TECHNICIAN_PASSWORD wajib diisi di production. "
            "Jangan biarkan akun superuser tanpa password."
        )
    if len(TECHNICIAN_PASSWORD) < 8:
        raise RuntimeError(
            "TECHNICIAN_PASSWORD terlalu pendek (minimal 8 karakter) untuk production."
        )
elif not TECHNICIAN_PASSWORD:
    print("[WARN] TECHNICIAN_PASSWORD belum diisi. Login superuser via env var dinonaktifkan.")

SUPABASE_URL_RAW = os.getenv("SUPABASE_URL") or os.getenv("API_URL", "")
SUPABASE_URL = SUPABASE_URL_RAW.split("/rest/v1")[0].rstrip("/") if SUPABASE_URL_RAW else ""
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("API_Key") or os.getenv("API_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "foto-aplikasi-ac")

class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        query = query.replace("?", "%s")
        return self._cursor.execute(query, params or ())

    def executemany(self, query, params):
        query = query.replace("?", "%s")
        return self._cursor.executemany(query, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def execute(self, query, params=None):
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()

def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        return PostgresConnection(
            psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        )
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def create_session(role: str, username: str = "") -> str:
    payload = base64.urlsafe_b64encode(f"{role}:{username}".encode()).decode().rstrip("=")
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"

def get_session(request: Request):
    """Kembalikan tuple (role, username) dari cookie sesi yang tertandatangani."""
    session = request.cookies.get("ac_session", "")
    try:
        payload, signature = session.split(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None, None
        decoded = base64.urlsafe_b64decode(payload + "==").decode()
        role, _, username = decoded.partition(":")
        return role, username
    except (ValueError, UnicodeDecodeError):
        return None, None

def get_session_role(request: Request):
    role, _ = get_session(request)
    return role

# --------------------------------------------------------------------------
# Proteksi CSRF
# --------------------------------------------------------------------------
def csrf_token(request: Request) -> str:
    """Token CSRF yang terikat pada cookie sesi saat ini."""
    session = request.cookies.get("ac_session", "")
    return hmac.new(SESSION_SECRET.encode(), f"csrf:{session}".encode(), hashlib.sha256).hexdigest()

def verify_csrf(request: Request, token: Optional[str]) -> bool:
    return bool(token) and hmac.compare_digest(token, csrf_token(request))

templates.env.globals["csrf_token"] = csrf_token

@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    if request.method == "POST":
        token = request.headers.get("x-csrf-token") or ""
        if not token:
            try:
                form = await request.form()
                token = form.get("csrf_token") or ""
            except Exception:
                token = ""
        if not verify_csrf(request, token):
            return PlainTextResponse(
                "Permintaan ditolak: CSRF token tidak valid atau kedaluwarsa.",
                status_code=403,
            )
    return await call_next(request)

# --------------------------------------------------------------------------
# Hashing password (PBKDF2-HMAC-SHA256) dengan dukungan hash lama (SHA-256)
# --------------------------------------------------------------------------
PBKDF2_ITERATIONS = 200_000
PBKDF2_PREFIX = "pbkdf2_sha256"

def hash_password(password: str) -> str:
    """Buat hash password PBKDF2 dengan salt acak (aman & lambat)."""
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"

def verify_password(stored: Optional[str], password: str) -> bool:
    """Verifikasi password terhadap hash PBKDF2 baru maupun hash SHA-256 lama."""
    if not stored:
        return False
    if stored.startswith(f"{PBKDF2_PREFIX}$"):
        try:
            _, iterations, salt_hex, hash_hex = stored.split("$")
            derived = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(derived.hex(), hash_hex)
        except (ValueError, TypeError):
            return False
    # Kompatibilitas ke belakang: hash SHA-256 tanpa salt (akan di-upgrade saat login).
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored, legacy)

# Nama lama tetap dipakai di beberapa tempat; arahkan ke implementasi baru.
hash_customer_password = hash_password

def get_dashboard_url(request: Request) -> str:
    role = get_session_role(request)
    if role in {"teknisi", "superuser"}:
        return "/dashboard"
    if role == "customer":
        return "/dashboard/customer"
    return "/"

# 🛠️ Inisialisasi Database (Menambahkan kolom kode_unik)
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unit_servis (
                id SERIAL PRIMARY KEY,
                kode_unik TEXT UNIQUE,
                nama_pelanggan TEXT NOT NULL,
                mode TEXT NOT NULL,
                unit TEXT NOT NULL,
                status TEXT NOT NULL,
                foto_perangkat TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pelanggan (
                id SERIAL PRIMARY KEY,
                nama TEXT NOT NULL UNIQUE,
                username TEXT,
                password_hash TEXT,
                alamat TEXT,
                no_hp TEXT,
                teknisi_username TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profil_teknisi (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                nama TEXT,
                no_hp TEXT,
                password_hash TEXT,
                role TEXT DEFAULT 'teknisi',
                is_active INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history_servis (
                id SERIAL PRIMARY KEY,
                unit_id INTEGER NOT NULL REFERENCES unit_servis(id),
                tanggal TEXT NOT NULL,
                item_servis TEXT NOT NULL,
                kondisi_before TEXT NOT NULL,
                kondisi_after TEXT NOT NULL,
                nama_teknisi TEXT NOT NULL,
                servis_selanjutnya TEXT NOT NULL,
                foto_before TEXT,
                foto_after TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history_foto (
                id SERIAL PRIMARY KEY,
                history_id INTEGER NOT NULL REFERENCES history_servis(id),
                jenis TEXT NOT NULL CHECK (jenis IN ('before', 'after')),
                nama_file TEXT NOT NULL
            )
        """)
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN IF NOT EXISTS username TEXT")
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN IF NOT EXISTS password_hash TEXT")
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN IF NOT EXISTS alamat TEXT")
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN IF NOT EXISTS no_hp TEXT")
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN IF NOT EXISTS teknisi_username TEXT")
        cursor.execute("ALTER TABLE unit_servis ADD COLUMN IF NOT EXISTS foto_perangkat TEXT")
        cursor.execute("ALTER TABLE history_servis ADD COLUMN IF NOT EXISTS foto_before TEXT")
        cursor.execute("ALTER TABLE history_servis ADD COLUMN IF NOT EXISTS foto_after TEXT")
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN IF NOT EXISTS nama TEXT")
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN IF NOT EXISTS password_hash TEXT")
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'teknisi'")
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1")
        cursor.execute(
            "INSERT INTO profil_teknisi (username, password_hash, role) VALUES (?, ?, ?) ON CONFLICT (username) DO NOTHING",
            (TECHNICIAN_USERNAME, hash_customer_password(TECHNICIAN_PASSWORD or ""), "superuser"),
        )
        cursor.execute(
            "UPDATE profil_teknisi SET password_hash = ?, role = 'superuser' WHERE username = ?",
            (hash_customer_password(TECHNICIAN_PASSWORD or ""), TECHNICIAN_USERNAME),
        )
        cursor.execute(
            "INSERT INTO pelanggan (nama) SELECT DISTINCT nama_pelanggan FROM unit_servis WHERE 1=1 "
            "ON CONFLICT (nama) DO NOTHING"
        )
        cursor.execute("UPDATE pelanggan SET username = nama WHERE username IS NULL OR username = ''")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pelanggan_username ON pelanggan(username)")
        conn.commit()
        conn.close()
        return
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unit_servis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_unik TEXT UNIQUE,
            nama_pelanggan TEXT NOT NULL,
            mode TEXT NOT NULL,
            unit TEXT NOT NULL,
            status TEXT NOT NULL,
            foto_perangkat TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pelanggan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL COLLATE NOCASE UNIQUE,
            username TEXT,
            password_hash TEXT,
            alamat TEXT,
            no_hp TEXT
        )
    """)
    customer_columns = {row[1] for row in cursor.execute("PRAGMA table_info(pelanggan)").fetchall()}
    if "username" not in customer_columns:
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN username TEXT")
    if "password_hash" not in customer_columns:
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN password_hash TEXT")
    if "alamat" not in customer_columns:
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN alamat TEXT")
    if "teknisi_username" not in customer_columns:
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN teknisi_username TEXT")
    if "no_hp" not in customer_columns:
        cursor.execute("ALTER TABLE pelanggan ADD COLUMN no_hp TEXT")
    unit_columns = {row[1] for row in cursor.execute("PRAGMA table_info(unit_servis)").fetchall()}
    if "foto_perangkat" not in unit_columns:
        cursor.execute("ALTER TABLE unit_servis ADD COLUMN foto_perangkat TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profil_teknisi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            nama TEXT,
            no_hp TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'teknisi',
            is_active INTEGER DEFAULT 1
        )
    """)
    profil_columns = {row[1] for row in cursor.execute("PRAGMA table_info(profil_teknisi)").fetchall()}
    if "nama" not in profil_columns:
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN nama TEXT")
    if "password_hash" not in profil_columns:
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN password_hash TEXT")
    if "role" not in profil_columns:
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN role TEXT DEFAULT 'teknisi'")
    if "is_active" not in profil_columns:
        cursor.execute("ALTER TABLE profil_teknisi ADD COLUMN is_active INTEGER DEFAULT 1")
    cursor.execute(
        "INSERT INTO profil_teknisi (username, nama, password_hash, role) VALUES (?, ?, ?, ?) ON CONFLICT (username) DO NOTHING",
        (TECHNICIAN_USERNAME, TECHNICIAN_USERNAME, hash_customer_password(TECHNICIAN_PASSWORD or ""), "superuser"),
    )
    cursor.execute(
        "UPDATE profil_teknisi SET nama = COALESCE(NULLIF(nama, ''), username), password_hash = ?, role = 'superuser' WHERE username = ?",
        (hash_customer_password(TECHNICIAN_PASSWORD or ""), TECHNICIAN_USERNAME),
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_servis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL,
            tanggal TEXT NOT NULL,
            item_servis TEXT NOT NULL,
            kondisi_before TEXT NOT NULL,
            kondisi_after TEXT NOT NULL,
            nama_teknisi TEXT NOT NULL,
            servis_selanjutnya TEXT NOT NULL,
            foto_before TEXT,
            foto_after TEXT,
            FOREIGN KEY (unit_id) REFERENCES unit_servis(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_foto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id INTEGER NOT NULL,
            jenis TEXT NOT NULL CHECK (jenis IN ('before', 'after')),
            nama_file TEXT NOT NULL,
            FOREIGN KEY (history_id) REFERENCES history_servis(id)
        )
    """)
    history_columns = {row[1] for row in cursor.execute("PRAGMA table_info(history_servis)").fetchall()}
    if "foto_before" not in history_columns:
        cursor.execute("ALTER TABLE history_servis ADD COLUMN foto_before TEXT")
    if "foto_after" not in history_columns:
        cursor.execute("ALTER TABLE history_servis ADD COLUMN foto_after TEXT")
    cursor.execute(
        "INSERT INTO pelanggan (nama) SELECT DISTINCT nama_pelanggan FROM unit_servis WHERE 1=1 "
        "ON CONFLICT (nama) DO NOTHING"
    )
    cursor.execute("UPDATE pelanggan SET username = nama WHERE username IS NULL OR username = ''")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pelanggan_username ON pelanggan(username)")
    conn.commit()
    conn.close()

init_db()

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# Batas ukuran file upload (5 MB) dan daftar MIME yang diizinkan
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def _detect_mime_from_bytes(data: bytes) -> Optional[str]:
    """Deteksi MIME type dari magic bytes, bukan hanya dari ekstensi filename."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None

def upload_to_supabase_storage(file_bytes: bytes, filename: str, content_type: str) -> Optional[str]:
    if not (SUPABASE_URL and SUPABASE_KEY and SUPABASE_BUCKET):
        return None
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
        req = urllib.request.Request(
            url,
            data=file_bytes,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": content_type,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status in (200, 201):
                return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
    except Exception as err:
        print(f"[Supabase Storage Error] {err}")
    return None

def simpan_foto(upload: Optional[UploadFile], prefix: str) -> Optional[str]:
    if not upload or not upload.filename:
        return None
    extension = os.path.splitext(upload.filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        print(f"[Upload Rejected] Ekstensi tidak diizinkan: {extension}")
        return None

    # Baca maksimal MAX_UPLOAD_BYTES + 1 byte untuk deteksi overflow
    file_bytes = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        print(f"[Upload Rejected] File melebihi {MAX_UPLOAD_BYTES} bytes")
        return None
    if not file_bytes:
        return None

    detected_mime = _detect_mime_from_bytes(file_bytes)
    if detected_mime not in ALLOWED_MIME:
        print(f"[Upload Rejected] Konten bukan gambar valid: {upload.filename}")
        return None

    filename = f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}{extension}"

    try:
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as output:
            output.write(file_bytes)
    except Exception as e:
        print(f"[Local Save Error] {e}")

    content_type = MIME_TYPES.get(extension, detected_mime or "application/octet-stream")
    public_url = upload_to_supabase_storage(file_bytes, filename, content_type)
    if public_url:
        return public_url

    return filename

def simpan_banyak_foto(uploads: Optional[List[UploadFile]], prefix: str) -> List[str]:
    filenames = []
    for index, upload in enumerate(uploads or [], start=1):
        filename = simpan_foto(upload, f"{prefix}-{index}")
        if filename:
            filenames.append(filename)
    return filenames

def reminder_text(next_date: Optional[str]) -> Optional[str]:
    if not next_date:
        return None
    try:
        days_left = (date.fromisoformat(next_date) - date.today()).days
    except ValueError:
        return None
    if days_left == 3:
        return "Pengingat: jadwal servis perangkat ini tinggal 3 hari lagi."
    if days_left == 0:
        return "Pengingat: jadwal servis perangkat ini adalah hari ini."
    if days_left < 0:
        return f"Jadwal servis berikutnya sudah lewat {abs(days_left)} hari."
    return None

def normalize_phone(phone: Optional[str]) -> str:
    digits = "".join(character for character in (phone or "") if character.isdigit())
    if digits.startswith("0"):
        return "62" + digits[1:]
    return digits

def get_current_user(request: Request):
    role, username = get_session(request)
    if role in {"teknisi", "superuser"}:
        return role, (username or TECHNICIAN_USERNAME)
    if role == "customer":
        return role, (username or None)
    return role, None

def set_session_cookies(response, role: str, username: str):
    """Simpan sesi (role + username) dalam satu cookie yang tertandatangani."""
    response.set_cookie(
        "ac_session",
        create_session(role, username),
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )

def can_access_unit(conn, unit, role: str, username: Optional[str]) -> bool:
    if role == "superuser":
        return True
    if role != "teknisi":
        return False
    if not unit or not username:
        return False
    customer = conn.execute(
        "SELECT teknisi_username FROM pelanggan WHERE lower(nama) = lower(?)",
        (unit["nama_pelanggan"],),
    ).fetchone()
    if not customer:
        return username == TECHNICIAN_USERNAME
    if customer["teknisi_username"] in (None, ""):
        return username == TECHNICIAN_USERNAME
    return customer["teknisi_username"] == username

def can_manage_history(record, role: str, username: Optional[str]) -> bool:
    if role == "superuser":
        return True
    if role != "teknisi" or not username:
        return False
    created_by = record["nama_teknisi"] if record and "nama_teknisi" in record.keys() else ""
    return (created_by or "").strip().lower() == username.strip().lower()

# -------------------------------------------------------------
# 🌐 ENDPOINTS APLIKASI
# -------------------------------------------------------------

@app.get("/")
def halaman_awal(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"nama_aplikasi": "Sistem Manajemen Servis AC"}
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/dashboard")
def halaman_dashboard(request: Request):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    search_query = (request.query_params.get("q") or "").strip().lower()

    conn = get_db_connection()
    current_username = current_username or TECHNICIAN_USERNAME
    teknisi = conn.execute(
        "SELECT * FROM profil_teknisi WHERE username = ?",
        (current_username,),
    ).fetchone()
    if role == "superuser":
        daftar_pelanggan = conn.execute("SELECT * FROM pelanggan ORDER BY lower(nama)").fetchall()
        daftar_teknisi_raw = conn.execute("SELECT * FROM profil_teknisi ORDER BY username").fetchall()
        daftar_teknisi = []
        for teknisi_item in daftar_teknisi_raw:
            pelanggan_teknisi = conn.execute(
                "SELECT * FROM pelanggan WHERE teknisi_username = ? ORDER BY lower(nama)",
                (teknisi_item["username"],),
            ).fetchall()
            history_teknisi = conn.execute(
                """
                SELECT h.*, u.nama_pelanggan, u.unit, u.kode_unik
                FROM history_servis h
                JOIN unit_servis u ON u.id = h.unit_id
                WHERE lower(h.nama_teknisi) = lower(?)
                ORDER BY h.id DESC
                """,
                (teknisi_item["username"],),
            ).fetchall()
            daftar_teknisi.append(
                {
                    "username": teknisi_item["username"],
                    "no_hp": teknisi_item["no_hp"],
                    "role": teknisi_item["role"],
                    "is_active": teknisi_item["is_active"],
                    "pelanggan": pelanggan_teknisi,
                    "history_count": len(history_teknisi),
                    "history": history_teknisi,
                }
            )
    else:
        daftar_pelanggan = conn.execute(
            "SELECT * FROM pelanggan WHERE teknisi_username = ? ORDER BY lower(nama)",
            (current_username,),
        ).fetchall()
        daftar_teknisi = []
    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    if search_query:
        if role == "superuser":
            daftar_pelanggan = [
                item for item in daftar_pelanggan
                if search_query in (item["nama"] or "").lower()
                or search_query in (item["username"] or "").lower()
                or search_query in (item["no_hp"] or "").lower()
                or search_query in (item["alamat"] or "").lower()
            ]
            daftar_teknisi = [
                item for item in daftar_teknisi
                if search_query in (item["username"] or "").lower()
                or search_query in (item["no_hp"] or "").lower()
                or search_query in " ".join(
                    pelanggan_item["nama"] for pelanggan_item in item["pelanggan"]
                ).lower()
            ]
        else:
            daftar_pelanggan = [
                item for item in daftar_pelanggan
                if search_query in (item["nama"] or "").lower()
                or search_query in (item["username"] or "").lower()
                or search_query in (item["no_hp"] or "").lower()
                or search_query in (item["alamat"] or "").lower()
            ]

    unit_per_pelanggan = {}
    for unit in daftar_unit:
        unit_per_pelanggan.setdefault(unit["nama_pelanggan"], []).append(unit)

    pelanggan = [
        {"nama": item["nama"], "units": unit_per_pelanggan.get(item["nama"], [])}
        for item in daftar_pelanggan
    ]

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "nama_aplikasi": "Sistem Manajemen Servis AC",
            "pelanggan": pelanggan,
            "jumlah_pelanggan": len(pelanggan),
            "teknisi": teknisi,
            "daftar_teknisi": daftar_teknisi,
            "role": role,
            "search_query": search_query,
        }
    )

@app.post("/profil-teknisi")
def simpan_profil_teknisi(request: Request, no_hp: str = Form("")):
    if get_session_role(request) != "teknisi":
        return RedirectResponse("/login/teknisi", status_code=303)
    conn = get_db_connection()
    conn.execute(
        "UPDATE profil_teknisi SET no_hp = ? WHERE username = ?",
        (no_hp.strip(), TECHNICIAN_USERNAME),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/dashboard/customer")
def halaman_dashboard_customer(request: Request):
    role, customer_username = get_current_user(request)
    if role != "customer":
        return RedirectResponse("/login/customer", status_code=303)

    conn = get_db_connection()
    customer = conn.execute(
        "SELECT nama FROM pelanggan WHERE username = ?",
        (customer_username,),
    ).fetchone()
    nama_pelanggan = customer["nama"] if customer else ""
    daftar_unit = conn.execute(
        "SELECT * FROM unit_servis WHERE lower(nama_pelanggan) = lower(?) ORDER BY id DESC",
        (nama_pelanggan,),
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        request=request,
        name="dashboard_customer.html",
        context={"nama_pelanggan": nama_pelanggan, "daftar_unit": daftar_unit},
    )

@app.get("/tambah-teknisi")
def halaman_tambah_teknisi(request: Request):
    role, _ = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="tambah_teknisi.html",
        context={"judul": "Tambah Teknisi", "error": None},
    )

@app.post("/tambah-teknisi")
def tambah_teknisi(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    nama: str = Form(""),
    no_hp: str = Form(""),
):
    role, _ = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(
            request=request,
            name="tambah_teknisi.html",
            context={"judul": "Tambah Teknisi", "error": "Username dan password wajib diisi."},
            status_code=400,
        )

    conn = get_db_connection()
    display_name = (nama.strip() or username).strip()
    conn.execute(
        "INSERT INTO profil_teknisi (username, nama, password_hash, role, no_hp) VALUES (?, ?, ?, 'teknisi', ?) "
        "ON CONFLICT(username) DO UPDATE SET nama = excluded.nama, password_hash = excluded.password_hash, role = 'teknisi', no_hp = excluded.no_hp",
        (username, display_name, hash_customer_password(password), no_hp.strip()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/tambah-pelanggan")
def halaman_tambah_pelanggan(request: Request):
    role, _ = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    daftar_teknisi = []
    if role == "superuser":
        daftar_teknisi = conn.execute("SELECT username, no_hp FROM profil_teknisi WHERE role != 'customer' ORDER BY username").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="tambah_pelanggan.html",
        context={"role": role, "daftar_teknisi": daftar_teknisi},
    )

@app.post("/tambah-pelanggan")
def tambah_pelanggan(
    request: Request,
    nama: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    alamat: str = Form(""),
    no_hp: str = Form(""),
    teknisi_username: str = Form(""),
):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    nama = nama.strip()
    if nama:
        conn = get_db_connection()
        assigned_technician = current_username or TECHNICIAN_USERNAME
        if role == "superuser":
            assigned_technician = teknisi_username.strip() or assigned_technician
        conn.execute(
            "INSERT INTO pelanggan (nama, username, password_hash, alamat, no_hp, teknisi_username) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (nama) DO NOTHING",
            (nama, username.strip(), hash_customer_password(password), alamat.strip(), no_hp.strip(), assigned_technician),
        )
        conn.execute(
            "UPDATE pelanggan SET alamat = ?, no_hp = ?, teknisi_username = ? WHERE nama = ?",
            (alamat.strip(), no_hp.strip(), assigned_technician, nama),
        )
        conn.commit()
        conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/pelanggan/{nama_pelanggan}/edit")
def halaman_edit_pelanggan(request: Request, nama_pelanggan: str):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    pelanggan = conn.execute(
        "SELECT * FROM pelanggan WHERE lower(nama) = lower(?)",
        (nama_pelanggan,),
    ).fetchone()
    daftar_teknisi = []
    if role == "superuser":
        daftar_teknisi = conn.execute("SELECT username, no_hp FROM profil_teknisi WHERE role != 'customer' ORDER BY username").fetchall()
    conn.close()
    if not pelanggan:
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Pelanggan Tidak Ditemukan", "pesan": "Data pelanggan tidak terdaftar.", "dashboard_url": "/dashboard"},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="edit_pelanggan.html",
        context={"pelanggan": pelanggan, "role": role, "daftar_teknisi": daftar_teknisi},
    )

@app.get("/pelanggan/{nama_pelanggan}/detail")
def halaman_detail_pelanggan(request: Request, nama_pelanggan: str):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    pelanggan = conn.execute(
        "SELECT * FROM pelanggan WHERE lower(nama) = lower(?)",
        (nama_pelanggan,),
    ).fetchone()
    if not pelanggan:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Pelanggan Tidak Ditemukan", "pesan": "Data pelanggan tidak terdaftar.", "dashboard_url": "/dashboard"},
            status_code=404,
        )

    if role == "teknisi" and pelanggan["teknisi_username"] not in (None, current_username):
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    daftar_unit = conn.execute(
        "SELECT * FROM unit_servis WHERE lower(nama_pelanggan) = lower(?) ORDER BY id DESC",
        (nama_pelanggan,),
    ).fetchall()
    history = conn.execute(
        """
        SELECT h.*, u.kode_unik, u.unit
        FROM history_servis h
        JOIN unit_servis u ON u.id = h.unit_id
        WHERE lower(u.nama_pelanggan) = lower(?)
        ORDER BY h.id DESC
        """,
        (nama_pelanggan,),
    ).fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="detail_pelanggan.html",
        context={
            "pelanggan": pelanggan,
            "daftar_unit": daftar_unit,
            "history": history,
            "role": role,
            "dashboard_url": "/dashboard",
        },
    )

@app.post("/pelanggan/{nama_pelanggan}/edit")
def edit_pelanggan(
    request: Request,
    nama_pelanggan: str,
    nama: str = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    alamat: str = Form(""),
    no_hp: str = Form(""),
    teknisi_username: str = Form(""),
):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    nama = nama.strip()
    username = username.strip()
    if not nama or not username:
        return RedirectResponse(f"/pelanggan/{nama_pelanggan}/edit", status_code=303)

    conn = get_db_connection()
    pelanggan = conn.execute(
        "SELECT * FROM pelanggan WHERE lower(nama) = lower(?)",
        (nama_pelanggan,),
    ).fetchone()
    if not pelanggan:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    if role == "teknisi" and pelanggan["teknisi_username"] not in (None, current_username):
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    password_hash = pelanggan["password_hash"]
    if password.strip():
        password_hash = hash_customer_password(password)
    assigned = current_username if role == "teknisi" else (teknisi_username.strip() or pelanggan["teknisi_username"] or TECHNICIAN_USERNAME)
    conn.execute(
        "UPDATE pelanggan SET nama = ?, username = ?, password_hash = ?, alamat = ?, no_hp = ?, teknisi_username = ? WHERE id = ?",
        (nama, username, password_hash, alamat.strip(), no_hp.strip(), assigned, pelanggan["id"]),
    )
    conn.execute(
        "UPDATE unit_servis SET nama_pelanggan = ? WHERE lower(nama_pelanggan) = lower(?)",
        (nama, nama_pelanggan),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/pelanggan/{nama_pelanggan}/tambah-perangkat")
def halaman_tambah_perangkat(request: Request, nama_pelanggan: str):
    role, _ = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="tambah_perangkat.html",
        context={"nama_pelanggan": nama_pelanggan},
    )

@app.post("/pelanggan/{nama_pelanggan}/tambah-perangkat")
def tambah_perangkat(
    request: Request,
    nama_pelanggan: str,
    mode: str = Form(...),
    nama_unit: str = Form(...),
    kategori_lainnya: str = Form(""),
    foto_perangkat: UploadFile = File(None),
):
    role, _ = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    mode = kategori_lainnya.strip() if mode == "Other" else mode.strip()
    if not mode:
        return RedirectResponse("/dashboard", status_code=303)

    foto_perangkat_name = simpan_foto(
        foto_perangkat,
        f"perangkat-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
    )

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO pelanggan (nama) VALUES (?) ON CONFLICT (nama) DO NOTHING",
        (nama_pelanggan.strip(),),
    )
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO unit_servis (nama_pelanggan, mode, unit, status, foto_perangkat) VALUES (?, ?, ?, ?, ?) RETURNING id",
        (nama_pelanggan.strip(), mode, nama_unit.strip(), "Baru Terdaftar", foto_perangkat_name),
    )
    unit_id = cursor.fetchone()["id"]
    kode_unik = f"{mode[:3].upper()}-{unit_id:03d}"
    cursor.execute("UPDATE unit_servis SET kode_unik = ? WHERE id = ?", (kode_unik, unit_id))
    cursor.execute(
        """
        INSERT INTO history_servis
        (unit_id, tanggal, item_servis, kondisi_before, kondisi_after, nama_teknisi, servis_selanjutnya)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (unit_id, datetime.now().strftime("%Y-%m-%d"), "Registrasi perangkat", "-", "Belum diservis", "Teknisi", "-"),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.post("/tambah-unit")
def tambah_unit(
    request: Request,
    nama_pelanggan: str = Form(...),
    mode: str = Form(...),
    nama_unit: str = Form(...)
):
    role, _ = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()

    # 1. Simpan unit dulu untuk mendapatkan ID otomatis
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO unit_servis (nama_pelanggan, mode, unit, status) VALUES (?, ?, ?, ?) RETURNING id",
        (nama_pelanggan, mode, nama_unit, "Baru Terdaftar")
    )
    unit_id = cursor.fetchone()["id"]

    # 2. Buat Kode Unik berdasarkan ID (Contoh: AC-001, KLK-002, dll)
    prefix = mode[:3].upper() # AC -> AC, Kulkas -> KUL
    kode_unik = f"{prefix}-{unit_id:03d}" # Contoh: AC-001

    # 3. Update kode_unik ke database
    cursor.execute("UPDATE unit_servis SET kode_unik = ? WHERE id = ?", (kode_unik, unit_id))
    cursor.execute(
        """
        INSERT INTO history_servis
        (unit_id, tanggal, item_servis, kondisi_before, kondisi_after, nama_teknisi, servis_selanjutnya)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (unit_id, datetime.now().strftime("%Y-%m-%d"), "Registrasi perangkat", "-", "Belum diservis", "Teknisi", "-"),
    )
    conn.commit()

    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# 🏷️ ENDPOINT KHUSUS: Generate QR Code dalam bentuk Gambar (PNG)
@app.get("/generate-qr/{kode_unik}")
def generate_qr(request: Request, kode_unik: str):
    # Buat QR Code berisi teks kode_unik
    img = qrcode.make(str(request.url_for("lihat_unit", kode_unik=kode_unik)))

    # Simpan ke memori sementara (RAM) lalu kirim sebagai gambar PNG
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")

@app.get("/print-qr/{kode_unik}")
def print_qr(request: Request, kode_unik: str):
    role = get_session_role(request)
    if role not in {"teknisi", "customer", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    _, username = get_current_user(request)
    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    if role == "customer":
        customer = conn.execute("SELECT nama FROM pelanggan WHERE username = ?", (username,)).fetchone()
        if not customer or not unit or customer["nama"].lower() != unit["nama_pelanggan"].lower():
            unit = None
    conn.close()
    if not unit:
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Perangkat Tidak Ditemukan", "pesan": "Kode perangkat tidak terdaftar.", "dashboard_url": "/dashboard"},
            status_code=404,
        )
    return templates.TemplateResponse(request=request, name="print_qr.html", context={"unit": unit})

@app.post("/ubah-status/{unit_id}")
def ubah_status(request: Request, unit_id: int):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE id = ?", (unit_id,)).fetchone()

    # Cek ownership: teknisi hanya boleh ubah unit milik pelanggannya
    if not unit or not can_access_unit(conn, unit, role, current_username):
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    status_sekarang = unit["status"]
    if status_sekarang == "Baru Terdaftar" or status_sekarang == "Perlu Servis":
        status_baru = "Dalam Proses"
    elif status_sekarang == "Dalam Proses":
        status_baru = "Selesai"
    else:
        status_baru = "Perlu Servis"

    conn.execute("UPDATE unit_servis SET status = ? WHERE id = ?", (status_baru, unit_id))
    conn.execute(
        """
        INSERT INTO history_servis
        (unit_id, tanggal, item_servis, kondisi_before, kondisi_after, nama_teknisi, servis_selanjutnya)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit_id,
            datetime.now().strftime("%Y-%m-%d"),
            "Perubahan status perangkat",
            status_sekarang,
            status_baru,
            current_username or "Teknisi",
            "Sesuai kebutuhan",
        ),
    )
    conn.commit()

    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

@app.post("/hapus-unit/{unit_id}")
def hapus_unit(request: Request, unit_id: int):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE id = ?", (unit_id,)).fetchone()
    if not unit or not can_access_unit(conn, unit, role, current_username):
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    # Hapus dulu history & foto terkait untuk hindari orphan record
    history_rows = conn.execute(
        "SELECT id FROM history_servis WHERE unit_id = ?", (unit_id,)
    ).fetchall()
    if history_rows:
        history_ids = [row["id"] for row in history_rows]
        placeholders = ", ".join("?" for _ in history_ids)
        conn.execute(f"DELETE FROM history_foto WHERE history_id IN ({placeholders})", history_ids)
        conn.execute(f"DELETE FROM history_servis WHERE id IN ({placeholders})", history_ids)
    conn.execute("DELETE FROM unit_servis WHERE id = ?", (unit_id,))
    conn.commit()

    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

@app.get("/scan-qr")
def halaman_scan_qr(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="scan_qr.html",
        context={"judul": "Scan QR Perangkat", "dashboard_url": get_dashboard_url(request)}
    )

@app.get("/login/teknisi")
def halaman_login_teknisi(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "judul": "Login Teknisi",
            "role": "teknisi",
            "error": None
        }
    )

@app.post("/login/teknisi")
def login_teknisi(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    teknisi = conn.execute(
        "SELECT username, password_hash, role, is_active FROM profil_teknisi WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()

    if teknisi and (teknisi["is_active"] in (None, 1, True, "1")) and verify_password(teknisi["password_hash"], password):
        # Upgrade transparan hash lama (SHA-256) ke PBKDF2 saat login berhasil.
        if teknisi["password_hash"] and not teknisi["password_hash"].startswith(f"{PBKDF2_PREFIX}$"):
            conn.execute(
                "UPDATE profil_teknisi SET password_hash = ? WHERE username = ?",
                (hash_password(password), teknisi["username"]),
            )
            conn.commit()
        conn.close()
        response = RedirectResponse("/dashboard", status_code=303)
        set_session_cookies(response, teknisi["role"] or "teknisi", teknisi["username"])
        return response
    conn.close()

    # Fallback login superuser via env var — hanya jalan bila TECHNICIAN_PASSWORD diisi.
    if (
        TECHNICIAN_PASSWORD
        and hmac.compare_digest(username.strip(), TECHNICIAN_USERNAME)
        and hmac.compare_digest(password, TECHNICIAN_PASSWORD)
    ):
        response = RedirectResponse("/dashboard", status_code=303)
        set_session_cookies(response, "superuser", TECHNICIAN_USERNAME)
        return response

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"judul": "Login Teknisi", "role": "teknisi", "error": "Username atau password salah."},
        status_code=401
    )

@app.get("/teknisi/{username}/edit")
def halaman_edit_teknisi(request: Request, username: str):
    role, current_username = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    teknisi = conn.execute(
        "SELECT * FROM profil_teknisi WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()
    conn.close()

    if not teknisi:
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Teknisi Tidak Ditemukan", "pesan": "Akun teknisi tidak terdaftar.", "dashboard_url": "/dashboard"},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="edit_teknisi.html",
        context={"teknisi": teknisi, "current_username": current_username},
    )

@app.post("/teknisi/{username}/edit")
def edit_teknisi(
    request: Request,
    username: str,
    nama: str = Form(""),
    new_username: str = Form(""),
    no_hp: str = Form(""),
    password: str = Form(""),
):
    role, current_username = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    teknisi = conn.execute(
        "SELECT * FROM profil_teknisi WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()
    if not teknisi:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    updated_username = (new_username or username).strip()
    updated_nama = (nama or teknisi["nama"] or teknisi["username"]).strip()
    if not updated_username:
        updated_username = teknisi["username"]
    if not updated_nama:
        updated_nama = teknisi["username"]

    existing_teknisi = conn.execute(
        "SELECT id FROM profil_teknisi WHERE lower(username) = lower(?) AND id != ?",
        (updated_username, teknisi["id"]),
    ).fetchone()
    if existing_teknisi:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    password_hash = teknisi["password_hash"]
    if password.strip():
        password_hash = hash_customer_password(password)

    conn.execute(
        "UPDATE profil_teknisi SET nama = ?, username = ?, no_hp = ?, password_hash = ? WHERE id = ?",
        (updated_nama, updated_username, no_hp.strip(), password_hash, teknisi["id"]),
    )
    if updated_username != teknisi["username"]:
        conn.execute(
            "UPDATE pelanggan SET teknisi_username = ? WHERE teknisi_username = ?",
            (updated_username, teknisi["username"]),
        )
        conn.commit()
    conn.close()

    response = RedirectResponse("/dashboard", status_code=303)
    if current_username == teknisi["username"]:
        # Perbarui sesi agar username baru langsung dipakai tanpa perlu login ulang.
        set_session_cookies(response, "superuser", updated_username)
    return response

@app.post("/teknisi/{username}/delete")
def hapus_teknisi(request: Request, username: str):
    role, current_username = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    teknisi = conn.execute(
        "SELECT * FROM profil_teknisi WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()
    if not teknisi or teknisi["username"] == current_username:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    conn.execute(
        "UPDATE pelanggan SET teknisi_username = NULL WHERE teknisi_username = ?",
        (teknisi["username"],),
    )
    conn.execute("DELETE FROM profil_teknisi WHERE id = ?", (teknisi["id"],))
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.post("/pelanggan/{nama_pelanggan}/delete")
def hapus_pelanggan(request: Request, nama_pelanggan: str):
    role, _ = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    pelanggan = conn.execute(
        "SELECT * FROM pelanggan WHERE lower(nama) = lower(?)",
        (nama_pelanggan.strip(),),
    ).fetchone()
    if pelanggan:
        unit_rows = conn.execute(
            "SELECT id FROM unit_servis WHERE lower(nama_pelanggan) = lower(?)",
            (nama_pelanggan.strip(),),
        ).fetchall()
        for unit_row in unit_rows:
            conn.execute("DELETE FROM history_servis WHERE unit_id = ?", (unit_row["id"],))
        if unit_rows:
            unit_ids = [row["id"] for row in unit_rows]
            placeholders = ", ".join("?" for _ in unit_ids)
            conn.execute(f"DELETE FROM unit_servis WHERE id IN ({placeholders})", unit_ids)
        conn.execute("DELETE FROM pelanggan WHERE id = ?", (pelanggan["id"],))
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.post("/teknisi/{username}/toggle-status")
def toggle_teknisi_status(request: Request, username: str, is_active: str = Form("0")):
    role, current_username = get_current_user(request)
    if role != "superuser":
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    teknisi = conn.execute(
        "SELECT * FROM profil_teknisi WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()
    if not teknisi:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    if teknisi["username"] == current_username:
        conn.close()
        return RedirectResponse("/dashboard", status_code=303)

    new_status = 1 if is_active in ("1", "true", "on", "aktif") else 0
    conn.execute(
        "UPDATE profil_teknisi SET is_active = ? WHERE id = ?",
        (new_status, teknisi["id"]),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/login/customer")
def halaman_login_customer(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "judul": "Login Customer",
            "role": "customer",
            "error": None
        }
    )

@app.post("/login/customer")
def login_customer(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    customer = conn.execute(
        "SELECT id, nama, username, password_hash FROM pelanggan WHERE username = ?",
        (username.strip(),),
    ).fetchone()

    if customer and verify_password(customer["password_hash"], password):
        # Upgrade transparan hash lama ke PBKDF2.
        if customer["password_hash"] and not customer["password_hash"].startswith(f"{PBKDF2_PREFIX}$"):
            conn.execute(
                "UPDATE pelanggan SET password_hash = ? WHERE id = ?",
                (hash_password(password), customer["id"]),
            )
            conn.commit()
        conn.close()
        response = RedirectResponse("/dashboard/customer", status_code=303)
        set_session_cookies(response, "customer", customer["username"])
        return response
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "judul": "Login Customer",
            "role": "customer",
            "error": "Username atau password customer salah."
        },
        status_code=401
    )

@app.get("/unit/{kode_unik}")
def lihat_unit(request: Request, kode_unik: str):
    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    history = []
    foto_history = {}
    alamat_pelanggan = ""
    teknisi_phone = ""
    if unit:
        history = conn.execute(
            "SELECT * FROM history_servis WHERE unit_id = ? ORDER BY tanggal DESC, id DESC",
            (unit["id"],),
        ).fetchall()
        customer = conn.execute(
            "SELECT alamat FROM pelanggan WHERE lower(nama) = lower(?)",
            (unit["nama_pelanggan"],),
        ).fetchone()
        alamat_pelanggan = customer["alamat"] if customer and customer["alamat"] else "Belum ada alamat"
        if history:
            serviced_by = conn.execute(
                "SELECT no_hp FROM profil_teknisi WHERE username = ? OR username = ? LIMIT 1",
                (history[0]["nama_teknisi"], TECHNICIAN_USERNAME),
            ).fetchone()
            teknisi_phone = normalize_phone(serviced_by["no_hp"] if serviced_by else "")
        foto_rows = conn.execute(
            "SELECT history_id, jenis, nama_file FROM history_foto WHERE history_id IN (SELECT id FROM history_servis WHERE unit_id = ?)",
            (unit["id"],),
        ).fetchall()
        for foto in foto_rows:
            foto_history.setdefault(foto["history_id"], {}).setdefault(foto["jenis"], []).append(foto["nama_file"])
        for item in history:
            foto_history.setdefault(item["id"], {})
            if item["foto_before"] and not foto_history[item["id"]].get("before"):
                foto_history[item["id"]]["before"] = [item["foto_before"]]
            if item["foto_after"] and not foto_history[item["id"]].get("after"):
                foto_history[item["id"]]["after"] = [item["foto_after"]]
    conn.close()
    if not unit:
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Perangkat Tidak Ditemukan", "pesan": "Kode QR tidak terdaftar.", "dashboard_url": get_dashboard_url(request)},
            status_code=404
        )
    request_text = (
        "Halo Teknisi, saya ingin request servis.\n"
        f"Nama pelanggan: {unit['nama_pelanggan']}\n"
        f"Alamat: {alamat_pelanggan}\n"
        f"Perangkat: {unit['unit']} ({unit['kode_unik']})\n"
        "Request servis: "
    )
    request_url = f"https://wa.me/{teknisi_phone}?text={quote(request_text)}" if teknisi_phone else None
    role, current_username = get_current_user(request)
    return templates.TemplateResponse(
        request=request,
        name="unit.html",
        context={
            "unit": unit,
            "history": history,
            "foto_history": foto_history,
            "is_teknisi": role in {"teknisi", "superuser"},
            "is_superuser": role == "superuser",
            "is_logged_in": role in {"teknisi", "customer", "superuser"},
            "current_username": current_username,
            "reminder": reminder_text(history[0]["servis_selanjutnya"] if history else None),
            "dashboard_url": get_dashboard_url(request),
            "request_url": request_url,
            "teknisi_phone_available": bool(teknisi_phone),
        },
    )

@app.post("/unit/{kode_unik}/foto-perangkat")
def tambah_foto_perangkat(
    request: Request,
    kode_unik: str,
    foto_perangkat: UploadFile = File(...),
):
    role = get_session_role(request)
    if role not in {"teknisi", "customer"}:
        return RedirectResponse("/", status_code=303)

    if not foto_perangkat or not foto_perangkat.filename:
        return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

    filename = simpan_foto(foto_perangkat, f"{kode_unik}-perangkat")
    if not filename:
        return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

    conn = get_db_connection()
    unit = conn.execute("SELECT id FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    if not unit:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Perangkat Tidak Ditemukan", "pesan": "Kode perangkat tidak terdaftar.", "dashboard_url": get_dashboard_url(request)},
            status_code=404,
        )

    conn.execute(
        "UPDATE unit_servis SET foto_perangkat = ? WHERE id = ?",
        (filename, unit["id"]),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

@app.get("/unit/{kode_unik}/tambah-history")
def halaman_tambah_history(request: Request, kode_unik: str):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    conn.close()
    if not unit:
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Perangkat Tidak Ditemukan", "pesan": "Kode perangkat tidak terdaftar.", "dashboard_url": get_dashboard_url(request)},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="tambah_history.html",
        context={
            "unit": unit,
            "tanggal_sekarang": datetime.now().strftime("%Y-%m-%d"),
            "nama_teknisi": current_username or TECHNICIAN_USERNAME,
        },
    )

@app.post("/unit/{kode_unik}/tambah-history")
def tambah_history(
    request: Request,
    kode_unik: str,
    tanggal: str = Form(...),
    item_servis: List[str] = Form(...),
    kondisi_before: str = Form(...),
    kondisi_after: str = Form(...),
    nama_teknisi: str = Form(...),
    servis_selanjutnya: str = Form(...),
    foto_before: List[UploadFile] = File(None),
    foto_after: List[UploadFile] = File(None),
):
    role, _ = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    unit = conn.execute("SELECT id FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    if not unit:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "Perangkat Tidak Ditemukan", "pesan": "Kode perangkat tidak terdaftar.", "dashboard_url": get_dashboard_url(request)},
            status_code=404,
        )

    item_list = [item.strip() for item in item_servis if item.strip()]
    if not item_list:
        conn.close()
        return RedirectResponse(f"/unit/{kode_unik}/tambah-history", status_code=303)

    before_filenames = simpan_banyak_foto(foto_before, f"{kode_unik}-before")
    after_filenames = simpan_banyak_foto(foto_after, f"{kode_unik}-after")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO history_servis
        (unit_id, tanggal, item_servis, kondisi_before, kondisi_after, nama_teknisi, servis_selanjutnya, foto_before, foto_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
        """,
        (
            unit["id"],
            tanggal.strip(),
            "\n".join(item_list),
            kondisi_before.strip(),
            kondisi_after.strip(),
            nama_teknisi.strip(),
            servis_selanjutnya.strip(),
            before_filenames[0] if before_filenames else None,
            after_filenames[0] if after_filenames else None,
        ),
    )
    history_id = cursor.fetchone()["id"]
    cursor.executemany(
        "INSERT INTO history_foto (history_id, jenis, nama_file) VALUES (?, ?, ?)",
        [(history_id, "before", filename) for filename in before_filenames]
        + [(history_id, "after", filename) for filename in after_filenames],
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

@app.get("/unit/{kode_unik}/history/{history_id}/edit")
def halaman_edit_history(request: Request, kode_unik: str, history_id: int):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    record = conn.execute(
        """
        SELECT h.*, u.kode_unik, u.nama_pelanggan, u.unit
        FROM history_servis h JOIN unit_servis u ON u.id = h.unit_id
        WHERE u.kode_unik = ? AND h.id = ?
        """,
        (kode_unik, history_id),
    ).fetchone()
    if not record:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="aksi.html",
            context={"judul": "History Tidak Ditemukan", "pesan": "Catatan history tidak terdaftar.", "dashboard_url": get_dashboard_url(request)},
            status_code=404,
        )
    if not can_manage_history(record, role, current_username):
        conn.close()
        return RedirectResponse(f"/unit/{kode_unik}", status_code=303)
    photos = conn.execute(
        "SELECT jenis, nama_file FROM history_foto WHERE history_id = ? ORDER BY id",
        (history_id,),
    ).fetchall()
    conn.close()
    photo_map = {"before": [], "after": []}
    for photo in photos:
        photo_map[photo["jenis"]].append(photo["nama_file"])
    if record["foto_before"] and not photo_map["before"]:
        photo_map["before"].append(record["foto_before"])
    if record["foto_after"] and not photo_map["after"]:
        photo_map["after"].append(record["foto_after"])
    return templates.TemplateResponse(request=request, name="edit_history.html", context={"record": record, "photo_map": photo_map})

@app.post("/unit/{kode_unik}/history/{history_id}/edit")
def edit_history(
    request: Request,
    kode_unik: str,
    history_id: int,
    tanggal: str = Form(...),
    item_servis: str = Form(...),
    kondisi_before: str = Form(...),
    kondisi_after: str = Form(...),
    nama_teknisi: str = Form(...),
    servis_selanjutnya: str = Form(...),
    foto_before: List[UploadFile] = File(None),
    foto_after: List[UploadFile] = File(None),
):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    record = conn.execute(
        "SELECT h.* FROM history_servis h JOIN unit_servis u ON u.id = h.unit_id WHERE u.kode_unik = ? AND h.id = ?",
        (kode_unik, history_id),
    ).fetchone()
    if not record or not can_manage_history(record, role, current_username):
        conn.close()
        return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

    before_filenames = simpan_banyak_foto(foto_before, f"{kode_unik}-before")
    after_filenames = simpan_banyak_foto(foto_after, f"{kode_unik}-after")
    before_filename = record["foto_before"] or (before_filenames[0] if before_filenames else None)
    after_filename = record["foto_after"] or (after_filenames[0] if after_filenames else None)
    conn.execute(
        """
        UPDATE history_servis
        SET tanggal = ?, item_servis = ?, kondisi_before = ?, kondisi_after = ?,
            nama_teknisi = ?, servis_selanjutnya = ?, foto_before = ?, foto_after = ?
        WHERE id = ?
        """,
        (
            tanggal.strip(), item_servis.strip(), kondisi_before.strip(), kondisi_after.strip(),
            nama_teknisi.strip(), servis_selanjutnya.strip(), before_filename, after_filename, history_id,
        ),
    )
    conn.executemany(
        "INSERT INTO history_foto (history_id, jenis, nama_file) VALUES (?, ?, ?)",
        [(history_id, "before", filename) for filename in before_filenames]
        + [(history_id, "after", filename) for filename in after_filenames],
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

@app.post("/unit/{kode_unik}/history/{history_id}/delete")
def hapus_history(request: Request, kode_unik: str, history_id: int):
    role, current_username = get_current_user(request)
    if role not in {"teknisi", "superuser"}:
        return RedirectResponse("/login/teknisi", status_code=303)

    conn = get_db_connection()
    record = conn.execute(
        "SELECT h.* FROM history_servis h JOIN unit_servis u ON u.id = h.unit_id WHERE u.kode_unik = ? AND h.id = ?",
        (kode_unik, history_id),
    ).fetchone()
    if not record or not can_manage_history(record, role, current_username):
        conn.close()
        return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

    conn.execute("DELETE FROM history_foto WHERE history_id = ?", (history_id,))
    conn.execute("DELETE FROM history_servis WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/unit/{kode_unik}", status_code=303)

@app.get("/unit")
def cari_unit(request: Request, kode_unik: str):
    return RedirectResponse(f"/unit/{kode_unik.strip().upper()}", status_code=303)

@app.get("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("ac_session")
    response.delete_cookie("technician_username")
    response.delete_cookie("customer_name")
    response.delete_cookie("customer_username")
    return response