import json
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.hash import bcrypt
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import engine, SessionLocal, Base
from models import Feedback, Message, User
from schemas import UserCreate, UserResponse

# Create all database tables on startup
Base.metadata.create_all(bind=engine)

# --- Simple migration: add new columns if they don't exist ---
# (create_all only creates new tables, it doesn't alter existing ones)
from sqlalchemy import inspect, text

inspector = inspect(engine)
if "users" in inspector.get_table_names():
    columns = [col["name"] for col in inspector.get_columns("users")]
    if "profile_picture" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN profile_picture VARCHAR"))
    if "age" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN age INTEGER"))
    if "gender" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN gender VARCHAR"))

app = FastAPI(title="Skill Exchange API")

# Secret key for signing session cookies (change this in production!)
app.add_middleware(SessionMiddleware, secret_key="skill-exchange-secret-key-change-me")

# Set up Jinja2 templates from the "templates" folder next to this file
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Serve static files (CSS, JS, images) from the "static" folder
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Folder where profile pictures are stored
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed image extensions and max file size (5 MB)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


# Dependency to get a database session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Password Helpers ----------


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt via passlib."""
    return bcrypt.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.verify(plain_password, hashed_password)
    except ValueError:
        # If the stored value isn't a valid hash, verification fails
        return False


# ---------- Profile Picture Helpers ----------


def save_profile_picture(upload: UploadFile) -> str | None:
    """Save an uploaded profile picture to static/uploads/ and return the filename.

    Returns None if no file was uploaded. Raises ValueError for invalid files.
    """
    if not upload or not upload.filename:
        return None

    # Check the file extension
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, and WEBP images are allowed.")

    # Read the file content to check size
    contents = upload.file.read()
    if len(contents) > MAX_IMAGE_SIZE:
        raise ValueError("Image file is too large. Maximum size is 5 MB.")

    # Generate a unique filename to avoid collisions
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / unique_name

    # Write the file to disk
    with open(file_path, "wb") as f:
        f.write(contents)

    return unique_name


def delete_profile_picture(filename: str | None) -> None:
    """Delete a profile picture file from static/uploads/ if it exists."""
    if not filename:
        return
    file_path = UPLOAD_DIR / filename
    if file_path.exists():
        file_path.unlink()


# ---------- Session Helpers ----------


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Return the logged-in User object, or None if not logged in."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id).first()


def are_users_matched(user1: User, user2: User) -> bool:
    """Check if two users are a two-way skill match.

    A match means:
    - user2 can teach at least one skill user1 wants to learn
    - AND user1 can teach at least one skill user2 wants to learn
    """
    user1_can_teach = set(user1.get_skills_can_teach())
    user1_wants_to_learn = set(user1.get_skills_want_to_learn())
    user2_can_teach = set(user2.get_skills_can_teach())
    user2_wants_to_learn = set(user2.get_skills_want_to_learn())

    they_teach_my_needs = bool(user1_wants_to_learn & user2_can_teach)
    they_need_my_skills = bool(user1_can_teach & user2_wants_to_learn)

    return they_teach_my_needs and they_need_my_skills


# ---------- PAGES (Jinja2 Templates) ----------


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the registration / create profile form."""
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": False,
            "new_user_id": None,
            "current_user": current_user,
        },
    )


def _parse_skills(raw: str) -> list[str]:
    """Split a comma-separated skill string, strip whitespace, and filter empties."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _validate_email(email: str) -> str | None:
    """Return an error message if the email is invalid, otherwise None."""
    if not email:
        return "Email is required."
    # Simple email regex check
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        return "Please enter a valid email address."
    return None


@app.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    skills_can_teach: str = Form(""),
    skills_want_to_learn: str = Form(""),
    bio: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    db: Session = Depends(get_db),
):
    """Handle registration form submission with server-side validation."""
    errors: dict[str, str] = {}
    form_data = {
        "name": name,
        "email": email,
        "password": password,
        "skills_can_teach": skills_can_teach,
        "skills_want_to_learn": skills_want_to_learn,
        "bio": bio,
        "age": age,
        "gender": gender,
    }

    # --- Validate age (optional, but must be a positive integer if provided) ---
    age_value = None
    if age.strip():
        try:
            age_value = int(age.strip())
            if age_value < 1 or age_value > 120:
                errors["age"] = "Age must be between 1 and 120."
        except ValueError:
            errors["age"] = "Age must be a valid number."

    # --- Validate gender (optional, must be one of the allowed values) ---
    ALLOWED_GENDERS = {"Male", "Female", "Other", "Prefer not to say"}
    gender_value = None
    if gender.strip():
        if gender not in ALLOWED_GENDERS:
            errors["gender"] = "Please choose a valid gender option."
        else:
            gender_value = gender

    # --- Validate name ---
    if not name.strip():
        errors["name"] = "Name is required."

    # --- Validate email ---
    email_error = _validate_email(email)
    if email_error:
        errors["email"] = email_error
    else:
        # Check for duplicate email (only if the format is valid)
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            errors["email"] = "This email is already registered."

    # --- Validate password ---
    if not password:
        errors["password"] = "Password is required."
    elif len(password) < 6:
        errors["password"] = "Password must be at least 6 characters long."

    # --- Validate skills can teach ---
    parsed_can_teach = _parse_skills(skills_can_teach)
    if not parsed_can_teach:
        errors["skills_can_teach"] = "Please enter at least one skill you can teach."

    # --- Validate skills want to learn ---
    parsed_want_to_learn = _parse_skills(skills_want_to_learn)
    if not parsed_want_to_learn:
        errors["skills_want_to_learn"] = "Please enter at least one skill you want to learn."

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "app_name": "Skill Exchange",
                "errors": errors,
                "form_data": form_data,
                "success": False,
                "new_user_id": None,
                "current_user": None,
            },
        )

    # --- Create the user (hash the password before saving) ---
    db_user = User(
        name=name.strip(),
        email=email.strip(),
        password=hash_password(password),
        skills_can_teach=json.dumps(parsed_can_teach),
        skills_want_to_learn=json.dumps(parsed_want_to_learn),
        bio=bio.strip(),
        age=age_value,
        gender=gender_value,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    # Auto-login the new user after registration
    request.session["user_id"] = db_user.id

    # Render success
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": True,
            "new_user_id": db_user.id,
            "current_user": db_user,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the login form."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "app_name": "Skill Exchange",
            "error": None,
            "form_data": {},
            "current_user": current_user,
        },
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    """Handle login form submission. On success, start a session and redirect."""
    form_data = {"email": email}

    # Find the user by email
    user = db.query(User).filter(User.email == email.strip()).first()

    # Generic error message so we don't reveal whether the email exists
    error = "Invalid email or password. Please try again."

    if not user or not user.password:
        # No user with that email, or the user has no password set (e.g. created via API)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "app_name": "Skill Exchange",
                "error": error,
                "form_data": form_data,
                "current_user": None,
            },
        )

    # Verify the password against the stored hash
    if not verify_password(password, user.password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "app_name": "Skill Exchange",
                "error": error,
                "form_data": form_data,
                "current_user": None,
            },
        )

    # Success: store the user ID in the session
    request.session["user_id"] = user.id

    # Redirect to the user's profile page
    return RedirectResponse(url=f"/user/{user.id}", status_code=303)


@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    """Clear the session and redirect to the home page."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, current_user: User = Depends(get_current_user)):
    """Home page - simple welcome page."""
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"app_name": "Skill Exchange", "current_user": current_user},
    )


@app.get("/users-page", response_class=HTMLResponse)
def users_page(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Page showing all users with their name, skills they teach, and skills they want to learn."""
    users = db.query(User).all()
    # Convert JSON skill strings into Python lists for the template
    user_data = [
        {
            "id": user.id,
            "name": user.name,
            "skills_can_teach": user.get_skills_can_teach(),
            "skills_want_to_learn": user.get_skills_want_to_learn(),
            "profile_picture": user.profile_picture,
        }
        for user in users
    ]
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={"app_name": "Skill Exchange", "users": user_data, "current_user": current_user},
    )


@app.get("/user/{user_id}", response_class=HTMLResponse)
def user_page(request: Request, user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Single user page - full details of one user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Determine if the logged-in user and this user are matched
    is_matched = False
    if current_user and current_user.id != user.id:
        is_matched = are_users_matched(current_user, user)

    return templates.TemplateResponse(
        request=request,
        name="user.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "is_matched": is_matched,
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "skills_can_teach": user.get_skills_can_teach(),
                "skills_want_to_learn": user.get_skills_want_to_learn(),
                "bio": user.bio,
                "age": user.age,
                "gender": user.gender,
                "profile_picture": user.profile_picture,
            },
        },
    )


@app.get("/edit-profile", response_class=HTMLResponse)
def edit_profile_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the edit profile form, pre-filled with the current user's data.

    Only accessible when logged in. If not logged in, redirect to login.
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    # Pre-fill the form with the current user's data
    form_data = {
        "name": current_user.name,
        "email": current_user.email,
        "password": "",
        "skills_can_teach": ", ".join(current_user.get_skills_can_teach()),
        "skills_want_to_learn": ", ".join(current_user.get_skills_want_to_learn()),
        "bio": current_user.bio or "",
        "age": current_user.age if current_user.age is not None else "",
        "gender": current_user.gender or "",
    }

    return templates.TemplateResponse(
        request=request,
        name="edit_profile.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": form_data,
            "success": False,
            "error": None,
            "current_user": current_user,
        },
    )


@app.post("/edit-profile", response_class=HTMLResponse)
def edit_profile_submit(
    request: Request,
    name: str = Form(""),
    password: str = Form(""),
    skills_can_teach: str = Form(""),
    skills_want_to_learn: str = Form(""),
    bio: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    profile_picture: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handle edit profile form submission.

    Updates the logged-in user's information. The email is not editable.
    The password is only updated (and re-hashed) if the user provides a new one.
    A profile picture can be uploaded (JPG, JPEG, PNG, WEBP; max 5 MB).
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    errors: dict[str, str] = {}
    form_data = {
        "name": name,
        "email": current_user.email,
        "password": password,
        "skills_can_teach": skills_can_teach,
        "skills_want_to_learn": skills_want_to_learn,
        "bio": bio,
        "age": age,
        "gender": gender,
    }

    # --- Validate age (optional, but must be a positive integer if provided) ---
    age_value = None
    if age.strip():
        try:
            age_value = int(age.strip())
            if age_value < 1 or age_value > 120:
                errors["age"] = "Age must be between 1 and 120."
        except ValueError:
            errors["age"] = "Age must be a valid number."

    # --- Validate gender (optional, must be one of the allowed values) ---
    ALLOWED_GENDERS = {"Male", "Female", "Other", "Prefer not to say"}
    gender_value = None
    if gender.strip():
        if gender not in ALLOWED_GENDERS:
            errors["gender"] = "Please choose a valid gender option."
        else:
            gender_value = gender

    # --- Validate name ---
    if not name.strip():
        errors["name"] = "Name is required."

    # --- Validate password (only if provided) ---
    if password and len(password) < 6:
        errors["password"] = "Password must be at least 6 characters long."

    # --- Validate skills can teach ---
    parsed_can_teach = _parse_skills(skills_can_teach)
    if not parsed_can_teach:
        errors["skills_can_teach"] = "Please enter at least one skill you can teach."

    # --- Validate skills want to learn ---
    parsed_want_to_learn = _parse_skills(skills_want_to_learn)
    if not parsed_want_to_learn:
        errors["skills_want_to_learn"] = "Please enter at least one skill you want to learn."

    # --- Validate profile picture (if uploaded) ---
    if profile_picture and profile_picture.filename:
        try:
            new_picture = save_profile_picture(profile_picture)
        except ValueError as e:
            errors["profile_picture"] = str(e)

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="edit_profile.html",
            context={
                "app_name": "Skill Exchange",
                "errors": errors,
                "form_data": form_data,
                "success": False,
                "error": None,
                "current_user": current_user,
            },
        )

    # --- Update the user's information ---
    current_user.name = name.strip()
    current_user.skills_can_teach = json.dumps(parsed_can_teach)
    current_user.skills_want_to_learn = json.dumps(parsed_want_to_learn)
    current_user.bio = bio.strip()
    current_user.age = age_value
    current_user.gender = gender_value

    # Only hash and update the password if the user provided a new one
    if password:
        current_user.password = hash_password(password)

    # Update the profile picture if a new one was uploaded
    if profile_picture and profile_picture.filename:
        # Delete the old picture file if it exists
        delete_profile_picture(current_user.profile_picture)
        current_user.profile_picture = new_picture

    db.commit()

    # Render success (the template redirects to the profile page after 1.5s)
    return templates.TemplateResponse(
        request=request,
        name="edit_profile.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": form_data,
            "success": True,
            "error": None,
            "current_user": current_user,
        },
    )


@app.get("/chat/{user_id}", response_class=HTMLResponse)
def chat_page(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Chat page between the logged-in user and another user.

    Only accessible when logged in. If not logged in, redirect to login.
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    # Cannot chat with yourself
    if current_user.id == user_id:
        return RedirectResponse(url=f"/user/{user_id}", status_code=303)

    # The other user must exist
    other_user = db.query(User).filter(User.id == user_id).first()
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Only allow chatting with users you have matched with
    if not are_users_matched(current_user, other_user):
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context={
                "app_name": "Skill Exchange",
                "current_user": current_user,
                "other_user": {
                    "id": other_user.id,
                    "name": other_user.name,
                    "profile_picture": other_user.profile_picture,
                },
                "messages": [],
                "not_matched": True,
            },
        )

    # Fetch all messages between the two users, oldest to newest
    messages = (
        db.query(Message)
        .filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == user_id))
            | ((Message.sender_id == user_id) & (Message.receiver_id == current_user.id))
        )
        .order_by(Message.timestamp.asc(), Message.id.asc())
        .all()
    )

    # Build a list of dicts for the template, with a flag for "sent by me"
    message_data = [
        {
            "id": msg.id,
            "sender_id": msg.sender_id,
            "receiver_id": msg.receiver_id,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "is_mine": msg.sender_id == current_user.id,
        }
        for msg in messages
    ]

    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "other_user": {
                "id": other_user.id,
                "name": other_user.name,
                "profile_picture": other_user.profile_picture,
            },
            "messages": message_data,
            "not_matched": False,
        },
    )


@app.post("/chat/{user_id}", response_class=HTMLResponse)
def chat_send(
    request: Request,
    user_id: int,
    content: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a new message to another user.

    Only accessible when logged in. If not logged in, redirect to login.
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    # Cannot chat with yourself
    if current_user.id == user_id:
        return RedirectResponse(url=f"/user/{user_id}", status_code=303)

    # The other user must exist
    other_user = db.query(User).filter(User.id == user_id).first()
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Only allow sending messages to users you have matched with
    if not are_users_matched(current_user, other_user):
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context={
                "app_name": "Skill Exchange",
                "current_user": current_user,
                "other_user": {
                    "id": other_user.id,
                    "name": other_user.name,
                    "profile_picture": other_user.profile_picture,
                },
                "messages": [],
                "not_matched": True,
            },
        )

    # Only save the message if there is actual content
    if content.strip():
        db_message = Message(
            sender_id=current_user.id,
            receiver_id=user_id,
            content=content.strip(),
        )
        db.add(db_message)
        db.commit()

    # Redirect back to the chat page (GET) so the new message appears
    return RedirectResponse(url=f"/chat/{user_id}", status_code=303)


@app.get("/matches", response_class=HTMLResponse)
def matches_page(
    request: Request,
    user_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Page to find skill-exchange matches for a given user.

    If the user is logged in, their ID is used automatically and they don't
    need to type it. If they are not logged in, they can still enter an ID
    manually.
    """
    context = {
        "app_name": "Skill Exchange",
        "requested_id": user_id,
        "error": None,
        "matches": None,
        "current_user": current_user,
    }

    # If logged in, always use the logged-in user's ID (no manual entry needed)
    effective_user_id = current_user.id if current_user else user_id

    if effective_user_id is not None:
        user = db.query(User).filter(User.id == effective_user_id).first()
        if not user:
            context["error"] = f"No user found with ID {effective_user_id}. Please check the ID and try again."
        else:
            # Logic mirroring /match/{user_id}: both directions of skill exchange must match
            user_can_teach = set(user.get_skills_can_teach())
            user_wants_to_learn = set(user.get_skills_want_to_learn())

            matches = []
            for other in db.query(User).filter(User.id != effective_user_id).all():
                other_can_teach = set(other.get_skills_can_teach())
                other_wants_to_learn = set(other.get_skills_want_to_learn())

                they_teach_my_needs = bool(user_wants_to_learn & other_can_teach)
                they_need_my_skills = bool(user_can_teach & other_wants_to_learn)

                if they_teach_my_needs and they_need_my_skills:
                    # Compute the actual overlapping skills for this match
                    matched_they_teach_me = sorted(user_wants_to_learn & other_can_teach)
                    matched_i_teach_them = sorted(user_can_teach & other_wants_to_learn)

                    matches.append(
                        {
                            "id": other.id,
                            "name": other.name,
                            "skills_can_teach": other.get_skills_can_teach(),
                            "skills_want_to_learn": other.get_skills_want_to_learn(),
                            "bio": other.bio,
                            "profile_picture": other.profile_picture,
                            # New: the actual skills that matched between us
                            "matched_they_teach_me": matched_they_teach_me,
                            "matched_i_teach_them": matched_i_teach_them,
                        }
                    )
            context["matches"] = matches

    return templates.TemplateResponse(request=request, name="matches.html", context=context)


# ---------- FEEDBACK ROUTES ----------


@app.get("/feedback", response_class=HTMLResponse)
def feedback_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the feedback form.

    Users can submit feedback with an optional name and a required message.
    If logged in, the user's ID is automatically linked to the feedback.
    """
    return templates.TemplateResponse(
        request=request,
        name="feedback.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": False,
            "current_user": current_user,
        },
    )


@app.post("/feedback", response_class=HTMLResponse)
def feedback_submit(
    request: Request,
    name: str = Form(""),
    message: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handle feedback form submission.

    Saves the feedback to the database. The name is optional; if the user is
    logged in, their user_id is stored as well. The message is required.
    """
    errors: dict[str, str] = {}
    form_data = {"name": name, "message": message}

    # --- Validate message (required) ---
    if not message.strip():
        errors["message"] = "Message is required."

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="feedback.html",
            context={
                "app_name": "Skill Exchange",
                "errors": errors,
                "form_data": form_data,
                "success": False,
                "current_user": current_user,
            },
        )

    # --- Save the feedback ---
    # If logged in, link the feedback to the user and use their name as default
    feedback_name = name.strip() or (current_user.name if current_user else "")
    db_feedback = Feedback(
        user_id=current_user.id if current_user else None,
        name=feedback_name or None,
        message=message.strip(),
    )
    db.add(db_feedback)
    db.commit()

    # Render success
    return templates.TemplateResponse(
        request=request,
        name="feedback.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": True,
            "current_user": current_user,
        },
    )


@app.get("/admin/feedbacks", response_class=HTMLResponse)
def admin_feedbacks(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin page showing all submitted feedback.

    For now, this page has no password protection. It will be improved later.
    """
    feedbacks = db.query(Feedback).order_by(Feedback.created_at.desc(), Feedback.id.desc()).all()

    # Build a list of dicts for the template
    feedback_data = [
        {
            "id": fb.id,
            "user_id": fb.user_id,
            "name": fb.name,
            "message": fb.message,
            "created_at": fb.created_at,
        }
        for fb in feedbacks
    ]

    return templates.TemplateResponse(
        request=request,
        name="admin_feedbacks.html",
        context={
            "app_name": "Skill Exchange",
            "feedbacks": feedback_data,
            "current_user": current_user,
        },
    )


# ---------- API ROUTES ----------

@app.post("/users/", response_model=UserResponse)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """Create a new user."""
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Convert skill lists to JSON strings for SQLite storage
    # If a password was provided, hash it before storing
    db_user = User(
        name=user.name,
        email=user.email,
        password=hash_password(user.password) if user.password else None,
        skills_can_teach=json.dumps(user.skills_can_teach),
        skills_want_to_learn=json.dumps(user.skills_want_to_learn),
        bio=user.bio,
        age=user.age,
        gender=user.gender,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@app.get("/users/", response_model=list[UserResponse])
def get_users(db: Session = Depends(get_db)):
    """Get all users."""
    users = db.query(User).all()
    return users


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Get a single user by ID."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/match/{user_id}", response_model=list[UserResponse])
def get_matches(user_id: int, db: Session = Depends(get_db)):
    """Find users who are a good match for the given user.

    A good match means:
    - The other user can teach at least one skill I want to learn
    - AND I can teach at least one skill the other user wants to learn
    """
    # The user we are trying to find matches for
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # The requesting user's skills, as sets for easy intersection checks
    user_can_teach = set(user.get_skills_can_teach())
    user_wants_to_learn = set(user.get_skills_want_to_learn())

    matches = []

    # Check every other user in the system
    for other in db.query(User).filter(User.id != user_id).all():
        other_can_teach = set(other.get_skills_can_teach())
        other_wants_to_learn = set(other.get_skills_want_to_learn())

        # Condition 1: the other user teaches something I want to learn
        they_teach_my_needs = bool(user_wants_to_learn & other_can_teach)

        # Condition 2: I teach something the other user wants to learn
        they_need_my_skills = bool(user_can_teach & other_wants_to_learn)

        # Both conditions must hold for a good two-way match
        if they_teach_my_needs and they_need_my_skills:
            matches.append(other)

    return matches