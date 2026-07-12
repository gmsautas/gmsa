from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import Expense, Project, Transaction, User
from app.schemas.finance import ExpenseCreate, ExpenseOut, FinanceSummaryOut, TransactionOut

router = APIRouter(prefix="/admin", tags=["finance"])


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    type: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[TransactionOut]:
    query = select(Transaction).options(
        selectinload(Transaction.user), selectinload(Transaction.project)
    )

    if type:
        query = query.where(Transaction.type == type)
    if status_:
        query = query.where(Transaction.status == status_)

    query = query.order_by(Transaction.created_at.desc())
    result = await db.execute(query)
    transactions = result.scalars().all()

    return [
        TransactionOut(
            id=tx.id,
            date=tx.created_at.date(),
            name=tx.user.name if tx.user else (tx.donor_name or "Anonymous"),
            type=tx.type,
            description=tx.description,
            amount=tx.amount,
            currency=tx.currency,
            method=tx.method,
            reference=tx.reference,
            status=tx.status,
            project=tx.project.title if tx.project else None,
        )
        for tx in transactions
    ]


@router.get("/expenses", response_model=list[ExpenseOut])
async def list_expenses(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[ExpenseOut]:
    query = (
        select(Expense)
        .options(selectinload(Expense.recorded_by))
        .order_by(Expense.date.desc())
    )
    result = await db.execute(query)
    expenses = result.scalars().all()

    return [
        ExpenseOut(
            id=expense.id,
            date=expense.date,
            description=expense.description,
            category=expense.category,
            amount=expense.amount,
            currency=expense.currency,
            recorded_by=expense.recorded_by.name if expense.recorded_by else None,
            receipt_url=expense.receipt_url,
        )
        for expense in expenses
    ]


@router.post("/expenses", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ExpenseOut:
    data = payload.model_dump()
    if data.get("date") is None:
        data["date"] = date.today()

    expense = Expense(**data, recorded_by_id=admin.id)
    db.add(expense)
    await db.commit()
    await db.refresh(expense)

    return ExpenseOut(
        id=expense.id,
        date=expense.date,
        description=expense.description,
        category=expense.category,
        amount=expense.amount,
        currency=expense.currency,
        recorded_by=admin.name,
        receipt_url=expense.receipt_url,
    )


@router.get("/finance/summary", response_model=FinanceSummaryOut)
async def finance_summary(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> FinanceSummaryOut:
    total_income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.status == "success"
        )
    )
    total_income = total_income_result.scalar_one()

    total_expenses_result = await db.execute(
        select(func.coalesce(func.sum(Expense.amount), 0))
    )
    total_expenses = total_expenses_result.scalar_one()

    active_projects_result = await db.execute(
        select(func.count()).select_from(Project).where(Project.status == "open")
    )
    active_projects = active_projects_result.scalar_one()

    return FinanceSummaryOut(
        total_income=total_income,
        total_expenses=total_expenses,
        net_balance=total_income - total_expenses,
        active_projects=active_projects,
    )
