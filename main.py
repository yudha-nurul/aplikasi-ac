import sqlite3
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB_NAME = "aplikasi_ac.db"

# 🛠️ Fungsi pembantu untuk koneksi ke Database SQLite
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    # conn.row_factory memungkingkan kita mengambil data dalam bentuk dictionary
    conn.row_factory = sqlite3.Row
    return conn

# 🛠️ Inisialisasi Tabel SQLite saat aplikasi dijalankan
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unit_servis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama_pelanggan TEXT NOT NULL,
            mode TEXT NOT NULL,
            unit TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# Jalankan fungsi pembuat tabel
init_db()

# -------------------------------------------------------------
# 🌐 ROUTE & ENDPOINTS APLIKASI
# -------------------------------------------------------------

# 1. Halaman Utama: Mengambil data dari SQLite
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

# 2. Tambah Data Baru ke Database SQLite
@app.post("/tambah-unit")
def tambah_unit(
    request: Request,
    nama_pelanggan: str = Form(...),
    mode: str = Form(...),
    nama_unit: str = Form(...)
):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO unit_servis (nama_pelanggan, mode, unit, status) VALUES (?, ?, ?, ?)",
        (nama_pelanggan, mode, nama_unit, "Baru Terdaftar")
    )
    conn.commit()
    
    # Ambil ulang daftar unit terbaru dari DB
    daftar_unit = conn.execute("SELECT * FROM unit_servis ORDER BY id DESC").fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# 3. Ubah Status Unit Berdasarkan ID Unik di Database
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

# 4. Hapus Unit Berdasarkan ID Unik di Database
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