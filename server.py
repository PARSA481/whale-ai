
from flask import Flask, request, jsonify, send_from_directory, Response
from openai import OpenAI
from dotenv import load_dotenv
import sqlite3
import os
import sys
import json
import time
import threading
from functools import wraps

# =========================================================
# WHALE AI — ROBUST SERVER
# =========================================================

# ---------------------------------------------------------
# مسیرها
# ---------------------------------------------------------

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

ENV_FILE = os.path.join(BASE_DIR, ".env")
DB_FILE = os.path.join(BASE_DIR, "whale_ai.db")

load_dotenv(ENV_FILE)

# ---------------------------------------------------------
# Flask
# ---------------------------------------------------------

app = Flask(__name__)

app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# ---------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------

API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "openrouter/free"
).strip()

APP_URL = os.getenv(
    "APP_URL",
    ""
).strip()

APP_NAME = os.getenv(
    "APP_NAME",
    "Whale AI"
).strip()

REQUEST_TIMEOUT = float(
    os.getenv(
        "AI_TIMEOUT",
        "90"
    )
)

MAX_RETRIES = int(
    os.getenv(
        "AI_RETRIES",
        "2"
    )
)

MAX_HISTORY = int(
    os.getenv(
        "MAX_HISTORY",
        "30"
    )
)

print("====================================")
print("           WHALE AI SERVER")
print("====================================")
print("API KEY:", bool(API_KEY))
print("MODEL:", MODEL)
print("DATABASE:", DB_FILE)
print("====================================")

# ---------------------------------------------------------
# OpenRouter Client
# ---------------------------------------------------------

client = None

if API_KEY:
    client = OpenAI(
        api_key=API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=REQUEST_TIMEOUT,
        max_retries=MAX_RETRIES
    )

# ---------------------------------------------------------
# Database Lock
# ---------------------------------------------------------

db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with db_lock:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            PRAGMA journal_mode=WAL
        """)

        cursor.execute("""
            PRAGMA foreign_keys=ON
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'گفت‌وگوی جدید',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (
                    conversation_id
                )
                REFERENCES conversations(id)
                ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_messages_conversation
            ON messages(conversation_id, id)
        """)

        conn.commit()
        conn.close()


# ---------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------

def create_conversation(title="گفت‌وگوی جدید"):

    with db_lock:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO conversations(title)
            VALUES(?)
            """,
            (title,)
        )

        conversation_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return conversation_id


def conversation_exists(conversation_id):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM conversations
        WHERE id = ?
        """,
        (conversation_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def save_message(
    conversation_id,
    role,
    content
):

    with db_lock:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO messages(
                conversation_id,
                role,
                content
            )
            VALUES (?, ?, ?)
            """,
            (
                conversation_id,
                role,
                content
            )
        )

        conn.commit()
        conn.close()


def get_history(conversation_id):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (
            conversation_id,
            MAX_HISTORY
        )
    )

    rows = cursor.fetchall()

    conn.close()

    return [
        {
            "role": row["role"],
            "content": row["content"]
        }
        for row in rows
    ]


# ---------------------------------------------------------
# AI System Prompt
# ---------------------------------------------------------

SYSTEM_PROMPT = """
تو Whale AI هستی؛ یک دستیار هوشمند عمومی.

قوانین:
- پاسخ‌ها دقیق، واضح و مفید باشند.
- زبان پاسخ را با زبان کاربر هماهنگ کن.
- اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده.
- اگر کاربر انگلیسی صحبت کرد، انگلیسی پاسخ بده.
- اگر سؤال نیاز به توضیح دارد، مرحله‌به‌مرحله توضیح بده.
- اطلاعات را جعل نکن.
- اگر درباره موضوعی مطمئن نیستی، صادقانه بگو.
- برای کدنویسی، کد تمیز و قابل اجرا ارائه کن.
- پاسخ‌ها را تا حد ممکن ساختاریافته نگه دار.
""".strip()


# ---------------------------------------------------------
# Error Helper
# ---------------------------------------------------------

def json_error(
    message,
    status=400,
    code=None
):

    payload = {
        "success": False,
        "error": message
    }

    if code:
        payload["code"] = code

    return jsonify(payload), status


# ---------------------------------------------------------
# Request Logging
# ---------------------------------------------------------

@app.before_request
def request_log():

    request.start_time = time.time()

    print(
        f"[REQUEST] {request.method} {request.path}"
    )


@app.after_request
def response_log(response):

    elapsed = (
        time.time() -
        getattr(
            request,
            "start_time",
            time.time()
        )
    )

    print(
        f"[RESPONSE] "
        f"{request.method} "
        f"{request.path} "
        f"{response.status_code} "
        f"{elapsed:.2f}s"
    )

    response.headers["X-Powered-By"] = "Whale AI"

    return response


# ---------------------------------------------------------
# Security / Basic Validation
# ---------------------------------------------------------

def validate_conversation_id(value):

    if value is None:
        return None

    try:

        value = int(value)

        if value <= 0:
            return None

        return value

    except (
        TypeError,
        ValueError
    ):

        return None


# ---------------------------------------------------------
# INDEX
# ---------------------------------------------------------

@app.route("/")
def index():

    if getattr(sys, "frozen", False):

        index_path = os.path.join(
            BUNDLE_DIR,
            "index.html"
        )

    else:

        index_path = os.path.join(
            BASE_DIR,
            "index.html"
        )

    if not os.path.exists(index_path):

        return (
            """
            <h2>Whale AI</h2>
            <p>index.html پیدا نشد.</p>
            """,
            404
        )

    return send_from_directory(
        os.path.dirname(index_path),
        "index.html"
    )


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "Whale AI",
        "ai_configured": bool(API_KEY),
        "model": MODEL
    })


# ---------------------------------------------------------
# API STATUS
# ---------------------------------------------------------

@app.route("/api/status")
def api_status():

    return jsonify({

        "success": True,

        "server": "online",

        "ai": (
            "configured"
            if API_KEY
            else "missing_key"
        ),

        "model": MODEL,

        "database": os.path.exists(
            DB_FILE
        )

    })


# =========================================================
# CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["POST"]
)
def new_conversation():

    conversation_id = create_conversation()

    return jsonify({

        "success": True,

        "id": conversation_id,

        "title": "گفت‌وگوی جدید"

    })


@app.route(
    "/conversations",
    methods=["GET"]
)
def get_conversations():

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            c.id,
            c.title,
            c.created_at,
            COUNT(m.id) AS message_count
        FROM conversations c

        LEFT JOIN messages m
        ON c.id = m.conversation_id

        GROUP BY c.id

        ORDER BY c.id DESC
    """)

    rows = cursor.fetchall()

    conn.close()

    return jsonify([

        {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "message_count": row["message_count"]
        }

        for row in rows

    ])


@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(conversation_id):

    if not conversation_exists(
        conversation_id
    ):

        return json_error(
            "گفت‌وگو پیدا نشد.",
            404,
            "CONVERSATION_NOT_FOUND"
        )

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            role,
            content,
            created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,)
    )

    rows = cursor.fetchall()

    conn.close()

    return jsonify([

        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        }

        for row in rows

    ])


# =========================================================
# CHAT
# =========================================================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    data = request.get_json(
        silent=True
    )

    if not isinstance(
        data,
        dict
    ):

        return json_error(
            "داده نامعتبر است.",
            400,
            "INVALID_JSON"
        )

    text = str(
        data.get(
            "message",
            ""
        )
    ).strip()

    conversation_id = validate_conversation_id(
        data.get(
            "conversation_id"
        )
    )

    # ---------------------------------------------
    # Validation
    # ---------------------------------------------

    if not text:

        return json_error(
            "پیامی دریافت نشد.",
            400,
            "EMPTY_MESSAGE"
        )

    if len(text) > 20000:

        return json_error(
            "پیام بیش از حد طولانی است.",
            413,
            "MESSAGE_TOO_LONG"
        )

    # ---------------------------------------------
    # API KEY
    # ---------------------------------------------

    if not API_KEY or client is None:

        return json_error(
            "کلید OpenRouter تنظیم نشده است.",
            500,
            "AI_NOT_CONFIGURED"
        )

    # ---------------------------------------------
    # Conversation
    # ---------------------------------------------

    if conversation_id is None:

        conversation_id = create_conversation()

    elif not conversation_exists(
        conversation_id
    ):

        return json_error(
            "گفت‌وگو پیدا نشد.",
            404,
            "CONVERSATION_NOT_FOUND"
        )

    # ---------------------------------------------
    # Save User Message
    # ---------------------------------------------

    save_message(
        conversation_id,
        "user",
        text
    )

    # ---------------------------------------------
    # History
    # ---------------------------------------------

    history = get_history(
        conversation_id
    )

    messages = [

        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }

    ]

    messages.extend(history)

    # ---------------------------------------------
    # Streaming Generator
    # ---------------------------------------------

    def generate():

        full_reply = ""

        try:

            print(
                "[AI] Sending request..."
            )

            extra_headers = {}

            if APP_NAME:

                extra_headers[
                    "X-Title"
                ] = APP_NAME

            if APP_URL:

                extra_headers[
                    "HTTP-Referer"
                ] = APP_URL

            # -------------------------------------
            # OpenRouter Request
            # -------------------------------------

            response = client.chat.completions.create(

                model=MODEL,

                messages=messages,

                temperature=0.7,

                stream=True,

                extra_headers=extra_headers

            )

            for chunk in response:

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]

                delta = getattr(
                    choice.delta,
                    "content",
                    None
                )

                if not delta:
                    continue

                full_reply += delta

                yield (
                    json.dumps(
                        {
                            "type": "text",
                            "content": delta
                        },
                        ensure_ascii=False
                    )
                    + "\n"
                )

            # -------------------------------------
            # Empty Response
            # -------------------------------------

            if not full_reply.strip():

                full_reply = (
                    "متأسفانه پاسخی از مدل دریافت نشد."
                )

                yield (
                    json.dumps(
                        {
                            "type": "text",
                            "content": full_reply
                        },
                        ensure_ascii=False
                    )
                    + "\n"
                )

            # -------------------------------------
            # Save AI Response
            # -------------------------------------

            save_message(
                conversation_id,
                "assistant",
                full_reply
            )

            # -------------------------------------
            # Automatic Title
            # -------------------------------------

            if len(history) <= 1:

                title = text.replace(
                    "\n",
                    " "
                ).strip()

                if len(title) > 50:

                    title = (
                        title[:50] +
                        "..."
                    )

                with db_lock:

                    conn = get_db()
                    cursor = conn.cursor()

                    cursor.execute(
                        """
                        UPDATE conversations
                        SET title = ?
                        WHERE id = ?
                        """,
                        (
                            title,
                            conversation_id
                        )
                    )

                    conn.commit()
                    conn.close()

            # -------------------------------------
            # Done
            # -------------------------------------

            yield (
                json.dumps(
                    {
                        "type": "done",
                        "conversation_id":
                            conversation_id
                    },
                    ensure_ascii=False
                )
                + "\n"
            )

            print(
                "[AI] Response completed."
            )

        except Exception as error:

            print(
                "[AI ERROR]",
                repr(error)
            )

            error_text = str(error)

            # -------------------------------------
            # Authentication
            # -------------------------------------

            if (
                "401" in error_text
                or
                "Authentication" in error_text
                or
                "Missing Authentication" in error_text
            ):

                message = (
                    "خطای احراز هویت OpenRouter. "
                    "کلید API را بررسی کن."
                )

            # -------------------------------------
            # Rate Limit
            # -------------------------------------

            elif (
                "429" in error_text
                or
                "rate" in error_text.lower()
            ):

                message = (
                    "تعداد درخواست‌ها زیاد است. "
                    "چند لحظه بعد دوباره تلاش کن."
                )

            # -------------------------------------
            # Timeout
            # -------------------------------------

            elif (
                "timeout" in error_text.lower()
                or
                "timed out" in error_text.lower()
            ):

                message = (
                    "زمان دریافت پاسخ تمام شد. "
                    "دوباره تلاش کن."
                )

            # -------------------------------------
            # Generic
            # -------------------------------------

            else:

                message = (
                    "ارتباط با هوش مصنوعی برقرار نشد."
                )

            yield (
                json.dumps(
                    {
                        "type": "error",
                        "content": message
                    },
                    ensure_ascii=False
                )
                + "\n"
            )

    # ---------------------------------------------
    # Response
    # ---------------------------------------------

    response = Response(
        generate(),
        mimetype="application/x-ndjson"
    )

    response.headers[
        "Cache-Control"
    ] = "no-cache"

    response.headers[
        "X-Accel-Buffering"
    ] = "no"

    response.headers[
        "Connection"
    ] = "keep-alive"

    return response


# =========================================================
# DELETE ONE CONVERSATION
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(
    conversation_id
):

    if not conversation_exists(
        conversation_id
    ):

        return json_error(
            "گفت‌وگو پیدا نشد.",
            404,
            "CONVERSATION_NOT_FOUND"
        )

    with db_lock:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            DELETE FROM messages
            WHERE conversation_id = ?
            """,
            (conversation_id,)
        )

        cursor.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        )

        conn.commit()
        conn.close()

    return jsonify({
        "success": True
    })


# =========================================================
# DELETE ALL
# =========================================================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

    with db_lock:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM messages"
        )

        cursor.execute(
            "DELETE FROM conversations"
        )

        conn.commit()
        conn.close()

    return jsonify({
        "success": True
    })


# =========================================================
# GLOBAL ERROR HANDLERS
# =========================================================

@app.errorhandler(413)
def too_large(error):

    return json_error(
        "حجم درخواست بیش از حد مجاز است.",
        413,
        "REQUEST_TOO_LARGE"
    )


@app.errorhandler(404)
def not_found(error):

    return json_error(
        "مسیر موردنظر پیدا نشد.",
        404,
        "NOT_FOUND"
    )


@app.errorhandler(405)
def method_not_allowed(error):

    return json_error(
        "این متد برای این مسیر مجاز نیست.",
        405,
        "METHOD_NOT_ALLOWED"
    )


@app.errorhandler(500)
def internal_error(error):

    print(
        "[SERVER ERROR]",
        repr(error)
    )

    return json_error(
        "خطای داخلی سرور.",
        500,
        "INTERNAL_SERVER_ERROR"
    )


# =========================================================
# START
# =========================================================

init_db()

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print()
    print("====================================")
    print("              WHALE AI")
    print("====================================")
    print("Server: 0.0.0.0")
    print("Port:", port)
    print("Model:", MODEL)
    print("AI:", "READY" if API_KEY else "NO KEY")
    print("====================================")
    print()

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )
