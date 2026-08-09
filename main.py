from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Data sementara dengan status awal
daftar_unit = [
    {"nama_pelanggan": "Budi Santoso", "mode": "AC", "unit": "AC Ruang Tamu (1 PK)", "status": "Perlu Servis"},
    {"nama_pelanggan": "Siti Rahma", "mode": "Kulkas", "unit": "Kulkas 2 Pintu Dapur", "status": "Selesai"}
]

@app.get("/")
def halaman_utama(request: Request):
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
    mode: str = Form(...),
    nama_unit: str = Form(...)
):
    unit_baru = {
        "nama_pelanggan": nama_pelanggan,
        "mode": mode,
        "unit": nama_unit,
        "status": "Baru Terdaftar"
    }
    daftar_unit.insert(0, unit_baru)
    
    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# 🔄 Endpoint 1: Ubah Status Unit
@app.post("/ubah-status/{index}")
def ubah_status(request: Request, index: int):
    if 0 <= index < len(daftar_unit):
        # Siklus perubahan status: Baru Terdaftar -> Dalam Proses -> Selesai -> Perlu Servis
        status_sekarang = daftar_unit[index]["status"]
        if status_sekarang == "Baru Terdaftar" or status_sekarang == "Perlu Servis":
            daftar_unit[index]["status"] = "Dalam Proses"
        elif status_sekarang == "Dalam Proses":
            daftar_unit[index]["status"] = "Selesai"
        else:
            daftar_unit[index]["status"] = "Perlu Servis"

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )

# ❌ Endpoint 2: Hapus Unit
@app.post("/hapus-unit/{index}")
def hapus_unit(request: Request, index: int):
    if 0 <= index < len(daftar_unit):
        daftar_unit.pop(index)

    return templates.TemplateResponse(
        request=request,
        name="partials/daftar_unit.html",
        context={"daftar_unit": daftar_unit}
    )