from werkzeug.security import generate_password_hash, check_password_hash
from db import get_connection
from datetime import datetime
from models import User


def create_school(name, email, password, logo=None):
    conn = get_connection()
    cur = conn.cursor()

    # Check if email already exists
    cur.execute("SELECT * FROM schools WHERE email = ?", (email,))
    if cur.fetchone():
        conn.close()
        raise ValueError("A school with this email already exists")

    password_hash = generate_password_hash(password)

    # Insert school and get its id
    cur.execute(
        """
        INSERT INTO schools (name, email, password_hash, logo, created_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (name, email, password_hash, logo, datetime.now()),
    )
    school_id_row = cur.fetchone()
    school_id = school_id_row["id"]

    # Create admin user for the school
    cur.execute(
        """
        INSERT INTO users (name, email, password_hash, school_id, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, email, password_hash, school_id, "admin", datetime.now()),
    )

    conn.commit()
    conn.close()


def create_teacher(school_id, name, email, password):
    """Create a teacher for a given school."""
    conn = get_connection()
    cur = conn.cursor()

    # Check if email already exists for this school
    cur.execute(
        "SELECT * FROM users WHERE email = ? AND school_id = ?", (email, school_id)
    )
    if cur.fetchone():
        cur.close()
        conn.close()
        raise ValueError("A teacher with this email already exists in this school")

    password_hash = generate_password_hash(password)
    now = datetime.now()

    cur.execute(
        """
        INSERT INTO users (school_id, name, email, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (school_id, name, email, password_hash, "teacher", now),
    )

    conn.commit()
    conn.close()


def authenticate(email, password):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.*, s.name AS school_name,
               s.logo AS school_logo          -- ✅ added
        FROM users u
        LEFT JOIN schools s ON u.school_id = s.id
        WHERE u.email = ?
    """,
        (email,),
    )
    row = cur.fetchone()
    conn.close()

    if row and check_password_hash(row["password_hash"], password):
        return User(row)
    return None
