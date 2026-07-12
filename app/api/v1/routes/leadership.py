from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import Committee, CommitteeMember, LeadershipBoard, LeadershipMember
from app.schemas.leadership import (
    CommitteeCreate,
    CommitteeMemberCreate,
    CommitteeMemberOut,
    CommitteeMemberUpdate,
    CommitteeOut,
    CommitteeUpdate,
    LeadershipBoardCreate,
    LeadershipBoardOut,
    LeadershipBoardUpdate,
    LeadershipMemberCreate,
    LeadershipMemberOut,
    LeadershipMemberUpdate,
)

router = APIRouter(prefix="/leadership", tags=["leadership"])


def _board_load_options():
    return (
        selectinload(LeadershipBoard.members),
        selectinload(LeadershipBoard.committees).selectinload(Committee.members),
    )


@router.get("", response_model=list[LeadershipBoardOut])
async def list_leadership_boards(db: AsyncSession = Depends(get_db)) -> list[LeadershipBoard]:
    result = await db.execute(
        select(LeadershipBoard)
        .options(*_board_load_options())
        .order_by(LeadershipBoard.is_current.desc(), LeadershipBoard.id.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/boards",
    response_model=LeadershipBoardOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_leadership_board(
    payload: LeadershipBoardCreate, db: AsyncSession = Depends(get_db)
) -> LeadershipBoard:
    board = LeadershipBoard(**payload.model_dump())
    db.add(board)
    await db.commit()
    await db.refresh(board, attribute_names=["members", "committees"])
    return board


@router.patch(
    "/boards/{board_id}",
    response_model=LeadershipBoardOut,
    dependencies=[Depends(require_admin)],
)
async def update_leadership_board(
    board_id: int, payload: LeadershipBoardUpdate, db: AsyncSession = Depends(get_db)
) -> LeadershipBoard:
    result = await db.execute(
        select(LeadershipBoard)
        .options(*_board_load_options())
        .where(LeadershipBoard.id == board_id)
    )
    board = result.scalar_one_or_none()
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership board not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(board, field, value)
    await db.commit()
    await db.refresh(board, attribute_names=["members", "committees"])
    return board


@router.delete(
    "/boards/{board_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_leadership_board(board_id: int, db: AsyncSession = Depends(get_db)) -> None:
    board = await db.get(LeadershipBoard, board_id)
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership board not found")

    await db.delete(board)
    await db.commit()


@router.post(
    "/boards/{board_id}/members",
    response_model=LeadershipMemberOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_leadership_member(
    board_id: int, payload: LeadershipMemberCreate, db: AsyncSession = Depends(get_db)
) -> LeadershipMember:
    board = await db.get(LeadershipBoard, board_id)
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership board not found")

    member = LeadershipMember(board_id=board_id, **payload.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.patch(
    "/members/{member_id}",
    response_model=LeadershipMemberOut,
    dependencies=[Depends(require_admin)],
)
async def update_leadership_member(
    member_id: int, payload: LeadershipMemberUpdate, db: AsyncSession = Depends(get_db)
) -> LeadershipMember:
    member = await db.get(LeadershipMember, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership member not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    await db.commit()
    await db.refresh(member)
    return member


@router.delete(
    "/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_leadership_member(member_id: int, db: AsyncSession = Depends(get_db)) -> None:
    member = await db.get(LeadershipMember, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership member not found")

    await db.delete(member)
    await db.commit()


@router.post(
    "/boards/{board_id}/committees",
    response_model=CommitteeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_committee(
    board_id: int, payload: CommitteeCreate, db: AsyncSession = Depends(get_db)
) -> Committee:
    board = await db.get(LeadershipBoard, board_id)
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leadership board not found")

    committee = Committee(board_id=board_id, **payload.model_dump())
    db.add(committee)
    await db.commit()
    await db.refresh(committee, attribute_names=["members"])
    return committee


@router.patch(
    "/committees/{committee_id}",
    response_model=CommitteeOut,
    dependencies=[Depends(require_admin)],
)
async def update_committee(
    committee_id: int, payload: CommitteeUpdate, db: AsyncSession = Depends(get_db)
) -> Committee:
    result = await db.execute(
        select(Committee)
        .options(selectinload(Committee.members))
        .where(Committee.id == committee_id)
    )
    committee = result.scalar_one_or_none()
    if committee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Committee not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(committee, field, value)
    await db.commit()
    await db.refresh(committee, attribute_names=["members"])
    return committee


@router.delete(
    "/committees/{committee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_committee(committee_id: int, db: AsyncSession = Depends(get_db)) -> None:
    committee = await db.get(Committee, committee_id)
    if committee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Committee not found")

    await db.delete(committee)
    await db.commit()


@router.post(
    "/committees/{committee_id}/members",
    response_model=CommitteeMemberOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_committee_member(
    committee_id: int, payload: CommitteeMemberCreate, db: AsyncSession = Depends(get_db)
) -> CommitteeMember:
    committee = await db.get(Committee, committee_id)
    if committee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Committee not found")

    member = CommitteeMember(committee_id=committee_id, **payload.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.patch(
    "/committee-members/{member_id}",
    response_model=CommitteeMemberOut,
    dependencies=[Depends(require_admin)],
)
async def update_committee_member(
    member_id: int, payload: CommitteeMemberUpdate, db: AsyncSession = Depends(get_db)
) -> CommitteeMember:
    member = await db.get(CommitteeMember, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Committee member not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    await db.commit()
    await db.refresh(member)
    return member


@router.delete(
    "/committee-members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_committee_member(member_id: int, db: AsyncSession = Depends(get_db)) -> None:
    member = await db.get(CommitteeMember, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Committee member not found")

    await db.delete(member)
    await db.commit()
