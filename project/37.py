import json
import os
import secrets
import sqlite3
from datetime import datetime
from io import BytesIO

import pandas as pd
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    make_response,
    redirect,
    request,
    send_file,
    send_from_directory,
)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ATTENDANCE.db")
ALLOWED_SUBJECTS = ["MYANMAR", "ENGLISH", "MATH", "PHYSICS", "CEIT", "ME"]
ALLOWED_MONTHS = ["total", "july", "august", "september"]
PUBLIC_ENDPOINTS = {"login", "login_background", "chart_js", "favicon"}
PROFILE_DIR = os.path.join(BASE_DIR, "profile")

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS one_time_attendance (
            unique_id TEXT,
            subject TEXT,
            period_weight INTEGER,
            date TEXT,
            PRIMARY KEY (unique_id, subject, date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_check (
            date TEXT,
            unique_id TEXT,
            subject TEXT,
            PRIMARY KEY (date, unique_id, subject)
        )
    """)
    cur.execute("CREATE TABLE IF NOT EXISTS COOKIE (unique_id TEXT, cookie_id TEXT)")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS attendance_rate (date TEXT, subject TEXT, percentage INTEGER, present INTEGER)"
    )
    con.commit()
    con.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10.0)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def read_file(filepath, default=""):
    if filepath.startswith("project/"):
        filepath = filepath[len("project/"):]
    full_path = os.path.join(BASE_DIR, filepath)
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return default


def render_login_page(err=False):
    login_html = read_file(
        "project/login.html", "<html><body><h2>Login Required</h2></body></html>"
    )
    error_msg = (
        '<div class="error-alert">Account or password is incorrect. Please try again</div>'
        if err
        else ""
    )
    return Response(
        login_html.replace("{{ error_message }}", error_msg),
        status=200,
        mimetype="text/html",
    )


def get_current_subject(cursor):
    now = datetime.now()
    current_day = now.strftime("%A")
    current_time = now.strftime("%H:%M")

    try:
        cursor.execute(
            "SELECT subject FROM TIMETABLE WHERE day_of_week = ? AND ? BETWEEN start_time AND end_time",
            (current_day, current_time),
        )
        row = cursor.fetchone()
        if row and row[0] and row[0].upper() in ALLOWED_SUBJECTS:
            return row[0].upper()
    except sqlite3.OperationalError:
        pass

    return "MYANMAR"


def get_live_schedule(cursor, teacher_subject=""):
    now = datetime.now()
    current_day = now.strftime("%A")
    current_time = now.strftime("%H:%M")

    teacher_subj_str = (teacher_subject or "").upper()

    def format_pill(subj_name):
        subj_name = subj_name or "N/A"
        if subj_name in ("N/A", "NONE", "WEEKEND", "CLASSES OVER"):
            return f'<div class="subject-pill inactive">{subj_name}</div>'

        is_teacher_subject = bool(
            teacher_subj_str and subj_name.upper() == teacher_subj_str
        )
        highlight_cls = " highlight-teacher" if is_teacher_subject else ""
        badge_html = (
            '<span class="teacher-badge">Your Subject</span>'
            if is_teacher_subject
            else ""
        )

        return f'<div class="subject-pill{highlight_cls}"><span>{subj_name}</span>{badge_html}</div>'

    if current_day in ["Saturday", "Sunday"]:
        current_html = format_pill("WEEKEND")
        next_html = "".join([format_pill("N/A") for _ in range(3)])
        return current_html, next_html

    cursor.execute(
        "SELECT real_sub_name, start_time, end_time FROM TIMETABLE WHERE day_of_week = ? ORDER BY start_time ASC",
        (current_day,),
    )
    timetable = cursor.fetchall()

    current_sub = "NONE"
    start_next_index = 0
    classes_over = False

    if not timetable:
        classes_over = True
    else:
        found_future_or_current = False
        for i, (subj, start, end) in enumerate(timetable):
            if start <= current_time <= end:
                current_sub = (subj or "NONE").upper()
                start_next_index = i + 1
                found_future_or_current = True
                break
            elif current_time < start:
                start_next_index = i
                found_future_or_current = True
                break

        if not found_future_or_current:
            classes_over = True

    if classes_over:
        current_sub = "CLASSES OVER"
        upcoming = []
    else:
        upcoming = [
            (row[0] or "N/A").upper()
            for row in timetable[start_next_index : start_next_index + 3]
        ]

    while len(upcoming) < 3:
        upcoming.append("N/A")

    current_html = format_pill(current_sub)
    next_html = "".join([format_pill(sub) for sub in upcoming])

    return current_html, next_html


@app.before_request
def authenticate_request():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    cookie_id = request.cookies.get("cookie_id")
    if not cookie_id:
        return render_login_page("error" in request.args)

    cur = get_db().cursor()
    cur.execute("SELECT unique_id FROM COOKIE WHERE cookie_id = ?", (cookie_id,))
    c_cookie = cur.fetchone()
    if c_cookie is None:
        return render_login_page("error" in request.args)

    unique_id = c_cookie[0]
    cur.execute("SELECT role FROM MEMBER WHERE unique_id = ?", (unique_id,))
    c_role = cur.fetchone()

    g.cookie_id = cookie_id
    g.unique_id = unique_id
    g.role = c_role[0] if c_role else "student"
    return None

@app.route("/login_background.png", methods=["GET"])
def login_background():
    return send_from_directory(BASE_DIR, "login_background.png", mimetype="image/png")


@app.route("/chart.js", methods=["GET"])
def chart_js():
    return send_from_directory(BASE_DIR, "chart.js", mimetype="application/javascript")


@app.route("/favicon.ico")
def favicon():
    return Response(b"", status=204, mimetype="image/x-icon")


@app.route("/login", methods=["POST"])
def login():
    account = request.form.get("account", "")
    password = request.form.get("password", "")

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT unique_id FROM ACCOUNT WHERE account_name = ? AND password = ?",
        (account, password),
    )
    user = cur.fetchone()

    if user is not None:
        unique_id = user[0]
        random_cookie = secrets.token_hex(16)
        cur.execute(
            "INSERT INTO COOKIE (unique_id, cookie_id) VALUES (?, ?)",
            (unique_id, random_cookie),
        )
        con.commit()

        resp = make_response(redirect("/homepage"))
        resp.set_cookie(
            "cookie_id",
            random_cookie,
            max_age=2592000,
            httponly=True,
            path="/",
            samesite="Lax",
        )
        return resp

    return redirect("/?error=1")


@app.route("/logout", methods=["GET"])
def logout():
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM COOKIE WHERE cookie_id = ?", (g.cookie_id,))
    con.commit()

    resp = make_response(redirect("/"))
    resp.delete_cookie("cookie_id", path="/", samesite="Lax")
    return resp


@app.route("/")
def index():
    return redirect("/homepage")


@app.route("/homepage")
def homepage():
    cur = get_db().cursor()
    unique_id = g.unique_id
    role = g.role

    if role == "teacher":
        response_html = read_file("project/teacher_homepage.html")

        teacher_subject = "MYANMAR"
        try:
            cur.execute(
                "SELECT subject FROM teacher_major WHERE unique_id = ?", (unique_id,)
            )
            res_subj = cur.fetchone()
            if res_subj and res_subj[0]:
                teacher_subject = res_subj[0].upper()
        except sqlite3.OperationalError:
            pass

        month_prefix = datetime.now().strftime("%B").upper()
        total_students = 0
        try:
            cur.execute(f"SELECT COUNT(*) FROM {month_prefix}_attendance")
            total_students = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        if total_students == 0:
            try:
                cur.execute("SELECT COUNT(*) FROM MEMBER WHERE role = 'student'")
                total_students = cur.fetchone()[0]
            except sqlite3.OperationalError:
                total_students = 45

        scanned_count = 0
        try:
            cur.execute(
                "SELECT COUNT(*) FROM one_time_attendance WHERE subject = ?",
                (teacher_subject,),
            )
            scanned_count = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        current_active = get_current_subject(cur)
        if total_students > 0 and current_active and teacher_subject == current_active:
            attendance_percentage = round((scanned_count / total_students) * 100)
        else:
            attendance_percentage = 0

        box_class = "normal" if attendance_percentage >= 75 else "warning"

        chart_labels = []
        chart_data = []
        try:
            cur.execute(
                "SELECT date, percentage FROM attendance_rate WHERE subject = ?",
                (teacher_subject,),
            )
            for row in cur.fetchall():
                date_val = row[0] if row[0] else "Unknown"
                rate_val = row[1] if row[1] is not None else 0
                chart_labels.append(str(date_val))
                try:
                    chart_data.append(int(rate_val))
                except ValueError:
                    chart_data.append(0)
        except sqlite3.OperationalError:
            pass

        present_count = 0
        attendance_percentage_today = 0
        try:
            cur.execute(
                "SELECT present, percentage FROM attendance_rate WHERE subject = ? ORDER BY rowid DESC LIMIT 1",
                (teacher_subject,),
            )
            present_row = cur.fetchone()
            if present_row:
                present_count = present_row[0]
                attendance_percentage_today = present_row[1]
        except sqlite3.OperationalError:
            pass

        absent_count = (
            total_students - present_count if total_students >= present_count else 0
        )

        response_html = (
            response_html.replace("{{ today_attendance }}", str(attendance_percentage_today))
            .replace("{{ present_count }}", str(present_count))
            .replace("{{ absent_count }}", str(absent_count))
            .replace("{{ attendance_rate }}", str(attendance_percentage))
            .replace("{{ box_class }}", box_class)
            .replace("{{ teacher_subject }}", teacher_subject)
            .replace("{{ chart_labels }}", json.dumps(chart_labels))
            .replace("{{ chart_data }}", json.dumps(chart_data))
        )
        return Response(response_html, mimetype="text/html")

    # Student homepage
    requested_month = request.args.get("month", "july").lower()
    if requested_month not in ALLOWED_MONTHS:
        requested_month = "july"

    subjects = ALLOWED_SUBJECTS
    attendance_list = []

    if requested_month == "total":
        total_present_dict = {sub: 0 for sub in subjects}
        total_possible_dict = {sub: 0 for sub in subjects}

        for m in ["JULY", "AUGUST", "SEPTEMBER"]:
            try:
                cur.execute(
                    f"SELECT * FROM {m}_attendance WHERE unique_id = ?", (unique_id,)
                )
                att_row = cur.fetchone()
                if att_row:
                    for i, sub_name in enumerate(subjects):
                        raw_val = att_row[i + 2]
                        try:
                            raw_tally = int(str(raw_val).split("/")[0])
                        except (ValueError, IndexError):
                            raw_tally = 0
                        total_present_dict[sub_name] += raw_tally
            except sqlite3.OperationalError:
                pass

            try:
                cur.execute(f"SELECT attendance FROM {m}_total_attendance")
                total_rows = cur.fetchall()
                for i, sub_name in enumerate(subjects):
                    if i < len(total_rows):
                        total_possible_dict[sub_name] += int(total_rows[i][0])
            except sqlite3.OperationalError:
                pass

        for sub_name in subjects:
            present = total_present_dict[sub_name]
            possible = total_possible_dict[sub_name]
            percentage = round((present / possible) * 100) if possible > 0 else 0
            attendance_list.append(
                {"courseCode": sub_name, "percentage": min(percentage, 100)}
            )
    else:
        table_name = f"{requested_month.upper()}_attendance"
        try:
            cur.execute(f"SELECT * FROM {table_name} WHERE unique_id = ?", (unique_id,))
            att_row = cur.fetchone()
        except sqlite3.OperationalError:
            att_row = None

        total_table_name = f"{requested_month.upper()}_total_attendance"
        try:
            cur.execute(f"SELECT attendance FROM {total_table_name}")
            total_rows = cur.fetchall()
            month_totals = {
                subjects[i]: total_rows[i][0]
                for i in range(len(subjects))
                if i < len(total_rows)
            }
        except sqlite3.OperationalError:
            month_totals = {}

        if att_row:
            for i, sub_name in enumerate(subjects):
                raw_val = att_row[i + 2]
                try:
                    raw_tally = int(str(raw_val).split("/")[0])
                except (ValueError, IndexError):
                    raw_tally = 0

                total_for_sub = month_totals.get(sub_name, 0)
                percentage = (
                    round((raw_tally / total_for_sub) * 100)
                    if total_for_sub > 0
                    else 0
                )
                percentage = min(percentage, 100)
                display_name = (
                    "C Programming"
                    if sub_name == "CEIT"
                    else "Industrial Safety"
                    if sub_name == "ME"
                    else sub_name
                )
                attendance_list.append(
                    {"courseCode": display_name, "percentage": percentage}
                )
        else:
            attendance_list = [{"courseCode": sub, "percentage": 0} for sub in subjects]

    current_active_subject = get_current_subject(cur)
    if current_active_subject == "CEIT":
        current_active_subject = "C Programming"
    elif current_active_subject == "ME":
        current_active_subject = "Industrial Safety"

    response_html = read_file("project/student_homepage.html")
    response_html = (
        response_html.replace("{{ unique_id }}", str(unique_id))
        .replace("{{ attendance_json }}", json.dumps(attendance_list))
        .replace(
            "{{ current_subject }}",
            current_active_subject if current_active_subject else "NONE",
        )
    )
    return Response(response_html, mimetype="text/html")


@app.route("/student_id=", defaults={"student_id": ""}, methods=["GET"])
@app.route("/student_id=<student_id>", methods=["GET"])
def scan_student_qr(student_id):
    if g.role != "teacher":
        return Response(
            "Access Denied: Only authenticated teachers can scan student QR codes.",
            status=403,
            mimetype="text/plain",
        )

    con = get_db()
    cur = con.cursor()

    teacher_subject = ""
    try:
        cur.execute(
            "SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,)
        )
        teacher_subject_row = cur.fetchone()
        if teacher_subject_row and teacher_subject_row[0]:
            teacher_subject = teacher_subject_row[0].upper()
    except sqlite3.OperationalError:
        pass

    subject = get_current_subject(cur)
    now = datetime.now()
    current_day = now.strftime("%A")
    date = now.strftime("%d/%m/%Y")

    period_weight = 1
    if subject:
        try:
            cur.execute(
                "SELECT COUNT(*) FROM TIMETABLE WHERE day_of_week = ? AND subject = ?",
                (current_day, subject),
            )
            res_pw = cur.fetchone()
            if res_pw and res_pw[0] > 0:
                period_weight = res_pw[0]
        except sqlite3.OperationalError:
            pass

    if not subject or teacher_subject != subject:
        active_str = subject if subject else "No Active Class"
        error_msg = (
            f"Access Denied: You teach {teacher_subject if teacher_subject else 'no assigned subject'}, "
            f"but the current active period is {active_str}."
        )
        return Response(error_msg, status=403, mimetype="text/plain")

    if not student_id:
        return Response("Invalid Student ID Format", status=400, mimetype="text/plain")

    try:
        cur.execute(
            "SELECT unique_id FROM attendance_check WHERE unique_id = ? AND date = ? AND subject = ?",
            (student_id, date, subject),
        )
        if cur.fetchone() is None:
            cur.execute(
                "INSERT OR IGNORE INTO one_time_attendance (unique_id, subject, period_weight, date) VALUES (?, ?, ?, ?)",
                (student_id, subject, period_weight, date),
            )
            con.commit()

        cur.execute("SELECT name FROM MEMBER WHERE unique_id = ?", (student_id,))
        student_row = cur.fetchone()
        student_name = student_row[0] if student_row else "Unknown Student"
    except sqlite3.OperationalError:
        student_name = "Unknown Student"

    timestamp = datetime.now().strftime("%I:%M %p")
    response_html = read_file("project/approve.html")
    if response_html:
        response_html = (
            response_html.replace("{{ student_name }}", student_name)
            .replace("{{ student_id }}", student_id)
            .replace("{{ subject }}", subject)
            .replace("{{ timestamp }}", timestamp)
        )
    else:
        response_html = (
            f"<html><body><h2>Scanned: {student_name} ({student_id})</h2>"
            f"<p>Logged for {subject} at {timestamp}</p></body></html>"
        )

    return Response(response_html, mimetype="text/html")


@app.route("/commit_qr_attendance", methods=["POST"])
def commit_qr_attendance():
    if g.role != "teacher":
        return Response("Access Denied", status=403, mimetype="text/plain")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,))
    teacher_sub_row = cur.fetchone()
    teacher_subject = (
        teacher_sub_row[0].upper()
        if (teacher_sub_row and teacher_sub_row[0])
        else ""
    )

    cur.execute(
        "SELECT unique_id, period_weight, date FROM one_time_attendance WHERE subject = ?",
        (teacher_subject,),
    )
    queued_students = cur.fetchall()

    if queued_students and teacher_subject in ALLOWED_SUBJECTS:
        scans_by_date = {}
        for student_id, p_weight, scan_date in queued_students:
            scans_by_date.setdefault(scan_date, []).append((student_id, p_weight))

        for scan_date, records in scans_by_date.items():
            try:
                dt_obj = datetime.strptime(scan_date, "%d/%m/%Y")
                month_prefix = dt_obj.strftime("%B").upper()
            except ValueError:
                month_prefix = datetime.now().strftime("%B").upper()

            student_table = f"{month_prefix}_attendance"
            total_table = f"{month_prefix}_total_attendance"

            try:
                cur.execute(f"SELECT COUNT(*) FROM {student_table}")
                total_students = cur.fetchone()[0]
            except sqlite3.OperationalError:
                total_students = 0

            if total_students == 0:
                try:
                    cur.execute("SELECT COUNT(*) FROM MEMBER WHERE role = 'student'")
                    total_students = cur.fetchone()[0]
                except sqlite3.OperationalError:
                    total_students = 45

            session_weight = records[0][1] if records and records[0][1] else 1
            date_scanned_count = len(records)

            for student_id, p_weight in records:
                cur.execute(
                    f"UPDATE {student_table} SET {teacher_subject} = COALESCE({teacher_subject}, 0) + ? WHERE unique_id = ?",
                    (p_weight, student_id),
                )
                cur.execute(
                    "INSERT OR IGNORE INTO attendance_check (date, unique_id, subject) VALUES (?, ?, ?)",
                    (scan_date, student_id, teacher_subject),
                )

            cur.execute(
                "SELECT present FROM attendance_rate WHERE date = ? AND subject = ?",
                (scan_date, teacher_subject),
            )
            existing_row = cur.fetchone()

            if existing_row:
                new_present = existing_row[0] + date_scanned_count
                attendance_percentage = (
                    round((new_present / total_students) * 100)
                    if total_students > 0
                    else 0
                )
                cur.execute(
                    "UPDATE attendance_rate SET present = ?, percentage = ? WHERE date = ? AND subject = ?",
                    (new_present, attendance_percentage, scan_date, teacher_subject),
                )
            else:
                attendance_percentage = (
                    round((date_scanned_count / total_students) * 100)
                    if total_students > 0
                    else 0
                )
                cur.execute(
                    "INSERT INTO attendance_rate (date, subject, percentage, present) VALUES (?, ?, ?, ?)",
                    (scan_date, teacher_subject, attendance_percentage, date_scanned_count),
                )
                cur.execute(
                    f"UPDATE {total_table} SET attendance = COALESCE(attendance, 0) + ? WHERE subject = ?",
                    (session_weight, teacher_subject),
                )
            con.commit()

        cur.execute(
            "DELETE FROM one_time_attendance WHERE subject = ?", (teacher_subject,)
        )
        con.commit()

    return redirect("/homepage")


# --- Attendance Management & Export ---


@app.route("/update_attendance", methods=["POST"])
def update_attendance():
    subject = request.form.get("subject", "").upper()
    month = request.form.get("month", "total").lower()
    rows_data = request.form.get("rows", "")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,))
    teacher_sub_row = cur.fetchone()
    teacher_subject = (
        teacher_sub_row[0].upper()
        if (teacher_sub_row and teacher_sub_row[0])
        else ""
    )

    if (
        g.role == "teacher"
        and subject == teacher_subject
        and subject in ALLOWED_SUBJECTS
        and month in ALLOWED_MONTHS
        and rows_data
    ):
        table_name = (
            "total_attendance" if month == "total" else f"{month.upper()}_attendance"
        )
        for item in rows_data.split(","):
            if not item or "|" not in item:
                continue
            parts = item.split("|")
            if len(parts) == 3:
                uid, att, _ = parts
                cur.execute(
                    f"UPDATE {table_name} SET {subject} = ? WHERE unique_id = ?",
                    (att, uid),
                )
        con.commit()
        return Response("Success", status=200, mimetype="text/plain")

    return Response(
        "Access Denied: Subject mismatch or invalid parameters.",
        status=403,
        mimetype="text/plain",
    )


@app.route("/attendance", methods=["GET"])
def attendance():
    subject = request.args.get("subject", "MYANMAR").upper()
    month = request.args.get("month", "total").lower()

    if subject not in ALLOWED_SUBJECTS or month not in ALLOWED_MONTHS:
        return Response("400 Bad Request", status=400, mimetype="text/plain")

    cur = get_db().cursor()
    html_rows = ""

    if month == "total":
        total_lectures = 0
        for m in ["JULY", "AUGUST", "SEPTEMBER"]:
            try:
                cur.execute(
                    f"SELECT attendance FROM {m}_total_attendance WHERE subject = ?",
                    (subject,),
                )
                res = cur.fetchone()
                if res:
                    total_lectures += int(res[0])
            except sqlite3.OperationalError:
                pass

        query = f"""
            SELECT j.unique_id, j.name,
            (COALESCE(j.{subject}, 0) + COALESCE(a.{subject}, 0) + COALESCE(s.{subject}, 0)) as total_present
            FROM JULY_attendance j
            LEFT JOIN AUGUST_attendance a ON j.unique_id = a.unique_id
            LEFT JOIN SEPTEMBER_attendance s ON j.unique_id = s.unique_id
        """
        cur.execute(query)
        for row in cur.fetchall():
            student_id, name, total_present = (
                row[0],
                row[1],
                (row[2] if row[2] is not None else 0),
            )
            pct_value = (
                str(round((total_present / total_lectures) * 100))
                if total_lectures > 0
                else "0"
            )
            html_rows += f"""
            <tr data-id="{student_id}">
                <td><a href="/student_profile={student_id}">{student_id}</a></td>
                <td>{name}</td>
                <td><span class="attendance-value">{total_present}/{total_lectures}</span></td>
                <td><span class="percentage-value">{pct_value}%</span></td>
            </tr>"""
    else:
        total_table = f"{month.upper()}_total_attendance"
        cur.execute(
            f"SELECT attendance FROM {total_table} WHERE subject = ?", (subject,)
        )
        res_month = cur.fetchone()
        month_total_lectures = int(res_month[0]) if res_month else 0

        student_table = f"{month.upper()}_attendance"
        cur.execute(f"SELECT unique_id, name, {subject} FROM {student_table}")
        for row in cur.fetchall():
            student_id, name = row[0], row[1]
            raw_value = str(row[2]) if row[2] is not None else "0"
            if "/" in raw_value:
                raw_value = raw_value.split("/")[0]
            try:
                monthly_present = int(raw_value)
            except ValueError:
                monthly_present = 0

            pct_value = (
                str(round((monthly_present / month_total_lectures) * 100))
                if month_total_lectures > 0
                else "0"
            )
            html_rows += f"""
            <tr data-id="{student_id}">
                <td><a href="/student_profile={student_id}">{student_id}</a></td>
                <td>{name}</td>
                <td>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <button class="edit-btn" onclick="changeAttendance(this, -1)" style="display:none; padding:2px 8px; border-radius:4px; border:1px solid #cbd5e1; background:white; cursor:pointer; font-weight:bold; color:var(--text-main);">-</button>
                        <span class="attendance-value">{monthly_present}/{month_total_lectures}</span>
                        <button class="edit-btn" onclick="changeAttendance(this, 1)" style="display:none; padding:2px 8px; border-radius:4px; border:1px solid #cbd5e1; background:white; cursor:pointer; font-weight:bold; color:var(--text-main);">+</button>
                    </div>
                </td>
                <td><span class="percentage-value">{pct_value}%</span></td>
            </tr>"""

    response_html = read_file("project/attendance_list.html").replace(
        "{{ table_rows }}", html_rows
    )

    cur.execute("SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,))
    res_subj = cur.fetchone()
    teacher_subject = res_subj[0].upper() if (res_subj and res_subj[0]) else ""

    if g.role == "teacher" and month != "total" and subject == teacher_subject:
        edit_html = """
        <div style="display: flex; gap: 10px;">
            <button id="editModeBtn" onclick="toggleEditMode()" style="background:var(--primary); color:white; border:none; padding:10px 18px; border-radius:8px; cursor:pointer; font-weight:600;">Edit Mode</button>
            <button id="confirmBtn" onclick="saveChanges()" style="background:#22c55e; color:white; border:none; padding:10px 18px; border-radius:8px; cursor:pointer; font-weight:600; display:none;">Confirm</button>
        </div>"""
        response_html = response_html.replace("{{ edit_controls }}", edit_html)
    else:
        response_html = response_html.replace("{{ edit_controls }}", "")

    return Response(response_html, mimetype="text/html")


@app.route("/export_excel", methods=["GET"])
def export_excel():
    subject = request.args.get("subject", "MYANMAR").upper()
    month = request.args.get("month", "total").lower()

    if subject not in ALLOWED_SUBJECTS or month not in ALLOWED_MONTHS:
        return Response("400 Bad Request", status=400, mimetype="text/plain")

    cur = get_db().cursor()
    data_for_excel = []

    if month == "total":
        total_lectures = 0
        for m in ["JULY", "AUGUST", "SEPTEMBER"]:
            try:
                cur.execute(
                    f"SELECT attendance FROM {m}_total_attendance WHERE subject = ?",
                    (subject,),
                )
                res = cur.fetchone()
                if res:
                    total_lectures += int(res[0])
            except sqlite3.OperationalError:
                pass

        query = f"""
            SELECT j.unique_id, j.name,
            (COALESCE(j.{subject}, 0) + COALESCE(a.{subject}, 0) + COALESCE(s.{subject}, 0)) as total_present
            FROM JULY_attendance j
            LEFT JOIN AUGUST_attendance a ON j.unique_id = a.unique_id
            LEFT JOIN SEPTEMBER_attendance s ON j.unique_id = s.unique_id
        """
        cur.execute(query)
        for row in cur.fetchall():
            student_id, name, total_present = (
                row[0],
                row[1],
                (row[2] if row[2] is not None else 0),
            )
            pct_value = (
                round((total_present / total_lectures) * 100)
                if total_lectures > 0
                else 0
            )
            data_for_excel.append(
                {
                    "Student ID": student_id,
                    "Name": name,
                    "Attendance": f"{total_present}/{total_lectures}",
                    "Percentage (%)": pct_value,
                }
            )
    else:
        total_table = f"{month.upper()}_total_attendance"
        cur.execute(
            f"SELECT attendance FROM {total_table} WHERE subject = ?", (subject,)
        )
        res_month = cur.fetchone()
        month_total_lectures = int(res_month[0]) if res_month else 0

        student_table = f"{month.upper()}_attendance"
        cur.execute(f"SELECT unique_id, name, {subject} FROM {student_table}")
        for row in cur.fetchall():
            student_id, name = row[0], row[1]
            raw_value = str(row[2]) if row[2] is not None else "0"
            if "/" in raw_value:
                raw_value = raw_value.split("/")[0]
            try:
                monthly_present = int(raw_value)
            except ValueError:
                monthly_present = 0
            pct_value = (
                round((monthly_present / month_total_lectures) * 100)
                if month_total_lectures > 0
                else 0
            )
            data_for_excel.append(
                {
                    "Student ID": student_id,
                    "Name": name,
                    "Attendance": f"{monthly_present}/{month_total_lectures}",
                    "Percentage (%)": pct_value,
                }
            )

    df = pd.DataFrame(data_for_excel)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=f"{subject}_{month.capitalize()}")
    output.seek(0)

    filename = f"Attendance_{subject}_{month.capitalize()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/timetable", methods=["GET"])
def timetable():
    cur = get_db().cursor()
    response_html = read_file("project/timetable.html")

    cur.execute("SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,))
    res_subj = cur.fetchone()
    raw_teacher_subject = res_subj[0] if (res_subj and res_subj[0]) else ""

    display_teacher_subject = raw_teacher_subject.upper()
    if display_teacher_subject == "CEIT":
        display_teacher_subject = "C Programming"
    elif display_teacher_subject == "ME":
        display_teacher_subject = "Industrial Safety"

    current_sub_html, next_subs_html = get_live_schedule(cur, display_teacher_subject)
    response_html = response_html.replace(
        "{{ current_subject_box | safe }}", current_sub_html or ""
    ).replace("{{ next_3_subjects_list | safe }}", next_subs_html or "")

    response_html = response_html.replace(
        "{{ teacher_subject }}", raw_teacher_subject if g.role == "teacher" else ""
    )
    return Response(response_html, mimetype="text/html")


@app.route("/profile", methods=["GET"])
def profile():
    cur = get_db().cursor()
    unique_id = g.unique_id

    if g.role == "teacher":
        teacher_name = f"Professor (ID: {unique_id})"
        teacher_subject = "General Faculty"
        raw_courses = ""

        try:
            cur.execute(
                "SELECT name, major, couese FROM teacher_info WHERE unique_id = ?",
                (unique_id,),
            )
            res_teacher = cur.fetchone()
            if res_teacher:
                teacher_name = res_teacher[0] or teacher_name
                teacher_subject = res_teacher[1] or teacher_subject
                raw_courses = res_teacher[2] or ""
        except sqlite3.OperationalError:
            pass

        normalized_courses = raw_courses.replace("\n", ",").split(",")
        unique_courses = [c.strip() for c in normalized_courses if c.strip()]
        course_badges_html = "".join(
            [f'<div class="class-badge">{course}</div>\n' for course in set(unique_courses)]
        )
        if not course_badges_html:
            course_badges_html = '<div class="class-badge" style="grid-column: span 2;">No Courses Assigned</div>'

        response_html = (
            read_file("project/teacher_profile.html")
            .replace("{{ teacher_name }}", teacher_name)
            .replace("{{ teacher_subject }}", teacher_subject)
            .replace("{{ course_badges }}", course_badges_html)
            .replace("{{ unique_id }}", str(unique_id))
        )
    else:
        cur.execute(
            "SELECT name, major, year FROM student_info WHERE unique_id = ?",
            (unique_id,),
        )
        res_st = cur.fetchone()
        student_name = res_st[0] if (res_st and res_st[0]) else f"Student #{unique_id}"
        student_major = res_st[1] if (res_st and res_st[1]) else ""
        student_year = res_st[2] if (res_st and res_st[2]) else ""

        response_html = (
            read_file("project/student_profile.html")
            .replace("{{ student_name }}", student_name)
            .replace("{{ major }}", student_major)
            .replace("{{ year }}", student_year)
            .replace("{{ unique_id }}", str(unique_id))
            .replace("{{edit_form_section}}", "")
            .replace("{{ edit_button }}", "")
        )

    return Response(response_html, mimetype="text/html")


@app.route("/student_profile=<student_id>", methods=["GET"])
def student_profile(student_id):
    cur = get_db().cursor()
    cur.execute(
        "SELECT name, major, year FROM student_info WHERE unique_id = ?", (student_id,)
    )
    res_st = cur.fetchone()
    student_name = res_st[0] if (res_st and res_st[0]) else f"Student #{student_id}"
    student_major = res_st[1] if (res_st and res_st[1]) else ""
    student_year = res_st[2] if (res_st and res_st[2]) else ""

    edit_form_section = f"""
    <form id="editSection" action="/update_profile={student_id}" method="POST" style="display: none;">
        <label>Name</label>
        <input type="text" name="name" class="a" value="{student_name}" required>

        <label>Major</label>
        <input type="text" name="major" class="a" value="{student_major}" placeholder="Enter major">

        <label>Year</label>
        <input type="text" name="year" class="a" value="{student_year}" placeholder="Enter year">

        <button type="submit" class="action-btn">Save Changes</button>
        <button type="button" class="cancel-btn" onclick="toggleEditMode()">Cancel</button>
    </form>"""
    edit_button = '<button type="button" id="editToggleBtn" class="edit-toggle-btn" onclick="toggleEditMode()">Edit Profile</button>'

    response_html = (
        read_file("project/student_profile.html")
        .replace("{{ student_name }}", student_name)
        .replace("{{ major }}", student_major)
        .replace("{{ year }}", student_year)
        .replace("{{ unique_id }}", str(student_id))
        .replace("{{edit_form_section}}", edit_form_section if g.role == "teacher" else "")
        .replace("{{ edit_button }}", edit_button if g.role == "teacher" else "")
    )
    return Response(response_html, mimetype="text/html")


@app.route("/upload_profile=<u_id>", methods=["POST"])
def upload_profile(u_id):
    body_bytes = request.get_data()
    if not body_bytes:
        return Response("No image data received", status=400, mimetype="text/plain")

    os.makedirs(PROFILE_DIR, exist_ok=True)
    file_path = os.path.join(PROFILE_DIR, f"user_{u_id}.jpg")
    with open(file_path, "wb") as f:
        f.write(body_bytes)

    return Response("Success", status=200, mimetype="text/plain")

@app.route("/update_profile=<p_id>", methods=["POST"])
def update_profile(p_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT role FROM MEMBER WHERE unique_id = ?", (p_id,))
    res_role = cur.fetchone()
    r = res_role[0] if (res_role and res_role[0]) else ""

    new_name = request.form.get("name", "")

    if r == "teacher":
        new_dept = request.form.get("major", "")
        cur.execute(
            "UPDATE teacher_info SET name = ?, department = ? WHERE unique_id = ?",
            (new_name, new_dept, p_id),
        )
        cur.execute("UPDATE MEMBER SET name = ? WHERE unique_id = ?", (new_name, p_id))
        con.commit()
        return redirect("/profile")

    if r == "student":
        new_major = request.form.get("major", "")
        new_year = request.form.get("year", "")

        cur.execute("UPDATE MEMBER SET name = ? WHERE unique_id = ?", (new_name, p_id))
        cur.execute("UPDATE ACCOUNT SET name = ? WHERE unique_id = ?", (new_name, p_id))
        cur.execute(
            "UPDATE student_info SET name = ?, major = ?, year = ? WHERE unique_id = ?",
            (new_name, new_major, new_year, p_id),
        )
        for m in ["JULY", "AUGUST", "SEPTEMBER"]:
            try:
                cur.execute(
                    f"UPDATE {m}_attendance SET name = ? WHERE unique_id = ?",
                    (new_name, p_id),
                )
            except sqlite3.OperationalError:
                pass
        con.commit()
        return redirect(f"/student_profile={p_id}")

    return redirect("/profile")

@app.route("/project/profile/<filename>", methods=["GET"])
def serve_profile_image(filename):
    return send_from_directory(PROFILE_DIR, filename, mimetype="image/jpeg")

@app.route("/api/kpis", methods=["GET"])
def api_kpis():
    cur = get_db().cursor()

    cur.execute("SELECT COUNT(*) FROM MEMBER WHERE role = 'student'")
    total_students_row = cur.fetchone()
    total_students = total_students_row[0] if total_students_row else 45
    if total_students == 0:
        total_students = 45

    cur.execute("SELECT subject FROM teacher_major WHERE unique_id = ?", (g.unique_id,))
    res_subj = cur.fetchone()
    teacher_subject = (
        res_subj[0].upper() if (res_subj and res_subj[0]) else "MYANMAR"
    )

    cur.execute(
        "SELECT COUNT(*) FROM one_time_attendance WHERE subject = ?",
        (teacher_subject,),
    )
    scanned_count = cur.fetchone()[0]

    current_active = get_current_subject(cur)
    if total_students > 0 and current_active and teacher_subject == current_active:
        attendance_percentage = round((scanned_count / total_students) * 100)
    else:
        attendance_percentage = 0

    cur.execute(
        "SELECT present, percentage FROM attendance_rate WHERE subject = ? ORDER BY rowid DESC LIMIT 1",
        (teacher_subject,),
    )
    present_row = cur.fetchone()
    if present_row:
        present_count = present_row[0]
        today_attendance = present_row[1]
    else:
        present_count = 0
        today_attendance = 0

    absent_count = (
        total_students - present_count if total_students >= present_count else 0
    )

    return jsonify(
        {
            "total_students": total_students,
            "attendance_rate": attendance_percentage,
            "present_count": present_count,
            "absent_count": absent_count,
            "today_attendance": today_attendance,
        }
    )

@app.errorhandler(404)
def page_not_found(e):
    return Response("<h2>404 Page Not Found</h2>", status=404, mimetype="text/html")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, threaded=True)