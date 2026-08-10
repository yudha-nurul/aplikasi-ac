import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import qrcode
from datetime import datetime, timedelta
from io import BytesIO
from fastapi import FastAPI, Request, Form, HTTPException, status, Response
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

# Mount folder static & PWA
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# 🔒 Credential Login Teknisi
ADMIN_USERNAME = "teknisi"
ADMIN_PASSWORD = "ac123password"

# 🗄️ DATABASE CONFIGURATION (Neon.tech PostgreSQL / SQLite Fallback)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        # Jika DATABASE_URL ada (Vercel / Neon.tech)
        # Penanganan khusus jika URL diawali postgres:// menjadi postgresql://
        db_url = DATABASE_URL.replace("postgres://", "postgresql://")
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn
    else:
        # Jika dijalankan secara lokal (SQLite)
        conn = sqlite3.connect("aplikasi_ac.db")
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            # Query khusus PostgreSQL (Neon.tech)
            query = """
                CREATE TABLE IF NOT EXISTS unit_servis (
                    id SERIAL PRIMARY KEY,
                    kode_unik VARCHAR(50) UNIQUE,
                    nama_pelanggan VARCHAR(100) NOT NULL,
                    no_hp VARCHAR(20),
                    mode VARCHAR(50) NOT NULL,
                    unit VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    tgl_servis VARCHAR(20),
                    tgl_next_servis VARCHAR(20)
                );
            """
        else:
            # Query khusus SQLite lokal
            query = """
                CREATE TABLE IF NOT EXISTS unit_servis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kode_unik VARCHAR(50) UNIQUE,
                    nama_pelanggan VARCHAR(100) NOT NULL,
                    no_hp VARCHAR(20),
                    mode VARCHAR(50) NOT NULL,
                    unit VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    tgl_servis VARCHAR(20),
                    tgl_next_servis VARCHAR(20)
                );
            """
            
        cursor.execute(query)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error Database Init: {e}")

# Jalankan inisialisasi tabel otomatis saat aplikasi berjalan
init_db()

# --- SERVICE WORKER PWA ---
@app.get("/sw.js")
def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")

# --- HELPER CEK LOGIN ---
def cek_login_teknisi(request: Request) -> bool:
    user_session = request.cookies.get("session_user")
    return user_session == ADMIN_USERNAME

# --- ROUTE LOGIN & LOGOUT ---
@app.get("/login", response_class=HTMLResponse)
def halaman_login(request: Request):
    if cek_login_teknisi(request):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/login")
def proses_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(key="session_user", value=username, httponly=True)
        return response
    else:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Username atau Password salah!"}
        )

@app.get("/logout")
def proses_logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("session_user")
    return response

# --- DASHBOARD TEKNISI ---
@app.get("/")
def halaman_utama(request: Request):
    if not cek_login_teknisi(request):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    init_db() # Memastikan tabel sudah terbuat
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM unit_servis ORDER BY id DESC")
    daftar_unit = cursor.fetchall()
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
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak.")

    tgl_obj = datetime.strptime(tgl_servis, "%Y-%m-%d")
    tgl_next_obj = tgl_obj + timedelta(days=90)
    tgl_next_servis = tgl_next_obj.strftime("%Y-%m-%d")

    no_hp_clean = no_hp.strip().replace("-", "").replace(" ", "")
    if no_hp_clean.startswith("0"):
        no_hp_clean = "62" + no_hp_clean[1:]

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if DATABASE_URL:
        # PostgreSQL syntax (Neon.tech)
        cursor.execute("""
            INSERT INTO unit_servis (nama_pelanggan, no_hp, mode, unit, status, tgl_servis, tgl_next_servis) 
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (nama_pelanggan, no_hp_clean, mode, nama_unit, "Baru Terdaftar", tgl_servis, tgl_next_servis))
        row = cursor.fetchone()
        unit_id = row['id'] if isinstance(row, dict) else row[0]
        
        prefix = mode[:3].upper()
        kode_unik = f"{prefix}-{unit_id:03d}"
        cursor.execute("UPDATE unit_servis SET kode_unik = %s WHERE id = %s", (kode_unik, unit_id))
    else:
        # SQLite syntax
        cursor.execute("""
            INSERT INTO unit_servis (nama_pelanggan, no_hp, mode, unit, status, tgl_servis, tgl_next_servis) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (nama_pelanggan, no_hp_clean, mode, nama_unit, "Baru Terdaftar", tgl_servis, tgl_next_servis))
        unit_id = cursor.lastrowid
        
        prefix = mode[:3].upper()
        kode_unik = f"{prefix}-{unit_id:03d}"
        cursor.execute("UPDATE unit_servis SET kode_unik = ? WHERE id = ?", (kode_unik, unit_id))
        
    conn.commit()
    
    cursor.execute("SELECT * FROM unit_servis ORDER BY id DESC")
    daftar_unit = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

@app.post("/ubah-status/{unit_id}")
def ubah_status(request: Request, unit_id: int):
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak")

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if DATABASE_URL:
        cursor.execute("SELECT status FROM unit_servis WHERE id = %s", (unit_id,))
    else:
        cursor.execute("SELECT status FROM unit_servis WHERE id = ?", (unit_id,))
        
    unit = cursor.fetchone()
    
    if unit:
        status_sekarang = unit["status"] if isinstance(unit, dict) else unit[0]
        if status_sekarang in ["Baru Terdaftar", "Perlu Servis"]:
            status_baru = "Dalam Proses"
        elif status_sekarang == "Dalam Proses":
            status_baru = "Selesai"
        else:
            status_baru = "Perlu Servis"

        if DATABASE_URL:
            cursor.execute("UPDATE unit_servis SET status = %s WHERE id = %s", (status_baru, unit_id))
        else:
            cursor.execute("UPDATE unit_servis SET status = ? WHERE id = ?", (status_baru, unit_id))
            
        conn.commit()

    cursor.execute("SELECT * FROM unit_servis ORDER BY id DESC")
    daftar_unit = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

@app.post("/hapus-unit/{unit_id}")
def hapus_unit(request: Request, unit_id: int):
    if not cek_login_teknisi(request):
        raise HTTPException(status_code=401, detail="Akses ditolak")

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if DATABASE_URL:
        cursor.execute("DELETE FROM unit_servis WHERE id = %s", (unit_id,))
    else:
        cursor.execute("DELETE FROM unit_servis WHERE id = ?", (unit_id,))
        
    conn.commit()

    cursor.execute("SELECT * FROM unit_servis ORDER BY id DESC")
    daftar_unit = cursor.fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# --- HALAMAN PUBLIK & QR CODE ---
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
    cursor = conn.cursor()
    
    if DATABASE_URL:
        cursor.execute("SELECT * FROM unit_servis WHERE kode_unik = %s", (kode_unik,))
    else:
        cursor.execute("SELECT * FROM unit_servis WHERE kode_unik = ?", (kode_unik,))
        
    unit = cursor.fetchone()
    conn.close()

    if not unit:
        raise HTTPException(status_code=404, detail="Unit tidak ditemukan")

    no_wa_teknisi = "6281234567890"

    return templates.TemplateResponse(
        request=request,
        name="publik_unit.html",
        context={
            "unit": unit,
            "no_wa_teknisi": no_wa_teknisi
        }
    )