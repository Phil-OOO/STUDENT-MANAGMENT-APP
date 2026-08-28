# import sqlite3
import os
import sqlite3
from datetime import datetime

DB_NAME = "gcc.db"


def _convert_timestamp(val):
    val = val.decode()
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return val


sqlite3.register_converter("TIMESTAMP", _convert_timestamp)  # ← module level


def get_connection():
    conn = sqlite3.connect(
        DB_NAME,
        timeout=10,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Schools
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            fee_limit NUMERIC(10,2) DEFAULT 0,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            logo TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Users
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Students
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            first_name TEXT NOT NULL,
            last_name TEXT,
            parent_name TEXT,
            parent_phone TEXT,
            profile_picture TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Password resets
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL
        )
    """
    )

    # Attendance
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            status TEXT CHECK (status IN ('Present','Absent','Late')) NOT NULL,
            school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE
        )
    """
    )

    # Fee structures
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_structures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER REFERENCES schools(id) ON DELETE CASCADE,
            class_name VARCHAR(100),
            academic_year VARCHAR(20),
            term VARCHAR(20),
            total_fee NUMERIC(10,2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Student fees
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS student_fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            fee_structure_id INTEGER REFERENCES fee_structures(id) ON DELETE CASCADE,
            status TEXT CHECK (status IN ('Paid','Partial','Not Paid')) DEFAULT 'Not Paid',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Payments
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_fee_id INTEGER REFERENCES student_fees(id) ON DELETE CASCADE,
            amount_paid NUMERIC(10,2) NOT NULL,
            payment_method VARCHAR(50),
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        )
    """
    )

    conn.commit()
    conn.close()
