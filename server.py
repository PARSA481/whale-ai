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
        "openrouter_key": bool(API_KEY),
        "key_length": len(API_KEY)
    }), 200


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
    }), 200


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
        # READ REQUEST
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
        # EMPTY MESSAGE
        # -------------------------------------------------

        if not user_message:

            return jsonify({
                "error": "پیام خالی است."
            }), 400


        # -------------------------------------------------
        # CHECK API KEY
        # -------------------------------------------------

        if not API_KEY:

            print(
                "ERROR: OPENROUTER_API_KEY is missing"
            )

            return jsonify({
                "error":
                    "کلید OpenRouter در Environment Variables تنظیم نشده است."
            }), 500


        # -------------------------------------------------
        # CREATE CONVERSATION IF NEEDED
        # -------------------------------------------------

        if not conversation_id:

            conn = get_db()

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
            conn.close()


        # -------------------------------------------------
        # GET OLD MESSAGES
        # -------------------------------------------------

        conn = get_db()

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
        # BUILD MESSAGES FOR OPENROUTER
        # -------------------------------------------------

        messages = [

            {
                "role": "system",
                "content": (
                    "تو Whale AI هستی. "
                    "دقیق، مفید و طبیعی پاسخ بده. "
                    "اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده. "
                    "پاسخ‌ها را واضح و قابل فهم ارائه کن."
                )
            }

        ]


        for row in rows:

            messages.append({
                "role": row["role"],
                "content": row["content"]
            })


        messages.append({
            "role": "user",
            "content": user_message
        })


        # -------------------------------------------------
        # LOG
        # -------------------------------------------------

        print("================================")
        print("WHALE AI")
        print(
            "OPENROUTER KEY:",
            bool(API_KEY)
        )
        print(
            "KEY LENGTH:",
            len(API_KEY)
        )
        print(
            "SENDING REQUEST TO OPENROUTER"
        )
        print("================================")


        # -------------------------------------------------
        # OPENROUTER HEADERS
        # -------------------------------------------------

        headers = {
            "Authorization":
                "Bearer " + API_KEY,

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                "https://snapdeploy.dev",

            "X-Title":
                "Whale AI"
        }


        # -------------------------------------------------
        # OPENROUTER PAYLOAD
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


        # -------------------------------------------------
        # SEND REQUEST
        # -------------------------------------------------

        response = requests.post(

            OPENROUTER_URL,

            headers=headers,

            json=payload,

            timeout=120

        )


        # -------------------------------------------------
        # LOG RESPONSE
        # -------------------------------------------------

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
                    response.text[:1000]

            }), 502


        # -------------------------------------------------
        # PARSE JSON
        # -------------------------------------------------

        result = response.json()


        # -------------------------------------------------
        # CHECK CHOICES
        # -------------------------------------------------

        if "choices" not in result:

            print(
                "ERROR: choices not found"
            )

            return jsonify({
                "error":
                    "OpenRouter پاسخ قابل استفاده‌ای برنگرداند."
            }), 502


        if not result["choices"]:

            return jsonify({
                "error":
                    "OpenRouter پاسخ خالی برگرداند."
            }), 502


        # -------------------------------------------------
        # GET REPLY
        # -------------------------------------------------

        message_data = result["choices"][0].get(
            "message",
            {}
        )

        reply = message_data.get(
            "content",
            ""
        )


        if not reply:

            return jsonify({
                "error":
                    "متن پاسخ OpenRouter خالی است."
            }), 502


        # -------------------------------------------------
        # SAVE ASSISTANT MESSAGE
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
        # RETURN TO FRONTEND
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
# DELETE ALL CONVERSATIONS
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
    print(
        "KEY LENGTH:",
        len(API_KEY)
    )
    print("================================")

    app.run(
        host="0.0.0.0",
        port=port
    )
