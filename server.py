import os
import sqlite3
import json
import requests

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    Response,
)

# =========================================================
# APP
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "whale.db")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# =========================================================
# API KEY
# =========================================================

def get_api_key():
    return os.environ.get(
        "OPENROUTER_API_KEY",
        ""
    ).strip()


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'گفتگوی جدید',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    return conn


# =========================================================
# MEMORY
# =========================================================

def get_memories():

    conn = get_db()

    rows = conn.execute("""
        SELECT id, key, value
        FROM memories
        ORDER BY id ASC
    """).fetchall()

    conn.close()

    return [
        {
            "id": row["id"],
            "key": row["key"],
            "value": row["value"]
        }
        for row in rows
    ]


def save_memory(key, value):

    key = str(key).strip()
    value = str(value).strip()

    if not key or not value:
        return

    conn = get_db()

    existing = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE key = ?
        """,
        (key,)
    ).fetchone()

    if existing:

        conn.execute(
            """
            UPDATE memories
            SET value = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = ?
            """,
            (value, key)
        )

    else:

        conn.execute(
            """
            INSERT INTO memories
            (key, value)
            VALUES (?, ?)
            """,
            (key, value)
        )

    conn.commit()
    conn.close()


def delete_memory(key):

    conn = get_db()

    conn.execute(
        """
        DELETE FROM memories
        WHERE key = ?
        """,
        (key,)
    )

    conn.commit()
    conn.close()


def clear_memories():

    conn = get_db()

    conn.execute(
        "DELETE FROM memories"
    )

    conn.commit()
    conn.close()


def build_memory_context():

    memories = get_memories()

    if not memories:
        return ""

    lines = []

    for memory in memories:

        lines.append(
            "- {}: {}".format(
                memory["key"],
                memory["value"]
            )
        )

    return (
        "\n\n"
        "اطلاعات ذخیره‌شده درباره کاربر:\n"
        + "\n".join(lines)
        + "\n"
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    index_file = os.path.join(
        BASE_DIR,
        "index.html"
    )

    if os.path.exists(index_file):

        return send_from_directory(
            BASE_DIR,
            "index.html"
        )

    return "Whale AI is running."


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    api_key = get_api_key()

    try:

        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()

        database = True

    except Exception as error:

        print(
            "HEALTH DATABASE ERROR:",
            repr(error)
        )

        database = False

    return jsonify({
        "status": "ok",
        "openrouter_key": bool(api_key),
        "key_length": len(api_key),
        "database": database,
        "memory": True
    })


# =========================================================
# CONVERSATIONS
# =========================================================

@app.route("/conversations", methods=["GET"])
def get_conversations():

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                c.id,
                c.title,
                c.created_at,
                COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m
                ON m.conversation_id = c.id
            GROUP BY
                c.id,
                c.title,
                c.created_at
            ORDER BY c.id DESC
        """).fetchall()

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

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت گفتگوها."
        }), 500


@app.route("/conversations", methods=["POST"])
def create_conversation():

    try:

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO conversations (title)
            VALUES (?)
            """,
            ("گفتگوی جدید",)
        )

        conversation_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return jsonify({
            "id": conversation_id,
            "title": "گفتگوی جدید"
        })

    except Exception as error:

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ساخت گفتگو."
        }), 500


@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(conversation_id):

    try:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,)
        ).fetchall()

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

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت پیام‌ها."
        }), 500


@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):

    try:

        conn = get_db()

        conn.execute(
            """
            DELETE FROM messages
            WHERE conversation_id = ?
            """,
            (conversation_id,)
        )

        conn.execute(
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

    except Exception as error:

        print(
            "DELETE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف گفتگو."
        }), 500


@app.route("/conversations", methods=["DELETE"])
def delete_all_conversations():

    try:

        conn = get_db()

        conn.execute(
            "DELETE FROM messages"
        )

        conn.execute(
            "DELETE FROM conversations"
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True
        })

    except Exception as error:

        print(
            "DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در پاک کردن گفتگوها."
        }), 500


# =========================================================
# MEMORY API
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
            "error": "خطا در دریافت حافظه."
        }), 500


@app.route("/memory", methods=["POST"])
def memory_add():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        key = str(
            data.get("key", "")
        ).strip()

        value = str(
            data.get("value", "")
        ).strip()

        if not key or not value:

            return jsonify({
                "error": "key و value الزامی هستند."
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
            "MEMORY ADD ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ذخیره حافظه."
        }), 500


@app.route(
    "/memory/<path:key>",
    methods=["DELETE"]
)
def memory_delete(key):

    try:

        delete_memory(key)

        return jsonify({
            "success": True
        })

    except Exception as error:

        print(
            "MEMORY DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف حافظه."
        }), 500


@app.route("/memory", methods=["DELETE"])
def memory_delete_all():

    try:

        clear_memories()

        return jsonify({
            "success": True
        })

    except Exception as error:

        print(
            "MEMORY DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در پاک کردن حافظه."
        }), 500


# =========================================================
# FILE UPLOAD
# =========================================================

@app.route("/upload", methods=["POST"])
def upload_file():

    try:

        if "file" not in request.files:

            return jsonify({
                "error": "فایلی ارسال نشده است."
            }), 400

        file = request.files["file"]

        if not file or not file.filename:

            return jsonify({
                "error": "نام فایل نامعتبر است."
            }), 400

        filename = file.filename

        extension = (
            os.path.splitext(filename)[1]
            .lower()
            .replace(".", "")
        )

        allowed = {
            "txt",
            "md",
            "csv",
            "json"
        }

        if extension not in allowed:

            return jsonify({
                "error":
                    "در این نسخه فقط TXT، MD، CSV و JSON قابل خواندن هستند."
            }), 400

        raw = file.read()

        if len(raw) > 20 * 1024 * 1024:

            return jsonify({
                "error":
                    "حجم فایل نباید بیشتر از 20MB باشد."
            }), 400

        text = raw.decode(
            "utf-8",
            errors="replace"
        )

        return jsonify({
            "success": True,
            "filename": filename,
            "extension": extension,
            "text": text
        })

    except Exception as error:

        print(
            "UPLOAD ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خواندن فایل انجام نشد."
        }), 500


# =========================================================
# CHAT
# =========================================================

def build_system_prompt():

    prompt = """
تو Whale AI هستی؛ یک دستیار هوشمند فارسی.

قوانین:

1. اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده.
2. پاسخ طبیعی، دقیق و خوانا باشد.
3. از Markdown استفاده کن.
4. برای عنوان‌های مهم از Markdown heading استفاده کن.
5. برای مراحل از فهرست شماره‌دار استفاده کن.
6. برای مقایسه‌ها در صورت مناسب بودن جدول Markdown استفاده کن.
7. برای کد از code block استفاده کن.
8. پاسخ را بی‌دلیل طولانی نکن.
9. اگر اطلاعات جدید یا وابسته به زمان لازم است، از ابزار جست‌وجوی وب استفاده کن.
10. اگر جست‌وجوی وب فعال است، اطلاعات پیدا شده را با دقت در پاسخ استفاده کن.
11. اطلاعات حافظه فقط در صورت مرتبط بودن استفاده شود.
12. اطلاعات خصوصی حافظه را بی‌دلیل نمایش نده.
"""

    prompt += build_memory_context()

    return prompt


def create_messages(
    conversation_id,
    user_message,
    file_text=""
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,)
    ).fetchall()

    conn.close()

    messages = [
        {
            "role": "system",
            "content": build_system_prompt()
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

    current_content = user_message

    if file_text:

        current_content += (
            "\n\n"
            "محتوای فایل ارسال‌شده:\n"
            "--------------------\n"
            + file_text[:500000]
            + "\n--------------------"
        )

    messages.append({
        "role": "user",
        "content": current_content
    })

    return messages


# =========================================================
# CHAT STREAM
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():

    data = request.get_json(
        silent=True
    ) or {}

    user_message = str(
        data.get("message", "")
    ).strip()

    file_text = str(
        data.get("file_text", "")
    )

    conversation_id = data.get(
        "conversation_id"
    )

    web_search = bool(
        data.get("web_search", False)
    )

    if not user_message and not file_text:

        return jsonify({
            "error": "پیام خالی است."
        }), 400

    api_key = get_api_key()

    if not api_key:

        return jsonify({
            "error":
                "OPENROUTER_API_KEY در Environment Variables تنظیم نشده است."
        }), 500

    conn = get_db()

    if conversation_id:

        try:
            conversation_id = int(
                conversation_id
            )
        except:
            conversation_id = None

    if conversation_id:

        exists = conn.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        ).fetchone()

        if not exists:
            conversation_id = None

    if not conversation_id:

        title = (
            user_message[:45]
            if user_message
            else "فایل جدید"
        )

        cursor = conn.execute(
            """
            INSERT INTO conversations (title)
            VALUES (?)
            """,
            (title,)
        )

        conversation_id = cursor.lastrowid

    conn.execute(
        """
        INSERT INTO messages
        (conversation_id, role, content)
        VALUES (?, ?, ?)
        """,
        (
            conversation_id,
            "user",
            user_message
        )
    )

    conn.commit()
    conn.close()

    messages = create_messages(
        conversation_id,
        user_message,
        file_text
    )

    headers = {
        "Authorization":
            "Bearer " + api_key,
        "Content-Type":
            "application/json",
        "HTTP-Referer":
            "https://snapdeploy.dev",
        "X-Title":
            "Whale AI"
    }

    payload = {
        "model": "openrouter/free",
        "messages": messages,
        "temperature": 0.7,
        "stream": True
    }

    # =====================================================
    # WEB SEARCH
    # =====================================================

    if web_search:

        payload["plugins"] = [
            {
                "id": "web",
                "max_results": 5
            }
        ]

    print("================================")
    print("WHALE AI CHAT")
    print("CONVERSATION:", conversation_id)
    print("WEB SEARCH:", web_search)
    print("KEY:", bool(api_key))
    print("================================")

    def generate():

        assistant_text = ""

        try:

            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=180,
                stream=True
            )

            if response.status_code != 200:

                error_text = response.text[:3000]

                yield "data: " + json.dumps(
                    {
                        "type": "error",
                        "error":
                            "OpenRouter خطا داد.",
                        "details":
                            error_text,
                        "conversation_id":
                            conversation_id
                    },
                    ensure_ascii=False
                ) + "\n\n"

                yield "data: [DONE]\n\n"

                return

            for raw_line in response.iter_lines(
                decode_unicode=True
            ):

                if not raw_line:
                    continue

                line = raw_line.strip()

                if not line.startswith("data:"):
                    continue

                raw_data = line[5:].strip()

                if raw_data == "[DONE]":

                    break

                try:

                    chunk = json.loads(
                        raw_data
                    )

                except Exception:

                    continue

                if chunk.get("error"):

                    yield "data: " + json.dumps(
                        {
                            "type": "error",
                            "error":
                                "OpenRouter خطا داد.",
                            "details":
                                chunk["error"]
                        },
                        ensure_ascii=False
                    ) + "\n\n"

                    return

                choices = chunk.get(
                    "choices",
                    []
                )

                if not choices:
                    continue

                delta = choices[0].get(
                    "delta",
                    {}
                )

                content = delta.get(
                    "content",
                    ""
                )

                if isinstance(
                    content,
                    list
                ):

                    parts = []

                    for item in content:

                        if isinstance(
                            item,
                            dict
                        ):

                            text = item.get(
                                "text",
                                ""
                            )

                            if text:
                                parts.append(
                                    text
                                )

                        elif isinstance(
                            item,
                            str
                        ):

                            parts.append(item)

                    content = "".join(parts)

                if content:

                    assistant_text += content

                    yield "data: " + json.dumps(
                        {
                            "type": "delta",
                            "content": content,
                            "conversation_id":
                                conversation_id
                        },
                        ensure_ascii=False
                    ) + "\n\n"

            assistant_text = assistant_text.strip()

            if assistant_text:

                conn = get_db()

                conn.execute(
                    """
                    INSERT INTO messages
                    (conversation_id, role, content)
                    VALUES (?, ?, ?)
                    """,
                    (
                        conversation_id,
                        "assistant",
                        assistant_text
                    )
                )

                conn.commit()
                conn.close()

            yield "data: " + json.dumps(
                {
                    "type": "done",
                    "conversation_id":
                        conversation_id
                },
                ensure_ascii=False
            ) + "\n\n"

            yield "data: [DONE]\n\n"

        except requests.RequestException as error:

            print(
                "OPENROUTER REQUEST ERROR:",
                repr(error)
            )

            yield "data: " + json.dumps(
                {
                    "type": "error",
                    "error":
                        "ارتباط با OpenRouter برقرار نشد.",
                    "details":
                        str(error)
                },
                ensure_ascii=False
            ) + "\n\n"

        except GeneratorExit:

            print(
                "CLIENT DISCONNECTED"
            )

        except Exception as error:

            print(
                "STREAM ERROR:",
                repr(error)
            )

            yield "data: " + json.dumps(
                {
                    "type": "error",
                    "error":
                        "خطای داخلی سرور.",
                    "details":
                        str(error)
                },
                ensure_ascii=False
            ) + "\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    print("================================")
    print("WHALE AI STARTING")
    print("PORT:", port)
    print(
        "OPENROUTER KEY:",
        bool(get_api_key())
    )
    print(
        "MEMORY SYSTEM: ENABLED"
    )
    print(
        "WEB SEARCH: ENABLED"
    )
    print("================================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )
