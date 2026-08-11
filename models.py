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