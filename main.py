import json
import os
import re
import uuid
from pathlib import Path

import cloudinary
import cloudinary.uploader
from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.hash import bcrypt
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from database import engine, SessionLocal, Base
from models import Feedback, Listing, Message, Rating, Report, User
from schemas import UserCreate, UserResponse

# Create all database tables on startup
Base.metadata.create_all(bind=engine)

# --- Simple migration: add new columns if they don't exist ---
# (create_all only creates new tables, it doesn't alter existing ones)
from sqlalchemy import inspect, text

inspector = inspect(engine)
if "users" in inspector.get_table_names():
    columns = [col["name"] for col in inspector.get_columns("users")]
    if "password" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN password VARCHAR"))
    if "profile_picture" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN profile_picture VARCHAR"))
    if "age" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN age INTEGER"))
    if "gender" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN gender VARCHAR"))
    if "is_suspended" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_suspended BOOLEAN DEFAULT FALSE"))
    if "country" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN country VARCHAR"))
    if "language" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN language VARCHAR"))

# --- PostgreSQL-safe migration for the listings table ---
# create_all creates the table if it doesn't exist. If it already exists
# (e.g. from a previous run), we add any missing columns here.
inspector = inspect(engine)
if "listings" in inspector.get_table_names():
    columns = [col["name"] for col in inspector.get_columns("listings")]
    if "price_amount" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE listings ADD COLUMN price_amount VARCHAR"))
    if "is_active" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE listings ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
    if "thumbnail" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE listings ADD COLUMN thumbnail VARCHAR"))

app = FastAPI(title="Skill Exchange API")

# Secret key for signing session cookies (change this in production!)
app.add_middleware(SessionMiddleware, secret_key="skill-exchange-secret-key-change-me")

# Set up Jinja2 templates from the "templates" folder next to this file
BASE_DIR = Path(__file__).resolve().parent

# ---------- Cloudinary Configuration ----------
# Cloudinary credentials come from environment variables:
#   CLOUDINARY_CLOUD_NAME
#   CLOUDINARY_API_KEY
#   CLOUDINARY_API_SECRET
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True,
)


def sidebar_context(request: Request) -> dict:
    """Add sidebar data (matches and recent chats) to every template context.

    This runs for every page render. When the user is logged in, it computes:
      - sidebar_matches: users the current user has matched with
      - sidebar_chats:   users the current user has exchanged messages with
    When not logged in, both lists are empty (the template shows a login prompt).
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        return {"sidebar_matches": [], "sidebar_chats": []}

    db = SessionLocal()
    try:
        current_user = db.query(User).filter(User.id == user_id).first()
        if not current_user:
            return {"sidebar_matches": [], "sidebar_chats": []}

        # --- My Matches: users the current user has matched with ---
        matches = []
        for other in db.query(User).filter(User.id != current_user.id).all():
            if are_users_matched(current_user, other):
                matches.append(
                    {
                        "id": other.id,
                        "name": other.name,
                        "profile_picture": other.profile_picture,
                    }
                )

        # --- Recent Chats: users the current user has exchanged messages with ---
        # Fetch all messages involving the current user, newest first
        messages = (
            db.query(Message)
            .filter(
                (Message.sender_id == current_user.id)
                | (Message.receiver_id == current_user.id)
            )
            .order_by(Message.timestamp.desc(), Message.id.desc())
            .all()
        )

        # Keep only the most recent message per other user (dict keeps first-inserted order)
        last_message_by_user = {}
        for msg in messages:
            other_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
            if other_id not in last_message_by_user:
                last_message_by_user[other_id] = msg.timestamp

        chats = []
        for other_id, last_ts in last_message_by_user.items():
            other = db.query(User).filter(User.id == other_id).first()
            if other:
                chats.append(
                    {
                        "id": other.id,
                        "name": other.name,
                        "profile_picture": other.profile_picture,
                        "last_message_time": last_ts,
                    }
                )

        return {
            "sidebar_matches": matches,
            "sidebar_chats": chats,
        }
    finally:
        db.close()


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates"),
    context_processors=[sidebar_context],
)

# Serve static files (CSS, JS, images) from the "static" folder
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Allowed image extensions and max file size (5 MB)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
# Max file size for listing thumbnails (2 MB)
MAX_THUMBNAIL_SIZE = 2 * 1024 * 1024  # 2 MB


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
    """Upload a profile picture to Cloudinary and return its secure URL.

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

    # Generate a unique public ID to avoid collisions
    public_id = f"profile_pictures/{uuid.uuid4().hex}"

    # Upload to Cloudinary
    result = cloudinary.uploader.upload(
        contents,
        public_id=public_id,
        folder="profile_pictures",
        overwrite=True,
        resource_type="image",
    )

    # Return the secure Cloudinary URL
    return result.get("secure_url")


def delete_profile_picture(url_or_filename: str | None) -> None:
    """Delete a profile picture from Cloudinary if it was uploaded there.

    Also handles legacy local filenames (from before Cloudinary migration)
    by attempting to remove the file from static/uploads/ if it exists.
    """
    if not url_or_filename:
        return

    # If it's a Cloudinary URL, extract the public ID and delete from Cloudinary
    if url_or_filename.startswith("http"):
        # Extract the public ID from the URL (e.g. .../profile_pictures/abc123.png)
        # Cloudinary URLs look like: https://res.cloudinary.com/<cloud>/image/upload/v123/profile_pictures/abc123.png
        try:
            # Split on "/upload/" and take the part after the version
            parts = url_or_filename.split("/upload/")
            if len(parts) == 2:
                # Remove the version segment (e.g. "v1234567890/")
                path_part = parts[1]
                segments = path_part.split("/")
                if segments and segments[0].startswith("v"):
                    segments = segments[1:]
                # Remove the file extension for the public ID
                public_id = "/".join(segments)
                if public_id.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    public_id = public_id.rsplit(".", 1)[0]
                if public_id:
                    cloudinary.uploader.destroy(public_id)
        except Exception:
            # If Cloudinary deletion fails, don't crash the request
            pass
        return

    # Legacy: try to delete a local file from static/uploads/ if it exists
    file_path = BASE_DIR / "static" / "uploads" / url_or_filename
    if file_path.exists():
        file_path.unlink()


# ---------- Listing Thumbnail Helpers ----------


def save_listing_thumbnail(upload: UploadFile) -> str | None:
    """Upload a listing thumbnail to Cloudinary and return its secure URL.

    Returns None if no file was uploaded. Raises ValueError for invalid files.
    Accepts JPG, JPEG, PNG, WEBP. Max file size is 2 MB.
    """
    if not upload or not upload.filename:
        return None

    # Check the file extension
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, and WEBP images are allowed.")

    # Read the file content to check size
    contents = upload.file.read()
    if len(contents) > MAX_THUMBNAIL_SIZE:
        raise ValueError("Image file is too large. Maximum size is 2 MB.")

    # Generate a unique public ID to avoid collisions
    public_id = f"listing_thumbnails/{uuid.uuid4().hex}"

    # Upload to Cloudinary
    result = cloudinary.uploader.upload(
        contents,
        public_id=public_id,
        folder="listing_thumbnails",
        overwrite=True,
        resource_type="image",
    )

    # Return the secure Cloudinary URL
    return result.get("secure_url")


def delete_listing_thumbnail(url_or_filename: str | None) -> None:
    """Delete a listing thumbnail from Cloudinary if it was uploaded there.

    Reuses the same Cloudinary URL parsing logic as profile pictures since
    listing thumbnails are stored in the same format (Cloudinary secure URL).
    """
    delete_profile_picture(url_or_filename)


# ---------- Session Helpers ----------


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Return the logged-in User object, or None if not logged in.

    Suspended users are treated as logged out: their session is cleared
    and they are returned as None so they cannot access protected pages.
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.is_suspended:
        # Suspended users lose access immediately
        request.session.pop("user_id", None)
        return None
    return user


# ---------- Admin Helpers ----------

# Admin password comes from the environment variable ADMIN_PASSWORD.
# If it is not set, fall back to a default for local development.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def require_admin(request: Request):
    """Dependency that protects admin routes.

    If the session does not have is_admin=True, raise an HTTPException that
    redirects to the admin login page. FastAPI dependencies cannot return
    responses directly, so we use an HTTPException with a Location header.
    """
    if not request.session.get("is_admin"):
        raise HTTPException(
            status_code=303,
            detail="Admin login required",
            headers={"Location": "/admin/login"},
        )
    return True


def _normalize_skill_list(skills: list[str]) -> set[str]:
    """Normalize a list of skills to a set for case-insensitive comparison."""
    return {normalize_skill(s) for s in skills}


def are_users_matched(user1: User, user2: User) -> bool:
    """Check if two users are a two-way skill match.

    A match means:
    - user2 can teach at least one skill user1 wants to learn
    - AND user1 can teach at least one skill user2 wants to learn

    Matching is case-insensitive: skills are normalized before comparison.
    """
    user1_can_teach = _normalize_skill_list(user1.get_skills_can_teach())
    user1_wants_to_learn = _normalize_skill_list(user1.get_skills_want_to_learn())
    user2_can_teach = _normalize_skill_list(user2.get_skills_can_teach())
    user2_wants_to_learn = _normalize_skill_list(user2.get_skills_want_to_learn())

    they_teach_my_needs = bool(user1_wants_to_learn & user2_can_teach)
    they_need_my_skills = bool(user1_can_teach & user2_wants_to_learn)

    return they_teach_my_needs and they_need_my_skills


# ---------- Match Score & Filter Helpers ----------

# Country filter options for the Matches page (order matches the registration dropdown)
MATCH_COUNTRY_OPTIONS = [
    "India", "Bangladesh", "Pakistan", "Nepal", "Sri Lanka",
    "USA", "UK", "Canada", "Other",
]

# Language filter options for the Matches page (order matches the registration dropdown)
MATCH_LANGUAGE_OPTIONS = [
    "English", "Hindi", "Bengali", "Urdu", "Tamil", "Telugu",
    "Marathi", "Gujarati", "Punjabi", "Other",
]

# Gender options for the Matches page filter (blank value means "Any")
GENDER_FILTER_OPTIONS = ["Male", "Female"]

# Age-range options for the Matches page filter
AGE_FILTER_OPTIONS = [
    {"value": "", "label": "Any"},
    {"value": "under_18", "label": "Under 18"},
    {"value": "18_25", "label": "18 - 25"},
    {"value": "26_35", "label": "26 - 35"},
    {"value": "36_plus", "label": "36 +"},
]


def average_rating_stats(db: Session, user_id: int) -> tuple[float | None, int]:
    """Return (average_rating, rating_count) for a user, or (None, 0) if unrated."""
    ratings = db.query(Rating).filter(Rating.to_user_id == user_id).all()
    if not ratings:
        return None, 0
    average = round(sum(r.score for r in ratings) / len(ratings), 1)
    return average, len(ratings)


def compute_match_score(
    current_user: User,
    other_user: User,
    average_rating: float | None,
) -> int:
    """Compute a 0-100 match score between two already-matched users.

    The score combines three components:

    1. Skill swap quality (0-50, highest weight). Measures how much of the
       current user's "want to learn" list the other user can teach (60% of
       the skill component) and how much of the other user's "want to learn"
       list the current user can teach (40%). Two-way skill matching is
       already guaranteed by the caller, so both coverages are > 0.

    2. Average rating (0-30). The other user's average rating out of 5,
       scaled to 30 points. Users with no ratings get a neutral default of
       3.5/5 so discovery isn't unfairly penalized.

    3. Basic preference fit (0-20). Age (within 10 years), Gender, Country,
       and Language each worth up to 5 points - but only when *both* users
       have that attribute stored. Missing data is skipped without penalty.
    """
    my_can_teach = _normalize_skill_list(current_user.get_skills_can_teach())
    my_wants_to_learn = _normalize_skill_list(current_user.get_skills_want_to_learn())
    their_can_teach = _normalize_skill_list(other_user.get_skills_can_teach())
    their_wants_to_learn = _normalize_skill_list(other_user.get_skills_want_to_learn())

    # --- 1. Skill swap quality (max 50) ---
    they_teach_me_count = len(my_wants_to_learn & their_can_teach)
    i_teach_them_count = len(my_can_teach & their_wants_to_learn)

    my_needs_total = max(len(my_wants_to_learn), 1)
    their_needs_total = max(len(their_wants_to_learn), 1)

    # Fraction of my needs they can cover (capped at 1)
    they_cover = min(1.0, they_teach_me_count / my_needs_total)
    # Fraction of their needs I can cover (capped at 1)
    i_cover = min(1.0, i_teach_them_count / their_needs_total)

    skill_score = 50.0 * ((0.6 * they_cover) + (0.4 * i_cover))

    # --- 2. Average rating (max 30) ---
    if average_rating is None:
        rating_score = 30.0 * (3.5 / 5.0)
    else:
        rating_score = 30.0 * (average_rating / 5.0)

    # --- 3. Basic preference fit (max 20) ---
    pref_score = 0.0

    # Age: both have it and within 10 years
    if current_user.age is not None and other_user.age is not None:
        if abs(current_user.age - other_user.age) <= 10:
            pref_score += 5.0

    # Gender: both have it and it matches
    if current_user.gender and other_user.gender:
        if current_user.gender.strip().lower() == other_user.gender.strip().lower():
            pref_score += 5.0

    # Country: both have it and it matches
    if current_user.country and other_user.country:
        if current_user.country.strip().lower() == other_user.country.strip().lower():
            pref_score += 5.0

    # Language: both have it and it matches
    if current_user.language and other_user.language:
        if current_user.language.strip().lower() == other_user.language.strip().lower():
            pref_score += 5.0

    total = skill_score + rating_score + pref_score
    return int(round(min(100.0, max(0.0, total))))


def matches_basic_filters(
    other: User,
    gender: str = "",
    country: str = "",
    language: str = "",
    age: str = "",
) -> bool:
    """Return True if `other` passes every active basic filter.

    Filters are applied with AND semantics *after* the existing two-way
    skill matching, so a user must be a real skill match AND satisfy all
    active filters. Empty strings and "Any" disable the corresponding filter.
    """
    # Gender: Any / Male / Female
    if gender and gender != "Any":
        if not other.gender or other.gender.strip().lower() != gender.strip().lower():
            return False

    # Country: Any / existing country options
    if country and country != "Any":
        if not other.country or other.country != country:
            return False

    # Language: Any / existing language options
    if language and language != "Any":
        if not other.language or other.language != language:
            return False

    # Age range: Any / under 18 / 18-25 / 26-35 / 36+
    if age and age != "Any":
        if other.age is None:
            return False
        if age == "under_18" and not (other.age < 18):
            return False
        if age == "18_25" and not (18 <= other.age <= 25):
            return False
        if age == "26_35" and not (26 <= other.age <= 35):
            return False
        if age == "36_plus" and not (other.age > 35):
            return False

    return True


# ---------- ADMIN ROUTES ----------


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    """Show the admin login form.

    If already logged in as admin, redirect straight to the dashboard.
    """
    if request.session.get("is_admin"):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"app_name": "Skill Exchange", "error": None},
    )


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login_submit(request: Request, password: str = Form("")):
    """Handle admin login form submission.

    Compares the submitted password against the ADMIN_PASSWORD environment
    variable. On success, stores is_admin=True in the session.
    """
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={
            "app_name": "Skill Exchange",
            "error": "Incorrect password. Please try again.",
        },
    )


@app.get("/admin/logout", response_class=HTMLResponse)
def admin_logout(request: Request):
    """Log out of the admin panel and return to the admin login page."""
    request.session.pop("is_admin", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin dashboard combining feedbacks, reports, and user management.

    Shows summary counts, recent feedbacks, recent reports, and a full list
    of users with suspend/delete actions.
    """
    # --- Summary counts ---
    total_users = db.query(User).count()
    total_feedbacks = db.query(Feedback).count()
    total_reports = db.query(Report).count()
    total_listings = db.query(Listing).count()
    active_listings = db.query(Listing).filter(Listing.is_active == True).count()

    # --- Recent feedbacks (latest 10) ---
    recent_feedbacks = (
        db.query(Feedback)
        .order_by(Feedback.created_at.desc(), Feedback.id.desc())
        .limit(10)
        .all()
    )
    feedback_data = [
        {
            "id": fb.id,
            "user_id": fb.user_id,
            "name": fb.name,
            "message": fb.message,
            "created_at": fb.created_at,
        }
        for fb in recent_feedbacks
    ]

    # --- Recent reports (latest 10) ---
    recent_reports = (
        db.query(Report)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(10)
        .all()
    )
    report_data = []
    for rep in recent_reports:
        reporter_name = None
        if rep.reporter_id:
            reporter = db.query(User).filter(User.id == rep.reporter_id).first()
            reporter_name = reporter.name if reporter else None

        target_name = None
        if rep.target_user_id:
            target = db.query(User).filter(User.id == rep.target_user_id).first()
            target_name = target.name if target else None

        report_data.append(
            {
                "id": rep.id,
                "reporter_id": rep.reporter_id,
                "reporter_name": reporter_name,
                "report_type": rep.report_type,
                "report_type_label": REPORT_TYPE_LABELS.get(rep.report_type, rep.report_type),
                "target_user_id": rep.target_user_id,
                "target_name": target_name,
                "message": rep.message,
                "created_at": rep.created_at,
            }
        )

    # --- All users with management info ---
    users = db.query(User).order_by(User.id.asc()).all()
    user_data = [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "age": user.age,
            "gender": user.gender,
            "skills_count": len(user.get_skills_can_teach()) + len(user.get_skills_want_to_learn()),
            "is_suspended": user.is_suspended,
        }
        for user in users
    ]

    # --- All marketplace listings with owner info ---
    listings = db.query(Listing).order_by(Listing.created_at.desc(), Listing.id.desc()).all()
    listing_data = []
    for listing in listings:
        owner = db.query(User).filter(User.id == listing.user_id).first()
        listing_data.append(
            {
                "id": listing.id,
                "title": listing.title,
                "skill": listing.skill,
                "listing_type": listing.listing_type,
                "price_type": listing.price_type,
                "price_amount": listing.price_amount,
                "is_active": listing.is_active,
                "created_at": listing.created_at,
                "owner": {
                    "id": owner.id if owner else None,
                    "name": owner.name if owner else "Unknown",
                },
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "app_name": "Skill Exchange",
            "total_users": total_users,
            "total_feedbacks": total_feedbacks,
            "total_reports": total_reports,
            "total_listings": total_listings,
            "active_listings": active_listings,
            "feedbacks": feedback_data,
            "reports": report_data,
            "users": user_data,
            "listings": listing_data,
        },
    )


@app.post("/admin/users/{user_id}/suspend", response_class=HTMLResponse)
def admin_suspend_user(
    user_id: int,
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Suspend a user so they can no longer log in."""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_suspended = True
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/unsuspend", response_class=HTMLResponse)
def admin_unsuspend_user(
    user_id: int,
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reinstate a suspended user so they can log in again."""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_suspended = False
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def admin_delete_user(
    user_id: int,
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a user and all their related data.

    Removes the user's messages, ratings, and profile picture. Feedback and
    reports that reference the user are unlinked (set to NULL) rather than
    deleted, so the moderation history is preserved.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        # Delete messages involving this user
        db.query(Message).filter(
            (Message.sender_id == user_id) | (Message.receiver_id == user_id)
        ).delete(synchronize_session=False)

        # Delete ratings involving this user
        db.query(Rating).filter(
            (Rating.from_user_id == user_id) | (Rating.to_user_id == user_id)
        ).delete(synchronize_session=False)

        # Delete marketplace listings owned by this user
        db.query(Listing).filter(Listing.user_id == user_id).delete(
            synchronize_session=False
        )

        # Unlink feedback and reports from this user (keep the records)
        db.query(Feedback).filter(Feedback.user_id == user_id).update(
            {"user_id": None}, synchronize_session=False
        )
        db.query(Report).filter(Report.reporter_id == user_id).update(
            {"reporter_id": None}, synchronize_session=False
        )
        db.query(Report).filter(Report.target_user_id == user_id).update(
            {"target_user_id": None}, synchronize_session=False
        )

        # Delete the profile picture file if it exists
        delete_profile_picture(user.profile_picture)

        # Finally delete the user
        db.delete(user)
        db.commit()

    return RedirectResponse(url="/admin", status_code=303)


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


# ---------- Skill Normalization & Validation ----------


def normalize_skill(skill: str) -> str:
    """Normalize a single skill string.

    - Strips extra spaces
    - Converts to Title Case
    - Example: "  business STUDIES " → "Business Studies"
    """
    return " ".join(skill.strip().split()).title()


def normalize_skills(raw: str) -> list[str]:
    """Split a comma-separated skill string, normalize each skill, and remove empties.

    Example: "  python, PYTHON,  business STUDIES " → ["Python", "Python", "Business Studies"]
    """
    return [normalize_skill(s) for s in raw.split(",") if s.strip()]


def validate_skills(
    skills: list[str],
    field_name: str,
    errors: dict[str, str],
    other_skills: list[str] | None = None,
) -> list[str]:
    """Validate a list of normalized skills and add error messages to `errors`.

    Rules:
    - Max 10 skills per section
    - Each skill must be 2 to 30 characters
    - Allowed characters: letters, spaces, +, #
    - No duplicate skills inside the same section
    - A skill cannot exist in both "can teach" and "want to learn" (checked via other_skills)

    Returns the deduplicated, validated skill list (or the original list if invalid).
    """
    if not skills:
        errors[field_name] = "Please enter at least one skill."
        return skills

    # Max 10 skills per section
    if len(skills) > 10:
        errors[field_name] = "You can enter at most 10 skills."
        return skills

    # Allowed characters: letters, spaces, +, #
    allowed_pattern = re.compile(r"^[A-Za-z +#]+$")

    # Check each skill
    seen = set()
    for skill in skills:
        # Length check: 2 to 30 characters
        if len(skill) < 2 or len(skill) > 30:
            errors[field_name] = f'Skill "{skill}" must be between 2 and 30 characters.'
            return skills

        # Allowed characters check
        if not allowed_pattern.match(skill):
            errors[field_name] = (
                f'Skill "{skill}" contains invalid characters. '
                "Only letters, spaces, +, and # are allowed."
            )
            return skills

        # Duplicate check within the same section (case-insensitive via normalization)
        if skill in seen:
            errors[field_name] = f'Duplicate skill "{skill}" is not allowed.'
            return skills
        seen.add(skill)

    # Cross-section check: a skill cannot exist in both lists
    if other_skills is not None:
        for skill in skills:
            if skill in other_skills:
                errors[field_name] = (
                    f'Skill "{skill}" cannot be in both "Skills I Can Teach" '
                    'and "Skills I Want to Learn".'
                )
                return skills

    return list(seen)


def _validate_email(email: str) -> str | None:
    """Return an error message if the email is invalid, otherwise None."""
    if not email:
        return "Email is required."
    # Simple email regex check
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, email):
        return "Please enter a valid email address."
    return None


# ---------- Country & Language Dropdown Options ----------

# Allowed country options (must match the dropdown in the templates)
ALLOWED_COUNTRIES = {
    "India", "Bangladesh", "Pakistan", "Nepal", "Sri Lanka",
    "USA", "UK", "Canada", "Other",
}

# Allowed language options (must match the dropdown in the templates)
ALLOWED_LANGUAGES = {
    "English", "Hindi", "Bengali", "Urdu", "Tamil", "Telugu",
    "Marathi", "Gujarati", "Punjabi", "Other",
}


def validate_country(country: str, errors: dict[str, str]) -> str | None:
    """Validate a country value against the allowed list.

    Returns the validated country string, or None if empty/invalid.
    Adds an error message to `errors` if the value is not in the allowed list.
    """
    if not country.strip():
        return None
    if country not in ALLOWED_COUNTRIES:
        errors["country"] = "Please choose a valid country option."
        return None
    return country


def validate_language(language: str, errors: dict[str, str]) -> str | None:
    """Validate a language value against the allowed list.

    Returns the validated language string, or None if empty/invalid.
    Adds an error message to `errors` if the value is not in the allowed list.
    """
    if not language.strip():
        return None
    if language not in ALLOWED_LANGUAGES:
        errors["language"] = "Please choose a valid language option."
        return None
    return language


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
    country: str = Form(""),
    language: str = Form(""),
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
        "country": country,
        "language": language,
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

    # --- Validate country (optional, must be from the allowed dropdown list) ---
    country_value = validate_country(country, errors)

    # --- Validate language (optional, must be from the allowed dropdown list) ---
    language_value = validate_language(language, errors)

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
    parsed_can_teach = normalize_skills(skills_can_teach)
    parsed_can_teach = validate_skills(parsed_can_teach, "skills_can_teach", errors)

    # --- Validate skills want to learn ---
    parsed_want_to_learn = normalize_skills(skills_want_to_learn)
    parsed_want_to_learn = validate_skills(
        parsed_want_to_learn,
        "skills_want_to_learn",
        errors,
        other_skills=parsed_can_teach,
    )

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
        country=country_value,
        language=language_value,
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

    # Suspended users cannot log in
    if user.is_suspended:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "app_name": "Skill Exchange",
                "error": "Your account has been suspended. Please contact support.",
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

    # --- Rating data ---
    # All ratings this user has received
    ratings = db.query(Rating).filter(Rating.to_user_id == user.id).all()
    rating_count = len(ratings)
    average_rating = round(sum(r.score for r in ratings) / rating_count, 1) if rating_count > 0 else None

    # If the logged-in user is viewing someone else's profile, check if they already rated
    existing_rating = None
    if current_user and current_user.id != user.id:
        existing_rating = (
            db.query(Rating)
            .filter(Rating.from_user_id == current_user.id, Rating.to_user_id == user.id)
            .first()
        )

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
                "country": user.country,
                "language": user.language,
                "profile_picture": user.profile_picture,
            },
            "rating_count": rating_count,
            "average_rating": average_rating,
            "existing_rating": existing_rating,
        },
    )


@app.post("/user/{user_id}/rate", response_class=HTMLResponse)
def rate_user(
    request: Request,
    user_id: int,
    score: int = Form(...),
    review: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a rating for another user.

    Rules:
    - Must be logged in
    - Cannot rate yourself
    - Score must be between 1 and 5
    - A user can only rate another user once
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    # Cannot rate yourself
    if current_user.id == user_id:
        return RedirectResponse(url=f"/user/{user_id}", status_code=303)

    # The rated user must exist
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate score is between 1 and 5
    if score < 1 or score > 5:
        return RedirectResponse(url=f"/user/{user_id}", status_code=303)

    # Check if the current user has already rated this user
    existing = (
        db.query(Rating)
        .filter(Rating.from_user_id == current_user.id, Rating.to_user_id == user_id)
        .first()
    )
    if existing:
        # Already rated - redirect back to the profile page
        return RedirectResponse(url=f"/user/{user_id}", status_code=303)

    # Save the new rating
    db_rating = Rating(
        from_user_id=current_user.id,
        to_user_id=user_id,
        score=score,
        review=review.strip() or None,
    )
    db.add(db_rating)
    db.commit()

    # Redirect back to the profile page so the new rating is visible
    return RedirectResponse(url=f"/user/{user_id}", status_code=303)


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
        "country": current_user.country or "",
        "language": current_user.language or "",
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
    country: str = Form(""),
    language: str = Form(""),
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
        "country": country,
        "language": language,
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

    # --- Validate country (optional, must be from the allowed dropdown list) ---
    country_value = validate_country(country, errors)

    # --- Validate language (optional, must be from the allowed dropdown list) ---
    language_value = validate_language(language, errors)

    # --- Validate name ---
    if not name.strip():
        errors["name"] = "Name is required."

    # --- Validate password (only if provided) ---
    if password and len(password) < 6:
        errors["password"] = "Password must be at least 6 characters long."

    # --- Validate skills can teach ---
    parsed_can_teach = normalize_skills(skills_can_teach)
    parsed_can_teach = validate_skills(parsed_can_teach, "skills_can_teach", errors)

    # --- Validate skills want to learn ---
    parsed_want_to_learn = normalize_skills(skills_want_to_learn)
    parsed_want_to_learn = validate_skills(
        parsed_want_to_learn,
        "skills_want_to_learn",
        errors,
        other_skills=parsed_can_teach,
    )

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
    current_user.country = country_value
    current_user.language = language_value

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
    gender: str = "",
    country: str = "",
    language: str = "",
    age: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Page to find skill-exchange matches for a given user.

    If the user is logged in, their ID is used automatically and they don't
    need to type it. If they are not logged in, they can still enter an ID
    manually.

    Filters (gender, country, language, age) are applied *after* the existing
    two-way skill matching so users must be both a real skill match AND pass
    every active basic filter. Matches are sorted by match score (highest
    first).
    """
    # Normalize filter values: treat "Any", "any", "" the same as "no filter"
    def clean_filter(value: str) -> str:
        value = (value or "").strip()
        if value.lower() == "any":
            return ""
        return value

    gender = clean_filter(gender)
    country = clean_filter(country)
    language = clean_filter(language)
    age = clean_filter(age)

    context = {
        "app_name": "Skill Exchange",
        "requested_id": user_id,
        "error": None,
        "matches": None,
        "current_user": current_user,
        # Filter values so the form can show what's selected after submit
        "filter_gender": gender,
        "filter_country": country,
        "filter_language": language,
        "filter_age": age,
        # Filter dropdown option lists
        "gender_options": GENDER_FILTER_OPTIONS,
        "country_options": MATCH_COUNTRY_OPTIONS,
        "language_options": MATCH_LANGUAGE_OPTIONS,
        "age_options": AGE_FILTER_OPTIONS,
        # All filter labels for display in the heading (only show active ones)
        "active_filter_count": sum(
            1 for f in (gender, country, language, age) if f
        ),
    }

    # If logged in, always use the logged-in user's ID (no manual entry needed)
    effective_user_id = current_user.id if current_user else user_id

    if effective_user_id is not None:
        user = db.query(User).filter(User.id == effective_user_id).first()
        if not user:
            context["error"] = f"No user found with ID {effective_user_id}. Please check the ID and try again."
        else:
            # Logic mirroring /match/{user_id}: both directions of skill exchange must match
            # Normalize skills for case-insensitive comparison
            user_can_teach = _normalize_skill_list(user.get_skills_can_teach())
            user_wants_to_learn = _normalize_skill_list(user.get_skills_want_to_learn())

            matches = []
            for other in db.query(User).filter(User.id != effective_user_id).all():
                other_can_teach = _normalize_skill_list(other.get_skills_can_teach())
                other_wants_to_learn = _normalize_skill_list(other.get_skills_want_to_learn())

                they_teach_my_needs = bool(user_wants_to_learn & other_can_teach)
                they_need_my_skills = bool(user_can_teach & other_wants_to_learn)

                if they_teach_my_needs and they_need_my_skills:
                    # Apply basic filters (gender, country, language, age) on top
                    # of the existing two-way skill matching.
                    if not matches_basic_filters(
                        other,
                        gender=gender,
                        country=country,
                        language=language,
                        age=age,
                    ):
                        continue

                    # Compute the actual overlapping skills for this match
                    matched_they_teach_me = sorted(user_wants_to_learn & other_can_teach)
                    matched_i_teach_them = sorted(user_can_teach & other_wants_to_learn)

                    # --- Match Score ---
                    avg_rating, rating_count = average_rating_stats(db, other.id)
                    score = compute_match_score(user, other, avg_rating)

                    matches.append(
                        {
                            "id": other.id,
                            "name": other.name,
                            "skills_can_teach": other.get_skills_can_teach(),
                            "skills_want_to_learn": other.get_skills_want_to_learn(),
                            "bio": other.bio,
                            "profile_picture": other.profile_picture,
                            "age": other.age,
                            "gender": other.gender,
                            "country": other.country,
                            "language": other.language,
                            # The actual skills that matched between us
                            "matched_they_teach_me": matched_they_teach_me,
                            "matched_i_teach_them": matched_i_teach_them,
                            # Match score (0-100) + rating info
                            "match_score": score,
                            "average_rating": avg_rating,
                            "rating_count": rating_count,
                        }
                    )

            # Sort matches by score, highest first
            matches.sort(key=lambda m: m["match_score"], reverse=True)
            context["matches"] = matches

    return templates.TemplateResponse(request=request, name="matches.html", context=context)


# ---------- MARKETPLACE ROUTES ----------


# Allowed listing types and price types
ALLOWED_LISTING_TYPES = {"teach", "learn"}
ALLOWED_PRICE_TYPES = {"free", "paid", "swap"}


@app.get("/market", response_class=HTMLResponse)
def market_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marketplace page showing all active listings as cards."""
    listings = (
        db.query(Listing)
        .filter(Listing.is_active == True)
        .order_by(Listing.created_at.desc(), Listing.id.desc())
        .all()
    )

    # Build listing data with owner info
    listing_data = []
    for listing in listings:
        owner = db.query(User).filter(User.id == listing.user_id).first()
        if owner:
            listing_data.append(
                {
                    "id": listing.id,
                    "title": listing.title,
                    "description": listing.description,
                    "skill": listing.skill,
                    "listing_type": listing.listing_type,
                    "price_type": listing.price_type,
                    "price_amount": listing.price_amount,
                    "thumbnail": listing.thumbnail,
                    "created_at": listing.created_at,
                    "owner": {
                        "id": owner.id,
                        "name": owner.name,
                        "profile_picture": owner.profile_picture,
                    },
                    # True if the logged-in user owns this listing
                    "is_owner": current_user is not None and current_user.id == owner.id,
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="market.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "listings": listing_data,
        },
    )


@app.get("/market/new", response_class=HTMLResponse)
def market_new_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the create listing form. Login required."""
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="market_new.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "errors": {},
            "form_data": {},
        },
    )


@app.post("/market/new", response_class=HTMLResponse)
def market_new_submit(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    skill: str = Form(""),
    listing_type: str = Form(""),
    price_type: str = Form(""),
    price_amount: str = Form(""),
    thumbnail: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handle create listing form submission with validation. Login required.

    An optional thumbnail image can be uploaded (JPG, JPEG, PNG, WEBP; max 2 MB).
    If uploaded, it is stored on Cloudinary and the secure URL is saved in
    the listing's `thumbnail` field.
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    errors: dict[str, str] = {}
    form_data = {
        "title": title,
        "description": description,
        "skill": skill,
        "listing_type": listing_type,
        "price_type": price_type,
        "price_amount": price_amount,
    }

    # --- Validate title (required) ---
    if not title.strip():
        errors["title"] = "Title is required."

    # --- Validate description (required) ---
    if not description.strip():
        errors["description"] = "Description is required."

    # --- Validate skill (required) ---
    if not skill.strip():
        errors["skill"] = "Skill is required."

    # --- Validate listing_type (must be teach/learn) ---
    if listing_type not in ALLOWED_LISTING_TYPES:
        errors["listing_type"] = "Please choose a valid listing type."

    # --- Validate price_type (must be free/paid/swap) ---
    if price_type not in ALLOWED_PRICE_TYPES:
        errors["price_type"] = "Please choose a valid price type."

    # --- Validate thumbnail (if uploaded) ---
    thumbnail_url = None
    if thumbnail and thumbnail.filename:
        try:
            thumbnail_url = save_listing_thumbnail(thumbnail)
        except ValueError as e:
            errors["thumbnail"] = str(e)

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="market_new.html",
            context={
                "app_name": "Skill Exchange",
                "current_user": current_user,
                "errors": errors,
                "form_data": form_data,
            },
        )

    # --- Save the listing ---
    db_listing = Listing(
        user_id=current_user.id,
        title=title.strip(),
        description=description.strip(),
        skill=skill.strip(),
        listing_type=listing_type,
        price_type=price_type,
        price_amount=price_amount.strip() or None,
        thumbnail=thumbnail_url,
        is_active=True,
    )
    db.add(db_listing)
    db.commit()
    db.refresh(db_listing)

    # Redirect to the new listing's detail page
    return RedirectResponse(url=f"/market/{db_listing.id}", status_code=303)


@app.get("/market/{listing_id}", response_class=HTMLResponse)
def market_detail(
    request: Request,
    listing_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Listing detail page."""
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing or not listing.is_active:
        raise HTTPException(status_code=404, detail="Listing not found")

    owner = db.query(User).filter(User.id == listing.user_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    # Determine if the current user is the owner (cannot connect to own listing)
    is_owner = current_user is not None and current_user.id == owner.id

    return templates.TemplateResponse(
        request=request,
        name="market_detail.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "listing": {
                "id": listing.id,
                "title": listing.title,
                "description": listing.description,
                "skill": listing.skill,
                "listing_type": listing.listing_type,
                "price_type": listing.price_type,
                "price_amount": listing.price_amount,
                "thumbnail": listing.thumbnail,
                "created_at": listing.created_at,
                "is_active": listing.is_active,
            },
            "owner": {
                "id": owner.id,
                "name": owner.name,
                "profile_picture": owner.profile_picture,
            },
            "is_owner": is_owner,
        },
    )


@app.get("/market/{listing_id}/edit", response_class=HTMLResponse)
def market_edit_form(
    request: Request,
    listing_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Show the edit listing form. Only the listing owner can access it."""
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing or not listing.is_active:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Only the owner can edit their own listing
    if listing.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own listings")

    # Pre-fill the form with the listing's current data
    form_data = {
        "title": listing.title,
        "description": listing.description,
        "skill": listing.skill,
        "listing_type": listing.listing_type,
        "price_type": listing.price_type,
        "price_amount": listing.price_amount or "",
    }

    return templates.TemplateResponse(
        request=request,
        name="market_edit.html",
        context={
            "app_name": "Skill Exchange",
            "current_user": current_user,
            "listing_id": listing.id,
            "listing": {
                "id": listing.id,
                "title": listing.title,
                "thumbnail": listing.thumbnail,
            },
            "errors": {},
            "form_data": form_data,
        },
    )


@app.post("/market/{listing_id}/edit", response_class=HTMLResponse)
def market_edit_submit(
    request: Request,
    listing_id: int,
    title: str = Form(""),
    description: str = Form(""),
    skill: str = Form(""),
    listing_type: str = Form(""),
    price_type: str = Form(""),
    price_amount: str = Form(""),
    thumbnail: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handle edit listing form submission. Only the listing owner can edit."""
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing or not listing.is_active:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Only the owner can edit their own listing
    if listing.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own listings")

    errors: dict[str, str] = {}
    form_data = {
        "title": title,
        "description": description,
        "skill": skill,
        "listing_type": listing_type,
        "price_type": price_type,
        "price_amount": price_amount,
    }

    # --- Validate title (required) ---
    if not title.strip():
        errors["title"] = "Title is required."

    # --- Validate description (required) ---
    if not description.strip():
        errors["description"] = "Description is required."

    # --- Validate skill (required) ---
    if not skill.strip():
        errors["skill"] = "Skill is required."

    # --- Validate listing_type (must be teach/learn) ---
    if listing_type not in ALLOWED_LISTING_TYPES:
        errors["listing_type"] = "Please choose a valid listing type."

    # --- Validate price_type (must be free/paid/swap) ---
    if price_type not in ALLOWED_PRICE_TYPES:
        errors["price_type"] = "Please choose a valid price type."

    # --- Validate thumbnail (if uploaded) ---
    new_thumbnail_url = None
    if thumbnail and thumbnail.filename:
        try:
            new_thumbnail_url = save_listing_thumbnail(thumbnail)
        except ValueError as e:
            errors["thumbnail"] = str(e)

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="market_edit.html",
            context={
                "app_name": "Skill Exchange",
                "current_user": current_user,
                "listing_id": listing.id,
                "listing": {
                    "id": listing.id,
                    "title": listing.title,
                    "thumbnail": listing.thumbnail,
                },
                "errors": errors,
                "form_data": form_data,
            },
        )

    # --- Update the listing ---
    listing.title = title.strip()
    listing.description = description.strip()
    listing.skill = skill.strip()
    listing.listing_type = listing_type
    listing.price_type = price_type
    listing.price_amount = price_amount.strip() or None

    # If a new thumbnail was uploaded, delete the old one and save the new one
    if new_thumbnail_url:
        delete_listing_thumbnail(listing.thumbnail)
        listing.thumbnail = new_thumbnail_url

    db.commit()

    # Redirect to the updated listing's detail page
    return RedirectResponse(url=f"/market/{listing.id}", status_code=303)


@app.post("/market/{listing_id}/delete", response_class=HTMLResponse)
def market_delete(
    request: Request,
    listing_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a listing. Only the listing owner can delete their own listing.

    The listing is soft-deleted by setting is_active=False so it disappears
    from /market and its detail page returns 404.
    """
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Only the owner can delete their own listing
    if listing.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own listings")

    # Soft-delete: mark as inactive so it disappears from the marketplace
    listing.is_active = False
    db.commit()

    return RedirectResponse(url="/market", status_code=303)


@app.post("/admin/listings/{listing_id}/delete", response_class=HTMLResponse)
def admin_delete_listing(
    listing_id: int,
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin can delete any marketplace listing (password protected).

    The listing is soft-deleted by setting is_active=False so it disappears
    from /market and its detail page returns 404.
    """
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if listing:
        listing.is_active = False
        db.commit()

    return RedirectResponse(url="/admin", status_code=303)


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
def admin_feedbacks(
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin page showing all submitted feedback (password protected)."""
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
        },
    )


# ---------- REPORT ROUTES ----------


# Allowed report types
ALLOWED_REPORT_TYPES = {"bug", "user", "chat", "other"}

# Human-readable labels for report types (used in templates)
REPORT_TYPE_LABELS = {
    "bug": "Bug",
    "user": "User",
    "chat": "Chat",
    "other": "Other",
}


@app.get("/report", response_class=HTMLResponse)
def report_form(request: Request, current_user: User = Depends(get_current_user)):
    """Show the report form.

    Users can submit a report about a bug, another user, a chat, or other issues.
    If logged in, the user's ID is automatically linked to the report.
    """
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": False,
            "current_user": current_user,
        },
    )


@app.post("/report", response_class=HTMLResponse)
def report_submit(
    request: Request,
    report_type: str = Form(""),
    target_user_id: str = Form(""),
    message: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Handle report form submission.

    Saves the report to the database. The reporter_id is optional (if not logged in).
    The report_type must be one of: bug, user, chat, other.
    The target_user_id is optional and only used when reporting a user.
    The message is required.
    """
    errors: dict[str, str] = {}
    form_data = {
        "report_type": report_type,
        "target_user_id": target_user_id,
        "message": message,
    }

    # --- Validate report_type (required, must be one of the allowed values) ---
    if report_type not in ALLOWED_REPORT_TYPES:
        errors["report_type"] = "Please choose a valid report type."

    # --- Validate target_user_id (optional, but must be a valid user ID if provided) ---
    target_user_id_value = None
    if target_user_id.strip():
        try:
            target_user_id_value = int(target_user_id.strip())
            # Verify the target user exists
            target_user = db.query(User).filter(User.id == target_user_id_value).first()
            if not target_user:
                errors["target_user_id"] = "No user found with that ID."
        except ValueError:
            errors["target_user_id"] = "Target user ID must be a valid number."

    # --- Validate message (required) ---
    if not message.strip():
        errors["message"] = "Message is required."

    # --- If there are errors, re-render the form ---
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="report.html",
            context={
                "app_name": "Skill Exchange",
                "errors": errors,
                "form_data": form_data,
                "success": False,
                "current_user": current_user,
            },
        )

    # --- Save the report ---
    db_report = Report(
        reporter_id=current_user.id if current_user else None,
        report_type=report_type,
        target_user_id=target_user_id_value,
        message=message.strip(),
    )
    db.add(db_report)
    db.commit()

    # Render success
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={
            "app_name": "Skill Exchange",
            "errors": {},
            "form_data": {},
            "success": True,
            "current_user": current_user,
        },
    )


@app.get("/admin/reports", response_class=HTMLResponse)
def admin_reports(
    request: Request,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin page showing all submitted reports (password protected)."""
    reports = db.query(Report).order_by(Report.created_at.desc(), Report.id.desc()).all()

    # Build a list of dicts for the template, including reporter/target names
    report_data = []
    for rep in reports:
        # Look up the reporter's name (if linked to a user)
        reporter_name = None
        if rep.reporter_id:
            reporter = db.query(User).filter(User.id == rep.reporter_id).first()
            reporter_name = reporter.name if reporter else None

        # Look up the target user's name (if linked to a user)
        target_name = None
        if rep.target_user_id:
            target = db.query(User).filter(User.id == rep.target_user_id).first()
            target_name = target.name if target else None

        report_data.append(
            {
                "id": rep.id,
                "reporter_id": rep.reporter_id,
                "reporter_name": reporter_name,
                "report_type": rep.report_type,
                "report_type_label": REPORT_TYPE_LABELS.get(rep.report_type, rep.report_type),
                "target_user_id": rep.target_user_id,
                "target_name": target_name,
                "message": rep.message,
                "created_at": rep.created_at,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin_reports.html",
        context={
            "app_name": "Skill Exchange",
            "reports": report_data,
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

    # Normalize skills for consistent storage and case-insensitive matching
    normalized_can_teach = [normalize_skill(s) for s in user.skills_can_teach]
    normalized_want_to_learn = [normalize_skill(s) for s in user.skills_want_to_learn]

    # Convert skill lists to JSON strings for SQLite storage
    # If a password was provided, hash it before storing
    db_user = User(
        name=user.name,
        email=user.email,
        password=hash_password(user.password) if user.password else None,
        skills_can_teach=json.dumps(normalized_can_teach),
        skills_want_to_learn=json.dumps(normalized_want_to_learn),
        bio=user.bio,
        age=user.age,
        gender=user.gender,
        country=user.country,
        language=user.language,
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
    # Normalize skills for case-insensitive comparison
    user_can_teach = _normalize_skill_list(user.get_skills_can_teach())
    user_wants_to_learn = _normalize_skill_list(user.get_skills_want_to_learn())

    matches = []

    # Check every other user in the system
    for other in db.query(User).filter(User.id != user_id).all():
        other_can_teach = _normalize_skill_list(other.get_skills_can_teach())
        other_wants_to_learn = _normalize_skill_list(other.get_skills_want_to_learn())

        # Condition 1: the other user teaches something I want to learn
        they_teach_my_needs = bool(user_wants_to_learn & other_can_teach)

        # Condition 2: I teach something the other user wants to learn
        they_need_my_skills = bool(user_can_teach & other_wants_to_learn)

        # Both conditions must hold for a good two-way match
        if they_teach_my_needs and they_need_my_skills:
            matches.append(other)

    return matches