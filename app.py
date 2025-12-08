import os
import sqlite3
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------------------------------------
# APP SETTINGS
# -------------------------------------------------
app = Flask(__name__)
app.secret_key = "supersecretkey"

DB_NAME = "bir_imza.db"


# -------------------------------------------------
# DATABASE
# -------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # USERS (admin & teacher login table)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT CHECK(role IN ('admin', 'teacher')) NOT NULL
        )
    """)

    # PARENTS (veli hesapları)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT UNIQUE
        )
    """)

    # STUDENTS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER,
            FOREIGN KEY(parent_id) REFERENCES parents(id)
        )
    """)

    # HOMEWORKS (ödev yüklemeleri)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS homeworks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            parent_id INTEGER,
            subject TEXT,
            image_path TEXT,
            status TEXT DEFAULT 'bekliyor',
            teacher_note TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(parent_id) REFERENCES parents(id)
        )
    """)

    # Admin yoksa oluştur
    cur.execute("SELECT COUNT(*) FROM users WHERE username='admin'")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO users (full_name, username, password, role)
            VALUES (?, ?, ?, ?)
        """, ("Yönetici", "admin",
              generate_password_hash("123456"), "admin"))
        print("✔ Admin hesabı oluşturuldu (admin / 123456)")

    conn.commit()
    conn.close()


# -------------------------------------------------
# LOGIN SYSTEM
# -------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("Kullanıcı bulunamadı!", "danger")
            return redirect(url_for("login"))

        if not check_password_hash(user["password"], password):
            flash("Şifre hatalı!", "danger")
            return redirect(url_for("login"))

        # Login success
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------------------------------
# ADMIN DASHBOARD
# -------------------------------------------------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM students")
    total_students = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM parents")
    total_parents = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM homeworks WHERE status='bekliyor'")
    waiting_hw = cur.fetchone()[0]

    cur.execute("""
        SELECT h.id, s.name AS student_name, p.name AS parent_name,
               h.subject, h.created_at
        FROM homeworks h
        LEFT JOIN students s ON h.student_id = s.id
        LEFT JOIN parents p ON h.parent_id = p.id
        ORDER BY h.id DESC LIMIT 10
    """)
    last_homeworks = cur.fetchall()

    conn.close()

    return render_template("dashboard.html",
                           total_students=total_students,
                           total_parents=total_parents,
                           waiting_hw=waiting_hw,
                           last_homeworks=last_homeworks)


# -------------------------------------------------
# TEACHER MODULE (CRUD)
# -------------------------------------------------
@app.route("/teachers")
def teachers_list():
    if session.get("role") != "admin":
        return redirect(url_for("dashboard"))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE role='teacher'")
    teachers = cur.fetchall()
    conn.close()

    return render_template("teachers.html", teachers=teachers)


@app.route("/teachers/add", methods=["GET", "POST"])
def teachers_add():
    if session.get("role") != "admin":
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full = request.form["full_name"]
        user = request.form["username"]
        pwd = generate_password_hash(request.form["password"])

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (full_name, username, password, role)
            VALUES (?, ?, ?, 'teacher')
        """, (full, user, pwd))
        conn.commit()
        conn.close()

        flash("Öğretmen eklendi!", "success")
        return redirect(url_for("teachers_list"))

    return render_template("teacher_add.html")


# -------------------------------------------------
# STUDENT MODULE (CRUD)
# -------------------------------------------------
@app.route("/students")
def students_list():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.id, s.name, p.name AS parent_name
        FROM students s
        LEFT JOIN parents p ON s.parent_id = p.id
        ORDER BY s.id DESC
    """)
    data = cur.fetchall()
    conn.close()

    return render_template("students.html", students=data)


@app.route("/students/add", methods=["GET", "POST"])
def students_add():
    if request.method == "POST":
        sname = request.form["student_name"]
        pname = request.form["parent_name"]
        phone = request.form["parent_phone"]

        conn = get_db()
        cur = conn.cursor()

        # add parent
        cur.execute("INSERT INTO parents (name, phone) VALUES (?, ?)",
                    (pname, phone))
        parent_id = cur.lastrowid

        # add student
        cur.execute("""
            INSERT INTO students (name, parent_id)
            VALUES (?, ?)
        """, (sname, parent_id))

        conn.commit()
        conn.close()

        flash("Öğrenci eklendi!", "success")
        return redirect(url_for("students_list"))

    return render_template("student_add.html")


# -------------------------------------------------
# HOMEWORK MODULE
# -------------------------------------------------
@app.route("/homeworks")
def homeworks_list():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT h.*, s.name AS student_name, p.name AS parent_name
        FROM homeworks h
        LEFT JOIN students s ON h.student_id = s.id
        LEFT JOIN parents p ON h.parent_id = p.id
        ORDER BY h.id DESC
    """)
    data = cur.fetchall()
    conn.close()

    return render_template("homeworks.html", homeworks=data)


@app.route("/homeworks/add", methods=["GET", "POST"])
def homework_add():
    if request.method == "POST":
        student_id = request.form["student_id"]
        parent_id = request.form["parent_id"]
        subject = request.form["subject"]
        image = request.files["image"]

        folder = "static/homework"
        os.makedirs(folder, exist_ok=True)

        file_path = f"{folder}/{datetime.now().timestamp()}_{image.filename}"
        image.save(file_path)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO homeworks (student_id, parent_id, subject, image_path, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (student_id, parent_id, subject, file_path, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        flash("Ödev başarıyla yüklendi!", "success")
        return redirect(url_for("homeworks_list"))

    return render_template("homework_add.html")


# -------------------------------------------------
# HOMEWORK REVIEW API
# -------------------------------------------------
@app.route("/api/review_homework", methods=["POST"])
def api_review_homework():
    data = request.get_json()
    hw_id = data.get("homework_id")
    status = data.get("status")
    note = data.get("teacher_note", "")

    if status not in ("kontrol_edildi", "eksik"):
        return jsonify({"success": False, "error": "Geçersiz durum"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE homeworks
        SET status = ?, teacher_note = ?, updated_at = ?
        WHERE id = ?
    """, (status, note, datetime.now().isoformat(), hw_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})


# -------------------------------------------------
# MAIN (Render uyumlu)
# -------------------------------------------------
# Her ortamda çalışması gereken kısım:
os.makedirs(os.path.join("static", "homework"), exist_ok=True)
with app.app_context():
    init_db()

# Sadece lokal kullanım
if __name__ == "__main__":
    app.run(debug=True)
