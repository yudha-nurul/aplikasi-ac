# Aplikasi AC Service Management

Aplikasi ini merupakan sistem manajemen servis AC berbasis FastAPI dengan antarmuka web menggunakan Jinja2 dan Tailwind CSS. Aplikasi ini dibuat untuk membantu teknisi dan superuser dalam:

- mengelola data pelanggan,
- mengelola data perangkat/unit,
- menambahkan history servis,
- memantau status perangkat,
- mengelola akun teknisi,
- dan memanfaatkan QR code untuk melihat status perangkat.

---

## Fitur utama

### Untuk Superuser
- Login sebagai superuser
- Menambah teknisi baru
- Mengubah profil teknisi (nama, username, no HP, password)
- Mengaktifkan atau menonaktifkan akun teknisi
- Menghapus teknisi dan pelanggan
- Melihat daftar teknisi, pelanggan, dan history servis
- Mencari pelanggan/teknisi dari dashboard

### Untuk Teknisi
- Login ke dashboard teknisi
- Menambah pelanggan
- Menambah perangkat/unit pelanggan
- Menyimpan foto perangkat
- Menambah history servis
- Menyimpan foto sebelum/sesudah servis
- Menampilkan QR code perangkat
- Melihat status perangkat dan history
- Mengubah profil teknisi sendiri

### Untuk Customer
- Login dengan akun pelanggan
- Melihat daftar perangkat miliknya
- Melihat status perangkat
- Mencetak QR code perangkat
- Menambahkan foto perangkat jika diizinkan oleh aturan aplikasi

---

## Struktur project

```text
.
├── main.py
├── requirements.txt
├── .env.example
├── Procfile
├── render.yaml
├── static/
├── templates/
├── uploads/
├── aplikasi_ac.db
├── readme-command-summary.md
├── README.md
└── venv-ac/
```

---

## Teknologi yang digunakan

- Python 3
- FastAPI
- Jinja2 Templates
- SQLite (default)
- PostgreSQL (opsional via `DATABASE_URL`)
- Tailwind CSS via CDN
- QR Code library
- Python Multipart
- Pillow

---

## Persiapan awal

### 1) Clone project
```bash
git clone <url-repository>
cd aplikasi-ac
```

### 2) Aktivasi virtual environment
Di Windows PowerShell:

```powershell
(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& .\venv-ac\Scripts\Activate.ps1)
```

### 3) Install dependency
```powershell
pip install -r requirements.txt
```

### 4) Siapkan file environment
Copy file `.env.example` menjadi `.env`, lalu sesuaikan isinya:

```env
DATABASE_URL=postgresql://postgres:your-password@localhost:5432/aplikasi_ac
SESSION_SECRET=replace-with-a-long-random-secret
TECHNICIAN_USERNAME=teknisi
TECHNICIAN_PASSWORD=replace-with-your-technician-password
```

Catatan:
- Jika `DATABASE_URL` tidak diisi, aplikasi akan otomatis memakai SQLite (`aplikasi_ac.db`).
- `TECHNICIAN_USERNAME` dan `TECHNICIAN_PASSWORD` dipakai untuk akun awal superuser/teknisi.

---

## Cara menjalankan aplikasi

### Jalankan aplikasi lokal
```powershell
python main.py
```

Setelah itu, buka browser dan akses:

- `http://localhost:8000`

---

## Login dan role pengguna

### Superuser
- Username default: sesuai nilai `TECHNICIAN_USERNAME`
- Password default: sesuai nilai `TECHNICIAN_PASSWORD`

### Teknisi
- Login melalui halaman login teknisi
- Akun teknisi dapat dibuat oleh superuser dari dashboard

### Customer
- Login melalui halaman login customer
- Username dan password dibuat saat pelanggan ditambahkan

---

## Panduan penggunaan aplikasi

### A. Login sebagai Superuser
1. Buka halaman login teknisi.
2. Gunakan username dan password superuser.
3. Setelah login, Anda masuk ke dashboard superuser.

### B. Menambah teknisi
1. Dari dashboard superuser, klik tombol tambah teknisi.
2. Isi username, password, nama, dan nomor HP.
3. Simpan.

### C. Mengelola teknisi
Di dashboard superuser, Anda dapat:
- melihat daftar teknisi,
- mengubah profil teknisi,
- mengaktifkan atau menonaktifkan akun,
- menghapus teknisi,
- melihat pelanggan dan history servis tiap teknisi.

### D. Menambah pelanggan
1. Klik tombol tambah pelanggan.
2. Isi nama pelanggan, username, password, alamat, nomor HP, dan pilih teknisi yang bertanggung jawab.
3. Simpan.

### E. Menambah perangkat
1. Buka detail pelanggan.
2. Klik tombol tambah perangkat.
3. Isi kategori, nama unit, dan upload foto perangkat jika diperlukan.
4. Sistem akan otomatis menghasilkan kode unik perangkat.

### F. Menambah history servis
1. Buka halaman detail perangkat.
2. Klik tombol tambah history servis.
3. Isi tanggal, item servis, kondisi sebelum/sesudah, nama teknisi, dan servis selanjutnya.
4. Upload foto sebelum/sesudah jika tersedia.

### G. Menggunakan QR code
- QR code dibuat otomatis untuk setiap perangkat.
- QR code bisa dipindai melalui menu scan QR.
- Setelah scan, user dapat melihat status dan history perangkat.
- QR code juga bisa dicetak untuk kebutuhan operasional.

### H. Menambah foto perangkat
- Foto perangkat dapat diupload dari halaman detail perangkat.
- Fitur ini dapat dilakukan oleh teknisi dan customer yang sudah login.
- Jika belum login, pengguna tidak dapat menambahkan foto perangkat.

---

## Dashboard dan navigasi

### Dashboard superuser
- Menampilkan daftar teknisi
- Menampilkan pelanggan yang dimiliki masing-masing teknisi
- Menampilkan jumlah history servis per teknisi
- Menampilkan tombol edit/reset password
- Menampilkan fitur aktif/nonaktif teknisi
- Menampilkan tombol hapus teknisi dan pelanggan
- Menyediakan pencarian pelanggan/teknisi

### Dashboard teknisi
- Menampilkan daftar pelanggan milik teknisi
- Menampilkan daftar perangkat
- Menampilkan tombol scan QR perangkat
- Menampilkan menu tambah pelanggan dan tambah perangkat

### Dashboard customer
- Menampilkan perangkat milik customer tertentu
- Menampilkan status perangkat dan history
- Bisa menambah foto perangkat jika diizinkan

---

## Database

### Default
Secara default aplikasi memakai SQLite:

```text
aplikasi_ac.db
```

### Opsional
Jika Anda ingin memakai PostgreSQL, isi `DATABASE_URL` di file `.env`.

---

## Deployment

Project sudah dilengkapi dengan:

- `Procfile`
- `render.yaml`

Maksudnya, project ini bisa dideploy ke platform Render atau lingkungan server lain yang mendukung aplikasi FastAPI.

---

## Command penting

Untuk command lengkapnya, silakan lihat file:

- `readme-command-summary.md`

---

## Tips penggunaan

- Selalu jalankan `python -m compileall main.py` sebelum melakukan commit.
- Simpan file `.env` dengan aman dan jangan mengekspos password atau secret.
- Saat menambah teknisi baru, pastikan username unik.
- Gunakan peran yang sesuai agar akses antar role tetap aman dan konsisten.

---

## Catatan pengembangan

Aplikasi ini terus dikembangkan untuk kebutuhan manajemen servis AC, termasuk:

- pengelolaan teknisi,
- pengelolaan pelanggan,
- history servis,
- pencarian data,
- QR code,
- dan upload foto perangkat.

Semoga dokumentasi ini membantu Anda memahami dan mengelola project ini lebih mudah.
