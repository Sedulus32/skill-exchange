"""Tests for the Skill Marketplace feature."""

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


def test_market_page_loads():
    """GET /market should return 200 for anonymous users."""
    resp = client.get("/market")
    assert resp.status_code == 200
    assert "Skill Marketplace" in resp.text


def test_market_new_requires_login():
    """GET /market/new should redirect anonymous users to /login."""
    resp = client.get("/market/new")
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/login"


def test_market_detail_404():
    """GET /market/{id} should return 404 for non-existent listings."""
    resp = client.get("/market/99999")
    assert resp.status_code == 404


def test_create_listing_flow():
    """Register a user, create a listing, and verify it appears."""
    # Register a user (auto-login)
    resp = client.post(
        "/register",
        data={
            "name": "Market Test User",
            "email": _unique_email("market-test-user"),
            "password": "password123",
            "skills_can_teach": "Python, SQL",
            "skills_want_to_learn": "Guitar",
            "bio": "Testing marketplace",
        },
    )
    assert resp.status_code == 200

    # Create a listing
    resp = client.post(
        "/market/new",
        data={
            "title": "Learn Python from scratch",
            "description": "I can teach Python programming from the basics.",
            "skill": "Python",
            "listing_type": "teach",
            "price_type": "free",
            "price_amount": "",
        },
    )
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert location.startswith("/market/")

    # Fetch the listing detail page
    resp = client.get(location)
    assert resp.status_code == 200
    assert "Learn Python from scratch" in resp.text
    assert "Market Test User" in resp.text
    assert "Free" in resp.text
    assert "Teach" in resp.text

    # Verify it appears on the market page
    resp = client.get("/market")
    assert resp.status_code == 200
    assert "Learn Python from scratch" in resp.text


def test_create_listing_validation():
    """Invalid listing data should show validation errors."""
    # Register a user (auto-login)
    resp = client.post(
        "/register",
        data={
            "name": "Market Validation User",
            "email": _unique_email("market-validation"),
            "password": "password123",
            "skills_can_teach": "Python",
            "skills_want_to_learn": "Guitar",
            "bio": "",
        },
    )
    assert resp.status_code == 200

    # Create a listing with invalid data
    resp = client.post(
        "/market/new",
        data={
            "title": "",
            "description": "",
            "skill": "",
            "listing_type": "invalid",
            "price_type": "invalid",
            "price_amount": "",
        },
    )
    assert resp.status_code == 200
    assert "Title is required." in resp.text
    assert "Description is required." in resp.text
    assert "Skill is required." in resp.text
    assert "Please choose a valid listing type." in resp.text
    assert "Please choose a valid price type." in resp.text


def test_create_paid_and_swap_listings():
    """Create listings with paid and swap price types."""
    # Register a user (auto-login)
    resp = client.post(
        "/register",
        data={
            "name": "Market Price User",
            "email": _unique_email("market-price"),
            "password": "password123",
            "skills_can_teach": "Guitar",
            "skills_want_to_learn": "SQL",
            "bio": "",
        },
    )
    assert resp.status_code == 200

    # Paid listing
    resp = client.post(
        "/market/new",
        data={
            "title": "Guitar lessons",
            "description": "Professional guitar lessons.",
            "skill": "Guitar",
            "listing_type": "teach",
            "price_type": "paid",
            "price_amount": "25/hour",
        },
    )
    assert resp.status_code == 303

    # Swap listing
    resp = client.post(
        "/market/new",
        data={
            "title": "Want to learn SQL",
            "description": "Looking for SQL lessons in exchange for Python.",
            "skill": "SQL",
            "listing_type": "learn",
            "price_type": "swap",
            "price_amount": "Python lessons",
        },
    )
    assert resp.status_code == 303

    # Verify both appear on market page
    resp = client.get("/market")
    assert resp.status_code == 200
    assert "Guitar lessons" in resp.text
    assert "Want to learn SQL" in resp.text