import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
import random

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

# ----------------- AYARLAR -----------------
DB_NAME = "bir_imza.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")  # değiştirilebilir


# ----------------- VERİTABANI -----------------
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Uygulama ilk açıldığında tablo yoksa oluşturur."""
    conn = get_db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,               -- 'admin', 'teacher'
            full_name TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            is_verified INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            school TEXT,
            grade TEXT,
            address TEXT,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (parent_id) REFERENCES parents(id)
        );

        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT,
            description TEXT,
            image_url TEXT,
            status TEXT,                  -- 'gonderildi', 'kontrol_edildi', 'eksik'
            teacher_note TEXT,
            teacher_image_url TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date TEXT,                    -- '2025-12-07' gibi
            status TEXT,                  -- 'gelecek', 'gelmeyecek', 'kararsız'
            noted_by TEXT,                -- 'veli', 'ogretmen'
            created_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS pickups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            datetime TEXT,
            picked_by_teacher_id INTEGER,
            whatsapp_sent INTEGER DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (picked_by_teacher_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS otp_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            expires_at TEXT,
            is_used INTEGER DEFAULT 0,
            FOREIGN KEY (parent_id) REFERENCES parents(id)
        );
        """
    )
    conn.commit()

    # İlk admin kullanıcı yoksa oluştur
    cur.execute("SELECT COUNT(*) AS c FROM users")
    row = cur.fetchone()
    if row["c"] == 0:
        # admin: admin / 123456
        pw_hash = generate_password_hash("123456")
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("admin", pw_hash, "admin", "Sistem Yöneticisi", datetime.now().isoformat()),
        )
        conn.commit()

    conn.close()


# ----------------- OTURUM / YETKİ KONTROL -----------------
def login_required(role=None):
    """Sadece giriş yapmış kullanıcılar erişir. role verilirse rol de kontrol edilir."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                return redirect(url_for("login", next=request.path))

            if role:
                user_role = session.get("role")
                if user_role != role:
                    flash("Bu sayfaya erişim yetkiniz yok.", "danger")
                    return redirect(url_for("dashboard"))

            return f(*args, **kwargs)
        return wrapped
    return decorator


# ----------------- WEB ROTALARI (ÖĞRETMEN / ADMIN PANEL) -----------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            flash("Giriş başarılı.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Kullanıcı adı veya şifre hatalı.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Çıkış yapıldı.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required()
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    # Genel sayılar
    cur.execute("SELECT COUNT(*) AS c FROM students")
    total_students = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM parents")
    total_parents = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM homework WHERE status = 'gonderildi'")
    waiting_hw = cur.fetchone()["c"]

    # Son 10 gelen ödev
    cur.execute(
        """
        SELECT h.id, h.subject, h.created_at,
               s.name AS student_name,
               p.name AS parent_name
        FROM homework h
        JOIN students s ON h.student_id = s.id
        JOIN parents p ON s.parent_id = p.id
        ORDER BY h.created_at DESC
        LIMIT 10
        """
    )
    last_homeworks = cur.fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_parents=total_parents,
        waiting_hw=waiting_hw,
        last_homeworks=last_homeworks,
    )


# ----------------- ÖDEV KONTROL (WEB PANEL) -----------------

@app.route("/homeworks")
@login_required()
def homeworks_list():
    """
    Web panelde ödev listesi.
    ?status=gonderildi / kontrol_edildi / eksik / tum
    """
    status = request.args.get("status", "gonderildi")

    conn = get_db()
    cur = conn.cursor()

    base_query = """
        SELECT h.id, h.subject, h.description, h.image_url, h.status, h.created_at,
               s.name AS student_name,
               p.name AS parent_name
        FROM homework h
        JOIN students s ON h.student_id = s.id
        JOIN parents p ON s.parent_id = p.id
    """
    params = []

    if status != "tum":
        base_query += " WHERE h.status = ?"
        params.append(status)

    base_query += " ORDER BY h.created_at DESC"

    cur.execute(base_query, params)
    homeworks = cur.fetchall()
    conn.close()

    return render_template("homeworks.html",
                           homeworks=homeworks,
                           selected_status=status)

@app.route("/homeworks/add", methods=["GET", "POST"])
@login_required()
def homeworks_add():
    """
    Öğretmenin panelden yeni ödev tanımlaması:
    - Öğrenci seçer
    - Ders / konu yazar
    - İsterse ödev görseli yükler (PDF yerine foto vb.)
    """
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        student_id = request.form.get("student_id")
        subject = request.form.get("subject")
        description = request.form.get("description")
        image_file = request.files.get("image")

        if not student_id or not subject:
            flash("Öğrenci ve ders alanı zorunludur.", "danger")
            # öğrencileri tekrar çekmemiz gerekiyor
            cur.execute(
                """
                SELECT s.id, s.name AS student_name, p.name AS parent_name
                FROM students s
                JOIN parents p ON s.parent_id = p.id
                WHERE s.is_active = 1
                ORDER BY s.name
                """
            )
            students = cur.fetchall()
            conn.close()
            return render_template("homework_add.html", students=students)

        image_url = None

        # Öğretmen isterse ödeve ait görsel de yükleyebilir (sayfa fotoğrafı vs.)
        if image_file and image_file.filename:
            upload_dir = os.path.join("static", "homework")
            os.makedirs(upload_dir, exist_ok=True)

            filename = f"hw_teacher_{student_id}_{int(datetime.now().timestamp())}.jpg"
            save_path = os.path.join(upload_dir, filename)
            image_file.save(save_path)

            image_url = f"/static/homework/{filename}"

        cur.execute(
            """
            INSERT INTO homework
                (student_id, subject, description, image_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                subject,
                description,
                image_url,
                "gonderildi",  # Bekleyen/ödev verildi gibi düşün
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        flash("Ödev başarıyla eklendi.", "success")
        return redirect(url_for("homeworks_list"))

    # GET isteği: Öğrenci listesi çek
    cur.execute(
        """
        SELECT s.id, s.name AS student_name, p.name AS parent_name
        FROM students s
        JOIN parents p ON s.parent_id = p.id
        WHERE s.is_active = 1
        ORDER BY s.name
        """
    )
    students = cur.fetchall()
    conn.close()

    return render_template("homework_add.html", students=students)
    

@app.route("/homeworks/<int:hw_id>", methods=["GET", "POST"])
@login_required()
def homework_detail(hw_id):
    """
    Ödev detay & güncelleme ekranı.
    GET -> formu gösterir
    POST -> durumu ve öğretmen notunu/görselini günceller
    """
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        status = request.form.get("status")
        teacher_note = request.form.get("teacher_note", "")
        teacher_image_file = request.files.get("teacher_image")

        if status not in ("gonderildi", "kontrol_edildi", "eksik"):
            flash("Geçersiz durum seçildi.", "danger")
            return redirect(url_for("homework_detail", hw_id=hw_id))

        teacher_image_url = None

        # Öğretmen isterse düzeltilmiş fotoğraf yükleyebilir
        if teacher_image_file and teacher_image_file.filename:
            upload_dir = os.path.join("static", "homework")
            os.makedirs(upload_dir, exist_ok=True)

            filename = f"hw_review_{hw_id}_{int(datetime.now().timestamp())}.jpg"
            save_path = os.path.join(upload_dir, filename)
            teacher_image_file.save(save_path)

            teacher_image_url = f"/static/homework/{filename}"

            cur.execute(
                """
                UPDATE homework
                SET status = ?, teacher_note = ?, teacher_image_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, teacher_note, teacher_image_url, datetime.now().isoformat(), hw_id),
            )
        else:
            cur.execute(
                """
                UPDATE homework
                SET status = ?, teacher_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, teacher_note, datetime.now().isoformat(), hw_id),
            )

        conn.commit()
        conn.close()

        flash("Ödev bilgileri güncellendi.", "success")
        return redirect(url_for("homeworks_list"))

    # GET: Ödevi getir
    cur.execute(
        """
        SELECT h.*, s.name AS student_name, p.name AS parent_name
        FROM homework h
        JOIN students s ON h.student_id = s.id
        JOIN parents p ON s.parent_id = p.id
        WHERE h.id = ?
        """,
        (hw_id,),
    )
    hw = cur.fetchone()
    conn.close()

    if not hw:
        flash("Ödev bulunamadı.", "danger")
        return redirect(url_for("homeworks_list"))

    return render_template("homework_detail.html", hw=hw)
    

# ----------------- ÖĞRETMEN YÖNETİMİ (ADMIN PANELİ) -----------------

@app.route("/teachers")
@login_required(role="admin")
def teachers_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, full_name, role, created_at
        FROM users
        WHERE role = 'teacher'
        ORDER BY created_at DESC
        """
    )
    teachers = cur.fetchall()
    conn.close()

    return render_template("teachers.html", teachers=teachers)


@app.route("/teachers/add", methods=["GET", "POST"])
@login_required(role="admin")
def teachers_add():
    if request.method == "POST":
        username = request.form.get("username")
        full_name = request.form.get("full_name")
        password = request.form.get("password")

        if not all([username, password]):
            flash("Kullanıcı adı ve şifre zorunludur.", "danger")
            return redirect(url_for("teachers_add"))

        pw_hash = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name, created_at)
                VALUES (?, ?, 'teacher', ?, ?)
                """,
                (username, pw_hash, full_name, datetime.now().isoformat()),
            )
            conn.commit()
            flash("Öğretmen başarıyla eklendi.", "success")
        except sqlite3.IntegrityError:
            flash("Bu kullanıcı adı zaten kullanılıyor.", "danger")
        finally:
            conn.close()

        return redirect(url_for("teachers_list"))

    return render_template("teacher_add.html")


@app.route("/teachers/reset/<int:teacher_id>", methods=["POST"])
@login_required(role="admin")
def teachers_reset_password(teacher_id):
    """
    Şifreyi varsayılan '123456' yapar (istersen değiştiririz).
    """
    new_password = "123456"
    pw_hash = generate_password_hash(new_password)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET password_hash = ?
        WHERE id = ? AND role = 'teacher'
        """,
        (pw_hash, teacher_id),
    )
    conn.commit()
    conn.close()

    flash("Öğretmen şifresi 123456 olarak güncellendi.", "info")
    return redirect(url_for("teachers_list"))


@app.route("/teachers/delete/<int:teacher_id>", methods=["POST"])
@login_required(role="admin")
def teachers_delete(teacher_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,))
    conn.commit()
    conn.close()

    flash("Öğretmen kaydı silindi.", "warning")
    return redirect(url_for("teachers_list"))


# ----------------- API - VELİ KAYIT / SMS DOĞRULAMA -----------------

@app.route("/api/register_parent", methods=["POST"])
def api_register_parent():
    """
    Veli kayıt isteği:
    {
      "name": "Veli Adı",
      "phone": "05xxxxxxxxx",
      "password": "123456"
    }
    """
    data = request.get_json()
    name = data.get("name")
    phone = data.get("phone")
    password = data.get("password")

    if not all([name, phone, password]):
        return jsonify({"success": False, "error": "Eksik bilgi"}), 400

    pw_hash = generate_password_hash(password)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO parents (name, phone, password_hash, is_verified, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (name, phone, pw_hash, datetime.now().isoformat()),
        )
        parent_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Bu telefon ile kayıt mevcut"}), 409

    # OTP üret (şimdilik random 6 haneli, SMS entegrasyonu TODO)
    code = str(random.randint(100000, 999999))
    expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
    cur.execute(
        """
        INSERT INTO otp_codes (parent_id, code, expires_at, is_used)
        VALUES (?, ?, ?, 0)
        """,
        (parent_id, code, expires_at),
    )
    conn.commit()
    conn.close()

    # TODO: Burada gerçek SMS API çağrısı yapılacak
    print("DEBUG OTP CODE (SMS ile gidecek):", code)

    return jsonify({"success": True, "parent_id": parent_id})


@app.route("/api/verify_otp", methods=["POST"])
def api_verify_otp():
    """
    {
      "parent_id": 1,
      "code": "123456"
    }
    """
    data = request.get_json()
    parent_id = data.get("parent_id")
    code = data.get("code")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM otp_codes
        WHERE parent_id = ? AND code = ? AND is_used = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (parent_id, code),
    )
    otp = cur.fetchone()

    if not otp:
        conn.close()
        return jsonify({"success": False, "error": "Kod hatalı veya kullanılmış"}), 400

    # Süre kontrolü
    if datetime.fromisoformat(otp["expires_at"]) < datetime.now():
        conn.close()
        return jsonify({"success": False, "error": "Kodun süresi dolmuş"}), 400

    # Kodu kullanıldı işaretle, veliyi onayla
    cur.execute("UPDATE otp_codes SET is_used = 1 WHERE id = ?", (otp["id"],))
    cur.execute("UPDATE parents SET is_verified = 1 WHERE id = ?", (parent_id,))
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route("/api/parent_login", methods=["POST"])
def api_parent_login():
    """
    {
      "phone": "05xxxxxxxxx",
      "password": "123456"
    }
    """
    data = request.get_json()
    phone = data.get("phone")
    password = data.get("password")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM parents WHERE phone = ?", (phone,))
    parent = cur.fetchone()
    conn.close()

    if not parent or not check_password_hash(parent["password_hash"], password):
        return jsonify({"success": False, "error": "Bilgiler hatalı"}), 401

    if not parent["is_verified"]:
        return jsonify({"success": False, "error": "Hesap henüz SMS ile doğrulanmamış"}), 403

    # Mobil tarafta kullanılacak basit token (şimdilik)
    token = f"parent-{parent['id']}"
    return jsonify({"success": True, "parent_id": parent["id"], "token": token})


# ----------------- ÖĞRENCİ YÖNETİMİ (ADMIN PANELİ) -----------------

@app.route("/students")
@login_required()
def students_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id,
               s.name AS student_name,
               s.school,
               s.grade,
               s.address,
               s.is_active,
               p.name AS parent_name,
               p.phone AS parent_phone
        FROM students s
        JOIN parents p ON s.parent_id = p.id
        ORDER BY s.name
        """
    )
    students = cur.fetchall()
    conn.close()

    return render_template("students.html", students=students)


@app.route("/students/add", methods=["GET", "POST"])
@login_required(role="admin")
def students_add():
    """
    Admin panelden öğrenci ekleme.
    Aynı formdan veli bilgisi de alınır; telefon numarası üzerinden
    varsa aynı veliyi kullanır, yoksa yeni veli açar.
    """
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        student_name = request.form.get("student_name")
        school = request.form.get("school")
        grade = request.form.get("grade")
        address = request.form.get("address")

        parent_name = request.form.get("parent_name")
        parent_phone = request.form.get("parent_phone")

        if not all([student_name, parent_name, parent_phone]):
            flash("Öğrenci adı, veli adı ve veli telefon zorunludur.", "danger")
            conn.close()
            return render_template("student_add.html")

        # 1) Veliyi bul / yoksa oluştur
        cur.execute("SELECT id FROM parents WHERE phone = ?", (parent_phone,))
        parent = cur.fetchone()

        if parent:
            parent_id = parent["id"]
        else:
            # Şifre vermiyoruz, SMS kaydı değil admin kaydı; is_verified=1 işaretliyoruz.
            cur.execute(
                """
                INSERT INTO parents (name, phone, is_verified, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (parent_name, parent_phone, datetime.now().isoformat()),
            )
            parent_id = cur.lastrowid

        # 2) Öğrenciyi ekle
        cur.execute(
            """
            INSERT INTO students (parent_id, name, school, grade, address, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (parent_id, student_name, school, grade, address),
        )
        conn.commit()
        conn.close()

        flash("Öğrenci başarıyla eklendi.", "success")
        return redirect(url_for("students_list"))

    conn.close()
    return render_template("student_add.html")


@app.route("/students/delete/<int:student_id>", methods=["POST"])
@login_required(role="admin")
def students_delete(student_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()

    flash("Öğrenci kaydı silindi.", "warning")
    return redirect(url_for("students_list"))


# ----------------- API - ÖĞRENCİ ve ÖDEV İŞLEMLERİ -----------------

@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    """
    {
      "parent_id": 1,
      "name": "Öğrenci Adı",
      "school": "Okul adı",
      "grade": "5. Sınıf",
      "address": "Adres"
    }
    """
    data = request.get_json()
    parent_id = data.get("parent_id")
    name = data.get("name")
    school = data.get("school")
    grade = data.get("grade")
    address = data.get("address")

    if not all([parent_id, name]):
        return jsonify({"success": False, "error": "Eksik bilgi"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO students (parent_id, name, school, grade, address, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (parent_id, name, school, grade, address),
    )
    conn.commit()
    student_id = cur.lastrowid
    conn.close()

    return jsonify({"success": True, "student_id": student_id})


@app.route("/api/upload_homework", methods=["POST"])
def api_upload_homework():
    """
    form-data:
      student_id: 1
      subject: "Matematik"
      description: "Sayfa 25 1-10 arası"
      image: (file)
    """
    student_id = request.form.get("student_id")
    subject = request.form.get("subject")
    description = request.form.get("description")
    image_file = request.files.get("image")

    if not all([student_id, image_file]):
        return jsonify({"success": False, "error": "Öğrenci ve resim zorunlu"}), 400

    # Klasör yoksa oluştur
    upload_dir = os.path.join("static", "homework")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"hw_{student_id}_{int(datetime.now().timestamp())}.jpg"
    save_path = os.path.join(upload_dir, filename)
    image_file.save(save_path)

    image_url = f"/static/homework/{filename}"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO homework
            (student_id, subject, description, image_url, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (student_id, subject, description, image_url, "gonderildi", datetime.now().isoformat()),
    )
    conn.commit()
    hw_id = cur.lastrowid
    conn.close()

    return jsonify({"success": True, "homework_id": hw_id, "image_url": image_url})


@app.route("/api/teacher_login", methods=["POST"])
def api_teacher_login():
    """
    Öğretmen mobil giriş:
    {
      "username": "ogretmen1",
      "password": "123456"
    }
    """
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND role = 'teacher'", (username,))
    user = cur.fetchone()
    conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"success": False, "error": "Bilgiler hatalı"}), 401

    token = f"teacher-{user['id']}"
    return jsonify({"success": True, "teacher_id": user["id"], "token": token})


@app.route("/api/teacher_homeworks", methods=["GET"])
def api_teacher_homeworks():
    """
    Öğretmen için bekleyen ödev listesi (şimdilik herkes için ortak)
    GET /api/teacher_homeworks?status=gonderildi
    """
    status = request.args.get("status", "gonderildi")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT h.id, h.subject, h.description, h.image_url, h.status, h.created_at,
               s.name AS student_name,
               p.name AS parent_name
        FROM homework h
        JOIN students s ON h.student_id = s.id
        JOIN parents p ON s.parent_id = p.id
        WHERE h.status = ?
        ORDER BY h.created_at DESC
        """,
        (status,),
    )
    rows = cur.fetchall()
    conn.close()

    data = []
    for r in rows:
        data.append(
            {
                "id": r["id"],
                "subject": r["subject"],
                "description": r["description"],
                "image_url": r["image_url"],
                "status": r["status"],
                "created_at": r["created_at"],
                "student_name": r["student_name"],
                "parent_name": r["parent_name"],
            }
        )

    return jsonify({"success": True, "items": data})


@app.route("/api/review_homework", methods=["POST"])
def api_review_homework():
    """
    Öğretmenin ödev durumu güncellemesi:
    {
      "homework_id": 10,
      "status": "kontrol_edildi",   # veya "eksik"
      "teacher_note": "Gayet iyi"
    }
    """
    data = request.get_json()
    hw_id = data.get("homework_id")
    status = data.get("status")
    note = data.get("teacher_note", "")

    if status not in ("kontrol_edildi", "eksik"):
        return jsonify({"success": False, "error": "Geçersiz durum"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE homework
        SET status = ?, teacher_note = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, note, datetime.now().isoformat(), hw_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True})


# ----------------- MAIN -----------------
if __name__ == "__main__":
    os.makedirs("static/homework", exist_ok=True)
    init_db()
    app.run(debug=True)
