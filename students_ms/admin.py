from werkzeug.security import generate_password_hash
from db import get_connection

conn = get_connection()
cur = conn.cursor()

# create a superadmin user
cur.execute(
    """
INSERT INTO users (school_id, name, email, password_hash, role, created_at)
VALUES (?, ?, ?, ?, ?, datetime('now'))
""",
    (
        0,
        "Super Admin",
        "superadmin@example.com",
        generate_password_hash("supersecret"),
        "superadmin",
    ),
)

conn.commit()
conn.close()
