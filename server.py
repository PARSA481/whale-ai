import os
import sqlite3
import requests

from flask import Flask, request, jsonify, send_from_directory


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
    """
    کلید را هر بار از Environment می‌خوانیم
    تا اگر محیط تغییر کرد، برنامه کلید قدیمی نداشته باشد.
    """

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
        SELECT
            id,
            key,
            value
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
            SET
                value = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = ?
            """,
            (
                value,
                key
            )
        )

    else:

        conn.execute(
            """
            INSERT INTO memories
            (
                key,
                value
            )
            VALUES (?, ?)
            """,
            (
                key,
                value
            )
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
    }), 200


# =========================================================
# GET CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["GET"]
)
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
            "error": "خطا در دریافت تاریخچه گفتگوها."
        }), 500


# =========================================================
# CREATE CONVERSATION
# =========================================================

@app.route(
    "/conversations",
    methods=["POST"]
)
def create_conversation():

    try:

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO conversations
            (title)
            VALUES (?)
            """,
            (
                "گفتگوی جدید",
            )
        )

        conversation_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return jsonify({
            "id": conversation_id,
            "title": "گفتگوی جدید"
        }), 200

    except Exception as error:

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ساخت گفتگو."
        }), 500


# =========================================================
# GET MESSAGES
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(conversation_id):

    try:

        conn = get_db()

        rows = conn.execute(
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
            (
                conversation_id,
            )
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
            "error": "خطا در دریافت پیام‌های گفتگو."
        }), 500


# =========================================================
# MEMORY GET
# =========================================================

@app.route(
    "/memory",
    methods=["GET"]
)
def memory_get():

    try:

        return jsonify({
            "memories": get_memories()
        }), 200

    except Exception as error:

        print(
            "MEMORY GET ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت حافظه."
        }), 500


# =========================================================
# MEMORY ADD / UPDATE
# =========================================================

@app.route(
    "/memory",
    methods=["POST"]
)
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
                "error":
                    "key و value الزامی هستند."
            }), 400

        save_memory(
            key,
            value
        )

        return jsonify({
            "success": True,
            "key": key,
            "value": value
        }), 200

    except Exception as error:

        print(
            "MEMORY ADD ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ذخیره حافظه."
        }), 500


# =========================================================
# MEMORY DELETE ONE
# =========================================================

@app.route(
    "/memory/<path:key>",
    methods=["DELETE"]
)
def memory_delete(key):

    try:

        delete_memory(key)

        return jsonify({
            "success": True
        }), 200

    except Exception as error:

        print(
            "MEMORY DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف حافظه."
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
        }), 200

    except Exception as error:

        print(
            "MEMORY DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در پاک کردن حافظه."
        }), 500


# =========================================================
# CHAT
# =========================================================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    try:

        # -------------------------------------------------
        # API KEY
        # -------------------------------------------------

        api_key = get_api_key()

        print("================================")
        print("WHALE AI / CHAT")
        print("API KEY EXISTS:", bool(api_key))
        print("API KEY LENGTH:", len(api_key))
        print("================================")

        if not api_key:

            print(
                "ERROR: OPENROUTER_API_KEY is missing"
            )

            return jsonify({
                "error":
                    "کلید OpenRouter در Environment Variables تنظیم نشده است."
            }), 500


        # -------------------------------------------------
        # REQUEST
        # -------------------------------------------------

        data = request.get_json(
            silent=True
        ) or {}

        user_message = str(
            data.get(
                "message",
                ""
            )
        ).strip()

        conversation_id = data.get(
            "conversation_id"
        )


        if not user_message:

            return jsonify({
                "error":
                    "پیام خالی است."
            }), 400


        # -------------------------------------------------
        # CONVERSATION
        # -------------------------------------------------

        conn = get_db()

        if conversation_id:

            try:

                conversation_id = int(
                    conversation_id
                )

            except (TypeError, ValueError):

                conversation_id = None


        if conversation_id:

            exists = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = ?
                """,
                (
                    conversation_id,
                )
            ).fetchone()

            if not exists:

                conversation_id = None


        if not conversation_id:

            cursor = conn.execute(
                """
                INSERT INTO conversations
                (title)
                VALUES (?)
                """,
                (
                    user_message[:40],
                )
            )

            conversation_id = cursor.lastrowid


        # -------------------------------------------------
        # OLD MESSAGES
        # -------------------------------------------------

        rows = conn.execute(
            """
            SELECT
                role,
                content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (
                conversation_id,
            )
        ).fetchall()


        # -------------------------------------------------
        # SAVE USER MESSAGE
        # -------------------------------------------------

        conn.execute(
            """
            INSERT INTO messages
            (
                conversation_id,
                role,
                content
            )
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


        # -------------------------------------------------
        # MEMORY
        # -------------------------------------------------

        memory_context = \
            build_memory_context()


        # -------------------------------------------------
        # SYSTEM PROMPT
        # -------------------------------------------------

        system_prompt = """
تو Whale AI هستی؛ یک دستیار هوشمند فارسی.

قوانین پاسخ:

1. اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده.

2. پاسخ‌ها طبیعی، واضح و خوانا باشند.

3. از Markdown استفاده کن.

4. برای عنوان‌های مهم از تیتر Markdown استفاده کن.

5. برای مراحل از فهرست شماره‌دار استفاده کن.

6. برای موارد مقایسه‌ای، اگر مناسب بود از جدول Markdown استفاده کن.

7. برای کد حتماً از code block استفاده کن.

8. پاسخ را بی‌دلیل به خطوط کوتاه و جداگانه تقسیم نکن.

9. پاسخ باید متناسب با سؤال باشد و بی‌دلیل طولانی نشود.

10. از اطلاعات حافظه فقط زمانی استفاده کن که به سؤال مربوط باشد.

11. اطلاعات خصوصی موجود در حافظه را بی‌دلیل نمایش نده.

12. اگر پاسخ نیاز به توضیح دارد، آن را منظم و مرحله‌به‌مرحله ارائه کن.
"""

        system_prompt += memory_context


        # -------------------------------------------------
        # BUILD OPENROUTER MESSAGES
        # -------------------------------------------------

        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]


        for row in rows:

            role = row["role"]

            if role not in (
                "user",
                "assistant",
                "system"
            ):
                continue

            messages.append({
                "role": role,
                "content": row["content"]
            })


        # -------------------------------------------------
        # CURRENT MESSAGE
        # -------------------------------------------------

        messages.append({
            "role": "user",
            "content": user_message
        })


        # -------------------------------------------------
        # OPENROUTER HEADERS
        # -------------------------------------------------

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://snapdeploy.dev",
            "X-Title": "Whale AI"
        }


        # -------------------------------------------------
        # OPENROUTER PAYLOAD
        # -------------------------------------------------

        payload = {
            "model": "openrouter/free",
            "messages": messages,
            "temperature": 0.7,
            "stream": False
        }


        print("CONVERSATION:", conversation_id)
        print("MEMORIES:", len(get_memories()))
        print("SENDING REQUEST TO OPENROUTER")


        # -------------------------------------------------
        # REQUEST
        # -------------------------------------------------

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=120
        )


        print(
            "OPENROUTER STATUS:",
            response.status_code
        )

        print(
            "OPENROUTER RESPONSE:",
            response.text[:3000]
        )


        # -------------------------------------------------
        # OPENROUTER ERROR
        # -------------------------------------------------

        if response.status_code != 200:

            return jsonify({

                "error":
                    "OpenRouter خطا داد.",

                "status":
                    response.status_code,

                "details":
                    response.text[:2000]

            }), 502


        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:

            result = response.json()

        except ValueError:

            print(
                "OPENROUTER JSON ERROR"
            )

            return jsonify({
                "error":
                    "پاسخ OpenRouter JSON معتبر نبود."
            }), 502


        # -------------------------------------------------
        # ERROR OBJECT
        # -------------------------------------------------

        if result.get("error"):

            print(
                "OPENROUTER API ERROR:",
                result["error"]
            )

            return jsonify({
                "error":
                    "OpenRouter خطا داد.",
                "details":
                    result["error"]
            }), 502


        # -------------------------------------------------
        # CHOICES
        # -------------------------------------------------

        choices = result.get(
            "choices"
        )

        if not choices:

            print(
                "ERROR: choices not found"
            )

            return jsonify({
                "error":
                    "OpenRouter پاسخ قابل استفاده‌ای برنگرداند.",
                "details":
                    result
            }), 502


        # -------------------------------------------------
        # MESSAGE
        # -------------------------------------------------

        message_data = choices[0].get(
            "message",
            {}
        )

        reply = message_data.get(
            "content",
            ""
        )


        # -------------------------------------------------
        # NORMALIZE
        # -------------------------------------------------

        if isinstance(
            reply,
            list
        ):

            parts = []

            for item in reply:

                if isinstance(
                    item,
                    dict
                ):

                    text = item.get(
                        "text",
                        ""
                    )

                    if text:
                        parts.append(text)

                elif isinstance(
                    item,
                    str
                ):

                    parts.append(item)

            reply = "".join(parts)


        reply = str(
            reply
        ).strip()


        if not reply:

            return jsonify({
                "error":
                    "متن پاسخ OpenRouter خالی است."
            }), 502


        # -------------------------------------------------
        # SAVE ASSISTANT
        # -------------------------------------------------

        conn = get_db()

        conn.execute(
            """
            INSERT INTO messages
            (
                conversation_id,
                role,
                content
            )
            VALUES (?, ?, ?)
            """,
            (
                conversation_id,
                "assistant",
                reply
            )
        )

        conn.commit()
        conn.close()


        # -------------------------------------------------
        # RETURN
        # -------------------------------------------------

        return jsonify({

            "type":
                "done",

            "content":
                reply,

            "conversation_id":
                conversation_id

        }), 200


    # =====================================================
    # REQUEST ERROR
    # =====================================================

    except requests.RequestException as error:

        print(
            "OPENROUTER REQUEST ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "ارتباط با OpenRouter برقرار نشد.",

            "details":
                str(error)

        }), 502


    # =====================================================
    # GENERAL ERROR
    # =====================================================

    except Exception as error:

        print(
            "CHAT ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "خطای داخلی سرور.",

            "details":
                str(error)

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

        conn = get_db()

        conn.execute(
            """
            DELETE FROM messages
            WHERE conversation_id = ?
            """,
            (
                conversation_id,
            )
        )

        conn.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
            """,
            (
                conversation_id,
            )
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
            "error":
                "خطا در حذف گفتگو."
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
            "error":
                "خطا در حذف گفتگوها."
        }), 500


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

    api_key = get_api_key()

    print("================================")
    print("WHALE AI STARTING")
    print("PORT:", port)
    print(
        "OPENROUTER KEY:",
        bool(api_key)
    )
    print(
        "KEY LENGTH:",
        len(api_key)
    )
    print(
        "MEMORY SYSTEM: ENABLED"
    )
    print("================================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
