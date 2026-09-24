from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.tables import Boat, Declaration, Pilot, Ship
from app.schemas import (
    BoatOut,
    DeclarationIn,
    DeclarationOut,
    PilotOut,
    ShipOut,
)
from app.serializers import boat_out, declaration_out, pilot_out, ship_out
from app.services.declarations import DeclError, create_declaration
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/declarations", response_model=DeclarationOut, status_code=201)
def post_declaration(
    payload: DeclarationIn, db: Session = Depends(get_session)
) -> DeclarationOut:
    try:
        decl = create_declaration(db, payload)
    except DeclError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )
    return declaration_out(decl)


@router.get("/declarations", response_model=list[DeclarationOut])
def list_declarations(db: Session = Depends(get_session)):
    rows = db.query(Declaration).order_by(Declaration.id).all()
    return [declaration_out(d) for d in rows]


@router.get("/ships", response_model=list[ShipOut])
def list_ships(db: Session = Depends(get_session)):
    return [ship_out(s) for s in db.query(Ship).order_by(Ship.id).all()]


@router.get("/pilots", response_model=list[PilotOut])
def list_pilots(db: Session = Depends(get_session)):
    rows = db.query(Pilot).order_by(Pilot.id).all()
    return [pilot_out(p) for p in rows]


@router.patch("/pilots/{pilot_id}", response_model=PilotOut)
def set_pilot_active(
    pilot_id: str, active: bool, db: Session = Depends(get_session)
):
    pilot = db.get(Pilot, pilot_id)
    if pilot is None:
        return JSONResponse(
            status_code=404,
            content={"error": "pilot_not_found", "message": f"引航员 {pilot_id} 不存在"},
        )
    pilot.active = active
    db.commit()
    db.refresh(pilot)
    return pilot_out(pilot)


@router.get("/boats", response_model=list[BoatOut])
def list_boats(db: Session = Depends(get_session)):
    return [boat_out(b) for b in db.query(Boat).order_by(Boat.id).all()]
