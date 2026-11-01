import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 123456789))
FIREBASE_URL = os.getenv("FIREBASE_URL", "https://your-project.firebaseio.com/")

# Render sets the PORT env variable automatically
PORT = int(os.getenv("PORT", 10000))

# Web Admin Auth
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

# Session Storage (Uses local folder on Render)
SESSION_DIR = os.getenv("SESSION_DIR", "./sessions")
os.makedirs(SESSION_DIR, exist_ok=True)
