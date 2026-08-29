import os
import sqlite3
import json
import requests

from flask import Flask, request, jsonify, send_from_directory, Response


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "whale.db")


# =========================================================
# OPENROUTER
# =========================================================

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

API_KEY = os.environ.get(
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
            role TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    return conn


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

    return jsonify({
        "status": "ok",
        "openrouter_key": bool(API_KEY)
    })


# =========================================================
# GET CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["GET"]
)
def get_conversations():

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


# =========================================================
# CREATE CONVERSATION
# =========================================================

@app.route(
    "/conversations",
    methods=["POST"]
)
def create_conversation():

    conn = get_db()

    cursor = conn.execute(
        """
        INSERT INTO conversations
        (title)
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


# =========================================================
# GET MESSAGES
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(conversation_id):

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


        # -------------------------------------------------
        # VALIDATION
        # -------------------------------------------------

        if not user_message:

            return jsonify({
                "error": "پیام خالی است."
            }), 400


        if not API_KEY:

            print(
                "ERROR: OPENROUTER_API_KEY is missing"
            )

            return jsonify({
                "error":
                    "کلید OpenRouter تنظیم نشده است."
            }), 500


        # -------------------------------------------------
        # CONVERSATION
        # -------------------------------------------------

        conn = get_db()


        if conversation_id:

            conversation = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,)
            ).fetchone()

            if not conversation:

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

            conn.commit()


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
        # BUILD AI MESSAGES
        # -------------------------------------------------

        messages = [

            {
                "role": "system",
                "content": """
تو Whale AI هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان.

قوانین پاسخ‌دهی:

- اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده.
- پاسخ‌ها طبیعی، روان و حرفه‌ای باشند.
- پاسخ را بی‌دلیل خط‌خطی نکن.
- از پاراگراف‌های مرتب استفاده کن.
- برای فهرست‌ها از bullet استفاده کن.
- اگر جدول واقعاً مفید بود از جدول Markdown استفاده کن.
- برای توضیحات آموزشی مرحله‌بندی واضح داشته باش.
- از تیترهای مناسب استفاده کن.
- پاسخ‌ها بیش از حد خشک و رباتی نباشند.
- اگر سؤال ساده است، پاسخ را کوتاه و مستقیم بده.
- اگر سؤال پیچیده است، کامل توضیح بده.
- کدها را داخل code block قرار بده.
"""
            }

        ]


        for row in rows:

            messages.append({
                "role": row["role"],
                "content": row["content"]
            })


        # پیام جدید قبلاً در دیتابیس ذخیره شده
        # اما برای OpenRouter باید در درخواست هم باشد.

        messages.append({
            "role": "user",
            "content": user_message
        })


        # -------------------------------------------------
        # HEADERS
        # -------------------------------------------------

        headers = {

            "Authorization":
                f"Bearer {API_KEY}",

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                "https://whale-ai-c216d.containers.snapdeploy.app",

            "X-Title":
                "Whale AI"

        }


        # -------------------------------------------------
        # PAYLOAD
        # -------------------------------------------------

        payload = {

            "model":
                "openrouter/free",

            "messages":
                messages,

            "temperature":
                0.7,

            "stream":
                False

        }


        print("================================")
        print("WHALE AI")
        print("OPENROUTER KEY:", bool(API_KEY))
        print("CONVERSATION:", conversation_id)
        print("SENDING REQUEST")
        print("================================")


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


        # -------------------------------------------------
        # OPENROUTER ERROR
        # -------------------------------------------------

        if response.status_code != 200:

            print(
                "OPENROUTER REQUEST ERROR"
            )

            print(
                response.text[:3000]
            )

            return jsonify({

                "error":
                    "OpenRouter خطا داد.",

                "status":
                    response.status_code,

                "details":
                    response.text[:1000]

            }), 502


        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:

            result = response.json()

        except Exception:

            return jsonify({
                "error":
                    "پاسخ OpenRouter معتبر نیست."
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
                    "OpenRouter پاسخ قابل استفاده‌ای نداد."
            }), 502


        # -------------------------------------------------
        # REPLY
        # -------------------------------------------------

        message_data = choices[0].get(
            "message",
            {}
        )

        reply = message_data.get(
            "content",
            ""
        )


        if isinstance(reply, list):

            reply = "".join(
                str(item)
                for item in reply
            )


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
        # UPDATE TITLE
        # -------------------------------------------------

        conn = get_db()

        conn.execute(
            """
            UPDATE conversations
            SET title = ?
            WHERE id = ?
            AND title = 'گفتگوی جدید'
            """,
            (
                user_message[:40],
                conversation_id
            )
        )

        conn.commit()
        conn.close()


        # -------------------------------------------------
        # STREAM-LIKE RESPONSE
        # -------------------------------------------------

        def generate():

            # ابتدا متن پاسخ

            yield json.dumps(
                {
                    "type": "text",
                    "content": reply
                },
                ensure_ascii=False
            ) + "\n"


            # پایان

            yield json.dumps(
                {
                    "type": "done",
                    "conversation_id":
                        conversation_id
                },
                ensure_ascii=False
            ) + "\n"


        return Response(
            generate(),
            mimetype="application/x-ndjson"
        )


    # =====================================================
    # REQUEST ERROR
    # =====================================================

    except requests.RequestException as error:

        print(
            "OPENROUTER CONNECTION ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "ارتباط با OpenRouter برقرار نشد."
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
                "خطای داخلی سرور."
        }), 500


# =========================================================
# DELETE ONE CONVERSATION
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):

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


# =========================================================
# DELETE ALL
# =========================================================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

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
        bool(API_KEY)
    )
    print("================================")

    app.run(
        host="0.0.0.0",
        port=port
    )
