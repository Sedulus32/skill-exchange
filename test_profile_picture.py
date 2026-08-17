"""Test the profile picture upload feature (Cloudinary integration)."""
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# A fake Cloudinary secure URL to use in tests
FAKE_CLOUDINARY_URL = "https://res.cloudinary.com/test-cloud/image/upload/v1234567890/profile_pictures/abc123.png"


def unique_email(name: str) -> str:
    """Generate a unique email so tests can be re-run without conflicts."""
    return f"{name}-{int(time.time() * 1000)}@example.com"


def test_profile_picture_upload():
    """Upload a profile picture and verify it appears on pages."""
    # Register a user
    email = unique_email("ppuser")
    r = client.post(
        "/register",
        data={
            "name": "Profile Pic User",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "FastAPI",
            "bio": "Bio",
        },
        follow_redirects=False,
    )
    print("Register status:", r.status_code)
    assert r.status_code == 200

    # Upload a profile picture (a tiny valid PNG) - mock Cloudinary upload
    png_data = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000002000146a1f0a90000000049454e44ae426082"
    )
    with patch("main.cloudinary.uploader.upload", return_value={"secure_url": FAKE_CLOUDINARY_URL}):
        r = client.post(
            "/edit-profile",
            data={
                "name": "Profile Pic User",
                "password": "",
                "skills_can_teach": "Python",
                "skills_want_to_learn": "FastAPI",
                "bio": "Bio",
            },
            files={"profile_picture": ("test.png", png_data, "image/png")},
        )
    print("Upload status:", r.status_code)
    assert r.status_code == 200
    assert "Success" in r.text
    print("PASS: Upload succeeded")

    # Check users page shows the picture (Cloudinary URL)
    r = client.get("/users-page")
    assert r.status_code == 200
    assert FAKE_CLOUDINARY_URL in r.text
    print("PASS: Profile picture displayed on users page")

    # Check edit profile page shows current picture
    r = client.get("/edit-profile")
    assert r.status_code == 200
    assert FAKE_CLOUDINARY_URL in r.text
    print("PASS: Profile picture displayed on edit profile page")


def test_profile_picture_invalid_type():
    """Uploading a non-image file should be rejected."""
    # Register a user
    email = unique_email("ppbad")
    client.post(
        "/register",
        data={
            "name": "Bad File User",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "FastAPI",
            "bio": "Bio",
        },
        follow_redirects=False,
    )

    # Try uploading a .txt file
    r = client.post(
        "/edit-profile",
        data={
            "name": "Bad File User",
            "password": "",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "FastAPI",
            "bio": "Bio",
        },
        files={"profile_picture": ("test.txt", b"Hello", "text/plain")},
    )
    assert r.status_code == 200
    assert "Only JPG, JPEG, PNG, and WEBP" in r.text
    print("PASS: Invalid file type rejected")


def test_profile_picture_no_upload_keeps_existing():
    """Submitting the form without a new file should keep the existing picture."""
    # Register a user
    email = unique_email("ppkeep")
    client.post(
        "/register",
        data={
            "name": "Keep Pic User",
            "email": email,
            "password": "secret123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "FastAPI",
            "bio": "Bio",
        },
        follow_redirects=False,
    )

    # Upload a profile picture - mock Cloudinary upload
    png_data = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000002000146a1f0a90000000049454e44ae426082"
    )
    with patch("main.cloudinary.uploader.upload", return_value={"secure_url": FAKE_CLOUDINARY_URL}):
        r = client.post(
            "/edit-profile",
            data={
                "name": "Keep Pic User",
                "password": "",
                "skills_can_teach": "Python",
                "skills_want_to_learn": "FastAPI",
                "bio": "Bio",
            },
            files={"profile_picture": ("test.png", png_data, "image/png")},
        )
    assert r.status_code == 200

    # Get the current picture URL from the edit page
    r = client.get("/edit-profile")
    assert r.status_code == 200
    assert FAKE_CLOUDINARY_URL in r.text

    # Submit without a new file - should keep the picture
    r = client.post(
        "/edit-profile",
        data={
            "name": "Keep Pic User",
            "password": "",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "FastAPI",
            "bio": "Bio",
        },
    )
    assert r.status_code == 200
    assert "Success" in r.text

    # Verify the picture is still there
    r = client.get("/edit-profile")
    assert r.status_code == 200
    assert FAKE_CLOUDINARY_URL in r.text
    print("PASS: Existing picture preserved when no new file uploaded")


if __name__ == "__main__":
    test_profile_picture_upload()
    print("PASS: upload")
    test_profile_picture_invalid_type()
    print("PASS: invalid type")
    test_profile_picture_no_upload_keeps_existing()
    print("PASS: keeps existing")
    print("\nALL PROFILE PICTURE TESTS PASSED")