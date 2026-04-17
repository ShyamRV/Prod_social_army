from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
from app.db.session import get_db
from app.models.job import User
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["Users"])

class CreateUserRequest(BaseModel):
    email: str

@router.post("/create")
async def create_user(body: CreateUserRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    existing = result.scalar_one_or_none()
    if existing:
        return {"user_id": str(existing.id), "email": existing.email, "status": "existing"}
    user = User(id=uuid.uuid4(), email=body.email)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"user_id": str(user.id), "email": user.email, "status": "created"}

@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, uuid.UUID(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": str(user.id), "email": user.email, "created_at": user.created_at}
