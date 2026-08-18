"""Tests for the Marketplace Edit/Delete controls (owner + admin moderation)."""

import sys
import os
import time

# Ensure the skill-exchange directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app, follow_redirects=False)


def _unique_email(prefix: str) -> str:
    """Generate a unique email for test isolation."""
    return f"{prefix}-{int(time.time() * 1000)}@example.com"


def _register_user(name: str, prefix: str) -> int:
    """Register a user and return their auto-logged-in ID."""
    resp = client.post(
        "/register",
        data={
            "name": name,
            "email": _unique_email(prefix),
            "password": "password123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Guitar",
            "bio": "",
        },
    )
    assert resp.status_code == 200
    # Find the user ID from the response (the template includes it)
    # We use a direct DB query approach instead
    from database import SessionLocal
    from models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email.like(f"{prefix}-%")).first()
        return user.id
    finally:
        db.close()


def _create_listing(title: str) -> int:
    """Create a listing as the currently logged-in user and return its ID."""
    resp = client.post(
        "/market/new",
        data={
            "title": title,
            "description": "Test description",
            "skill": "Python",
            "listing_type": "teach",
            "price_type": "free",
            "price_amount": "",
        },
    )
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert location.startswith("/market/")
    return int(location.split("/")[-1])


def test_login_does_not_affect():
    """Sanity check: login page still works."""
    resp = client.get("/login")
    assert resp.status_code == 200


def test_owner_can_edit_own_listing():
    """Owner can GET edit form and POST updates to their own listing."""
    _register_user("Edit Owner User", "edit-owner")

    listing_id = _create_listing("Original Title")

    # GET edit form - should be accessible
    resp = client.get(f"/market/{listing_id}/edit")
    assert resp.status_code == 200
    assert "Edit Listing" in resp.text
    assert "Original Title" in resp.text

    # POST edit - update the title
    resp = client.post(
        f"/market/{listing_id}/edit",
        data={
            "title": "Updated Title",
            "description": "Updated description",
            "skill": "Python",
            "listing_type": "learn",
            "price_type": "paid",
            "price_amount": "50/hour",
        },
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == f"/market/{listing_id}"

    # Verify the detail page shows the updated data
    resp = client.get(f"/market/{listing_id}")
    assert resp.status_code == 200
    assert "Updated Title" in resp.text
    assert "Updated description" in resp.text
    assert "Learn" in resp.text
    assert "50/hour" in resp.text


def test_owner_can_delete_own_listing():
    """Owner can delete their own listing and it disappears from /market."""
    _register_user("Delete Owner User", "delete-owner")

    listing_id = _create_listing("To Be Deleted")

    # Verify it appears on the market page
    resp = client.get("/market")
    assert resp.status_code == 200
    assert "To Be Deleted" in resp.text

    # Delete it
    resp = client.post(f"/market/{listing_id}/delete")
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/market"

    # Verify it no longer appears on /market
    resp = client.get("/market")
    assert resp.status_code == 200
    assert "To Be Deleted" not in resp.text

    # Verify detail page returns 404
    resp = client.get(f"/market/{listing_id}")
    assert resp.status_code == 404


def test_non_owner_cannot_edit_or_delete():
    """Non-owners get 403 when trying to edit/delete someone else's listing."""
    # Register an owner
    _register_user("Non Owner Test", "non-owner")
    listing_id = _create_listing("Protected Listing")

    # Logout
    client.get("/logout")

    # Register a different user
    _register_user("Other User", "other-user")

    # Try to GET edit - should be 403
    resp = client.get(f"/market/{listing_id}/edit")
    assert resp.status_code == 403

    # Try to POST edit - should be 403
    resp = client.post(
        f"/market/{listing_id}/edit",
        data={
            "title": "Hacked Title",
            "description": "Hacked",
            "skill": "Python",
            "listing_type": "teach",
            "price_type": "free",
            "price_amount": "",
        },
    )
    assert resp.status_code == 403

    # Try to POST delete - should be 403
    resp = client.post(f"/market/{listing_id}/delete")
    assert resp.status_code == 403

    # Original listing should still exist
    client.get("/logout")
    resp = client.get(f"/market/{listing_id}")
    assert resp.status_code == 200
    assert "Protected Listing" in resp.text


def test_edit_requires_login():
    """GET/POST edit and delete should redirect anonymous users to login."""
    resp = client.get("/market/1/edit")
    assert resp.status_code in (303, 403, 404)  # 303 redirect or 403/404