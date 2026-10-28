from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_data_dir():
    override = os.environ.get("MATH_HARNESS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MathHarness"
    return Path.home() / ".local" / "share" / "MathHarness"


class Secrets:
    """Session secrets everywhere; optional Windows DPAPI persistence, never plaintext."""

    def __init__(self):
        self.memory = {}
        self.lock = threading.RLock()

    @staticmethod
    def crypt(data: bytes, decrypt=False):
        if os.name != "nt":
            raise ValueError("持久保存 API Key 仅在 Windows 上使用系统加密；也可以仅在本次会话使用。")

        class BLOB(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = ctypes.create_string_buffer(data)
        source = BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = BLOB()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.POINTER(BLOB)]
        function.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise OSError(ctypes.get_last_error(), "Windows 凭据加密失败")
        try:
            return ctypes.string_at(target.data, target.length)
        finally:
            kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))

    def set(self, profile_id, key, persist):
        encrypted = self.crypt(key.encode("utf-8")) if persist and key else None
        with self.lock:
            if key:
                self.memory[profile_id] = key
            else:
                self.memory.pop(profile_id, None)
        return encrypted

    def get(self, profile_id, encrypted=None):
        with self.lock:
            if profile_id in self.memory:
                return self.memory[profile_id]
        if encrypted:
            return self.crypt(encrypted, decrypt=True).decode("utf-8")
        return ""

    def redact(self, text):
        with self.lock:
            for key in self.memory.values():
                if key:
                    text = text.replace(key, "[REDACTED]")
        return text


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.secrets = Secrets()
        self.db = self.root / "workbench.sqlite3"
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, encrypted_key BLOB
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL, round INTEGER NOT NULL, stage TEXT NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(run_id, round, stage)
            );
            """)
            rows = db.execute("SELECT id,data FROM runs").fetchall()
            for run_id, raw in rows:
                run = json.loads(raw)
                if run["status"] == "running":
                    run["status"] = "paused"
                    run["message"] = "上次运行已中断；已保存的阶段可继续使用。"
                    db.execute("UPDATE runs SET data=?,updated=? WHERE id=?",
                               (json.dumps(run, ensure_ascii=False), now(), run_id))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def profiles(self):
        with self.connection() as db:
            rows = db.execute("SELECT id,config,encrypted_key FROM profiles").fetchall()
        result = []
        for profile_id, raw, encrypted in rows:
            profile = json.loads(raw)
            profile["id"] = profile_id
            profile["has_key"] = bool(encrypted or self.secrets.get(profile_id))
            profile["key_persisted"] = bool(encrypted)
            result.append(profile)
        return result

    def save_profile(self, config, key=None, persist=False):
        profile_id = config.get("id") or uuid.uuid4().hex
        config = {k: v for k, v in config.items() if k not in
                  {"api_key", "encrypted_key", "persist_key", "has_key", "key_persisted"}}
        config["id"] = profile_id
        with self.connection() as db:
            row = db.execute("SELECT encrypted_key FROM profiles WHERE id=?", (profile_id,)).fetchone()
            encrypted = row[0] if row else None
            if key is not None:
                encrypted = self.secrets.set(profile_id, key, persist)
            elif persist and not encrypted:
                current_key = self.secrets.get(profile_id)
                if current_key:
                    encrypted = self.secrets.crypt(current_key.encode("utf-8"))
            elif encrypted and not persist:
                # Unchecking persistence transfers the existing secret to this session.
                current_key = self.secrets.get(profile_id, encrypted)
                with self.secrets.lock:
                    self.secrets.memory[profile_id] = current_key
                encrypted = None
            db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?)",
                       (profile_id, json.dumps(config, ensure_ascii=False), encrypted))
        return next(x for x in self.profiles() if x["id"] == profile_id)

    def profile(self, profile_id):
        return next((x for x in self.profiles() if x["id"] == profile_id), None)

    def key(self, profile_id):
        with self.connection() as db:
            row = db.execute("SELECT encrypted_key FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            return ""
        value = self.secrets.get(profile_id, row[0])
        if value:
            with self.secrets.lock:
                self.secrets.memory[profile_id] = value
        return value

    def save_run(self, run):
        run["updated_at"] = now()
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                       (run["id"], json.dumps(run, ensure_ascii=False), run["updated_at"]))

    def run(self, run_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在。")
        return json.loads(row[0])

    def runs(self):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM runs ORDER BY updated DESC LIMIT 200").fetchall()
        return [json.loads(x[0]) for x in rows]

    def step(self, run_id, round_number, stage):
        with self.connection() as db:
            row = db.execute("SELECT data FROM steps WHERE run_id=? AND round=? AND stage=?",
                             (run_id, round_number, stage)).fetchone()
        return json.loads(row[0]) if row else None

    def save_step(self, run_id, round_number, stage, data):
        # Commit a completed step before advancing the state. Resume replays this exact cache.
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO steps VALUES (?,?,?,?)",
                       (run_id, round_number, stage, json.dumps(data, ensure_ascii=False)))

    def steps(self, run_id):
        with self.connection() as db:
            rows = db.execute("SELECT round,stage,data FROM steps WHERE run_id=? ORDER BY round,"
                              "CASE stage WHEN 'draft' THEN 0 WHEN 'review' THEN 1 ELSE 2 END",
                              (run_id,)).fetchall()
        return [{"round": r, "stage": s, **json.loads(d)} for r, s, d in rows]

    def artifact_dir(self, run_id):
        if not run_id or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("任务标识无效。")
        path = self.root / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path
