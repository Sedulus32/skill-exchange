"""Tests for the Report system."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from database import SessionLocal, engine, Base
from models import Report, User

# Make sure the tables exist
Base.metadata.create_all(bind=engine)

from main import app

client = TestClient(app)


def test_report_page_loads():
    """The /report page should return 200 and contain the form."""
    response = client.get("/report")
    assert response.status_code == 200
    assert "Report" in response.text
    assert "Report Type" in response.text
    assert "Message / Description" in response.text
    assert "Target User ID" in response.text
    print("PASS: /report page loads with form")


def test_report_submit_anonymous():
    """Submitting a report anonymously should save it without a reporter_id."""
    # Count reports before
    db = SessionLocal()
    before = db.query(Report).count()
    db.close()

    response = client.post(
        "/report",
        data={
            "report_type": "bug",
            "target_user_id": "",
            "message": "Test bug report",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Thank you for your report" in response.text

    # Verify it was saved
    db = SessionLocal()
    report = db.query(Report).order_by(Report.id.desc()).first()
    after = db.query(Report).count()
    db.close()

    assert after == before + 1
    assert report is not None
    assert report.report_type == "bug"
    assert report.message == "Test bug report"
    assert report.reporter_id is None
    assert report.target_user_id is None
    print("PASS: Anonymous report submitted and saved")


def test_report_submit_with_target_user():
    """Submitting a report with a target user ID should save it."""
    # Get a valid user ID (create one if none exist)
    db = SessionLocal()
    user = db.query(User).first()
    if not user:
        user = User(
            name="Test User",
            email="test_report@example.com",
            skills_can_teach="['Python']",
            skills_want_to_learn="['Spanish']",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    target_user_id = user.id
    db.close()

    response = client.post(
        "/report",
        data={
            "report_type": "user",
            "target_user_id": str(target_user_id),
            "message": "Report about a specific user",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Thank you for your report" in response.text

    db = SessionLocal()
    report = db.query(Report).order_by(Report.id.desc()).first()
    db.close()
    assert report.report_type == "user"
    assert report.target_user_id == target_user_id
    print("PASS: Report with target user saved")


def test_report_validation():
    """Invalid report submissions should show errors."""
    # Missing message (required)
    response = client.post(
        "/report",
        data={
            "report_type": "bug",
            "target_user_id": "",
            "message": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Message is required." in response.text
    print("PASS: Missing message validation works")

    # Invalid report type
    response = client.post(
        "/report",
        data={
            "report_type": "invalid_type",
            "target_user_id": "",
            "message": "Test message",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Please choose a valid report type." in response.text
    print("PASS: Invalid report type validation works")

    # Invalid target user ID
    response = client.post(
        "/report",
        data={
            "report_type": "user",
            "target_user_id": "999999",
            "message": "Test message",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "No user found with that ID." in response.text
    print("PASS: Invalid target user ID validation works")


def test_admin_reports_page():
    """The /admin/reports page should show all reports (newest first)."""
    response = client.get("/admin/reports")
    assert response.status_code == 200
    assert "All Reports" in response.text
    assert "No reports yet" not in response.text  # There should be reports from above
    assert "Test bug report" in response.text
    print("PASS: /admin/reports page shows reports")

    # Verify newest first ordering
    db = SessionLocal()
    reports = db.query(Report).order_by(Report.created_at.desc(), Report.id.desc()).all()
    assert len(reports) >= 3
    assert reports[0].message == "Report about a specific user"  # Latest
    db.close()
    print("PASS: Reports are ordered newest first")


if __name__ == "__main__":
    test_report_page_loads()
    test_report_submit_anonymous()
    test_report_submit_with_target_user()
    test_report_validation()
    test_admin_reports_page()
    print("\nALL REPORT TESTS PASSED")
