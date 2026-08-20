import json

from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional


# Schema for creating a new user (what the client sends)
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: Optional[str] = None
    skills_can_teach: List[str] = []
    skills_want_to_learn: List[str] = []
    bio: Optional[str] = ""
    age: Optional[int] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None


# Schema for returning a user (what the API responds with)
class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    skills_can_teach: List[str]
    skills_want_to_learn: List[str]
    bio: str
    age: Optional[int] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    profile_picture: Optional[str] = None
    is_suspended: bool = False

    # Convert JSON strings from the database back into lists
    @field_validator("skills_can_teach", "skills_want_to_learn", mode="before")
    @classmethod
    def parse_skills(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    class Config:
        from_attributes = True


# Schema for creating a new community post
class PostCreate(BaseModel):
    content: str
    post_type: str  # "learned", "doubt", or "showcase"
    skill_tag: Optional[str] = None
    image_url: Optional[str] = None


# Schema for returning a community post
class PostResponse(BaseModel):
    id: int
    user_id: int
    content: str
    post_type: str
    skill_tag: Optional[str] = None
    image_url: Optional[str] = None
    created_at: object  # datetime

    class Config:
        from_attributes = True


# Schema for creating a new comment
class CommentCreate(BaseModel):
    content: str


# Schema for returning a comment
class CommentResponse(BaseModel):
    id: int
    post_id: int
    user_id: int
    content: str
    created_at: object  # datetime

    class Config:
        from_attributes = True


# Schema for returning a like
class LikeResponse(BaseModel):
    id: int
    post_id: int
    user_id: int

    class Config:
        from_attributes = True