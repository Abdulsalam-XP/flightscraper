import httpx
import os
import time
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
BASE = f"https://api.telegram.org/bot{TOKEN}"


def _post_with_retry(url, retries=3, **kwargs):
    kwargs.setdefault("timeout", 60)
    for attempt in range(retries):
        try:
            resp = httpx.post(url, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < retries - 1:
                print(f"[notify] attempt {attempt+1} failed: {e} — retrying in 5s")
                time.sleep(5)
            else:
                raise


def send_message(text: str):
    for chat_id in CHAT_IDS:
        try:
            _post_with_retry(f"{BASE}/sendMessage", json={"chat_id": chat_id, "text": text})
        except Exception as e:
            print(f"[notify] sendMessage failed for chat {chat_id}: {e} — skipping this chat")


def send_document(file_path: str, caption: str = ""):
    for chat_id in CHAT_IDS:
        try:
            with open(file_path, "rb") as f:
                _post_with_retry(
                    f"{BASE}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption},
                    files={"document": (os.path.basename(file_path), f, "text/csv")},
                )
        except Exception as e:
            print(f"[notify] sendDocument failed for chat {chat_id}: {e} — skipping this chat")


def notify(summary: str, csv_path: str):
    send_message(summary)
    send_document(csv_path, caption="Full results — open in Excel or Google Sheets")
