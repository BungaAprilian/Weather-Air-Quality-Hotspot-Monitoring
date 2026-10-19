"""
Script REFERENSI (tidak dipanggil oleh pipeline utama) untuk mereproduksi
atau memverifikasi nilai "bmkg_adm4" pada CITIES di config.py.

BMKG hanya menyediakan endpoint prakiraan cuaca per kelurahan/desa (adm4),
bukan per kota, sehingga kita perlu satu kode kelurahan yang representatif
untuk tiap kota. Sumber kode wilayah resmi yang dipakai di sini adalah
dataset kode wilayah administrasi Indonesia (sesuai Kepmendagri terbaru)
yang dirawat di https://github.com/cahyadsn/wilayah (db/wilayah.sql).
Ini akan mencetak ulang kode adm4 (kelurahan pertama pada kecamatan
pertama) untuk tiap kota di CITIES, sehingga bisa dibandingkan/diperbarui
manual di config.py jika suatu saat kode wilayah berubah.
"""
import re
import sys

from .config import CITIES

# Kode wilayah tingkat kabupaten/kota (2 level, format PP.KK) untuk tiap
# kota di CITIES. Dicocokkan berdasarkan nama "Kota <nama>" pada dataset.
KOTA_CODE_HINT = {
    "Jakarta": "Kota Administrasi Jakarta Pusat",
    "Bandung": "Kota Bandung",
    "Surabaya": "Kota Surabaya",
    "Semarang": "Kota Semarang",
    "Yogyakarta": "Kota Yogyakarta",
    "Medan": "Kota Medan",
    "Palembang": "Kota Palembang",
    "Makassar": "Kota Makassar",
    "Denpasar": "Kota Denpasar",
    "Balikpapan": "Kota Balikpapan",
    "Manado": "Kota Manado",
    "Pontianak": "Kota Pontianak",
    "Banda Aceh": "Kota Banda Aceh",
    "Jayapura": "Kota Jayapura",
    "Malang": "Kota Malang",
}


def load_entries(sql_path):
    with open(sql_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return re.findall(r"\('([\d.]+)',\s*'([^']*(?:''[^']*)*)'\)", content)


def resolve(sql_path):
    entries = load_entries(sql_path)
    by_name_lvl2 = {n.strip(): c for c, n in entries if c.count(".") == 1}

    results = {}
    for city in CITIES:
        name = city["city_name"]
        hint = KOTA_CODE_HINT.get(name, f"Kota {name}")
        kota_code = by_name_lvl2.get(hint)
        if not kota_code:
            results[name] = None
            continue
        kecamatan = sorted(c for c, n in entries if c.count(".") == 2 and c.startswith(kota_code + "."))
        if not kecamatan:
            results[name] = None
            continue
        first_kec = kecamatan[0]
        kelurahan = sorted((c, n) for c, n in entries if c.count(".") == 3 and c.startswith(first_kec + "."))
        results[name] = kelurahan[0] if kelurahan else None
    return results


def main():
    if len(sys.argv) != 2:
        print("Pemakaian: python -m datmin.resolve_bmkg_adm4 <path/ke/wilayah.sql>")
        sys.exit(1)

    results = resolve(sys.argv[1])
    for city in CITIES:
        name = city["city_name"]
        found = results.get(name)
        current = city["bmkg_adm4"]
        if found is None:
            print(f"{name:<12} TIDAK DITEMUKAN di dataset (cek KOTA_CODE_HINT)")
            continue
        adm4, kelurahan = found
        match = "OK" if adm4 == current else "BEDA dari config.py!"
        print(f"{name:<12} adm4={adm4}  kelurahan={kelurahan:<20} [{match}]")


if __name__ == "__main__":
    main()
