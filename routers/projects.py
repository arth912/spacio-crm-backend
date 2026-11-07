import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from db import get_db
import models
import schemas
from deps import require_authenticated_user_id

router = APIRouter(prefix="/api/v1", tags=["Projects & Rooms"])

@router.post("/projects", response_model=schemas.ProjectResponse)
async def create_project(
    project_data: schemas.ProjectCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    c_query = select(models.Client).where(models.Client.id == project_data.client_id, models.Client.user_id == current_user_id)
    c_res = await db.execute(c_query)
    if not c_res.scalars().first():
        raise HTTPException(status_code=404, detail="Client not found")
        
    new_project = models.Project(
        id=str(uuid.uuid4()),
        client_id=project_data.client_id,
        user_id=current_user_id,
        name=project_data.name,
        site_address=project_data.site_address,
        budget=project_data.budget,
        status=project_data.status
    )
    db.add(new_project)
    await db.flush()
    return new_project

@router.get("/projects", response_model=List[schemas.ProjectResponse])
async def get_projects(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Project).where(models.Project.user_id == current_user_id)
    result = await db.execute(query.order_by(models.Project.created_at.desc()).limit(limit).offset(offset))
    return result.scalars().all()

@router.get("/projects/{project_id}", response_model=schemas.ProjectResponse)
async def get_project(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Project).where(models.Project.id == project_id, models.Project.user_id == current_user_id)
    result = await db.execute(query)
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.put("/projects/{project_id}", response_model=schemas.ProjectResponse)
async def update_project(
    project_id: str, 
    project_data: schemas.ProjectUpdate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    query = select(models.Project).where(models.Project.id == project_id, models.Project.user_id == current_user_id)
    result = await db.execute(query)
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    project.name = project_data.name
    if project_data.client_id is not None:
        c_query = select(models.Client).where(models.Client.id == project_data.client_id, models.Client.user_id == current_user_id)
        c_res = await db.execute(c_query)
        if not c_res.scalars().first():
            raise HTTPException(status_code=400, detail="Client not found")
        project.client_id = project_data.client_id
    project.site_address = project_data.site_address
    project.budget = project_data.budget
    project.status = project_data.status
    
    await db.commit()
    await db.refresh(project)
    return project

# --- ROOM ENDPOINTS ---
@router.post("/rooms", response_model=schemas.RoomResponse)
async def create_room(
    room_data: schemas.RoomCreate, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    p_query = select(models.Project).where(models.Project.id == room_data.project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")
        
    new_room = models.Room(
        id=str(uuid.uuid4()),
        project_id=room_data.project_id,
        room_name=room_data.room_name
    )
    db.add(new_room)
    await db.flush()
    return new_room

@router.get("/projects/{project_id}/rooms", response_model=List[schemas.RoomResponse])
async def get_project_rooms(
    project_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(require_authenticated_user_id)
):
    p_query = select(models.Project).where(models.Project.id == project_id, models.Project.user_id == current_user_id)
    p_res = await db.execute(p_query)
    if not p_res.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(select(models.Room).where(models.Room.project_id == project_id))
    return result.scalars().all()
