from schemas import WalletAuth, GameResultCreate, GameResultResponse, ScoreSubmission
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import func
from datetime import datetime
import os

# -------------------
# Настройка приложения
# -------------------
app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# -------------------
# NEW: Модель пользователя (wallet-based)
# -------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    wallet_raw = Column(String, unique=True, index=True, nullable=False)
    wallet_user_friendly = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    total_earned = Column(Float, default=0.0)
    tournaments_won = Column(Integer, default=0)
    games_played = Column(Integer, default=0)

class GameResult(Base):
    __tablename__ = "game_results"
    id = Column(Integer, primary_key=True, index=True)
    wallet_raw = Column(String(64), ForeignKey("users.wallet_raw", ondelete="CASCADE"))
    wallet_user_friendly = Column(String, nullable=False)
    game_name = Column(String(64), nullable=False)
    score = Column(Float, nullable=False)
    played_at = Column(DateTime(timezone=True), server_default=func.now())

Base.metadata.create_all(bind=engine)

# -------------------
# CORS
# -------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажите конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------
# NEW: Wallet Authentication (FIXED VALIDATION)
# -------------------
@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth):
    db = SessionLocal()
    
    # Берём raw и user-friendly
    wallet_raw = auth.wallet_raw.strip()
    wallet_user_friendly = auth.wallet_user_friendly.strip()
    
    # Проверка
    if not wallet_raw or not wallet_user_friendly:
        db.close()
        raise HTTPException(status_code=400, detail="Wallet addresses cannot be empty")
    
    # Проверка существующего пользователя по raw адресу
    existing_user = db.query(User).filter(User.wallet_raw == wallet_raw).first()
    
    if not existing_user:
        # Регистрация нового
        new_user = User(
            wallet_raw=wallet_raw,
            wallet_user_friendly=wallet_user_friendly,
            created_at=datetime.utcnow(),
            total_earned=0.0,
            tournaments_won=0,
            games_played=0
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        db.close()
        return {"message": "User registered", "user": new_user.__dict__}
    
    db.close()
    return {"message": "Login successful", "user": existing_user.__dict__}


# -------------------
# Get user by wallet address
# -------------------
@app.get("/user/{wallet_address}")
def get_user(wallet_address: str):
    """
    Get user details by wallet address
    """
    db = SessionLocal()
    user = db.query(User).filter(User.wallet_address == wallet_address).first()
    db.close()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "wallet_address": user.wallet_address,
        "created_at": user.created_at.isoformat(),
        "total_earned": user.total_earned,
        "tournaments_won": user.tournaments_won,
        "games_played": user.games_played
    }

# -------------------
# Submit game score
# -------------------
@app.post("/submit-score")
def submit_score(submission: ScoreSubmission):
    """
    Submit a game score for a wallet address
    """
    db = SessionLocal()
    
    # Find user by wallet
    user = db.query(User).filter(User.wallet_raw == submission.wallet).first()
    
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found. Please connect wallet first.")
    
    # Update games played count
    user.games_played += 1
    db.commit()
    
    print(f"🎮 Score submitted - Wallet: {submission.wallet}, Game: {submission.game}, Score: {submission.score}")
    
    # TODO: Here you would:
    # 1. Store the score in a separate scores table
    # 2. Validate the score with anti-cheat logic
    # 3. Update leaderboards
    # 4. Check if user won a tournament and update total_earned
    
    db.close()
    
    return {
        "message": "Score submitted successfully",
        "game": submission.game,
        "score": submission.score,
        "wallet": submission.wallet
    }

# -------------------
# Update user earnings (admin endpoint - add auth later!)
# -------------------
@app.post("/update-earnings")
def update_earnings(wallet_address: str, amount: float, tournament_win: bool = False):
    """
    Update user's earnings after winning a tournament
    NOTE: This should be protected with admin authentication in production!
    """
    db = SessionLocal()
    
    user = db.query(User).filter(User.wallet_address == wallet_address).first()
    
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    user.total_earned += amount
    if tournament_win:
        user.tournaments_won += 1
    
    db.commit()
    db.refresh(user)
    
    print(f"💰 Earnings updated - Wallet: {wallet_address}, Amount: {amount} TON")
    
    response = {
        "message": "Earnings updated",
        "wallet_address": user.wallet_address,
        "total_earned": user.total_earned,
        "tournaments_won": user.tournaments_won
    }
    
    db.close()
    return response

# -------------------
# Get leaderboard
# -------------------
"""
@app.get("/leaderboard")
def get_leaderboard(limit: int = 10):
"""
#Get top earners leaderboard
"""
    db = SessionLocal()
    
    top_users = db.query(User).order_by(User.total_earned.desc()).limit(limit).all()
    
    leaderboard = [
        {
            "wallet_address": user.wallet_address,
            "wallet_short": f"{user.wallet_address[:6]}...{user.wallet_address[-4:]}",
            "total_earned": user.total_earned,
            "tournaments_won": user.tournaments_won,
            "games_played": user.games_played
        }
        for user in top_users
    ]
    
    db.close()
    return {"leaderboard": leaderboard}
"""

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/game/result", response_model=GameResultResponse)
def save_game_result(result: GameResultCreate, db: Session = Depends(get_db)):
    # Проверяем, что пользователь существует
    user = db.query(models.User).filter(models.User.wallet_raw == result.wallet_raw).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_result = models.GameResult(**result.dict())
    db.add(new_result)

    # Можно инкрементировать количество сыгранных игр
    user.games_played += 1

    db.commit()
    db.refresh(new_result)

    return new_result

# -------------------
# Health check
# -------------------
@app.get("/")
def health_check():
    return {
        "status": "ok", 
        "message": "TonArcade API is running",
        "endpoints": {
            "auth": "/auth/wallet",
            "user": "/user/{wallet_address}",
            "submit_score": "/submit-score",
            "leaderboard": "/leaderboard"
        }
    }

'''from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, desc, and_
from sqlalchemy.orm import Session, sessionmaker
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel
import os

# Import models (assuming models.py exists with the previous schema)
from models import Base, User, TicketTransaction, GameEvent, EventParticipation, GameResult, TicketPackage, EventStatus

app = FastAPI()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

# CORS - FIXED VERSION
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*"  # Allow all origins for now, or specify your domains
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============= SCHEMAS =============
class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TicketPurchaseRequest(BaseModel):
    wallet: str
    package_id: int
    transaction_hash: str  # TON blockchain transaction hash

class EventCreateRequest(BaseModel):
    event_name: str
    game_type: str
    start_time: datetime
    duration_minutes: int
    ticket_cost: int
    prize_pool_ton: float
    max_participants: Optional[int] = None

class EventJoinRequest(BaseModel):
    wallet: str
    event_id: int

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: dict
    timestamp: int

# ============= USER & AUTH =============
@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth, db: Session = Depends(get_db)):
    wallet_raw = auth.wallet_raw.strip()
    wallet_user_friendly = auth.wallet_user_friendly.strip()
    
    if not wallet_raw or not wallet_user_friendly:
        raise HTTPException(status_code=400, detail="Invalid wallet addresses")
    
    user = db.query(User).filter(User.wallet_raw == wallet_raw).first()
    
    if not user:
        # Register new user with welcome bonus
        user = User(
            wallet_raw=wallet_raw,
            wallet_user_friendly=wallet_user_friendly,
            ticket_balance=5  # Welcome bonus: 5 free tickets!
        )
        db.add(user)
        db.flush()  # This assigns the user.id without committing
        
        # Log welcome bonus - now user.id is available
        bonus_tx = TicketTransaction(
            user_id=user.id,
            wallet_raw=wallet_raw,
            transaction_type="bonus",
            amount=5,
            description="Welcome bonus"
        )
        db.add(bonus_tx)
        
        db.commit()
        db.refresh(user)
        
        return {
            "message": "Welcome! You received 5 free tickets!",
            "user": {
                "wallet_raw": user.wallet_raw,
                "wallet_user_friendly": user.wallet_user_friendly,
                "ticket_balance": user.ticket_balance,
                "total_earned": user.total_earned,
                "tournaments_won": user.tournaments_won,
                "games_played": user.games_played,
                "created_at": user.created_at.isoformat(),
                "is_new": True
            }
        }
    
    if user.is_banned:
        raise HTTPException(status_code=403, detail=f"Account banned: {user.ban_reason}")
    
    return {
        "message": "Login successful",
        "user": {
            "wallet_raw": user.wallet_raw,
            "wallet_user_friendly": user.wallet_user_friendly,
            "ticket_balance": user.ticket_balance,
            "total_earned": user.total_earned,
            "tournaments_won": user.tournaments_won,
            "games_played": user.games_played,
            "created_at": user.created_at.isoformat()
        }
    }

# ============= TICKET PACKAGES =============
@app.get("/tickets/packages")
def get_ticket_packages(db: Session = Depends(get_db)):
    packages = db.query(TicketPackage).filter(
        TicketPackage.is_active == True
    ).order_by(TicketPackage.display_order).all()
    
    return {
        "packages": [
            {
                "id": p.id,
                "name": p.package_name,
                "tickets": p.ticket_amount,
                "bonus": p.bonus_tickets,
                "total_tickets": p.ticket_amount + p.bonus_tickets,
                "price_ton": p.price_ton,
                "value": round((p.ticket_amount + p.bonus_tickets) / p.price_ton, 2)
            }
            for p in packages
        ]
    }

@app.post("/tickets/purchase")
def purchase_tickets(request: TicketPurchaseRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.wallet_raw == request.wallet).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    package = db.query(TicketPackage).filter(TicketPackage.id == request.package_id).first()
    if not package or not package.is_active:
        raise HTTPException(status_code=404, detail="Package not found")
    
    # TODO: Verify transaction_hash on TON blockchain
    # For now, we trust the hash (add verification in production!)
    
    total_tickets = package.ticket_amount + package.bonus_tickets
    user.ticket_balance += total_tickets
    
    # Log transaction
    tx = TicketTransaction(
        user_id=user.id,
        wallet_raw=user.wallet_raw,
        transaction_type="purchase",
        amount=total_tickets,
        ton_amount=package.price_ton,
        description=f"Purchased {package.package_name}",
        transaction_hash=request.transaction_hash
    )
    db.add(tx)
    db.commit()
    
    return {
        "message": f"Purchase successful! +{total_tickets} tickets",
        "new_balance": user.ticket_balance,
        "tickets_added": total_tickets
    }

# ============= GAME EVENTS =============
@app.get("/events")
def get_events(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(GameEvent)
    
    if status:
        query = query.filter(GameEvent.status == status)
    else:
        # By default, show upcoming and active events
        query = query.filter(GameEvent.status.in_([EventStatus.UPCOMING, EventStatus.ACTIVE]))
    
    events = query.order_by(GameEvent.start_time).all()
    
    return {
        "events": [
            {
                "id": e.id,
                "name": e.event_name,
                "game_type": e.game_type,
                "start_time": e.start_time.isoformat(),
                "end_time": e.end_time.isoformat(),
                "ticket_cost": e.ticket_cost,
                "prize_pool": e.prize_pool_ton,
                "status": e.status.value,
                "participants": e.total_participants,
                "max_participants": e.max_participants,
                "is_full": e.max_participants and e.total_participants >= e.max_participants,
                "is_paid_out": e.is_paid_out
            }
            for e in events
        ]
    }

@app.post("/events/create")
def create_event(request: EventCreateRequest, db: Session = Depends(get_db)):
    # TODO: Add admin authentication here!
    
    end_time = request.start_time + timedelta(minutes=request.duration_minutes)
    
    event = GameEvent(
        event_name=request.event_name,
        game_type=request.game_type,
        start_time=request.start_time,
        end_time=end_time,
        ticket_cost=request.ticket_cost,
        max_participants=request.max_participants,
        prize_pool_ton=request.prize_pool_ton
    )
    
    db.add(event)
    db.commit()
    db.refresh(event)
    
    return {
        "message": "Event created",
        "event_id": event.id,
        "event": {
            "id": event.id,
            "name": event.event_name,
            "start_time": event.start_time.isoformat()
        }
    }

@app.post("/events/join")
def join_event(request: EventJoinRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.wallet_raw == request.wallet).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    event = db.query(GameEvent).filter(GameEvent.id == request.event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Validation
    if event.status != EventStatus.UPCOMING:
        raise HTTPException(status_code=400, detail="Event already started or ended")
    
    if event.max_participants and event.total_participants >= event.max_participants:
        raise HTTPException(status_code=400, detail="Event is full")
    
    if user.ticket_balance < event.ticket_cost:
        raise HTTPException(status_code=400, detail=f"Insufficient tickets. Need {event.ticket_cost}, have {user.ticket_balance}")
    
    # Check if already joined
    existing = db.query(EventParticipation).filter(
        and_(
            EventParticipation.event_id == event.id,
            EventParticipation.user_id == user.id
        )
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Already joined this event")
    
    # Deduct tickets
    user.ticket_balance -= event.ticket_cost
    
    # Create participation
    participation = EventParticipation(
        event_id=event.id,
        user_id=user.id,
        wallet_raw=user.wallet_raw,
        tickets_spent=event.ticket_cost
    )
    db.add(participation)
    
    # Update event
    event.total_participants += 1
    
    # Log ticket spend
    tx = TicketTransaction(
        user_id=user.id,
        wallet_raw=user.wallet_raw,
        transaction_type="spent",
        amount=-event.ticket_cost,
        description=f"Joined event: {event.event_name}"
    )
    db.add(tx)
    
    db.commit()
    
    return {
        "message": "Successfully joined event!",
        "event_name": event.event_name,
        "tickets_spent": event.ticket_cost,
        "new_balance": user.ticket_balance
    }

# ============= SCORE SUBMISSION & VALIDATION =============
@app.post("/submit-score")
def submit_score(submission: ScoreSubmission, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.wallet_raw == submission.wallet).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Basic anti-cheat validation
    is_suspicious = False
    validation_notes = []
    
    # Check 1: Score reasonableness
    if submission.game == "aim-trainer":
        if submission.score > 10000:  # Adjust threshold
            is_suspicious = True
            validation_notes.append("Score exceeds maximum possible")
    
    # Check 2: Time validation
    if submission.gameData:
        game_duration = (submission.gameData.get("endTime", 0) - submission.gameData.get("startTime", 0)) / 1000
        if game_duration < 10:  # Game too short
            is_suspicious = True
            validation_notes.append("Game duration suspiciously short")
    
    # Save result
    result = GameResult(
        user_id=user.id,
        wallet_raw=user.wallet_raw,
        event_id=submission.event_id,
        game_name=submission.game,
        score=submission.score,
        game_data=submission.gameData,
        is_validated=not is_suspicious,
        is_suspicious=is_suspicious,
        validation_notes=", ".join(validation_notes) if validation_notes else None
    )
    db.add(result)
    
    # Update user stats
    user.games_played += 1
    
    # Update event participation if applicable
    if submission.event_id:
        participation = db.query(EventParticipation).filter(
            and_(
                EventParticipation.event_id == submission.event_id,
                EventParticipation.user_id == user.id
            )
        ).first()
        
        if participation:
            # Update best score if better
            if participation.best_score is None or submission.score > participation.best_score:
                participation.best_score = submission.score
    
    db.commit()
    
    response = {
        "message": "Score submitted",
        "score": submission.score,
        "validated": not is_suspicious
    }
    
    if is_suspicious:
        response["warning"] = "Score flagged for review"
    
    return response

# ============= LEADERBOARD =============
@app.get("/events/{event_id}/leaderboard")
def get_event_leaderboard(event_id: int, db: Session = Depends(get_db)):
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    participants = db.query(EventParticipation, User).join(
        User, User.id == EventParticipation.user_id
    ).filter(
        EventParticipation.event_id == event_id
    ).order_by(
        desc(EventParticipation.best_score)
    ).all()
    
    leaderboard = []
    for rank, (participation, user) in enumerate(participants, start=1):
        leaderboard.append({
            "rank": rank,
            "wallet": user.wallet_user_friendly[:8] + "..." + user.wallet_user_friendly[-6:],
            "score": participation.best_score,
            "prize": participation.prize_won if participation.is_paid else 0
        })
    
    return {
        "event_id": event_id,
        "event_name": event.event_name,
        "status": event.status.value,
        "leaderboard": leaderboard
    }

# ============= PRIZE DISTRIBUTION (Admin) =============
@app.post("/events/{event_id}/distribute-prizes")
def distribute_prizes(event_id: int, db: Session = Depends(get_db)):
    # TODO: Add admin authentication!
    
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if event.is_paid_out:
        raise HTTPException(status_code=400, detail="Prizes already distributed")
    
    if event.status != EventStatus.ENDED:
        raise HTTPException(status_code=400, detail="Event not ended yet")
    
    # Get top participants
    participants = db.query(EventParticipation, User).join(
        User, User.id == EventParticipation.user_id
    ).filter(
        and_(
            EventParticipation.event_id == event_id,
            EventParticipation.best_score.isnot(None)
        )
    ).order_by(
        desc(EventParticipation.best_score)
    ).limit(3).all()
    
    if not participants:
        raise HTTPException(status_code=400, detail="No valid participants")
    
    # Distribute prizes
    prize_pool = event.prize_pool_ton
    distribution = event.prize_distribution
    
    for rank, (participation, user) in enumerate(participants, start=1):
        rank_key = f"{rank}{'st' if rank == 1 else 'nd' if rank == 2 else 'rd'}"
        prize_percentage = distribution.get(rank_key, 0)
        prize_amount = prize_pool * prize_percentage
        
        participation.final_rank = rank
        participation.prize_won = prize_amount
        participation.is_paid = True
        
        user.total_earned += prize_amount
        if rank == 1:
            user.tournaments_won += 1
    
    event.is_paid_out = True
    event.status = EventStatus.ENDED
    
    db.commit()
    
    return {
        "message": "Prizes distributed",
        "total_distributed": prize_pool,
        "winners": [
            {
                "rank": p[0].final_rank,
                "wallet": p[1].wallet_user_friendly[:8] + "...",
                "prize": p[0].prize_won
            }
            for p in participants
        ]
    }

# ============= HEALTH CHECK =============
@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "TonArcade P2E API v2.0",
        "cors_enabled": True
    }

# Add OPTIONS handler for preflight requests
@app.options("/{full_path:path}")
async def options_handler():
    return {"status": "ok"}
'''
