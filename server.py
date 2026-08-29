import os
import sqlite3
import requests
import mimetypes
from flask import Flask, request, jsonify, send_from_directory

# Optional file readers
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
DB_FILE = os.path.join(BASE_DIR, "whale.db")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "openrouter/free"
)

MAX_HISTORY_MESSAGES = 40
MAX_FILE_CHARS = 50000
MAX_MESSAGE_CHARS = 20000


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
            title TEXT DEFAULT 'New Chat',
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
            (
                value,
                key
            )
        )

    else:

        conn.execute(
            """
            INSERT INTO memories
            (key, value)
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
        "Stored user memory:\n"
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

    if extension in ("txt", "md", "csv", "json"):

        raw = file.read()

        try:
            text = raw.decode("utf-8")

        except UnicodeDecodeError:

            text = raw.decode(
                "utf-8",
                errors="replace"
            )

        return text[:MAX_FILE_CHARS], extension


    if extension == "pdf":

        if PdfReader is None:

            raise RuntimeError(
                "PDF support is not installed. "
                "Install pypdf."
            )

        reader = PdfReader(file)

        parts = []

        for page in reader.pages:

            try:

                page_text = page.extract_text() or ""

            except Exception:

                page_text = ""

            if page_text:
                parts.append(page_text)

            if sum(
                len(x)
                for x in parts
            ) >= MAX_FILE_CHARS:

                break

        text = "\n\n".join(parts)

        return text[:MAX_FILE_CHARS], extension


    if extension == "docx":

        if Document is None:

            raise RuntimeError(
                "DOCX support is not installed. "
                "Install python-docx."
            )

        # DOCX expects a file path or file-like object.
        document = Document(file)

        paragraphs = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]

        text = "\n".join(paragraphs)

        return text[:MAX_FILE_CHARS], extension


    raise ValueError(
        "Unsupported file type."
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    api_key = get_api_key()

    try:

        conn = get_db()

        conn.execute(
            "SELECT 1"
        ).fetchone()

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

        "openrouter_key":
            bool(api_key),

        "key_length":
            len(api_key),

        "database":
            database,

        "model":
            DEFAULT_MODEL

    })


# =========================================================
# UPLOAD
# =========================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload_file():

    try:

        if "file" not in request.files:

            return jsonify({
                "error":
                    "No file was uploaded."
            }), 400

        file = request.files["file"]

        if not file.filename:

            return jsonify({
                "error":
                    "The file has no name."
            }), 400

        text, extension = extract_file_text(file)

        return jsonify({

            "success":
                True,

            "filename":
                file.filename,

            "extension":
                extension,

            "text":
                text

        })

    except ValueError as error:

        return jsonify({
            "error":
                str(error)
        }), 400

    except Exception as error:

        print(
            "UPLOAD ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "File processing failed.",
            "details":
                str(error)
        }), 500


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
                "id":
                    row["id"],

                "title":
                    row["title"],

                "created_at":
                    row["created_at"],

                "message_count":
                    row["message_count"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to load conversations."
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
            ("New Chat",)
        )

        conversation_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return jsonify({

            "id":
                conversation_id,

            "title":
                "New Chat"

        })


    except Exception as error:

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to create conversation."
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
            (conversation_id,)
        ).fetchall()

        conn.close()

        return jsonify([
            {
                "id":
                    row["id"],

                "role":
                    row["role"],

                "content":
                    row["content"],

                "created_at":
                    row["created_at"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to load messages."
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
            "memories":
                get_memories()
        })

    except Exception as error:

        print(
            "MEMORY GET ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to load memory."
        }), 500


# =========================================================
# MEMORY ADD
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
            data.get("key", "")
        ).strip()

        value = str(
            data.get("value", "")
        ).strip()

        if not key or not value:

            return jsonify({
                "error":
                    "key and value are required."
            }), 400

        save_memory(
            key,
            value
        )

        return jsonify({

            "success":
                True,

            "key":
                key,

            "value":
                value

        })

    except Exception as error:

        print(
            "MEMORY ADD ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to save memory."
        }), 500


# =========================================================
# MEMORY DELETE
# =========================================================

@app.route(
    "/memory/<path:key>",
    methods=["DELETE"]
)
def memory_delete(key):

    try:

        delete_memory(key)

        return jsonify({
            "success":
                True
        })

    except Exception as error:

        print(
            "MEMORY DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to delete memory."
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
            "success":
                True
        })

    except Exception as error:

        print(
            "MEMORY DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to clear memory."
        }), 500


# =========================================================
# BUILD MODEL MESSAGES
# =========================================================

def build_model_messages(
    conversation_id,
    user_message,
    file_text=""
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            role,
            content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            conversation_id,
            MAX_HISTORY_MESSAGES
        )
    ).fetchall()

    conn.close()

    rows = list(
        reversed(rows)
    )

    system_prompt = """
You are Whale AI, a helpful AI assistant.

Rules:

1. Answer in the same language as the user.
2. If the user writes Persian, answer in Persian.
3. Be direct and natural.
4. Do not unnecessarily delay simple questions.
5. Use Markdown when useful.
6. Use headings when they improve readability.
7. Use numbered lists for procedures.
8. Use code blocks for code.
9. Do not repeat the user's message.
10. Do not claim that you searched the web unless web search was actually enabled and used.
11. If web search results are available, use them when relevant.
12. Never expose private memory unless it is relevant to the request.
"""

    system_prompt += build_memory_context()

    if file_text:

        system_prompt += """

The user attached a file.

Use the following extracted file content when relevant:

--- FILE CONTENT ---
{}
--- END FILE CONTENT ---
""".format(
            file_text[:MAX_FILE_CHARS]
        )

    messages = [
        {
            "role":
                "system",

            "content":
                system_prompt
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

            "role":
                role,

            "content":
                row["content"]

        })

    # IMPORTANT:
    # The current user message is already saved
    # in the database before this function is called.
    #
    # Therefore DO NOT append user_message again.
    #
    # This prevents duplicate user messages.

    return messages


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
                "error":
                    "OPENROUTER_API_KEY is not configured."
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


        if len(user_message) > MAX_MESSAGE_CHARS:

            return jsonify({
                "error":
                    "Message is too long."
            }), 400


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


        conversation_id = data.get(
            "conversation_id"
        )


        if not user_message and not file_text:

            return jsonify({
                "error":
                    "Message is empty."
            }), 400


        # -------------------------------------------------
        # DATABASE / CONVERSATION
        # -------------------------------------------------

        conn = get_db()


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

            title_source = (
                user_message
                or file_name
                or "New Chat"
            )

            cursor = conn.execute(
                """
                INSERT INTO conversations
                (title)
                VALUES (?)
                """,
                (
                    title_source[:50],
                )
            )

            conversation_id = cursor.lastrowid


        # -------------------------------------------------
        # SAVE CURRENT USER MESSAGE ONCE
        # -------------------------------------------------

        display_message = user_message

        if file_text:

            display_message += (
                "\n\n"
                "[Attached file: {}]"
                .format(
                    file_name or "file"
                )
            )


        if not display_message:

            display_message = (
                "[Attached file: {}]"
                .format(
                    file_name or "file"
                )
            )


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
                display_message
            )
        )


        conn.commit()
        conn.close()


        # -------------------------------------------------
        # MODEL MESSAGES
        # -------------------------------------------------

        messages = build_model_messages(
            conversation_id,
            user_message,
            file_text
        )


        # -------------------------------------------------
        # HEADERS
        # -------------------------------------------------

        headers = {

            "Authorization":
                "Bearer " + api_key,

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                "http://localhost:5000",

            "X-Title":
                "Whale AI"

        }


        # -------------------------------------------------
        # PAYLOAD
        # -------------------------------------------------

        payload = {

            "model":
                DEFAULT_MODEL,

            "messages":
                messages,

            "temperature":
                0.7,

            "stream":
                False

        }


        # -------------------------------------------------
        # WEB SEARCH
        # -------------------------------------------------

        if web_search:

            payload["plugins"] = [
                {
                    "id":
                        "web",

                    "max_results":
                        5
                }
            ]


        print("\n==============================")
        print("WHALE AI REQUEST")
        print("==============================")
        print(
            "Conversation:",
            conversation_id
        )
        print(
            "Model:",
            DEFAULT_MODEL
        )
        print(
            "Web Search:",
            web_search
        )
        print(
            "Message:",
            user_message[:200]
        )
        print("==============================\n")


        # -------------------------------------------------
        # OPENROUTER REQUEST
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


        if response.status_code != 200:

            print(
                "OPENROUTER ERROR:",
                response.text[:3000]
            )

            return jsonify({

                "error":
                    "OpenRouter request failed.",

                "status":
                    response.status_code,

                "details":
                    response.text[:2000]

            }), 502


        # -------------------------------------------------
        # PARSE JSON
        # -------------------------------------------------

        try:

            result = response.json()

        except ValueError:

            return jsonify({
                "error":
                    "OpenRouter returned invalid JSON."
            }), 502


        if result.get("error"):

            print(
                "OPENROUTER API ERROR:",
                result["error"]
            )

            return jsonify({

                "error":
                    "OpenRouter returned an error.",

                "details":
                    result["error"]

            }), 502


        choices = result.get(
            "choices"
        )


        if not choices:

            return jsonify({

                "error":
                    "No response was returned by the model.",

                "details":
                    result

            }), 502


        message_data = choices[0].get(
            "message",
            {}
        )


        reply = message_data.get(
            "content",
            ""
        )


        # -------------------------------------------------
        # NORMALIZE CONTENT
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
                    "The model returned an empty response."

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
        # RESPONSE
        # -------------------------------------------------

        return jsonify({

            "type":
                "done",

            "content":
                reply,

            "conversation_id":
                conversation_id,

            "web_search":
                web_search

        })


    except requests.Timeout:

        print(
            "OPENROUTER TIMEOUT"
        )

        return jsonify({

            "error":
                "The request timed out."

        }), 504


    except requests.RequestException as error:

        print(
            "OPENROUTER REQUEST ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Could not connect to OpenRouter.",

            "details":
                str(error)

        }), 502


    except Exception as error:

        print(
            "CHAT ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Internal server error.",

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
            "success":
                True
        })

    except Exception as error:

        print(
            "DELETE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to delete conversation."
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
            "success":
                True
        })

    except Exception as error:

        print(
            "DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to delete conversations."
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

    print("\n================================")
    print("WHALE AI")
    print("================================")
    print(
        "PORT:",
        port
    )
    print(
        "MODEL:",
        DEFAULT_MODEL
    )
    print(
        "API KEY:",
        bool(get_api_key())
    )
    print(
        "MEMORY: ENABLED"
    )
    print(
        "WEB SEARCH: ENABLED"
    )
    print("================================\n")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )
