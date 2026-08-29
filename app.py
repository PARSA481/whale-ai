```python
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import sqlite3
import os
import requests

# =========================
# PATHS
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# =========================
# FLASK
# =========================

app = Flask(__name__)

# =========================
# OPENROUTER
# =========================

API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

print("================================")
print("WHALE AI")
print("OPENROUTER KEY:", bool(API_KEY))
print("================================")

# =========================
# DATABASE
# =========================

DB_FILE = os.path.join("/tmp", "whale_ai.db")


def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'گفت‌وگوی جدید',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
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


# =========================
# CREATE CONVERSATION
# =========================

def create_conversation():

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO conversations (title)
        VALUES (?)
        """,
        ("گفت‌وگوی جدید",)
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return conversation_id


# =========================
# HOME
# =========================

@app.route("/")
def index():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) AS count FROM conversations"
        )

        cursor.fetchone()

        conn.close()

        return jsonify({
            "status": "ok",
            "database": "ok",
            "openrouter_key": bool(API_KEY)
        })

    except Exception as error:

        print("HEALTH ERROR:", repr(error))

        return jsonify({
            "status": "error",
            "error": str(error)
        }), 500


# =========================
# NEW CONVERSATION
# =========================

@app.route(
    "/conversations",
    methods=["POST"]
)
def new_conversation():

    try:

        conversation_id = create_conversation()

        return jsonify({
            "id": conversation_id,
            "title": "گفت‌وگوی جدید"
        })

    except Exception as error:

        print(
            "NEW CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ساخت گفتگو."
        }), 500


# =========================
# GET CONVERSATIONS
# =========================

@app.route(
    "/conversations",
    methods=["GET"]
)
def get_conversations():

    try:

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

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت تاریخچه."
        }), 500


# =========================
# GET MESSAGES
# =========================

@app.route(
    "/conversations/<int:conversation_id>/messages"
)
def get_messages(conversation_id):

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
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
        ))

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

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت پیام‌ها."
        }), 500


# =========================
# CHAT
# =========================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        text = str(
            data.get("message", "")
        ).strip()

        conversation_id = data.get(
            "conversation_id"
        )

        if not text:

            return jsonify({
                "error": "پیامی دریافت نشد."
            }), 400

        if not API_KEY:

            return jsonify({
                "error":
                    "OPENROUTER_API_KEY تنظیم نشده است."
            }), 500

        # =========================
        # CONVERSATION
        # =========================

        if not conversation_id:

            conversation_id = create_conversation()

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

        conversation = cursor.fetchone()

        if not conversation:

            conn.close()

            conversation_id = create_conversation()

            conn = get_db()
            cursor = conn.cursor()

        # =========================
        # SAVE USER MESSAGE
        # =========================

        cursor.execute("""
            INSERT INTO messages
            (
                conversation_id,
                role,
                content
            )
            VALUES (?, ?, ?)
        """, (
            conversation_id,
            "user",
            text
        ))

        conn.commit()

        # =========================
        # LOAD HISTORY
        # =========================

        cursor.execute("""
            SELECT
                role,
                content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (
            conversation_id,
        ))

        history = cursor.fetchall()

        conn.close()

        # =========================
        # BUILD MESSAGES
        # =========================

        messages = [
            {
                "role": "system",
                "content": (
                    "تو Whale AI هستی. "
                    "دقیق، مفید، طبیعی و دوستانه پاسخ بده. "
                    "اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده."
                )
            }
        ]

        for item in history:

            messages.append({
                "role": item["role"],
                "content": item["content"]
            })

        # =========================
        # OPENROUTER REQUEST
        # =========================

        print("================================")
        print("SENDING REQUEST TO OPENROUTER")
        print("MODEL: openrouter/free")
        print("CONVERSATION:", conversation_id)
        print("================================")

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://whale-ai.local",
            "X-Title": "Whale AI"
        }

        payload = {
            "model": "openrouter/free",
            "messages": messages,
            "temperature": 0.7,
            "stream": False
        }

        try:

            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=120
            )

        except requests.RequestException as error:

            print("================================")
            print("OPENROUTER CONNECTION ERROR")
            print("TYPE:", type(error).__name__)
            print("ERROR:", str(error))
            print("================================")

            return jsonify({
                "error":
                    "ارتباط با OpenRouter برقرار نشد."
            }), 502

        # =========================
        # CHECK STATUS
        # =========================

        print(
            "OPENROUTER STATUS:",
            response.status_code
        )

        if response.status_code != 200:

            print("OPENROUTER RESPONSE:")
            print(response.text[:2000])

            try:
                error_data = response.json()
            except Exception:
                error_data = {
                    "message": response.text[:1000]
                }

            return jsonify({
                "error":
                    "OpenRouter درخواست را قبول نکرد.",
                "status":
                    response.status_code,
                "details":
                    error_data
            }), 502

        # =========================
        # PARSE RESPONSE
        # =========================

        try:

            result = response.json()

            reply = (
                result["choices"][0]["message"]["content"]
            )

        except Exception as error:

            print("RESPONSE PARSE ERROR:", repr(error))
            print("RAW RESPONSE:", response.text[:2000])

            return jsonify({
                "error":
                    "پاسخ نامعتبر از OpenRouter دریافت شد."
            }), 502

        if not reply:

            reply = "پاسخی دریافت نشد."

        reply = str(reply).strip()

        # =========================
        # SAVE AI RESPONSE
        # =========================

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO messages
            (
                conversation_id,
                role,
                content
            )
            VALUES (?, ?, ?)
        """, (
            conversation_id,
            "assistant",
            reply
        ))

        # =========================
        # TITLE
        # =========================

        cursor.execute("""
            SELECT COUNT(*) AS count
            FROM messages
            WHERE conversation_id = ?
        """, (
            conversation_id,
        ))

        count = cursor.fetchone()["count"]

        if count == 2:

            title = text[:40]

            if len(text) > 40:
                title += "..."

            cursor.execute("""
                UPDATE conversations
                SET title = ?
                WHERE id = ?
            """, (
                title,
                conversation_id
            ))

        conn.commit()
        conn.close()

        print("OPENROUTER RESPONSE RECEIVED")
        print("REPLY LENGTH:", len(reply))

        return jsonify({
            "type": "done",
            "content": reply,
            "conversation_id":
                conversation_id
        })

    except Exception as error:

        print("================================")
        print("CHAT ERROR")
        print("TYPE:", type(error).__name__)
        print("ERROR:", str(error))
        print("================================")

        return jsonify({
            "error": "خطای داخلی سرور."
        }), 500


# =========================
# DELETE ONE
# =========================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):

    try:

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

    except Exception as error:

        print(
            "DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف گفتگو."
        }), 500


# =========================
# DELETE ALL
# =========================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

    try:

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

    except Exception as error:

        print(
            "DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف گفتگوها."
        }), 500


# =========================
# START
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print("==============================")
    print("          WHALE AI")
    print("==============================")
    print("Server starting...")
    print("PORT:", port)
    print("==============================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
```
