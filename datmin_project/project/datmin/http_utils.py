"""Utility pemanggilan HTTP dengan retry/backoff sederhana."""
import time
import requests


def get_json(url, params, timeout=30, max_retries=3, backoff=2):
    """Ambil JSON dari sebuah endpoint REST API dengan retry otomatis.
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Gagal mengambil data dari {url} setelah {max_retries} percobaan: {last_err}")


def get_text(url, params=None, timeout=30, max_retries=3, backoff=2):
    """mengembalikan raw text (dipakai untuk
    endpoint yang membalas CSV, seperti NASA FIRMS)."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Gagal mengambil data dari {url} setelah {max_retries} percobaan: {last_err}")
