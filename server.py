```python
from flask import Flask, request, jsonify, send_from_directory, Response
from dotenv import load_dotenv
import sqlite3
import os
import sys
import json
import requests

# =========================
# مسیر برنامه
# =========================

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

# =========================
# تنظیمات
# =========================

ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)

app = Flask(__name__)

API_KEY = os.getenv("OPENROUTER_API_KEY")

print("OPENROUTER KEY:", bool(API_KEY))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# =========================
# Database
# =========================

DB_FILE = os.path.join(BASE_DIR, "whale_ai.db")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
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
    conn.close()


def create_conversation():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO conversations (title) VALUES (?)",
        ("گفت‌وگوی جدید",)
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return conversation_id


# =========================
# INDEX
# =========================

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

    print("INDEX:", index_path)

    if not os.path.exists(index_path):
        return (
            "index.html پیدا نشد<br><br>" + index_path,
            404
        )

    return send_from_directory(
        os.path.dirname(index_path),
        "index.html"
    )


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok"
    }), 200


# =========================
# CONVERSATIONS
# =========================

@app.route("/conversations", methods=["POST"])
def new_conversation():

    conversation_id = create_conversation()

    return jsonify({
        "id": conversation_id,
        "title": "گفت‌وگوی جدید"
    })


@app.route("/conversations", methods=["GET"])
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
    "/conversations/<int:conversation_id>/messages"
)
def get_messages(conversation_id):

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
    """, (conversation_id,))

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


# =========================
# CHAT
# =========================

@app.route("/chat", methods=["POST"])
def chat():

    data = request.get_json(silent=True) or {}

    text = data.get(
        "message",
        ""
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
            "error": "کلید API پیدا نشد."
        }), 500

    if not conversation_id:
        conversation_id = create_conversation()

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM conversations WHERE id = ?",
        (conversation_id,)
    )

    if not cursor.fetchone():

        conn.close()

        return jsonify({
            "error": "گفت‌وگو پیدا نشد."
        }), 404

    # ذخیره پیام کاربر
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

    # دریافت تاریخچه گفتگو
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

    messages = [
        {
            "role": "system",
            "content": (
                "تو Whale AI هستی. "
                "به کاربر دقیق و مفید پاسخ بده. "
                "اگر کاربر فارسی صحبت کرد، "
                "فارسی پاسخ بده."
            )
        }
    ]

    for item in history:

        messages.append({
            "role": item["role"],
            "content": item["content"]
        })


    def generate():

        full_reply = ""

        try:

            print("در حال دریافت پاسخ از OpenRouter...")

            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "openrouter/free",
                "messages": messages,
                "temperature": 0.7,
                "stream": True
            }

            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                stream=True,
                timeout=60
            )

            print(
                "OpenRouter status:",
                response.status_code
            )

            if response.status_code != 200:

                error_text = response.text[:2000]

                print(
                    "OPENROUTER ERROR:",
                    error_text
                )

                yield json.dumps({
                    "type": "error",
                    "content":
                        "خطا در ارتباط با سرویس هوش مصنوعی."
                }, ensure_ascii=False) + "\n"

                return

            # =========================
            # دریافت پاسخ Streaming
            # =========================

            for line in response.iter_lines(
                decode_unicode=True
            ):

                if not line:
                    continue

                if line.startswith("data: "):

                    data_line = line[6:]

                    if data_line == "[DONE]":
                        break

                    try:

                        chunk = json.loads(
                            data_line
                        )

                        choices = chunk.get(
                            "choices",
                            []
                        )

                        if not choices:
                            continue

                        delta = (
                            choices[0]
                            .get("delta", {})
                            .get("content", "")
                        )

                        if delta:

                            full_reply += delta

                            yield json.dumps({
                                "type": "text",
                                "content": delta
                            }, ensure_ascii=False) + "\n"

                    except json.JSONDecodeError:

                        continue


            if not full_reply:

                full_reply = "پاسخی دریافت نشد."


            # =========================
            # ذخیره پاسخ AI
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
                full_reply
            ))

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

            yield json.dumps({
                "type": "done",
                "conversation_id": conversation_id
            }, ensure_ascii=False) + "\n"


        except requests.exceptions.Timeout:

            print(
                "AI ERROR: OpenRouter timeout"
            )

            yield json.dumps({
                "type": "error",
                "content":
                    "زمان اتصال به هوش مصنوعی تمام شد."
            }, ensure_ascii=False) + "\n"


        except requests.exceptions.RequestException as error:

            print(
                "AI NETWORK ERROR:",
                repr(error)
            )

            yield json.dumps({
                "type": "error",
                "content":
                    "ارتباط با سرویس هوش مصنوعی برقرار نشد."
            }, ensure_ascii=False) + "\n"


        except Exception as error:

            print(
                "AI ERROR:",
                repr(error)
            )

            yield json.dumps({
                "type": "error",
                "content":
                    "ارتباط با هوش مصنوعی برقرار نشد."
            }, ensure_ascii=False) + "\n"


    return Response(
        generate(),
        mimetype="application/x-ndjson"
    )


# =========================
# DELETE
# =========================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM messages WHERE conversation_id = ?",
        (conversation_id,)
    )

    cursor.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

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


# =========================
# START
# =========================

init_db()

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print()
    print("==============================")
    print("          WHALE AI")
    print("==============================")
    print("Server starting...")
    print("Host: 0.0.0.0")
    print("Port:", port)
    print("==============================")
    print()

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
```
