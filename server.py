
import os
import time
import sqlite3
import threading
import requests

from flask import Flask, request, jsonify, send_from_directory

# =========================================================
# OPTIONAL FILE READERS
# =========================================================

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from docx import Document
except Exception:
    Document = None


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# دیتابیس دقیقاً کنار server.py ساخته می‌شود
DB_FILE = os.path.join(BASE_DIR, "whale.db")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# =========================================================
# CONFIG
# =========================================================

DEFAULT_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "minimax/minimax-m3:free"
).strip()

FALLBACK_MODEL = os.environ.get(
    "OPENROUTER_FALLBACK_MODEL",
    ""
).strip()

MAX_HISTORY_MESSAGES = 80
MAX_MEMORY_ITEMS = 100
MAX_FILE_CHARS = 100000
MAX_MESSAGE_CHARS = 30000
MAX_CONVERSATION_TITLE = 80

REQUEST_CONNECT_TIMEOUT = 20
REQUEST_READ_TIMEOUT = 180
MAX_RETRIES = 3

DB_TIMEOUT = 60
DB_RETRIES = 8
DB_RETRY_DELAY = 0.25

DB_WRITE_LOCK = threading.RLock()


# =========================================================
# STARTUP INFO
# =========================================================

print()
print("========================================")
print("           WHALE AI DATABASE")
print("========================================")
print("SERVER FILE:")
print(os.path.abspath(__file__))
print()
print("BASE DIR:")
print(BASE_DIR)
print()
print("DATABASE:")
print(DB_FILE)
print()
print("DATABASE EXISTS BEFORE:")
print(os.path.exists(DB_FILE))
print("========================================")
print()


# =========================================================
# API KEY
# =========================================================

def get_api_key():

    key = os.environ.get(
        "OPENROUTER_API_KEY",
        ""
    ).strip()

    if key.lower().startswith("bearer "):
        key = key[7:].strip()

    return key


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():

    # اطمینان از وجود پوشه
    os.makedirs(
        BASE_DIR,
        exist_ok=True
    )

    conn = sqlite3.connect(
        DB_FILE,
        timeout=DB_TIMEOUT,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    try:
        conn.execute(
            "PRAGMA foreign_keys = ON"
        )

        conn.execute(
            "PRAGMA busy_timeout = 60000"
        )

        conn.execute(
            "PRAGMA journal_mode = WAL"
        )

        conn.execute(
            "PRAGMA synchronous = NORMAL"
        )

    except Exception as error:

        print(
            "DATABASE PRAGMA WARNING:",
            repr(error)
        )

    return conn


# =========================================================
# DATABASE INITIALIZATION + MIGRATION
# =========================================================

def initialize_database():

    with DB_WRITE_LOCK:

        conn = get_db()

        try:

            # =================================================
            # CONVERSATIONS
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT 'New Chat',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================
            # MESSAGES
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================
            # MEMORIES
            # =================================================

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # =================================================
            # CONVERSATIONS MIGRATION
            # =================================================

            conversation_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(conversations)"
                ).fetchall()
            }

            print(
                "CONVERSATION COLUMNS BEFORE:",
                conversation_columns
            )

            if "title" not in conversation_columns:

                print(
                    "ADDING conversations.title"
                )

                conn.execute("""
                    ALTER TABLE conversations
                    ADD COLUMN title TEXT
                    DEFAULT 'New Chat'
                """)

            if "created_at" not in conversation_columns:

                print(
                    "ADDING conversations.created_at"
                )

                conn.execute("""
                    ALTER TABLE conversations
                    ADD COLUMN created_at TEXT
                """)

                conn.execute("""
                    UPDATE conversations
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                """)

            if "updated_at" not in conversation_columns:

                print(
                    "ADDING conversations.updated_at"
                )

                conn.execute("""
                    ALTER TABLE conversations
                    ADD COLUMN updated_at TEXT
                """)

                conn.execute("""
                    UPDATE conversations
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                """)

            # =================================================
            # MESSAGES MIGRATION
            # =================================================

            message_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(messages)"
                ).fetchall()
            }

            print(
                "MESSAGE COLUMNS BEFORE:",
                message_columns
            )

            # نسخه قدیمی ممکن است conversation داشته باشد
            if (
                "conversation_id" not in message_columns
                and "conversation" in message_columns
            ):

                print(
                    "ADDING messages.conversation_id"
                )

                conn.execute("""
                    ALTER TABLE messages
                    ADD COLUMN conversation_id INTEGER
                """)

                conn.execute("""
                    UPDATE messages
                    SET conversation_id = conversation
                    WHERE conversation_id IS NULL
                """)

            elif "conversation_id" not in message_columns:

                print(
                    "ADDING messages.conversation_id"
                )

                conn.execute("""
                    ALTER TABLE messages
                    ADD COLUMN conversation_id INTEGER
                """)

            if "role" not in message_columns:

                print(
                    "ADDING messages.role"
                )

                conn.execute("""
                    ALTER TABLE messages
                    ADD COLUMN role TEXT
                """)

            if "content" not in message_columns:

                print(
                    "ADDING messages.content"
                )

                conn.execute("""
                    ALTER TABLE messages
                    ADD COLUMN content TEXT
                """)

            if "created_at" not in message_columns:

                print(
                    "ADDING messages.created_at"
                )

                conn.execute("""
                    ALTER TABLE messages
                    ADD COLUMN created_at TEXT
                """)

                conn.execute("""
                    UPDATE messages
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                """)

            # =================================================
            # MEMORY MIGRATION
            # =================================================

            memory_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(memories)"
                ).fetchall()
            }

            if "created_at" not in memory_columns:

                conn.execute("""
                    ALTER TABLE memories
                    ADD COLUMN created_at TEXT
                """)

                conn.execute("""
                    UPDATE memories
                    SET created_at = CURRENT_TIMESTAMP
                    WHERE created_at IS NULL
                """)

            if "updated_at" not in memory_columns:

                conn.execute("""
                    ALTER TABLE memories
                    ADD COLUMN updated_at TEXT
                """)

                conn.execute("""
                    UPDATE memories
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE updated_at IS NULL
                """)

            # =================================================
            # FIX NULL VALUES
            # =================================================

            conn.execute("""
                UPDATE conversations
                SET title = 'New Chat'
                WHERE title IS NULL OR title = ''
            """)

            conn.execute("""
                UPDATE conversations
                SET created_at = CURRENT_TIMESTAMP
                WHERE created_at IS NULL OR created_at = ''
            """)

            conn.execute("""
                UPDATE conversations
                SET updated_at = CURRENT_TIMESTAMP
                WHERE updated_at IS NULL OR updated_at = ''
            """)

            conn.execute("""
                UPDATE messages
                SET created_at = CURRENT_TIMESTAMP
                WHERE created_at IS NULL OR created_at = ''
            """)

            # =================================================
            # INDEXES
            # =================================================

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_messages_conversation_id
                ON messages(conversation_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_messages_created_at
                ON messages(created_at)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_conversations_updated_at
                ON conversations(updated_at)
            """)

            # =================================================
            # COMMIT
            # =================================================

            conn.commit()

            # =================================================
            # FINAL CHECK
            # =================================================

            final_conversations = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(conversations)"
                ).fetchall()
            }

            final_messages = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(messages)"
                ).fetchall()
            }

            print()
            print("========================================")
            print("      DATABASE INITIALIZED")
            print("========================================")
            print("DATABASE:")
            print(DB_FILE)
            print()
            print("EXISTS:")
            print(os.path.exists(DB_FILE))
            print()
            print("CONVERSATIONS:")
            print(final_conversations)
            print()
            print("MESSAGES:")
            print(final_messages)
            print("========================================")
            print()

        except Exception as error:

            try:
                conn.rollback()
            except Exception:
                pass

            print()
            print("========================================")
            print("      DATABASE INITIALIZATION ERROR")
            print("========================================")
            print(repr(error))
            print("DATABASE:")
            print(DB_FILE)
            print("========================================")
            print()

            raise

        finally:

            conn.close()


# =========================================================
# DATABASE WRITE
# =========================================================

def execute_write(
    sql,
    params=(),
    fetchone=False,
    fetchall=False
):

    last_error = None

    with DB_WRITE_LOCK:

        for attempt in range(DB_RETRIES):

            conn = None

            try:

                conn = get_db()

                cursor = conn.execute(
                    sql,
                    params
                )

                result = None

                if fetchone:
                    result = cursor.fetchone()

                elif fetchall:
                    result = cursor.fetchall()

                conn.commit()

                return result

            except sqlite3.OperationalError as error:

                last_error = error

                if conn:

                    try:
                        conn.rollback()
                    except Exception:
                        pass

                text = str(error).lower()

                if (
                    "locked" in text
                    or "busy" in text
                ):

                    print(
                        f"DATABASE BUSY "
                        f"{attempt + 1}/{DB_RETRIES}"
                    )

                    time.sleep(
                        DB_RETRY_DELAY * (attempt + 1)
                    )

                    continue

                raise

            except Exception:

                if conn:

                    try:
                        conn.rollback()
                    except Exception:
                        pass

                raise

            finally:

                if conn:

                    try:
                        conn.close()
                    except Exception:
                        pass

    raise last_error


# =========================================================
# MEMORY
# =========================================================

def get_memories():

    conn = None

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                id,
                key,
                value,
                created_at,
                updated_at
            FROM memories
            ORDER BY id ASC
            LIMIT ?
        """, (
            MAX_MEMORY_ITEMS,
        )).fetchall()

        return [
            {
                "id": row["id"],
                "key": row["key"],
                "value": row["value"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ]

    finally:

        if conn:
            conn.close()


def save_memory(key, value):

    key = str(key).strip()
    value = str(value).strip()

    if not key or not value:
        return

    execute_write("""
        INSERT INTO memories
            (key, value)
        VALUES
            (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
    """, (
        key,
        value
    ))


def delete_memory(key):

    execute_write(
        "DELETE FROM memories WHERE key = ?",
        (key,)
    )


def clear_memories():

    execute_write(
        "DELETE FROM memories"
    )


def build_memory_context():

    memories = get_memories()

    if not memories:
        return ""

    lines = []

    for memory in memories:

        lines.append(
            f"- {memory['key']}: {memory['value']}"
        )

    return (
        "\n\nIMPORTANT USER MEMORY:\n"
        + "\n".join(lines)
        + "\n"
    )


# =========================================================
# FILE EXTRACTION
# =========================================================

def extract_file_text(file):

    filename = file.filename or ""

    extension = (
        os.path.splitext(filename)[1]
        .lower()
        .replace(".", "")
    )

    text_extensions = {
        "txt",
        "md",
        "csv",
        "json",
        "xml",
        "html",
        "py",
        "js",
        "css",
        "java",
        "cpp",
        "c",
        "sql",
        "ts",
        "tsx",
        "jsx"
    }

    if extension in text_extensions:

        raw = file.read()

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode(
                "utf-8",
                errors="replace"
            )

        return (
            text[:MAX_FILE_CHARS],
            extension
        )

    if extension == "pdf":

        if PdfReader is None:
            raise RuntimeError(
                "PDF support is not installed."
            )

        reader = PdfReader(file)

        parts = []
        total = 0

        for page in reader.pages:

            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""

            if page_text:

                parts.append(page_text)
                total += len(page_text)

            if total >= MAX_FILE_CHARS:
                break

        return (
            "\n\n".join(parts)[:MAX_FILE_CHARS],
            extension
        )

    if extension == "docx":

        if Document is None:
            raise RuntimeError(
                "DOCX support is not installed."
            )

        document = Document(file)

        paragraphs = []

        for paragraph in document.paragraphs:

            text = paragraph.text.strip()

            if text:
                paragraphs.append(text)

        return (
            "\n".join(paragraphs)[:MAX_FILE_CHARS],
            extension
        )

    raise ValueError(
        f"Unsupported file type: .{extension}"
    )


# =========================================================
# HOME
# =========================================================

@app.route("/", methods=["GET"])
def home():

    index_file = os.path.join(
        BASE_DIR,
        "index.html"
    )

    if not os.path.exists(index_file):

        return jsonify({
            "error": "index.html was not found."
        }), 404

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health", methods=["GET"])
def health():

    database = False
    database_error = None

    conn = None

    try:

        conn = get_db()

        conn.execute(
            "SELECT 1"
        ).fetchone()

        database = True

    except Exception as error:

        database_error = str(error)

    finally:

        if conn:
            conn.close()

    return jsonify({
        "status": "ok",
        "database": database,
        "database_error": database_error,
        "database_file": DB_FILE,
        "database_exists": os.path.exists(DB_FILE),
        "openrouter_key": bool(get_api_key()),
        "model": DEFAULT_MODEL,
        "memory": True,
        "conversations": True
    })


# =========================================================
# UPLOAD
# =========================================================

@app.route("/upload", methods=["POST"])
def upload_file():

    try:

        if "file" not in request.files:

            return jsonify({
                "error": "No file was uploaded."
            }), 400

        file = request.files["file"]

        if not file.filename:

            return jsonify({
                "error": "The file has no name."
            }), 400

        text, extension = extract_file_text(file)

        return jsonify({
            "success": True,
            "filename": file.filename,
            "extension": extension,
            "text": text,
            "characters": len(text)
        })

    except ValueError as error:

        return jsonify({
            "error": str(error)
        }), 400

    except Exception as error:

        print(
            "UPLOAD ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "File processing failed.",
            "details": str(error)
        }), 500


# =========================================================
# GET CONVERSATIONS
# =========================================================

@app.route("/conversations", methods=["GET"])
def get_conversations():

    conn = None

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                c.id,
                c.title,
                c.created_at,
                c.updated_at,
                COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m
                ON m.conversation_id = c.id
            GROUP BY
                c.id,
                c.title,
                c.created_at,
                c.updated_at
            ORDER BY
                c.updated_at DESC,
                c.id DESC
        """).fetchall()

        return jsonify([
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to load conversations.",
            "details": str(error)
        }), 500

    finally:

        if conn:
            conn.close()


# =========================================================
# CREATE CONVERSATION
# =========================================================

@app.route("/conversations", methods=["POST"])
def create_conversation():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        title = str(
            data.get(
                "title",
                "New Chat"
            )
        ).strip()

        if not title:
            title = "New Chat"

        title = title[:MAX_CONVERSATION_TITLE]

        # خیلی مهم:
        # lastrowid از همان connection گرفته می‌شود
        with DB_WRITE_LOCK:

            conn = get_db()

            try:

                cursor = conn.execute("""
                    INSERT INTO conversations
                        (title)
                    VALUES
                        (?)
                """, (
                    title,
                ))

                conversation_id = cursor.lastrowid

                conn.commit()

            except Exception:

                conn.rollback()
                raise

            finally:

                conn.close()

        return jsonify({
            "id": conversation_id,
            "title": title
        })

    except Exception as error:

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to create conversation.",
            "details": str(error)
        }), 500


# =========================================================
# RENAME
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["PATCH"]
)
def rename_conversation(conversation_id):

    try:

        data = request.get_json(
            silent=True
        ) or {}

        title = str(
            data.get(
                "title",
                ""
            )
        ).strip()

        if not title:

            return jsonify({
                "error": "Title is required."
            }), 400

        title = title[:MAX_CONVERSATION_TITLE]

        with DB_WRITE_LOCK:

            conn = get_db()

            try:

                cursor = conn.execute("""
                    UPDATE conversations
                    SET
                        title = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    title,
                    conversation_id
                ))

                conn.commit()

                if cursor.rowcount == 0:

                    return jsonify({
                        "error": "Conversation not found."
                    }), 404

            except Exception:

                conn.rollback()
                raise

            finally:

                conn.close()

        return jsonify({
            "success": True,
            "id": conversation_id,
            "title": title
        })

    except Exception as error:

        print(
            "RENAME ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to rename conversation.",
            "details": str(error)
        }), 500


# =========================================================
# GET MESSAGES
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(conversation_id):

    conn = None

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                id,
                role,
                content,
                created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (
            conversation_id,
        )).fetchall()

        return jsonify([
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to load messages.",
            "details": str(error)
        }), 500

    finally:

        if conn:
            conn.close()


# =========================================================
# SEARCH CONVERSATIONS
# =========================================================

@app.route(
    "/conversations/search",
    methods=["GET"]
)
def search_conversations():

    query = str(
        request.args.get(
            "q",
            ""
        )
    ).strip()

    if not query:
        return jsonify([])

    conn = None

    try:

        conn = get_db()

        pattern = "%" + query + "%"

        rows = conn.execute("""
            SELECT DISTINCT
                c.id,
                c.title,
                c.created_at,
                c.updated_at
            FROM conversations c
            LEFT JOIN messages m
                ON m.conversation_id = c.id
            WHERE
                c.title LIKE ?
                OR m.content LIKE ?
            ORDER BY
                c.updated_at DESC
            LIMIT 50
        """, (
            pattern,
            pattern
        )).fetchall()

        return jsonify([
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "SEARCH ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Conversation search failed.",
            "details": str(error)
        }), 500

    finally:

        if conn:
            conn.close()


# =========================================================
# MEMORY GET
# =========================================================

@app.route("/memory", methods=["GET"])
def memory_get():

    try:

        return jsonify({
            "memories": get_memories()
        })

    except Exception as error:

        print(
            "MEMORY GET ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to load memory.",
            "details": str(error)
        }), 500


# =========================================================
# MEMORY ADD
# =========================================================

@app.route("/memory", methods=["POST"])
def memory_add():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        key = str(
            data.get(
                "key",
                ""
            )
        ).strip()

        value = str(
            data.get(
                "value",
                ""
            )
        ).strip()

        if not key or not value:

            return jsonify({
                "error": "key and value are required."
            }), 400

        save_memory(
            key,
            value
        )

        return jsonify({
            "success": True,
            "key": key,
            "value": value
        })

    except Exception as error:

        print(
            "MEMORY SAVE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Failed to save memory.",
            "details": str(error)
        }), 500


# =========================================================
# MEMORY DELETE ONE
# =========================================================

@app.route(
    "/memory/<path:key>",
    methods=["DELETE"]
)
def memory_delete_route(key):

    try:

        delete_memory(key)

        return jsonify({
            "success": True
        })

    except Exception as error:

        return jsonify({
            "error": "Failed to delete memory.",
            "details": str(error)
        }), 500


# =========================================================
# MEMORY DELETE ALL
# =========================================================

@app.route(
    "/memory",
    methods=["DELETE"]
)
def memory_delete_all():

    try:

        clear_memories()

        return jsonify({
            "success": True
        })

    except Exception as error:

        return jsonify({
            "error": "Failed to clear memories.",
            "details": str(error)
        }), 500


# =========================================================
# MODEL MESSAGES
# =========================================================

def build_model_messages(
    conversation_id,
    file_text="",
    mode="auto"
):

    conn = None

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                role,
                content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            conversation_id,
            MAX_HISTORY_MESSAGES
        )).fetchall()

    finally:

        if conn:
            conn.close()

    rows = list(
        reversed(rows)
    )

    system_prompt = """
You are Whale AI, an advanced general-purpose AI assistant.

LANGUAGE:
- Always answer in the same language as the user.
- If the user writes Persian, answer naturally in Persian.
- Do not randomly switch languages.

GENERAL:
- Be accurate, useful and direct.
- Do not unnecessarily repeat the user's question.
- Use Markdown when useful.
- Use headings for long answers.
- Use numbered lists for procedures.
- Use code blocks for code.
- Never invent sources or citations.
- Never claim web search happened unless tools actually returned search results.
- If information may be outdated, say so.
- If uncertain, clearly state the uncertainty.
- Never expose API keys or secrets.

EDUCATION:
- Explain difficult concepts step by step.
- Adapt explanations to the user's level.
- Give examples when useful.

CODING:
- Prefer complete working code when requested.
- Preserve existing functionality when modifying code.
- Focus on correctness and debugging.

FILES:
- Use attached file content when relevant.
- Do not pretend to have read content that was not successfully extracted.

MEMORY:
- Treat stored memory as user-specific context.
- Do not invent memories.
"""

    mode = str(
        mode or "auto"
    ).lower().strip()

    if mode == "study":

        system_prompt += """
MODE: STUDY
Teach rather than merely giving the final answer.
Break complex subjects into understandable parts.
"""

    elif mode == "research":

        system_prompt += """
MODE: RESEARCH
Prioritize factual accuracy.
Distinguish current information from general knowledge.
"""

    elif mode == "code":

        system_prompt += """
MODE: CODE
Act as a senior software engineer.
Focus on debugging, architecture, correctness and maintainability.
"""

    elif mode == "deep":

        system_prompt += """
MODE: DEEP ANALYSIS
Analyze carefully and consider edge cases.
Do not expose private chain-of-thought.
Give concise reasoning summaries.
"""

    elif mode == "quick":

        system_prompt += """
MODE: QUICK
Answer concisely.
"""

    else:

        system_prompt += """
MODE: AUTO
Choose the appropriate response style automatically.
"""

    system_prompt += build_memory_context()

    if file_text:

        system_prompt += f"""

ATTACHED FILE CONTENT:

--- BEGIN FILE ---
{file_text[:MAX_FILE_CHARS]}
--- END FILE ---

Use this content when relevant.
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    for row in rows:

        if row["role"] not in (
            "user",
            "assistant",
            "system"
        ):
            continue

        messages.append({
            "role": row["role"],
            "content": row["content"]
        })

    return messages


# =========================================================
# HEADERS
# =========================================================

def build_headers(api_key):

    return {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get(
            "SITE_URL",
            "https://whale-ai.local"
        ),
        "X-Title": "Whale AI"
    }


# =========================================================
# PAYLOAD
# =========================================================

def build_payload(
    model,
    messages,
    web_search=False,
    stream=False
):

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "stream": stream
    }

    if web_search:

        payload["tools"] = [
            {
                "type": "openrouter:web_search"
            },
            {
                "type": "openrouter:web_fetch"
            }
        ]

        payload["tool_choice"] = "auto"

    return payload


# =========================================================
# OPENROUTER REQUEST
# =========================================================

def request_openrouter(
    api_key,
    model,
    payload
):

    headers = build_headers(
        api_key
    )

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=(
                    REQUEST_CONNECT_TIMEOUT,
                    REQUEST_READ_TIMEOUT
                )
            )

            if response.status_code == 200:
                return response

            if response.status_code in (
                408,
                429,
                500,
                502,
                503,
                504
            ):

                if attempt < MAX_RETRIES:

                    time.sleep(
                        1.5 * (attempt + 1)
                    )

                    continue

            return response

        except requests.Timeout:

            if attempt < MAX_RETRIES:

                time.sleep(
                    1.5 * (attempt + 1)
                )

                continue

            raise

        except requests.RequestException:

            if attempt < MAX_RETRIES:

                time.sleep(
                    1.5 * (attempt + 1)
                )

                continue

            raise

    return None


# =========================================================
# OPENROUTER ERROR
# =========================================================

def openrouter_error_response(
    response,
    model
):

    details = response.text[:5000]

    print()
    print("================================")
    print("OPENROUTER ERROR")
    print("STATUS:", response.status_code)
    print("MODEL:", model)
    print("DETAILS:", details)
    print("================================")
    print()

    status = response.status_code

    if status == 401:

        message = (
            "OpenRouter authentication failed. "
            "Check OPENROUTER_API_KEY."
        )

    elif status == 402:

        message = (
            "OpenRouter requires credits or payment "
            "for this request."
        )

    elif status == 403:

        message = (
            "OpenRouter rejected access to this model "
            "or tool."
        )

    elif status == 404:

        message = (
            "The selected OpenRouter model or endpoint "
            "was not found."
        )

    elif status == 408:

        message = "The request timed out."

    elif status == 429:

        message = (
            "OpenRouter rate limit reached."
        )

    elif 500 <= status <= 599:

        message = (
            "OpenRouter or the selected provider "
            "temporarily failed."
        )

    else:

        message = "OpenRouter request failed."

    return jsonify({
        "error": message,
        "status": status,
        "details": details,
        "model": model
    }), status


# =========================================================
# NORMALIZE REPLY
# =========================================================

def normalize_reply(result):

    choices = result.get(
        "choices"
    )

    if not choices:
        return ""

    message_data = choices[0].get(
        "message",
        {}
    )

    reply = message_data.get(
        "content",
        ""
    )

    if isinstance(reply, list):

        parts = []

        for item in reply:

            if isinstance(item, dict):

                text = item.get(
                    "text",
                    ""
                )

                if text:
                    parts.append(text)

            elif isinstance(item, str):

                parts.append(item)

        reply = "".join(parts)

    return str(
        reply
    ).strip()


# =========================================================
# SAVE MESSAGE
# =========================================================

def save_message(
    conversation_id,
    role,
    content
):

    try:

        conversation_id = int(
            conversation_id
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            "Invalid conversation_id."
        )

    role = str(
        role or ""
    ).strip()

    content = str(
        content or ""
    )

    if role not in (
        "user",
        "assistant",
        "system"
    ):

        raise ValueError(
            "Invalid message role."
        )

    if not content:

        raise ValueError(
            "Message content is empty."
        )

    with DB_WRITE_LOCK:

        conn = None

        try:

            conn = get_db()

            # بررسی conversation
            conversation = conn.execute("""
                SELECT id
                FROM conversations
                WHERE id = ?
            """, (
                conversation_id,
            )).fetchone()

            if not conversation:

                raise ValueError(
                    f"Conversation {conversation_id} "
                    f"does not exist."
                )

            # تراکنش
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            # ذخیره پیام
            conn.execute("""
                INSERT INTO messages
                    (
                        conversation_id,
                        role,
                        content,
                        created_at
                    )
                VALUES
                    (?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                conversation_id,
                role,
                content
            ))

            # بروزرسانی چت
            conn.execute("""
                UPDATE conversations
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                conversation_id,
            ))

            conn.commit()

            print(
                "MESSAGE SAVED:",
                conversation_id,
                role
            )

        except Exception as error:

            if conn:

                try:
                    conn.rollback()
                except Exception:
                    pass

            print(
                "SAVE MESSAGE ERROR:",
                repr(error)
            )

            raise

        finally:

            if conn:

                try:
                    conn.close()
                except Exception:
                    pass


# =========================================================
# CHAT
# =========================================================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    try:

        api_key = get_api_key()

        if not api_key:

            return jsonify({
                "error": (
                    "OPENROUTER_API_KEY "
                    "is not configured."
                )
            }), 500

        data = request.get_json(
            silent=True
        ) or {}

        user_message = str(
            data.get(
                "message",
                ""
            )
        ).strip()

        file_name = str(
            data.get(
                "file_name",
                ""
            )
        ).strip()

        file_text = str(
            data.get(
                "file_text",
                ""
            )
        )

        web_search = bool(
            data.get(
                "web_search",
                False
            )
        )

        mode = str(
            data.get(
                "mode",
                "auto"
            )
        ).strip().lower()

        conversation_id = data.get(
            "conversation_id"
        )

        if len(user_message) > MAX_MESSAGE_CHARS:

            return jsonify({
                "error": "Message is too long."
            }), 400

        if len(file_text) > MAX_FILE_CHARS:

            file_text = file_text[
                :MAX_FILE_CHARS
            ]

        if (
            not user_message
            and not file_text
        ):

            return jsonify({
                "error": "Message is empty."
            }), 400

        # =================================================
        # CONVERSATION ID
        # =================================================

        if conversation_id:

            try:

                conversation_id = int(
                    conversation_id
                )

            except (
                TypeError,
                ValueError
            ):

                conversation_id = None

        if conversation_id:

            conn = None

            try:

                conn = get_db()

                exists = conn.execute("""
                    SELECT id
                    FROM conversations
                    WHERE id = ?
                """, (
                    conversation_id,
                )).fetchone()

            finally:

                if conn:
                    conn.close()

            if not exists:
                conversation_id = None

        # =================================================
        # CREATE CONVERSATION
        # =================================================

        if not conversation_id:

            title_source = (
                user_message
                or file_name
                or "New Chat"
            )

            title_source = (
                title_source
                .replace("\n", " ")
                .strip()
            )

            with DB_WRITE_LOCK:

                conn = get_db()

                try:

                    cursor = conn.execute("""
                        INSERT INTO conversations
                            (title)
                        VALUES
                            (?)
                    """, (
                        title_source[
                            :MAX_CONVERSATION_TITLE
                        ],
                    ))

                    conversation_id = cursor.lastrowid

                    conn.commit()

                except Exception:

                    conn.rollback()
                    raise

                finally:

                    conn.close()

        # =================================================
        # SAVE USER MESSAGE
        # =================================================

        display_message = user_message

        if file_text:

            display_message += (
                "\n\n[Attached file: "
                f"{file_name or 'file'}]"
            )

        if not display_message:

            display_message = (
                "[Attached file: "
                f"{file_name or 'file'}]"
            )

        save_message(
            conversation_id,
            "user",
            display_message
        )

        # =================================================
        # BUILD MESSAGES
        # =================================================

        messages = build_model_messages(
            conversation_id,
            file_text,
            mode
        )

        # =================================================
        # OPENROUTER
        # =================================================

        payload = build_payload(
            DEFAULT_MODEL,
            messages,
            web_search=web_search,
            stream=False
        )

        print()
        print("================================")
        print("WHALE AI REQUEST")
        print("================================")
        print("Conversation:", conversation_id)
        print("Model:", DEFAULT_MODEL)
        print("Mode:", mode)
        print("Web Search:", web_search)
        print("API KEY: FOUND")
        print("================================")
        print()

        response = request_openrouter(
            api_key,
            DEFAULT_MODEL,
            payload
        )

        active_model = DEFAULT_MODEL

        # =================================================
        # FALLBACK
        # =================================================

        if (
            response is not None
            and response.status_code != 200
            and FALLBACK_MODEL
            and FALLBACK_MODEL != DEFAULT_MODEL
            and response.status_code in (
                408,
                429,
                500,
                502,
                503,
                504
            )
        ):

            print(
                "PRIMARY MODEL FAILED."
            )

            print(
                "TRYING FALLBACK:",
                FALLBACK_MODEL
            )

            fallback_payload = build_payload(
                FALLBACK_MODEL,
                messages,
                web_search=web_search,
                stream=False
            )

            response = request_openrouter(
                api_key,
                FALLBACK_MODEL,
                fallback_payload
            )

            active_model = FALLBACK_MODEL

        if response is None:

            return jsonify({
                "error": (
                    "No response "
                    "from OpenRouter."
                )
            }), 502

        # =================================================
        # HTTP ERROR
        # =================================================

        if response.status_code != 200:

            return openrouter_error_response(
                response,
                active_model
            )

        # =================================================
        # JSON
        # =================================================

        try:

            result = response.json()

        except ValueError:

            return jsonify({
                "error": (
                    "OpenRouter returned "
                    "invalid JSON."
                ),
                "details": response.text[:3000]
            }), 502

        if result.get("error"):

            return jsonify({
                "error": (
                    "OpenRouter returned "
                    "an error."
                ),
                "details": result["error"],
                "model": active_model
            }), 502

        # =================================================
        # REPLY
        # =================================================

        reply = normalize_reply(
            result
        )

        if not reply:

            return jsonify({
                "error": (
                    "The model returned "
                    "an empty response."
                )
            }), 502

        # =================================================
        # SAVE ASSISTANT
        # =================================================

        save_message(
            conversation_id,
            "assistant",
            reply
        )

        return jsonify({
            "type": "done",
            "content": reply,
            "conversation_id": conversation_id,
            "model": active_model,
            "web_search": web_search,
            "mode": mode
        })

    except sqlite3.OperationalError as error:

        print()
        print("================================")
        print("DATABASE ERROR IN /chat")
        print("================================")
        print(
            "ERROR:",
            repr(error)
        )
        print(
            "DATABASE:",
            DB_FILE
        )
        print(
            "EXISTS:",
            os.path.exists(DB_FILE)
        )
        print("================================")
        print()

        return jsonify({
            "error": "Database error.",
            "details": str(error),
            "database": DB_FILE
        }), 500

    except requests.Timeout:

        return jsonify({
            "error": (
                "The AI request timed out."
            )
        }), 504

    except requests.RequestException as error:

        return jsonify({
            "error": (
                "Could not connect "
                "to OpenRouter."
            ),
            "details": str(error)
        }), 502

    except Exception as error:

        print()
        print("================================")
        print("CHAT ERROR")
        print("================================")
        print(
            "ERROR:",
            repr(error)
        )
        print("================================")
        print()

        return jsonify({
            "error": "Internal server error.",
            "details": str(error)
        }), 500


# =========================================================
# REGENERATE
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/regenerate",
    methods=["POST"]
)
def regenerate(conversation_id):

    try:

        api_key = get_api_key()

        if not api_key:

            return jsonify({
                "error": (
                    "OPENROUTER_API_KEY "
                    "is not configured."
                )
            }), 500

        conn = None

        try:

            conn = get_db()

            rows = conn.execute("""
                SELECT
                    id,
                    role,
                    content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
            """, (
                conversation_id,
            )).fetchall()

        finally:

            if conn:
                conn.close()

        if not rows:

            return jsonify({
                "error": "Conversation is empty."
            }), 404

        last = rows[-1]

        if last["role"] == "assistant":

            execute_write("""
                DELETE FROM messages
                WHERE id = ?
            """, (
                last["id"],
            ))

            conn = get_db()

            try:

                rows = conn.execute("""
                    SELECT
                        role,
                        content
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id ASC
                """, (
                    conversation_id,
                )).fetchall()

            finally:

                conn.close()

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Whale AI.\n"
                    "Answer naturally in the same "
                    "language as the user.\n"
                    "Regenerate the response with "
                    "a better and more useful answer.\n"
                    + build_memory_context()
                )
            }
        ]

        for row in rows:

            if row["role"] in (
                "user",
                "assistant",
                "system"
            ):

                messages.append({
                    "role": row["role"],
                    "content": row["content"]
                })

        payload = build_payload(
            DEFAULT_MODEL,
            messages,
            web_search=False,
            stream=False
        )

        response = request_openrouter(
            api_key,
            DEFAULT_MODEL,
            payload
        )

        if response is None:

            return jsonify({
                "error": (
                    "No response "
                    "from OpenRouter."
                )
            }), 502

        if response.status_code != 200:

            return openrouter_error_response(
                response,
                DEFAULT_MODEL
            )

        result = response.json()

        reply = normalize_reply(
            result
        )

        if not reply:

            return jsonify({
                "error": (
                    "The model returned "
                    "an empty response."
                )
            }), 502

        save_message(
            conversation_id,
            "assistant",
            reply
        )

        return jsonify({
            "type": "done",
            "content": reply,
            "conversation_id": conversation_id,
            "model": DEFAULT_MODEL
        })

    except Exception as error:

        print(
            "REGENERATE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "Regeneration failed.",
            "details": str(error)
        }), 500


# =========================================================
# DELETE ONE CONVERSATION
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):

    try:

        with DB_WRITE_LOCK:

            conn = get_db()

            try:

                conn.execute(
                    "BEGIN IMMEDIATE"
                )

                conn.execute("""
                    DELETE FROM messages
                    WHERE conversation_id = ?
                """, (
                    conversation_id,
                ))

                cursor = conn.execute("""
                    DELETE FROM conversations
                    WHERE id = ?
                """, (
                    conversation_id,
                ))

                conn.commit()

                deleted = (
                    cursor.rowcount > 0
                )

            except Exception:

                conn.rollback()
                raise

            finally:

                conn.close()

        return jsonify({
            "success": True,
            "deleted": deleted
        })

    except Exception as error:

        return jsonify({
            "error": (
                "Failed to delete "
                "conversation."
            ),
            "details": str(error)
        }), 500


# =========================================================
# DELETE ALL CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

    try:

        with DB_WRITE_LOCK:

            conn = get_db()

            try:

                conn.execute(
                    "BEGIN IMMEDIATE"
                )

                conn.execute(
                    "DELETE FROM messages"
                )

                conn.execute(
                    "DELETE FROM conversations"
                )

                conn.commit()

            except Exception:

                conn.rollback()
                raise

            finally:

                conn.close()

        return jsonify({
            "success": True
        })

    except Exception as error:

        return jsonify({
            "error": (
                "Failed to delete "
                "conversations."
            ),
            "details": str(error)
        }), 500


# =========================================================
# API INFO
# =========================================================

@app.route(
    "/api",
    methods=["GET"]
)
def api_info():

    return jsonify({
        "name": "Whale AI",
        "version": "4.0",
        "model": DEFAULT_MODEL,
        "database": DB_FILE,
        "database_exists": os.path.exists(DB_FILE),

        "features": {
            "chat": True,
            "memory": True,
            "conversations": True,
            "conversation_search": True,
            "file_upload": True,
            "pdf": PdfReader is not None,
            "docx": Document is not None,
            "web_search": True,
            "web_fetch": True,
            "regenerate": True,
            "sqlite_migration": True,
            "cloud_ready": True
        }
    })


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "error": "Endpoint not found.",
        "path": request.path
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):

    return jsonify({
        "error": "Method not allowed.",
        "method": request.method,
        "path": request.path
    }), 405


# =========================================================
# START SERVER
# =========================================================

def start_server():

    host = "0.0.0.0"

    try:

        port = int(
            os.environ.get(
                "PORT",
                "5000"
            )
        )

    except (
        TypeError,
        ValueError
    ):

        port = 5000

    print()
    print("========================================")
    print("             WHALE AI 4.0")
    print("========================================")
    print("HOST:", host)
    print("PORT:", port)
    print("MODEL:", DEFAULT_MODEL)
    print(
        "FALLBACK:",
        FALLBACK_MODEL or "DISABLED"
    )
    print(
        "API KEY:",
        "FOUND"
        if get_api_key()
        else "MISSING"
    )
    print()
    print("DATABASE:")
    print(DB_FILE)
    print()
    print(
        "DATABASE EXISTS:",
        os.path.exists(DB_FILE)
    )
    print()
    print("MEMORY: ENABLED")
    print("CONVERSATIONS: ENABLED")
    print("FILE UPLOAD: ENABLED")
    print("WEB SEARCH: ENABLED")
    print("REGENERATE: ENABLED")
    print("SQLITE MIGRATION: ENABLED")
    print("========================================")
    print()

    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    # دیتابیس حتماً قبل از بالا آمدن Flask ساخته/اصلاح شود
    initialize_database()

    # بررسی نهایی
    if not os.path.exists(DB_FILE):

        raise RuntimeError(
            "whale.db could not be created.\n"
            f"Expected path: {DB_FILE}"
        )

    print()
    print("DATABASE READY:")
    print(DB_FILE)
    print()

    start_server()
