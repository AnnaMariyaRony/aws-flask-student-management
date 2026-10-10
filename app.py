
import os
import uuid
from flask import Flask, request, redirect, url_for, render_template_string
from werkzeug.utils import secure_filename
import pymysql
import boto3

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

s3 = boto3.client(
    "s3",
    region_name=os.environ.get("AWS_REGION", "ap-south-1")
)
BUCKET = os.environ["S3_BUCKET"]
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

def get_db():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        autocommit=True
    )

PAGE = """
<!doctype html>
<html>
<head>
    <title>Student Management System</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial; background: #f0f4f8; margin: 0; padding: 25px; }
        main { max-width: 900px; margin: auto; }
        section { background: white; padding: 22px; border-radius: 12px; margin-bottom: 20px; }
        h1 { color: #174ea6; }
        input, button { padding: 10px; margin: 6px 0; box-sizing: border-box; }
        input { width: 100%; }
        button { background: #174ea6; color: white; border: 0; border-radius: 5px; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid #ddd; }
        .table-wrap { overflow-x: auto; }
        .photo { width: 65px; height: 65px; object-fit: cover; border-radius: 8px; }
    </style>
</head>
<body>
<main>
    <section>
        <h1>Student Management System</h1>
        <p>Manage student records and optional photos.</p>
    </section>
    <section>
        <h2>Add a Student</h2>
        <form method="post" action="/students/add" enctype="multipart/form-data">
            <label>Name</label>
            <input name="name" maxlength="100" required>
            <label>Email</label>
            <input name="email" type="email" maxlength="255" required>
            <label>Course</label>
            <input name="course" maxlength="100" required>
            <label>Optional photo (JPG, PNG, or WEBP; max 5 MB)</label>
            <input name="photo" type="file" accept=".jpg,.jpeg,.png,.webp">
            <button type="submit">Add Student</button>
        </form>
        {% if message %}<p>{{ message }}</p>{% endif %}
    </section>
    <section>
        <h2>Student Records</h2>
        <div class="table-wrap">
        <table>
            <tr><th>Photo</th><th>ID</th><th>Name</th><th>Email</th><th>Course</th><th>Action</th></tr>
            {% for student in students %}
            <tr>
                <td>{% if student.photo_url %}<img class="photo" src="{{ student.photo_url }}" alt="Student photo">{% else %}—{% endif %}</td>
                <td>{{ student.id }}</td>
                <td>{{ student.name }}</td>
                <td>{{ student.email }}</td>
                <td>{{ student.course }}</td>
                <td>
                    <form method="post" action="/students/{{ student.id }}/delete"
                          onsubmit="return confirm('Delete this student record?');">
                        <button type="submit">Delete</button>
                    </form>
                </td>
            </tr>
            {% else %}
            <tr><td colspan="6">No students added yet.</td></tr>
            {% endfor %}
        </table>
        </div>
    </section>
</main>
</body>
</html>
"""

@app.route("/")
def home():
    message = request.args.get("message", "")
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, email, course, photo_key FROM students ORDER BY id DESC"
            )
            students = cursor.fetchall()
    finally:
        connection.close()

    for student in students:
        student["photo_url"] = None
        if student["photo_key"]:
            try:
                student["photo_url"] = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": BUCKET, "Key": student["photo_key"]},
                    ExpiresIn=900
                )
            except Exception:
                pass

    return render_template_string(PAGE, students=students, message=message)

@app.route("/students/add", methods=["POST"])
def add_student():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    course = request.form.get("course", "").strip()
    photo = request.files.get("photo")
    photo_key = None

    if not name or not email or not course:
        return redirect(url_for("home", message="Please complete every required field."))

    if photo and photo.filename:
        safe_name = secure_filename(photo.filename)
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if extension not in ALLOWED_EXTENSIONS:
            return redirect(url_for("home", message="Use a JPG, PNG, or WEBP photo."))

        photo_key = f"students/{uuid.uuid4().hex}.{extension}"
        try:
            s3.upload_fileobj(
                photo,
                BUCKET,
                photo_key,
                ExtraArgs={"ContentType": photo.mimetype or "application/octet-stream"}
            )
        except Exception:
            app.logger.exception("S3 photo upload failed")
            return redirect(url_for("home", message="Photo upload failed. Check the server configuration."))

    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO students (name, email, course, photo_key) VALUES (%s, %s, %s, %s)",
                (name, email, course, photo_key)
            )
    except pymysql.err.IntegrityError:
        if photo_key:
            try:
                s3.delete_object(Bucket=BUCKET, Key=photo_key)
            except Exception:
                app.logger.exception("Could not remove unused uploaded photo")
        return redirect(url_for("home", message="That email is already registered."))
    except Exception:
        if photo_key:
            try:
                s3.delete_object(Bucket=BUCKET, Key=photo_key)
            except Exception:
                app.logger.exception("Could not remove unused uploaded photo")
        app.logger.exception("Could not save student")
        return redirect(url_for("home", message="Could not save student. Check the server terminal."))
    finally:
        connection.close()

    return redirect(url_for("home", message="Student added successfully!"))

@app.errorhandler(413)
def too_large(error):
    return "File too large. Please choose a photo smaller than 5 MB.", 413


@app.route("/students/<int:student_id>/delete", methods=["POST"])
def delete_student(student_id):
    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM students WHERE id = %s",
                (student_id,)
            )
    except Exception:
        app.logger.exception("Could not delete student")
        return redirect(url_for("home", message="Could not delete student."))
    finally:
        connection.close()

    return redirect(url_for("home", message="Student record deleted."))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
