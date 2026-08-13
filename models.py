import json
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    # Store the hashed password (never the plaintext)
    password = Column(String, nullable=True)
    # Skills stored as JSON strings since SQLite has no native list type
    skills_can_teach = Column(Text, default="[]")
    skills_want_to_learn = Column(Text, default="[]")
    bio = Column(Text, default="")
    # Optional demographic fields
    age = Column(Integer, nullable=True, default=None)
    gender = Column(String, nullable=True, default=None)
    # Filename/path of the user's profile picture (stored in static/uploads/)
    profile_picture = Column(String, nullable=True, default=None)

    # Helper methods to convert between JSON strings and Python lists
    def get_skills_can_teach(self):
        return json.loads(self.skills_can_teach)

    def get_skills_want_to_learn(self):
        return json.loads(self.skills_want_to_learn)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    # Optional: the user who submitted the feedback (if logged in)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Optional: the name the user provided (if not logged in, or if they want to show a name)
    name = Column(String, nullable=True)
    # The actual feedback message (required)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Rating(Base):
    __tablename__ = "ratings"

    id = Column(Integer, primary_key=True, index=True)
    # The user who is giving the rating
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # The user who is receiving the rating
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Rating score from 1 to 5
    score = Column(Integer, nullable=False)
    # Optional written review
    review = Column(Text, nullable=True, default=None)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)