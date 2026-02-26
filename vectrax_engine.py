import os
import sqlite3
import hashlib
import re
from datetime import datetime

DB_REL_PATH = os.path.join("vault", "vectrax_ledger.db")

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


class VectraxEngine:

    def __init__(self):
        self.base_path = os.getcwd()
        self.db_path = os.path.join(self.base_path, DB_REL_PATH)

        self.session = {
            "expecting": None,
            "learn_name": None,
            "learn_buffer": [],
            "last_intent": None
        }

        self._init_db()

    # ---------------- DB ----------------

    def _connect(self):
        os.makedirs("vault", exist_ok=True)
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            source TEXT,
            content TEXT,
            tags TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            content TEXT,
            hash TEXT,
            created_at TEXT
        )
        """)

        conn.commit()
        conn.close()

    def log_event(self, source, content, tags=""):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO events (timestamp, source, content, tags) VALUES (?, ?, ?, ?)",
            (now_iso(), source, content, tags)
        )
        conn.commit()
        conn.close()

    # ---------------- KNOWLEDGE ----------------

    def save_knowledge(self, name, content):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO knowledge (name, content, hash, created_at) VALUES (?, ?, ?, ?)",
            (name, content, sha256_text(content), now_iso())
        )
        conn.commit()
        conn.close()

    def get_knowledge(self, name):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT content FROM knowledge WHERE name=? ORDER BY id DESC LIMIT 1",
            (name,)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    # ---------------- LEARN STREAM ----------------

    def _handle_learn_stream(self, text):

        if self.session["expecting"] != "learn":
            return None

        if text.strip() == "END":
            code = "\n".join(self.session["learn_buffer"])
            self.save_knowledge(self.session["learn_name"], code)

            self.session["expecting"] = None
            self.session["learn_buffer"] = []

            return {
                "text": f"✅ Aprendido: {self.session['learn_name']}",
                "intent": "command",
                "tags": "learn"
            }

        self.session["learn_buffer"].append(text)
        return {"text": "...", "intent": "learn", "tags": "learn"}

    # ---------------- COMMANDS ----------------

    def _handle_command(self, text):

        parts = text.split()

        if parts[0] == "/help":
            return {
                "text":
"""Comandos:
/help
/learn <nombre>
/build <nombre> <ruta>
/build! <nombre> <ruta>
/inject <nombre> <ruta>
salir""",
                "intent": "command",
                "tags": "help"
            }

        if parts[0] == "/learn":
            self.session["expecting"] = "learn"
            self.session["learn_name"] = parts[1]
            self.session["learn_buffer"] = []
            return {
                "text": "Pega el código y termina con END",
                "intent": "command",
                "tags": "learn"
            }

        if parts[0] in ["/build", "/build!"]:
            name = parts[1]
            path = parts[2]

            code = self.get_knowledge(name)
            if not code:
                return {"text": "Snippet no encontrado", "intent": "command", "tags": "error"}

            overwrite = parts[0] == "/build!"

            if os.path.exists(path) and not overwrite:
                return {"text": "Archivo existe. Usa /build!", "intent": "command", "tags": "error"}

            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, "w") as f:
                f.write(code)

            return {"text": f"✅ Creado {path}", "intent": "command", "tags": "build"}

        return {"text": "Comando no reconocido", "intent": "command", "tags": "error"}

    # ---------------- MAIN ROUTER ----------------

    def ingest(self, text, source="Mario"):

        # 1️⃣ PRIORIDAD ABSOLUTA: learn stream
        res = self._handle_learn_stream(text)
        if res:
            return res

        # 2️⃣ PRIORIDAD: comandos
        if text.startswith("/"):
            return self._handle_command(text)

        tl = text.lower()

        # 3️⃣ TRADING
        if any(w in tl for w in ["btc", "entrada", "stop", "trade"]):
            return {
                "text": "Operativa detectada. Dime Entrada <precio> y Stop <precio>.",
                "intent": "trading",
                "tags": "trading"
            }

        # 4️⃣ GENERAL
        return {
            "text": "Te escucho.",
            "intent": "general",
            "tags": "general"
        }


engine = VectraxEngine()
