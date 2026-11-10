import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from db import get_db
import models
import schemas
from deps import require_authenticated_user_id, require_admin

router = APIRouter(
    prefix="/api/v1/catalog",
    tags=["Master Catalog"],
)

def _visible(model, uid: str):
    """General catalogue (user_id NULL) plus the caller's own rows."""
    return or_(model.user_id == uid, model.user_id.is_(None))

def _item_out(item: models.Item, rate=None) -> dict:
    return {
        "id": item.id, "category_id": item.category_id, "subcategory": item.subcategory,
        "name": item.name, "pricing_type": item.pricing_type, "brand": item.brand,
        "base_rate": rate.base_rate if rate else item.base_rate,
        "labor_cost": rate.labor_cost if rate else item.labor_cost,
        "material": item.material, "dimensions": item.dimensions,
        "is_general": item.user_id is None, "has_custom_rate": rate is not None,
        "created_at": item.created_at,
    }

async def _items_out(db: AsyncSession, items, uid: str) -> list:
    ids = [i.id for i in items if i.user_id is None]
    rates = {}
    if ids:
        rows = (await db.execute(select(models.UserItemRate).where(
            models.UserItemRate.user_id == uid, models.UserItemRate.item_id.in_(ids)))).scalars().all()
        rates = {r.item_id: r for r in rows}
    return [_item_out(i, rates.get(i.id)) for i in items]

def _cat_out(c: models.Category) -> dict:
    return {"id": c.id, "name": c.name, "is_general": c.user_id is None, "created_at": c.created_at}

async def _own_item(db: AsyncSession, item_id: str, uid: str, own_only: bool = False) -> models.Item:
    scope = (models.Item.user_id == uid) if own_only else _visible(models.Item, uid)
    item = (await db.execute(select(models.Item).where(models.Item.id == item_id, scope))).scalars().first()
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
    return [_cat_out(c) for c in result.scalars().all()]

@router.post("/categories", response_model=schemas.CategoryResponse)
async def create_category(data: schemas.CategoryCreate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    name = data.name.strip()
    existing = (await db.execute(select(models.Category).where(func.lower(models.Category.name) == name.lower(), _visible(models.Category, uid)))).scalars().first()
    if existing:
        return _cat_out(existing)
    category = models.Category(id=str(uuid.uuid4()), name=name, user_id=uid)
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return _cat_out(category)

@router.get("/items", response_model=List[schemas.ItemResponse])
async def get_items(response: Response, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    response.headers["Cache-Control"] = "private, max-age=30"
    result = await db.execute(select(models.Item).where(_visible(models.Item, uid)).order_by(models.Item.subcategory, models.Item.name))
    return await _items_out(db, result.scalars().all(), uid)

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
        return (await _items_out(db, [existing_item], uid))[0]

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
    return _item_out(new_item)

@router.put("/items/{item_id}", response_model=schemas.ItemResponse)
async def update_item(item_id: str, item_data: schemas.ItemUpdate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    item = await _own_item(db, item_id, uid, own_only=True)
    changes = item_data.model_dump(exclude_unset=True)
    if "category_id" in changes:
        await _visible_category(db, changes["category_id"], uid)
    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(item, field, value)
    await db.flush()
    return _item_out(item)

@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: str, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    item = await _own_item(db, item_id, uid, own_only=True)
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
    return await _items_out(db, result.scalars().all(), uid)

# --- private rate override for general items (any user) ---
@router.put("/items/{item_id}/my-rate", response_model=schemas.ItemResponse)
async def set_my_rate(item_id: str, data: schemas.MyRateUpdate, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    item = await _own_item(db, item_id, uid)
    if item.user_id is not None:
        raise HTTPException(status_code=400, detail="Edit your own items directly")
    rate = (await db.execute(select(models.UserItemRate).where(
        models.UserItemRate.user_id == uid, models.UserItemRate.item_id == item_id))).scalars().first()
    if rate:
        rate.base_rate, rate.labor_cost = data.base_rate, data.labor_cost
    else:
        rate = models.UserItemRate(id=str(uuid.uuid4()), user_id=uid, item_id=item_id,
                                   base_rate=data.base_rate, labor_cost=data.labor_cost)
        db.add(rate)
    await db.flush()
    return _item_out(item, rate)

@router.delete("/items/{item_id}/my-rate", status_code=204)
async def reset_my_rate(item_id: str, db: AsyncSession = Depends(get_db), uid: str = Depends(require_authenticated_user_id)):
    rate = (await db.execute(select(models.UserItemRate).where(
        models.UserItemRate.user_id == uid, models.UserItemRate.item_id == item_id))).scalars().first()
    if rate:
        await db.delete(rate)
        await db.flush()
    return Response(status_code=204)

# --- Admin: manage the general catalogue ---
GENERAL = models.Category.user_id.is_(None)

async def _general_category(db: AsyncSession, category_id: str) -> models.Category:
    cat = (await db.execute(select(models.Category).where(models.Category.id == category_id, GENERAL))).scalars().first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat

async def _general_item(db: AsyncSession, item_id: str) -> models.Item:
    item = (await db.execute(select(models.Item).where(models.Item.id == item_id, models.Item.user_id.is_(None)))).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

async def _assert_unique_general_category(db: AsyncSession, name: str, exclude_id: str = None):
    q = select(models.Category.id).where(func.lower(models.Category.name) == name.lower(), GENERAL)
    if exclude_id:
        q = q.where(models.Category.id != exclude_id)
    if (await db.execute(q)).first():
        raise HTTPException(status_code=409, detail="A category with this name already exists")

@router.post("/admin/categories", response_model=schemas.CategoryResponse)
async def admin_create_category(data: schemas.CategoryCreate, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    name = data.name.strip()
    await _assert_unique_general_category(db, name)
    cat = models.Category(id=str(uuid.uuid4()), name=name, user_id=None)
    db.add(cat)
    await db.flush()
    await db.refresh(cat)
    return _cat_out(cat)

@router.put("/admin/categories/{category_id}", response_model=schemas.CategoryResponse)
async def admin_update_category(category_id: str, data: schemas.CategoryUpdate, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    cat = await _general_category(db, category_id)
    name = data.name.strip()
    await _assert_unique_general_category(db, name, exclude_id=category_id)
    cat.name = name
    await db.flush()
    return _cat_out(cat)

@router.delete("/admin/categories/{category_id}", status_code=204)
async def admin_delete_category(category_id: str, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    cat = await _general_category(db, category_id)
    count = (await db.execute(select(func.count()).select_from(models.Item).where(models.Item.category_id == category_id))).scalar_one()
    if count:
        raise HTTPException(status_code=409, detail="Category isn't empty. Delete or move its items first")
    await db.delete(cat)
    await db.flush()
    return Response(status_code=204)

@router.post("/admin/items", response_model=schemas.ItemResponse)
async def admin_create_item(data: schemas.ItemCreate, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    await _general_category(db, data.category_id)
    name = data.name.strip()
    dup = (await db.execute(select(models.Item.id).where(
        models.Item.category_id == data.category_id, models.Item.user_id.is_(None),
        func.lower(models.Item.name) == name.lower()))).first()
    if dup:
        raise HTTPException(status_code=409, detail="An item with this name already exists in this category")
    item = models.Item(id=str(uuid.uuid4()), category_id=data.category_id, subcategory=data.subcategory,
                       name=name, pricing_type=data.pricing_type, brand=data.brand, base_rate=data.base_rate,
                       labor_cost=data.labor_cost, material=data.material, dimensions=data.dimensions, user_id=None)
    db.add(item)
    await db.flush()
    return _item_out(item)

@router.put("/admin/items/{item_id}", response_model=schemas.ItemResponse)
async def admin_update_item(item_id: str, data: schemas.ItemUpdate, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    item = await _general_item(db, item_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("category_id"):
        await _general_category(db, changes["category_id"])
    if changes.get("name"):
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(item, field, value)
    await db.flush()
    return _item_out(item)

@router.delete("/admin/items/{item_id}", status_code=204)
async def admin_delete_item(item_id: str, db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin)):
    item = await _general_item(db, item_id)
    used = (await db.execute(select(func.count()).select_from(models.QuotationItem).where(models.QuotationItem.item_id == item_id))).scalar_one()
    if used:
        raise HTTPException(status_code=409, detail="This item is used in existing quotations and can't be deleted")
    await db.delete(item)
    await db.flush()
    return Response(status_code=204)
