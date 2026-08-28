import os
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    request,
    make_response,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from db import init_db, get_connection
from auth import create_school, create_teacher, authenticate
from datetime import datetime
import csv
from models import User
import uuid
import secrets
from datetime import timedelta
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
import random
import string
import threading
import webbrowser
import smtplib
from email.message import EmailMessage
from app_config import TEMPLATE_FOLDER, STATIC_FOLDER


app = Flask(__name__, template_folder=TEMPLATE_FOLDER, static_folder=STATIC_FOLDER)

app.secret_key = "try-me"

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.context_processor
def inject_year():
    return {"current_year": datetime.now().year}


@app.route("/", methods=["GET"])
def home():
    return render_template("home.html")


@login_manager.user_loader
def load_user(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.*, s.name AS school_name,
               s.logo AS school_logo
        FROM users u
        LEFT JOIN schools s ON u.school_id = s.id
        WHERE u.id = ?
    """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return User(row) if row else None


@app.route("/school/upload-logo", methods=["GET", "POST"])
@login_required
def upload_school_logo():
    if not current_user.is_admin() and not current_user.is_superadmin():
        return "Forbidden", 403

    if request.method == "POST":
        file = request.files.get("logo")

        if not file or not file.filename:
            flash("No file selected.", "danger")
            return redirect(url_for("upload_school_logo"))

        if not allowed_file(file.filename):
            flash("Only image files allowed (png, jpg, jpeg, gif).", "danger")
            return redirect(url_for("upload_school_logo"))

        upload_folder = app.config["UPLOAD_FOLDER"]
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)

        filename = f"school_{current_user.school_id}_{secure_filename(file.filename)}"
        filepath = os.path.join(upload_folder, filename)
        file.save(filepath)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE schools SET logo = ? WHERE id = ?",
            (filename, current_user.school_id),
        )
        conn.commit()
        conn.close()

        updated_user = load_user(current_user.id)
        login_user(updated_user)

        flash("Logo updated successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("upload_logo.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            logo_filename = None
            file = request.files.get("logo")

            if file and file.filename:
                if not allowed_file(file.filename):
                    flash("Only image files allowed (png, jpg, jpeg, gif).", "danger")
                    return render_template("register_school.html")

                upload_folder = app.config["UPLOAD_FOLDER"]
                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)

                filename = secure_filename(file.filename)
                unique_name = f"school_{filename}"
                filepath = os.path.join(upload_folder, unique_name)
                file.save(filepath)
                logo_filename = unique_name

            create_school(
                request.form["name"],
                request.form["email"],
                request.form["password"],
                logo_filename,
            )
            flash("School created. Login now.", "success")
            return redirect(url_for("login"))

        except ValueError as e:
            flash(str(e), "danger")
            return render_template("register_school.html")

    return render_template("register_school.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = authenticate(request.form["email"], request.form["password"])
        if user:
            login_user(user)
            return redirect(url_for("students"))
        flash("Invalid credentials", "danger")
    return render_template("login.html", current_year=datetime.now().year)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/teachers/new", methods=["GET", "POST"])
@login_required
def teacher_new():
    if not current_user.is_admin():
        return "Forbidden", 403

    if request.method == "POST":
        create_teacher(
            current_user.school_id,
            request.form["name"],
            request.form["email"],
            request.form["password"],
        )
        flash("Teacher created", "success")
        return redirect(url_for("students"))

    return render_template("register_teacher.html")


@app.route("/students")
@login_required
def students():
    conn = get_connection()
    cur = conn.cursor()

    per_page = 10
    page = request.args.get("page", 1, type=int)
    search_query = request.args.get("search", "").strip()
    offset = (page - 1) * per_page
    school_id = current_user.school_id

    if search_query:
        like_query = f"%{search_query}%"

        # SQLite: use LIKE with COLLATE NOCASE instead of ILIKE
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM students
            WHERE school_id = ?
              AND (first_name LIKE ? COLLATE NOCASE OR last_name LIKE ? COLLATE NOCASE)
        """,
            (school_id, like_query, like_query),
        )
        total_students = cur.fetchone()["total"]

        cur.execute(
            """
            SELECT
                s.*,
                COALESCE(fs.total_fee, 0)       AS total_fee,
                COALESCE(SUM(p.amount_paid), 0) AS total_paid,
                COALESCE(fs.total_fee, 0) - COALESCE(SUM(p.amount_paid), 0) AS balance,
                sf.status                        AS status
            FROM students s
            LEFT JOIN student_fees sf   ON sf.student_id       = s.id
            LEFT JOIN fee_structures fs ON sf.fee_structure_id = fs.id
            LEFT JOIN payments p        ON p.student_fee_id    = sf.id
            WHERE s.school_id = ?
              AND (s.first_name LIKE ? COLLATE NOCASE OR s.last_name LIKE ? COLLATE NOCASE)
            GROUP BY s.id, fs.total_fee, sf.status
            ORDER BY s.id DESC
            LIMIT ? OFFSET ?
        """,
            (school_id, like_query, like_query, per_page, offset),
        )

    else:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM students
            WHERE school_id = ?
        """,
            (school_id,),
        )
        total_students = cur.fetchone()["total"]

        cur.execute(
            """
            SELECT
                s.*,
                COALESCE(fs.total_fee, 0)       AS total_fee,
                COALESCE(SUM(p.amount_paid), 0) AS total_paid,
                COALESCE(fs.total_fee, 0) - COALESCE(SUM(p.amount_paid), 0) AS balance,
                sf.status                        AS status
            FROM students s
            LEFT JOIN student_fees sf   ON sf.student_id       = s.id
            LEFT JOIN fee_structures fs ON sf.fee_structure_id = fs.id
            LEFT JOIN payments p        ON p.student_fee_id    = sf.id
            WHERE s.school_id = ?
            GROUP BY s.id, fs.total_fee, sf.status
            ORDER BY s.id DESC
            LIMIT ? OFFSET ?
        """,
            (school_id, per_page, offset),
        )

    students_list = cur.fetchall()
    total_pages = (total_students + per_page - 1) // per_page

    conn.close()

    return render_template(
        "students.html",
        students=students_list,
        page=page,
        total_pages=total_pages,
        search_query=search_query,
    )


@app.route("/students/new", methods=["GET", "POST"])
@login_required
def student_new():
    if request.method == "POST":
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
           INSERT INTO students (school_id, first_name,
           last_name, parent_name, parent_phone, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           """,
            (
                current_user.school_id,
                request.form["first_name"],
                request.form.get("last_name"),
                request.form.get("parent_name"),
                request.form.get("parent_phone"),
                datetime.now().isoformat(),
            ),
        )

        conn.commit()
        conn.close()
        flash("Student added", "success")
        return redirect(url_for("students"))

    return render_template("student_form.html")


@app.route("/students/<int:id>")
@login_required
def student_detail(id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.*, sch.name AS school_name
        FROM students s
        JOIN schools sch ON s.school_id = sch.id
        WHERE s.id = ? AND s.school_id = ?
    """,
        (id, current_user.school_id),
    )
    student = cur.fetchone()
    conn.close()

    if not student:
        return "Student not found", 404

    return render_template("student_detail.html", student=student)


@app.route("/students/<int:id>/edit", methods=["GET", "POST"])
@login_required
def student_edit(id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM students WHERE id=? AND school_id=?",
        (id, current_user.school_id),
    )
    student = cur.fetchone()

    if not student:
        conn.close()
        return "Student not found", 404

    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form.get("last_name")
        parent_name = request.form.get("parent_name")
        parent_phone = request.form.get("parent_phone")

        file = request.files.get("profile_picture")
        profile_picture = (
            student["profile_picture"] if "profile_picture" in student.keys() else None
        )

        if file and file.filename:
            upload_folder = "static/uploads"
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)

            filename = f"{id}_{secure_filename(file.filename)}"
            filepath = os.path.join(upload_folder, filename)
            file.save(filepath)
            profile_picture = filename

        cur.execute(
            """
            UPDATE students
            SET first_name=?, last_name=?, parent_name=?, parent_phone=?, profile_picture=?
            WHERE id=? AND school_id=?
        """,
            (
                first_name,
                last_name,
                parent_name,
                parent_phone,
                profile_picture,
                id,
                current_user.school_id,
            ),
        )

        conn.commit()
        conn.close()
        flash("Student updated successfully", "success")
        return redirect(url_for("student_detail", id=id))

    conn.close()
    return render_template("student_detail.html", student=student)


@app.route("/students/<int:id>/delete", methods=["POST"])
@login_required
def student_delete(id):
    if not current_user.is_admin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM students WHERE id = ? AND school_id = ?",
        (id, current_user.school_id),
    )

    conn.commit()
    conn.close()

    flash("Student deleted", "success")
    return redirect(url_for("students"))


@app.route("/api/student/<int:student_id>/fees")
@login_required
def api_student_fees(student_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM students WHERE id = ? AND school_id = ?",
        (student_id, current_user.school_id),
    )
    if not cur.fetchone():
        conn.close()
        return {"error": "Student not found"}, 404

    cur.execute(
        """
        SELECT sf.id AS student_fee_id, fs.term, fs.academic_year, fs.total_fee,
               fs.class_name, sf.status,
               COALESCE(SUM(p.amount_paid), 0) AS total_paid
        FROM student_fees sf
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        LEFT JOIN payments p ON p.student_fee_id = sf.id
        WHERE sf.student_id = ? AND fs.school_id = ?
        GROUP BY sf.id, fs.term, fs.academic_year, fs.total_fee, fs.class_name, sf.status
        ORDER BY fs.academic_year DESC, fs.term
    """,
        (student_id, current_user.school_id),
    )

    fees = [dict(row) for row in cur.fetchall()]

    for fee in fees:
        fee["total_fee"] = float(fee["total_fee"])
        fee["total_paid"] = float(fee["total_paid"])
        fee["balance"] = round(fee["total_fee"] - fee["total_paid"], 2)

        # SQLite: use strftime instead of TO_CHAR
        cur.execute(
            """
            SELECT id, amount_paid, payment_method,
                   strftime('%Y-%m-%d', payment_date) AS payment_date
            FROM payments
            WHERE student_fee_id = ?
            ORDER BY payment_date DESC
        """,
            (fee["student_fee_id"],),
        )

        fee["payments"] = [dict(p) for p in cur.fetchall()]

        for p in fee["payments"]:
            p["amount_paid"] = float(p["amount_paid"])

    conn.close()
    return {"fees": fees}


@app.route("/fees/<int:student_fee_id>/pay", methods=["POST"])
@login_required
def record_payment(student_fee_id):
    data = request.get_json(silent=True)
    if not data:
        return {"error": "Invalid or missing JSON body"}, 400

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return {"error": "Invalid amount"}, 400

    if amount <= 0:
        return {"error": "Payment amount must be greater than zero"}, 400

    method = data.get("method", "").strip()
    if not method:
        return {"error": "Payment method is required"}, 400

    VALID_METHODS = {"Cash", "Mobile Money", "Bank Transfer", "Cheque"}
    if method not in VALID_METHODS:
        return {
            "error": f"Invalid payment method. Choose from: {', '.join(VALID_METHODS)}"
        }, 400

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT sf.id, fs.total_fee,
               COALESCE(SUM(p.amount_paid), 0) AS total_paid
        FROM student_fees sf
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        LEFT JOIN payments p ON p.student_fee_id = sf.id
        WHERE sf.id = ? AND fs.school_id = ?
        GROUP BY sf.id, fs.total_fee
    """,
        (student_fee_id, current_user.school_id),
    )

    fee_record = cur.fetchone()
    if not fee_record:
        conn.close()
        return {"error": "Fee record not found"}, 404

    total_fee = float(fee_record["total_fee"])
    already_paid = float(fee_record["total_paid"])
    remaining_balance = total_fee - already_paid

    if already_paid >= total_fee:
        conn.close()
        return {"error": "This fee has already been fully paid"}, 400

    if amount > remaining_balance:
        conn.close()
        return {
            "error": f"Amount exceeds outstanding balance of {remaining_balance:.2f}"
        }, 400

    cur.execute(
        """
        INSERT INTO payments (student_fee_id, amount_paid, payment_method, recorded_by)
        VALUES (?, ?, ?, ?)
    """,
        (student_fee_id, amount, method, current_user.id),
    )

    new_total_paid = already_paid + amount
    new_balance = round(total_fee - new_total_paid, 2)

    if new_total_paid >= total_fee:
        status = "Paid"
    elif new_total_paid > 0:
        status = "Partial"
    else:
        status = "Not Paid"

    cur.execute(
        "UPDATE student_fees SET status = ? WHERE id = ?",
        (status, student_fee_id),
    )

    conn.commit()

    # SQLite: use strftime instead of TO_CHAR
    cur.execute(
        """
        SELECT id, amount_paid, payment_method,
               strftime('%Y-%m-%d', payment_date) AS payment_date
        FROM payments
        WHERE student_fee_id = ?
        ORDER BY payment_date DESC
    """,
        (student_fee_id,),
    )

    payments = [dict(p) for p in cur.fetchall()]
    for p in payments:
        p["amount_paid"] = float(p["amount_paid"])

    conn.close()

    return {
        "new_balance": new_balance,
        "new_status": status,
        "total_paid": round(new_total_paid, 2),
        "payments": payments,
    }


@app.route("/fees/create", methods=["GET", "POST"])
@login_required
def create_fee():
    if not current_user.is_admin():
        return "Forbidden", 403

    if request.method == "POST":
        class_name = request.form.get("class_name", "").strip()
        academic_year = request.form.get("academic_year", "").strip()
        term = request.form.get("term", "").strip()
        total_fee_raw = request.form.get("total_fee", "").strip()

        errors = []
        if not class_name:
            errors.append("Class name is required.")
        if not academic_year:
            errors.append("Academic year is required.")
        if not term:
            errors.append("Term is required.")

        try:
            total_fee = float(total_fee_raw)
            if total_fee <= 0:
                errors.append("Fee amount must be greater than zero.")
        except ValueError:
            errors.append("Fee amount must be a valid number.")
            total_fee = None

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("create_fee.html")

        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id FROM fee_structures
            WHERE school_id = ? AND class_name = ?
              AND academic_year = ? AND term = ?
        """,
            (current_user.school_id, class_name, academic_year, term),
        )

        if cur.fetchone():
            conn.close()
            flash(
                f"A fee structure for {class_name} — {term} {academic_year} already exists.",
                "danger",
            )
            return render_template("create_fee.html")

        cur.execute(
            """
            INSERT INTO fee_structures (school_id, class_name, academic_year, term, total_fee)
            VALUES (?, ?, ?, ?, ?)
        """,
            (current_user.school_id, class_name, academic_year, term, total_fee),
        )

        conn.commit()
        conn.close()

        flash(
            f"Fee structure for {class_name} ({term} {academic_year}) created successfully!",
            "success",
        )
        return redirect(url_for("dashboard"))

    return render_template("create_fee.html")


@app.route("/students/<int:student_id>/assign-fee", methods=["GET", "POST"])
@login_required
def assign_fee(student_id):
    if not current_user.is_admin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM students WHERE id = ? AND school_id = ?",
        (student_id, current_user.school_id),
    )
    student = cur.fetchone()
    if not student:
        conn.close()
        return "Student not found", 404

    if request.method == "POST":
        fee_structure_id = request.form.get("fee_structure_id")

        cur.execute(
            """
            SELECT id FROM student_fees
            WHERE student_id = ? AND fee_structure_id = ?
        """,
            (student_id, fee_structure_id),
        )

        if cur.fetchone():
            flash("This fee structure is already assigned to the student.", "warning")
            return redirect(url_for("student_detail", id=student_id))

        cur.execute(
            """
            INSERT INTO student_fees (student_id, fee_structure_id, status)
            VALUES (?, ?, 'Not Paid')
        """,
            (student_id, fee_structure_id),
        )

        conn.commit()
        conn.close()
        flash("Fee assigned successfully!", "success")
        return redirect(url_for("student_detail", id=student_id))

    cur.execute(
        """
        SELECT fs.id, fs.class_name, fs.term, fs.academic_year, fs.total_fee
        FROM fee_structures fs
        WHERE fs.school_id = ?
        AND fs.id NOT IN (
            SELECT fee_structure_id FROM student_fees WHERE student_id = ?
        )
        ORDER BY fs.academic_year DESC, fs.term
    """,
        (current_user.school_id, student_id),
    )

    fee_structures = cur.fetchall()
    conn.close()

    return render_template(
        "assign_fee.html", student=student, fee_structures=fee_structures
    )


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_connection()
    cur = conn.cursor()

    school_id = current_user.school_id

    cur.execute(
        """
        SELECT COUNT(*) AS total_students
        FROM students
        WHERE school_id = ?
    """,
        (school_id,),
    )
    total_students = cur.fetchone()["total_students"]

    # SQLite: use strftime instead of DATE_TRUNC
    cur.execute(
        """
        SELECT COUNT(*) AS new_students
        FROM students
        WHERE school_id = ?
        AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
    """,
        (school_id,),
    )
    new_students = cur.fetchone()["new_students"]

    cur.execute(
        """
        SELECT COUNT(*) AS teachers
        FROM users
        WHERE school_id = ?
        AND role = 'teacher'
    """,
        (school_id,),
    )
    teachers = cur.fetchone()["teachers"]

    cur.execute(
        """
        SELECT id, first_name, last_name, parent_name, parent_phone, created_at
        FROM students
        WHERE school_id = ?
        ORDER BY id DESC
        LIMIT 5
    """,
        (school_id,),
    )
    recent_students = cur.fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        new_students=new_students,
        teachers=teachers,
        recent_students=recent_students,
    )


@app.route("/dashboard/revenue")
@login_required
def revenue_dashboard():
    conn = get_connection()
    cur = conn.cursor()
    school_id = current_user.school_id

    filter_year = request.args.get("academic_year", "").strip()
    filter_term = request.args.get("term", "").strip()

    filter_clause = "WHERE fs.school_id = ?"
    filter_params = [school_id]

    if filter_year:
        filter_clause += " AND fs.academic_year = ?"
        filter_params.append(filter_year)
    if filter_term:
        filter_clause += " AND fs.term = ?"
        filter_params.append(filter_term)

    cur.execute(
        """
        SELECT DISTINCT academic_year
        FROM fee_structures
        WHERE school_id = ?
        ORDER BY academic_year DESC
    """,
        (school_id,),
    )
    academic_years = [row["academic_year"] for row in cur.fetchall()]

    cur.execute(
        """
        SELECT DISTINCT term
        FROM fee_structures
        WHERE school_id = ?
        ORDER BY term
    """,
        (school_id,),
    )
    terms = [row["term"] for row in cur.fetchall()]

    cur.execute(
        f"""
        SELECT COALESCE(SUM(p.amount_paid), 0) AS total_revenue
        FROM payments p
        JOIN student_fees sf ON p.student_fee_id = sf.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
    """,
        filter_params,
    )
    total_revenue = float(cur.fetchone()["total_revenue"])

    cur.execute(
        f"""
        SELECT COALESCE(SUM(fs.total_fee), 0) AS total_expected
        FROM student_fees sf
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
    """,
        filter_params,
    )
    total_expected = float(cur.fetchone()["total_expected"])

    total_outstanding = max(total_expected - total_revenue, 0)

    cur.execute(
        """
        SELECT COUNT(*) AS total_students
        FROM students
        WHERE school_id = ?
    """,
        (school_id,),
    )
    total_students = cur.fetchone()["total_students"]

    # SQLite: use CASE instead of COUNT(*) FILTER (WHERE ...)
    cur.execute(
        f"""
        SELECT
            SUM(CASE WHEN sf.status = 'Paid' THEN 1 ELSE 0 END)     AS paid_count,
            SUM(CASE WHEN sf.status = 'Partial' THEN 1 ELSE 0 END)  AS partial_count,
            SUM(CASE WHEN sf.status = 'Not Paid'
                      OR sf.status IS NULL THEN 1 ELSE 0 END)        AS unpaid_count
        FROM student_fees sf
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
    """,
        filter_params,
    )
    status_breakdown = dict(cur.fetchone())

    # SQLite: use strftime instead of TO_CHAR, and CAST instead of ::float
    cur.execute(
        f"""
        SELECT
            strftime('%Y-%m', p.payment_date)   AS month,
            CAST(SUM(p.amount_paid) AS REAL)    AS total
        FROM payments p
        JOIN student_fees sf ON p.student_fee_id = sf.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
        GROUP BY month
        ORDER BY month
    """,
        filter_params,
    )
    monthly_data = cur.fetchall()

    cur.execute(
        f"""
        SELECT
            s.id,
            s.first_name,
            s.last_name,
            CAST(SUM(p.amount_paid) AS REAL)    AS total_paid,
            CAST(SUM(fs.total_fee) AS REAL)     AS total_fee
        FROM payments p
        JOIN student_fees sf ON p.student_fee_id = sf.id
        JOIN students s      ON sf.student_id    = s.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
        GROUP BY s.id, s.first_name, s.last_name
        ORDER BY total_paid DESC
        LIMIT 5
    """,
        filter_params,
    )
    top_students = cur.fetchall()

    cur.execute(
        f"""
        SELECT
            s.id,
            s.first_name,
            s.last_name,
            CAST(SUM(fs.total_fee) AS REAL)                             AS total_fee,
            CAST(COALESCE(SUM(p.amount_paid), 0) AS REAL)              AS total_paid,
            CAST(SUM(fs.total_fee) - COALESCE(SUM(p.amount_paid), 0) AS REAL) AS balance
        FROM student_fees sf
        JOIN students s        ON sf.student_id      = s.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        LEFT JOIN payments p   ON p.student_fee_id   = sf.id
        {filter_clause}
        GROUP BY s.id, s.first_name, s.last_name
        HAVING SUM(fs.total_fee) - COALESCE(SUM(p.amount_paid), 0) > 0
        ORDER BY balance DESC
    """,
        filter_params,
    )
    outstanding_students = cur.fetchall()

    # SQLite: use strftime instead of TO_CHAR, CAST instead of ::float
    cur.execute(
        f"""
        SELECT
            s.first_name,
            s.last_name,
            CAST(p.amount_paid AS REAL)                     AS amount_paid,
            p.payment_method,
            strftime('%Y-%m-%d', p.payment_date)            AS payment_date,
            fs.term,
            fs.academic_year
        FROM payments p
        JOIN student_fees sf  ON p.student_fee_id   = sf.id
        JOIN students s       ON sf.student_id      = s.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        {filter_clause}
        ORDER BY p.payment_date DESC
        LIMIT 10
    """,
        filter_params,
    )
    recent_payments = cur.fetchall()

    collection_rate = 0.0
    if total_expected > 0:
        collection_rate = round((total_revenue / total_expected) * 100, 2)

    conn.close()

    return render_template(
        "revenue_dashboard.html",
        total_revenue=total_revenue,
        total_expected=total_expected,
        total_outstanding=total_outstanding,
        total_students=total_students,
        collection_rate=collection_rate,
        monthly_data=monthly_data,
        top_students=top_students,
        outstanding_students=outstanding_students,
        status_breakdown=status_breakdown,
        recent_payments=recent_payments,
        academic_years=academic_years,
        terms=terms,
        filter_year=filter_year,
        filter_term=filter_term,
    )


@app.route("/dashboard/revenue/export")
@login_required
def export_revenue():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT payment_date, amount_paid, payment_method
        FROM payments p
        JOIN student_fees sf ON p.student_fee_id = sf.id
        JOIN fee_structures fs ON sf.fee_structure_id = fs.id
        WHERE fs.school_id = ?
        ORDER BY payment_date DESC
    """,
        (current_user.school_id,),
    )

    rows = cur.fetchall()
    conn.close()

    from io import StringIO

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Amount", "Method"])
    writer.writerows(rows)

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=revenue_report.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@app.route("/attendance/<int:student_id>", methods=["GET", "POST"])
@login_required
def attendance(student_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM students WHERE id=? AND school_id=?",
        (student_id, current_user.school_id),
    )
    student = cur.fetchone()
    if not student:
        conn.close()
        return "Student not found", 404

    if request.method == "POST":
        date = request.form.get("date")
        status = request.form.get("status")
        cur.execute(
            "INSERT INTO attendance (student_id, date, status, school_id) VALUES (?, ?, ?, ?)",
            (student_id, date, status, current_user.school_id),
        )
        conn.commit()
        flash("Attendance recorded successfully!", "success")
        return redirect(url_for("attendance", student_id=student_id))

    cur.execute(
        "SELECT * FROM attendance WHERE student_id=? AND school_id=? ORDER BY date DESC",
        (student_id, current_user.school_id),
    )
    records = cur.fetchall()

    conn.close()
    return render_template("attendance.html", student=student, records=records)


@app.route("/attendance_report")
@login_required
def attendance_report():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT s.id, s.first_name, s.last_name,
            SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS present_count,
            SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) AS absent_count,
            SUM(CASE WHEN a.status='Late' THEN 1 ELSE 0 END) AS late_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id
        WHERE s.school_id=?
        GROUP BY s.id
        ORDER BY s.first_name
    """,
        (current_user.school_id,),
    )
    students_attendance = cur.fetchall()

    conn.close()
    return render_template(
        "attendance_report.html", students_attendance=students_attendance
    )


@app.route("/attendance_report/<int:student_id>")
@login_required
def student_attendance_report(student_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM students WHERE id = ? AND school_id = ?",
        (student_id, current_user.school_id),
    )
    student = cur.fetchone()
    if not student:
        conn.close()
        return "Student not found", 404

    cur.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND school_id = ? ORDER BY date DESC",
        (student_id, current_user.school_id),
    )
    attendance_records = cur.fetchall()

    total_days = len(attendance_records)
    present_days = sum(
        1 for r in attendance_records if r["status"].lower() == "present"
    )
    absent_days = sum(1 for r in attendance_records if r["status"].lower() == "absent")
    late_days = sum(1 for r in attendance_records if r["status"].lower() == "late")

    conn.close()

    return render_template(
        "attendance_report.html",
        student=student,
        attendance_records=attendance_records,
        total_days=total_days,
        present_days=present_days,
        absent_days=absent_days,
        late_days=late_days,
    )


@app.route("/mark_attendance", methods=["POST"])
@login_required
def mark_attendance():
    conn = get_connection()
    cur = conn.cursor()

    date = request.form.get("attendance_date")

    cur.execute(
        "SELECT id FROM students WHERE school_id = ?", (current_user.school_id,)
    )
    all_students = cur.fetchall()

    for student in all_students:
        student_id = student["id"]
        status = request.form.get(f"status_{student_id}")
        if status:
            status = status.capitalize()
            cur.execute(
                "SELECT * FROM attendance WHERE student_id = ? AND date = ?",
                (student_id, date),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE attendance SET status = ? WHERE student_id = ? AND date = ?",
                    (status, student_id, date),
                )
            else:
                cur.execute(
                    "INSERT INTO attendance (student_id, date, status, school_id) VALUES (?, ?, ?, ?)",
                    (student_id, date, status, current_user.school_id),
                )

    conn.commit()
    conn.close()
    flash("Attendance has been saved successfully!", "success")
    return redirect(url_for("students"))


@app.route("/students/export")
@login_required
def export_students():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT first_name, last_name, parent_name, parent_phone, created_at
        FROM students
        WHERE school_id = ?
        """,
        (current_user.school_id,),
    )

    rows = cur.fetchall()
    conn.close()

    def generate():
        yield "First Name,Last Name,Parent Name,Parent Phone,Created At\n"
        for r in rows:
            yield f"{r['first_name']},{r['last_name']},{r['parent_name']},{r['parent_phone']},{r['created_at']}\n"

    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=students.csv"},
    )


@app.route("/students/import", methods=["POST"])
@login_required
def import_students():
    file = request.files.get("file")

    if not file:
        flash("No file uploaded", "danger")
        return redirect("/students")

    try:
        reader = csv.DictReader(file.stream.read().decode("utf-8").splitlines())
    except Exception:
        flash("Invalid CSV file.", "danger")
        return redirect("/students")

    required_columns = ["First Name", "Last Name", "Parent Name", "Parent Phone"]

    if not all(col in reader.fieldnames for col in required_columns):
        flash("CSV must contain required columns.", "danger")
        return redirect("/students")

    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for row in reader:
        if not row["First Name"]:
            skipped += 1
            continue

        cur.execute(
            """
            SELECT id FROM students
            WHERE school_id = ?
            AND first_name = ?
            AND parent_phone = ?
        """,
            (current_user.school_id, row["First Name"], row.get("Parent Phone")),
        )

        if cur.fetchone():
            skipped += 1
            continue

        # SQLite: use datetime.now().isoformat() instead of NOW()
        cur.execute(
            """
            INSERT INTO students
            (school_id, first_name, last_name, parent_name, parent_phone, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                current_user.school_id,
                row["First Name"],
                row.get("Last Name"),
                row.get("Parent Name"),
                row.get("Parent Phone"),
                datetime.now().isoformat(),
            ),
        )

        inserted += 1

    conn.commit()
    conn.close()

    flash(f"{inserted} students imported. {skipped} skipped.", "success")
    return redirect("/students")


def create_reset_token(user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=1)).isoformat()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO password_resets (user_id, token, expires_at)
        VALUES (?, ?, ?)
        """,
        (user_id, token, expires),
    )

    conn.commit()
    conn.close()

    return token


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"]

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        conn.close()

        if user:
            token = create_reset_token(user["id"])
            reset_link = url_for("reset_password", token=token, _external=True)

            send_email(
                email,
                "Password Reset",
                f"Click here to reset your password: {reset_link}",
            )

        flash("If the email exists, a reset link was sent.", "info")
        return redirect("/login")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_connection()
    cur = conn.cursor()

    # SQLite: compare expires_at string against current datetime string
    cur.execute(
        """
        SELECT * FROM password_resets
        WHERE token = ? AND expires_at > ?
        """,
        (token, datetime.now().isoformat()),
    )
    reset = cur.fetchone()

    if not reset:
        conn.close()
        return "Invalid or expired token", 400

    if request.method == "POST":
        password = generate_password_hash(request.form["password"])
        cur.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password, reset["user_id"]),
        )
        cur.execute("DELETE FROM password_resets WHERE token = ?", (token,))
        conn.commit()
        conn.close()

        flash("Password updated", "success")
        return redirect("/login")

    conn.close()
    return render_template("reset_password.html")


def send_email(to, subject, body):
    msg = EmailMessage()
    msg["From"] = "noreply@yourschoolapp.com"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login("YOUR_EMAIL", "APP_PASSWORD")
        s.send_message(msg)


@app.route("/superadmin/dashboard")
@login_required
def superadmin_dashboard():
    if not current_user.is_superadmin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM schools ORDER BY id DESC")
    schools = cur.fetchall()

    school_stats = []
    for s in schools:
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE school_id=? AND role='teacher'",
            (s["id"],),
        )
        teachers = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM students WHERE school_id=?", (s["id"],))
        students = cur.fetchone()[0]

        school_stats.append(
            {
                "id": s["id"],
                "name": s["name"],
                "email": s["email"],
                "teachers": teachers,
                "students": students,
            }
        )

    conn.close()
    return render_template("superadmin_dashboard.html", schools=school_stats)


@app.route("/superadmin/schools/<int:id>/delete", methods=["POST"])
@login_required
def superadmin_delete_school(id):
    if not current_user.is_superadmin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM students WHERE school_id=?", (id,))
    cur.execute("DELETE FROM users WHERE school_id=?", (id,))
    cur.execute("DELETE FROM schools WHERE id=?", (id,))

    conn.commit()
    conn.close()
    flash("School deleted", "success")
    return redirect(url_for("superadmin_dashboard"))


@app.route("/superadmin/schools")
@login_required
def schools():
    if not current_user.is_superadmin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM schools ORDER BY id DESC")
    schools_list = cur.fetchall()
    conn.close()

    return render_template("schools.html", schools=schools_list)


@app.route("/superadmin/schools/<int:id>/edit", methods=["GET", "POST"])
@login_required
def superadmin_edit_school(id):
    if not current_user.is_superadmin():
        return "Forbidden", 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM schools WHERE id=?", (id,))
    school = cur.fetchone()

    if not school:
        conn.close()
        return "School not found", 404

    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]

        cur.execute("UPDATE schools SET name=?, email=? WHERE id=?", (name, email, id))
        cur.execute(
            "UPDATE users SET name=?, email=? WHERE school_id=? AND role='admin'",
            (name, email, id),
        )

        conn.commit()
        conn.close()
        flash("School info updated", "success")
        return redirect(url_for("superadmin_dashboard"))

    conn.close()
    return render_template("superadmin_edit_school.html", school=school)


@app.route("/superadmin/schools/<int:id>/reset-password", methods=["POST"])
@login_required
def superadmin_reset_admin_password(id):
    if not current_user.is_superadmin():
        return "Forbidden", 403

    temp_password = "".join(random.choices(string.ascii_letters + string.digits, k=8))

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET password_hash=?
        WHERE school_id=? AND role='admin'
    """,
        (generate_password_hash(temp_password), id),
    )

    conn.commit()
    conn.close()

    flash(f"Temporary password for school admin: {temp_password}", "info")
    return redirect(url_for("superadmin_dashboard"))

if __name__ == "__main__":
    init_db()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start() 
    app.run(host="127.0.0.1",port=5000, debug=False)
