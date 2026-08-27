ecute("DELETE FROM messages")
    cursor.execute("DELETE FROM conversations")

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
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
