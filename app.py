from flask import Flask, request, jsonify, send_from_directory, Response
from openai import OpenAI
from dotenv import load_dotenv
import sqlite3
import os
import json

# =========================
# مسیر برنامه
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================
# تنظیمات
# =========================

load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)

API_KEY = os.getenv("OPENROUTER_API_KEY")

print("OPENROUTER KEY:", bool(API_KEY))

client = OpenAI(
    api_key=API_KEY,
    base_url="https://openrouter.ai/api/v1",
    timeout=60.0,
    max_retries=1
)

DB_FILE = os.path.join(BASE_DIR, "whale_ai.db")


# =========================
# Database
# =========================

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

    index_path = os.path.join(
        BASE_DIR,
        "index.html"
    )

    if not os.path.exists(index_path):
        return "index.html پیدا نشد.", 404

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


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


@app.route("/conversations/<int:conversation_id>/messages")
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

    text = data.get("message", "").strip()
    conversation_id = data.get("conversation_id")

    if not text:
        return jsonify({
            "error": "پیامی دریافت نشد."
        }), 400

    if not API_KEY:
        return jsonify({
            "error": "OPENROUTER_API_KEY تنظیم نشده است."
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

    cursor.execute("""
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
    """, (conversation_id,))

    history = cursor.fetchall()

    conn.close()

    messages = [
        {
            "role": "system",
            "content": (
                "تو Whale AI هستی. "
                "دقیق و مفید پاسخ بده. "
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

            print("در حال دریافت پاسخ...")

            response = client.chat.completions.create(
                model="openrouter/free",
                messages=messages,
                temperature=0.7,
                stream=True
            )

            for chunk in response:

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta.content

                if delta:

                    full_reply += delta

                    yield json.dumps({
                        "type": "text",
                        "content": delta
                    }, ensure_ascii=False) + "\n"


            if not full_reply:
                full_reply = "پاسخی دریافت نشد."


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
            """, (conversation_id,))

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


        except Exception as error:

            print("AI ERROR:", repr(error))

            yield json.dumps({
                "type": "error",
                "content": "ارتباط با هوش مصنوعی برقرار نشد."
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

    cursor.execute("DELETE FROM messages")
    cursor.execute("DELETE FROM conversations")

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


# =========================
# START
# =========================

if __name__ == "__main__":

    init_db()

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print("==============================")
    print("       WHALE AI")
    print("==============================")
    print("Server starting...")
    print("PORT:", port)
    print("==============================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
