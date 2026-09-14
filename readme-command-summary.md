# Command Summary

Berikut ringkasan command yang umum dipakai dalam pengembangan project ini, beserta penjelasan singkatnya.

## 1) Masuk ke folder project
```powershell
cd 'd:\Project\04_aplikasi-ac'
```

Penjelasan:
- Mengarahkan terminal ke folder utama project.
- Semua command berikutnya akan dijalankan di lokasi project yang benar.

---

## 2) Aktifkan virtual environment Python
```powershell
(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& d:\Project\04_aplikasi-ac\venv-ac\Scripts\Activate.ps1)
```

Penjelasan:
- Mengaktifkan environment Python lokal agar dependency project seperti FastAPI, Jinja2, qrcode, dan lainnya tersedia.
- `Set-ExecutionPolicy -Scope Process` hanya berlaku untuk session saat ini, jadi aman dan tidak permanen.

---

## 3) Jalankan aplikasi lokal
```powershell
python main.py
```

Penjelasan:
- Menjalankan aplikasi FastAPI secara langsung.
- Digunakan untuk mengecek aplikasi berjalan dengan benar di environment lokal.

---

## 4) Cek syntax Python
```powershell
python -m compileall main.py
```

Penjelasan:
- Mengecek apakah file `main.py` masih valid secara sintaks.
- Berguna sebelum commit, atau setelah melakukan perubahan besar pada kode.

---

## 5) Menambahkan file yang berubah ke Git
```powershell
git add main.py templates/dashboard.html
```

atau

```powershell
git add main.py templates/dashboard.html templates/edit_teknisi.html
```

Penjelasan:
- Menyiapkan file yang sudah diedit agar siap dibuat commit.
- `git add` memindahkan perubahan ke staging area.

---

## 6) Membuat commit
```powershell
git commit -m "Judul commit"
```

Contoh:
```powershell
git commit -m "Tambah edit profil teknisi dan fitur hapus pelanggan atau teknisi"
```

Penjelasan:
- Menyimpan perubahan ke history Git.
- `-m` dipakai untuk menambahkan judul commit yang jelas.

---

## 7) Push ke GitHub
```powershell
git push origin HEAD
```

Penjelasan:
- Mengirim commit terbaru ke repository GitHub.
- `origin` adalah nama remote repository.
- `HEAD` berarti branch aktif saat ini.

---

## 8) Cek status repository
```powershell
git status
```

Penjelasan:
- Menampilkan file yang berubah, sudah ditambahkan ke staging, atau sudah commit.
- Berguna untuk mengecek kondisi repo sebelum commit/push.

---

## 9) Cek log commit terakhir
```powershell
git log --oneline -5
```

Penjelasan:
- Menampilkan 5 commit terakhir.
- Berguna untuk melihat riwayat perubahan dan memastikan commit terakhir sudah benar.

---

## Alur kerja umum yang dipakai
```powershell
cd 'd:\Project\04_aplikasi-ac'
(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& d:\Project\04_aplikasi-ac\venv-ac\Scripts\Activate.ps1)
python -m compileall main.py
git add main.py templates/dashboard.html
git commit -m "Judul perubahan"
git push origin HEAD
```

---

## Catatan penting
- Selalu jalankan `python -m compileall main.py` sebelum commit agar tidak ada syntax error.
- Gunakan `git add` hanya untuk file yang sudah selesai diedit.
- Setelah push, deployment bisa di-trigger ulang jika project dipakai di platform seperti Render.
