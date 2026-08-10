import sqlite3
import qrcode
from datetime import datetime, timedelta
from io import BytesIO
from fastapi import FastAPI, Request, Form, HTTPException, status, Response
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Mount folder static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Endpoint khusus Service Worker PWA
@app.get("/sw.js")
def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")

DB_NAME = "aplikasi_ac.db"

# 🔒 Credential Login Teknisi (Bisa diubah sesuai keinginan)
ADMIN_USERNAME = "teknisi"
ADMIN_PASSWORD = "ac123password"

# -------------------------------------------------------------
# 🗄️ KONEKSI & INISIALISASI DATABASE
# -------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unit_servis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_unik TEXT UNIQUE,
            nama_pelanggan TEXT NOT NULL,
            no_hp TEXT,
            mode TEXT NOT NULL,
            unit TEXT NOT NULL,
            status TEXT NOT NULL,
            tgl_servis TEXT,
            tgl_next_servis TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------------------------------------------------
# 🔑 HELPER / CEK KUKI LOGIN
# -------------------------------------------------------------
def cek_login_teknisi(request: Request) -> bool:
    """Mengecek apakah user memiliki cookie session 'session_user' yang sah"""
    user_session = request.cookies.get("session_user")
    return user_session == ADMIN_USERNAME

# -------------------------------------------------------------
# 🔐 ENDPOINTS LOGIN & LOGOUT
# -------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def halaman_login(request: Request):
    # Jika sudah login, langsung lempar ke Dashboard utama
    if cek_login_teknisi(request):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
def proses_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # Jika cocok, buat kuki session & redirect ke Dashboard
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_user", value=username, httponly=True)
        return response
    else:
        # Jika gagal, kembalikan ke login.html dengan pesan error
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Username atau Password salah!"}
        )

@app.get("/logout")
def proses_logout():
    # Hapus cookie session lalu redirect ke login
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_user")
    return response

# -------------------------------------------------------------
# 🌐 ENDPOINTS DASHBOARD TEKNISI (TERPROTEKSI)
# -------------------------------------------------------------

@app.get("/")
def halaman_utama(request: Request):
    # 🛡️ Proteksi: Jika belum login, tendang ke /login
    if not cek_login_teknisi(request):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    conn = get_db_connection()
    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "nama_aplikasi": "Sistem Manajemen Servis AC",
            "daftar_unit": daftar_unit
        }
    )

@app.post("/tambah-unit")
def tambah_unit(
    request: Request,
    nama_pelanggan: str = Form(...),
    no_hp: str = Form(...),
    mode: str = Form(...),
    nama_unit: str = Form(...),
    tgl_servis: str = Form(...)
):
    # 🛡️ Proteksi Aksi
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak. Silakan login terlebih dahulu.")

    tgl_obj = datetime.strptime(tgl_servis, "%Y-%m-%d")
    tgl_next_obj = tgl_obj + timedelta(days=90)
    tgl_next_servis = tgl_next_obj.strftime("%Y-%m-%d")

    no_hp_clean = no_hp.strip().replace("-", "").replace(" ", "")
    if no_hp_clean.startswith("0"):
        no_hp_clean = "62" + no_hp_clean[1:]

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO unit_servis (nama_pelanggan, no_hp, mode, unit, status, tgl_servis, tgl_next_servis) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (nama_pelanggan, no_hp_clean, mode, nama_unit, "Baru Terdaftar", tgl_servis, tgl_next_servis))
    
    unit_id = cursor.lastrowid
    
    prefix = mode[:3].upper()
    kode_unik = f"{prefix}-{unit_id:03d}"
    
    cursor.execute("UPDATE unit_servis SET kode_unik = ? WHERE id = ?", (kode_unik, unit_id))
    conn.commit()
    
    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

@app.post("/ubah-status/{unit_id}")
def ubah_status(request: Request, unit_id: int):
    # 🛡️ Proteksi Aksi
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak")

    conn = get_db_connection()
    unit = conn.execute("SELECT status FROM unit_servis WHERE id = ?", (unit_id,)).fetchone()
    
    if unit:
        status_sekarang = unit["status"]
        if status_sekarang == "Baru Terdaftar" or status_sekarang == "Perlu Servis":
            status_baru = "Dalam Proses"
        elif status_sekarang == "Dalam Proses":
            status_baru = "Selesai"
        else:
            status_baru = "Perlu Servis"

        conn.execute("UPDATE unit_servis SET status = ? WHERE id = ?", (status_baru, unit_id))
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
    # 🛡️ Proteksi Aksi
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak")

    conn = get_db_connection()
    conn.execute("DELETE FROM unit_servis WHERE id = ?", (unit_id,))
    conn.commit()

    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# -------------------------------------------------------------
# 📱 HALAMAN PUBLIK / PELANGGAN & QR CODE (BEBAS AKSES)
# -------------------------------------------------------------
@app.get("/generate-qr/{kode_unik}")
def generate_qr(request: Request, kode_unik: str):
    base_url = str(request.base_url).rstrip('/')
    target_url = f"{base_url}/unit/{kode_unik}"
    
    img = qrcode.make(target_url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/unit/{kode_unik}")
def detail_unit_publik(request: Request, kode_unik: str):
    conn = get_db_connection()
    unit = conn.execute("SELECT * FROM unit_servis WHERE kode_unik = ?", (kode_unik,)).fetchone()
    conn.close()

    if not unit:
        raise HTTPException(status_code=404, detail="Unit tidak ditemukan")

    no_wa_teknisi = "6281234567890"  # Nomor WA Teknisi untuk booking

    return templates.TemplateResponse(
        request=request,
        name="publik_unit.html",
        context={
            "unit": unit,
            "no_wa_teknisi": no_wa_teknisi
        }
    )