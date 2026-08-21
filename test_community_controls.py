"""Tests for the Community Edit/Delete controls (owner + admin moderation)."""

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
    # Find the user ID from the DB
    from database import SessionLocal
    from models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email.like(f"{prefix}-%")).first()
        return user.id
    finally:
        db.close()


def _create_post(content: str, post_type: str = "learned", skill_tag: str = "") -> int:
    """Create a post as the currently logged-in user and return its ID."""
    resp = client.post(
        "/community/new",
        data={
            "content": content,
            "post_type": post_type,
            "skill_tag": skill_tag,
        },
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/community"
    # Find the post ID from the DB
    from database import SessionLocal
    from models import Post

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.content == content).first()
        return post.id
    finally:
        db.close()


def test_owner_can_edit_own_post():
    """Owner can GET edit form and POST updates to their own post."""
    _register_user("Edit Post Owner", "edit-post-owner")

    post_id = _create_post("Original content", "learned", "Python")

    # GET edit form - should be accessible
    resp = client.get(f"/community/{post_id}/edit")
    assert resp.status_code == 200
    assert "Edit Post" in resp.text
    assert "Original content" in resp.text

    # POST edit - update content, type, and skill tag
    resp = client.post(
        f"/community/{post_id}/edit",
        data={
            "content": "Updated content",
            "post_type": "showcase",
            "skill_tag": "Guitar",
        },
    )
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/community"

    # Verify the feed shows the updated data
    resp = client.get("/community")
    assert resp.status_code == 200
    assert "Updated content" in resp.text
    assert "Original content" not in resp.text
    assert "Showcase" in resp.text
    assert "#Guitar" in resp.text


def test_owner_can_delete_own_post():
    """Owner can delete their own post and it disappears from /community."""
    _register_user("Delete Post Owner", "delete-post-owner")

    post_id = _create_post("To Be Deleted Post")

    # Verify it appears on the community feed
    resp = client.get("/community")
    assert resp.status_code == 200
    assert "To Be Deleted Post" in resp.text

    # Delete it
    resp = client.post(f"/community/{post_id}/delete")
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/community"

    # Verify it no longer appears on /community
    resp = client.get("/community")
    assert resp.status_code == 200
    assert "To Be Deleted Post" not in resp.text


def test_non_owner_cannot_edit_or_delete():
    """Non-owners get 403 when trying to edit/delete someone else's post."""
    # Register an owner
    _register_user("Non Owner Post Test", "non-owner-post")
    post_id = _create_post("Protected Post Content")

    # Logout
    client.get("/logout")

    # Register a different user
    _register_user("Other Post User", "other-post-user")

    # Try to GET edit - should be 403
    resp = client.get(f"/community/{post_id}/edit")
    assert resp.status_code == 403

    # Try to POST edit - should be 403
    resp = client.post(
        f"/community/{post_id}/edit",
        data={
            "content": "Hacked content",
            "post_type": "learned",
            "skill_tag": "",
        },
    )
    assert resp.status_code == 403

    # Try to POST delete - should be 403
    resp = client.post(f"/community/{post_id}/delete")
    assert resp.status_code == 403

    # Original post should still exist
    client.get("/logout")
    resp = client.get("/community")
    assert resp.status_code == 200
    assert "Protected Post Content" in resp.text


def test_edit_requires_login():
    """GET/POST edit and delete should redirect anonymous users to login."""
    resp = client.get("/community/1/edit")
    assert resp.status_code in (303, 403, 404)  # 303 redirect or 403/404

    resp = client.post("/community/1/edit", data={"content": "x", "post_type": "learned"})
    assert resp.status_code in (303, 403, 404)

    resp = client.post("/community/1/delete")
    assert resp.status_code in (303, 403, 404)


def test_delete_removes_comments_and_likes():
    """Deleting a post also removes its comments and likes."""
    from database import SessionLocal
    from models import Comment, Like, Post, User

    _register_user("Cascade Owner", "cascade-owner")
    post_id = _create_post("Cascade Delete Test")

    # Add a comment and like via DB
    db = SessionLocal()
    try:
        # Get the current user
        user = db.query(User).filter(User.email.like("cascade-owner-%")).first()
        comment = Comment(post_id=post_id, user_id=user.id, content="Test comment")
        db.add(comment)
        like = Like(post_id=post_id, user_id=user.id)
        db.add(like)
        db.commit()

        # Verify they exist
        assert db.query(Comment).filter(Comment.post_id == post_id).count() == 1
        assert db.query(Like).filter(Like.post_id == post_id).count() == 1
    finally:
        db.close()

    # Delete the post
    resp = client.post(f"/community/{post_id}/delete")
    assert resp.status_code == 303

    # Verify comments and likes are gone
    db = SessionLocal()
    try:
        assert db.query(Comment).filter(Comment.post_id == post_id).count() == 0
        assert db.query(Like).filter(Like.post_id == post_id).count() == 0
        assert db.query(Post).filter(Post.id == post_id).count() == 0
    finally:
        db.close()


def test_admin_can_delete_any_post():
    """Admin can delete any community post (password protected)."""
    from database import SessionLocal
    from models import Post

    # Register a user and create a post
    _register_user("Admin Delete Owner", "admin-delete-owner")
    post_id = _create_post("Admin Will Delete This")

    # Logout the user
    client.get("/logout")

    # Admin login
    resp = client.post("/admin/login", data={"password": "admin123"})
    assert resp.status_code == 303

    # Admin dashboard should show the post
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Admin Will Delete This" in resp.text

    # Admin deletes the post
    resp = client.post(f"/admin/posts/{post_id}/delete")
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/admin"

    # Verify the post is gone from the DB
    db = SessionLocal()
    try:
        assert db.query(Post).filter(Post.id == post_id).count() == 0
    finally:
        db.close()

    # Verify it's gone from the community feed
    client.get("/admin/logout")
    resp = client.get("/community")
    assert resp.status_code == 200
    assert "Admin Will Delete This" not in resp.text


def test_admin_delete_requires_admin_login():
    """Admin post delete should redirect non-admins to admin login."""
    # Logout any existing session
    client.get("/logout")

    # Try to delete a post without admin login
    resp = client.post("/admin/posts/1/delete")
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/admin/login"


if __name__ == "__main__":
    # Run all test functions
    test_functions = [
        test_owner_can_edit_own_post,
        test_owner_can_delete_own_post,
        test_non_owner_cannot_edit_or_delete,
        test_edit_requires_login,
        test_delete_removes_comments_and_likes,
        test_admin_can_delete_any_post,
        test_admin_delete_requires_admin_login,
    ]
    for fn in test_functions:
        print(f"Running {fn.__name__}...")
        fn()
        print(f"  [OK] {fn.__name__} passed")
    print("\n=== ALL COMMUNITY CONTROLS TESTS PASSED ===")