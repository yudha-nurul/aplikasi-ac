import sqlite3
import qrcode
from datetime import datetime, timedelta
from io import BytesIO
from fastapi import FastAPI, Request, Form
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB_NAME = "aplikasi_ac.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# 🛠️ Inisialisasi Database dengan kolom No HP & Tanggal
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
# 🌐 ENDPOINTS APLIKASI
# -------------------------------------------------------------

@app.get("/")
def halaman_utama(request: Request):
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
    tgl_servis: str = Form(...) # Format YYYY-MM-DD dari HTML
):
    # Hitung Jadwal Servis Berikutnya (Otomatis +90 hari / 3 Bulan)
    tgl_obj = datetime.strptime(tgl_servis, "%Y-%m-%d")
    tgl_next_obj = tgl_obj + timedelta(days=90)
    tgl_next_servis = tgl_next_obj.strftime("%Y-%m-%d")

    # Formatkan Nomor HP agar berawalan 62 untuk WhatsApp (misal 0812... -> 62812...)
    no_hp_clean = no_hp.strip().replace("-", "").replace(" ", "")
    if no_hp_clean.startswith("0"):
        no_hp_clean = "62" + no_hp_clean[1:]

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Simpan data baru
    cursor.execute("""
        INSERT INTO unit_servis (nama_pelanggan, no_hp, mode, unit, status, tgl_servis, tgl_next_servis) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (nama_pelanggan, no_hp_clean, mode, nama_unit, "Baru Terdaftar", tgl_servis, tgl_next_servis))
    
    unit_id = cursor.lastrowid
    
    # 2. Buat Kode Unik (misal: AC-001)
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

@app.get("/generate-qr/{kode_unik}")
def generate_qr(kode_unik: str):
    img = qrcode.make(f"https://aplikasi-ac.com/unit/{kode_unik}")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.post("/ubah-status/{unit_id}")
def ubah_status(request: Request, unit_id: int):
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