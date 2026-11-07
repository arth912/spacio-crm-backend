import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from db import get_db
import models
import schemas
from deps import require_authenticated_user_id

router = APIRouter(
    prefix="/api/v1/catalog",
    tags=["Master Catalog"],
)

def _visible(model, uid: str):
    """Only the caller's own rows."""
    return model.user_id == uid

async def _own_item(db: AsyncSession, item_id: str, uid: str) -> models.Item:
    item = (await db.execute(select(models.Item).where(models.Item.id == item_id, _visible(models.Item, uid)))).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

async def _visible_category(db: AsyncSession, category_id: str, uid: str):
    cat = (await db.execute(select(models.Category).where(models.Category.id == category_id, _visible(models.Category, uid)))).scalars().first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat

@router.get("/categories", response_model=List[schemas.CategoryResponse])
async def get_categories(response: Response, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    response.headers["Cache-Control"] = "private, max-age=300"
    result = await db.execute(select(models.Category).where(_visible(models.Category, uid)).order_by(models.Category.name))
    return result.scalars().all()

@router.post("/categories", response_model=schemas.CategoryResponse)
async def create_category(data: schemas.CategoryCreate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    name = data.name.strip()
    existing = (await db.execute(select(models.Category).where(func.lower(models.Category.name) == name.lower(), _visible(models.Category, uid)))).scalars().first()
    if existing:
        return existing
    category = models.Category(id=str(uuid.uuid4()), name=name, user_id=uid)
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category

@router.get("/items", response_model=List[schemas.ItemResponse])
async def get_items(response: Response, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    response.headers["Cache-Control"] = "private, max-age=30"
    result = await db.execute(select(models.Item).where(_visible(models.Item, uid)).order_by(models.Item.subcategory, models.Item.name))
    return result.scalars().all()

@router.post("/items", response_model=schemas.ItemResponse)
async def create_item(item_data: schemas.ItemCreate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    await _visible_category(db, item_data.category_id, uid)

    existing_res = await db.execute(
        select(models.Item).where(
            models.Item.category_id == item_data.category_id,
            _visible(models.Item, uid),
            func.lower(models.Item.name) == item_data.name.strip().lower()
        )
    )
    existing_item = existing_res.scalars().first()
    if existing_item:
        return existing_item

    new_item = models.Item(
        id=str(uuid.uuid4()),
        category_id=item_data.category_id,
        subcategory=item_data.subcategory,
        name=item_data.name.strip(),
        pricing_type=item_data.pricing_type,
        brand=item_data.brand,
        base_rate=item_data.base_rate,
        labor_cost=item_data.labor_cost,
        material=item_data.material,
        dimensions=item_data.dimensions,
        user_id=uid,
    )
    db.add(new_item)
    await db.flush()
    return new_item

@router.put("/items/{item_id}", response_model=schemas.ItemResponse)
async def update_item(item_id: str, item_data: schemas.ItemUpdate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    item = await _own_item(db, item_id, uid)
    changes = item_data.model_dump(exclude_unset=True)
    if "category_id" in changes:
        await _visible_category(db, changes["category_id"], uid)
    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(item, field, value)
    await db.flush()
    return item

@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: str, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    item = await _own_item(db, item_id, uid)
    used = (await db.execute(select(func.count()).select_from(models.QuotationItem).where(models.QuotationItem.item_id == item_id))).scalar_one()
    if used:
        raise HTTPException(status_code=409, detail="This item is used in existing quotations and can't be deleted")
    await db.delete(item)
    await db.flush()
    return Response(status_code=204)

@router.get("/categories/{category_id}/items",response_model=List[schemas.ItemResponse])
async def get_category_items(category_id: str, response: Response, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    response.headers["Cache-Control"] = "private, max-age=30"
    result = await db.execute(select(models.Item).where(models.Item.category_id == category_id, _visible(models.Item, uid)).order_by(models.Item.name))
    return result.scalars().all()
