from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import func
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from enum import Enum
import os
import secrets
import hashlib

# ===== APP SETUP =====
app = FastAPI(title="TonArcade API")

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "your_admin_secret_here")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ===== MODELS =====

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    wallet_raw = Column(String, unique=True, index=True, nullable=False)
    wallet_user_friendly = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    total_earned = Column(Float, default=0.0)
    tournaments_won = Column(Integer, default=0)
    games_played = Column(Integer, default=0)

class TournamentStatus(str, Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    COMPLETED = "completed"

class Tournament(Base):
    __tablename__ = "tournaments"
    
    id = Column(Integer, primary_key=True, index=True)
    contract_address = Column(String, unique=True, index=True)
    game_type = Column(String, nullable=False)
    
    entry_fee_ton = Column(Float, nullable=False)
    total_pool = Column(Float, default=0.0)
    initial_prize_pool = Column(Float, default=0.0)
    
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    max_participants = Column(Integer, nullable=True)
    total_participants = Column(Integer, default=0)
    
    status = Column(String, default=TournamentStatus.ACTIVE)
    is_finalized = Column(Boolean, default=False)

class TournamentScore(Base):
    __tablename__ = "tournament_scores"
    
    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id", ondelete="CASCADE"))
    wallet_raw = Column(String, nullable=False)
    
    score = Column(Float, nullable=False)
    game_data = Column(JSON, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    
    is_validated = Column(Boolean, default=False)
    is_suspicious = Column(Boolean, default=False)

# Create tables
Base.metadata.create_all(bind=engine)

# ===== SCHEMAS =====

class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TournamentCreateRequest(BaseModel):
    game_type: str
    entry_fee: float
    duration_hours: int
    prize_pool: float = 0
    max_participants: Optional[int] = None

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: Dict[str, Any]
    timestamp: int

# ===== DEPENDENCIES =====

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_admin(admin_secret: str = Header(None, alias="X-Admin-Secret")):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

# ===== CORS =====

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== BASIC ENDPOINTS =====

@app.get("/")
def health_check():
    return {
        "status": "ok", 
        "message": "TonArcade API is running",
        "endpoints": {
            "auth": "/auth/wallet",
            "tournaments": "/tournaments",
            "admin": "/admin/tournaments/create"
        }
    }

@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth, db: Session = Depends(get_db)):
    wallet_raw = auth.wallet_raw.strip()
    wallet_user_friendly = auth.wallet_user_friendly.strip()
    
    if not wallet_raw or not wallet_user_friendly:
        raise HTTPException(status_code=400, detail="Wallet addresses cannot be empty")
    
    existing_user = db.query(User).filter(User.wallet_raw == wallet_raw).first()
    
    if not existing_user:
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
        return {"message": "User registered", "user": {
            "wallet_raw": new_user.wallet_raw,
            "wallet_user_friendly": new_user.wallet_user_friendly,
            "created_at": new_user.created_at.isoformat(),
            "total_earned": new_user.total_earned,
            "tournaments_won": new_user.tournaments_won,
            "games_played": new_user.games_played
        }}
    
    return {"message": "Login successful", "user": {
        "wallet_raw": existing_user.wallet_raw,
        "wallet_user_friendly": existing_user.wallet_user_friendly,
        "created_at": existing_user.created_at.isoformat(),
        "total_earned": existing_user.total_earned,
        "tournaments_won": existing_user.tournaments_won,
        "games_played": existing_user.games_played
    }}

# ===== ADMIN ENDPOINTS =====

@app.post("/admin/tournaments/create")
def admin_create_tournament(
    request: TournamentCreateRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Create a new tournament (ADMIN ONLY)"""
    contract_address = f"EQ{secrets.token_hex(32)}"
    
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(hours=request.duration_hours)
    
    tournament = Tournament(
        contract_address=contract_address,
        game_type=request.game_type,
        entry_fee_ton=request.entry_fee,
        initial_prize_pool=request.prize_pool,
        total_pool=request.prize_pool,
        start_time=start_time,
        end_time=end_time,
        max_participants=request.max_participants,
        status=TournamentStatus.ACTIVE
    )
    
    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    
    return {
        "tournament_id": tournament.id,
        "contract_address": tournament.contract_address,
        "game_type": tournament.game_type,
        "entry_fee": tournament.entry_fee_ton,
        "end_time": tournament.end_time.isoformat(),
        "message": "Tournament created successfully"
    }

@app.get("/admin/tournaments/{tournament_id}/status")
def admin_get_tournament_status(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Get detailed tournament status (ADMIN ONLY)"""
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    top_scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .order_by(TournamentScore.score.desc())\
        .limit(10)\
        .all()
    
    return {
        "tournament": {
            "id": tournament.id,
            "contract_address": tournament.contract_address,
            "game_type": tournament.game_type,
            "entry_fee_ton": tournament.entry_fee_ton,
            "total_pool": tournament.total_pool,
            "start_time": tournament.start_time.isoformat(),
            "end_time": tournament.end_time.isoformat(),
            "status": tournament.status,
            "total_participants": tournament.total_participants,
            "max_participants": tournament.max_participants
        },
        "top_scores": [
            {
                "wallet_raw": score.wallet_raw,
                "score": score.score,
                "submitted_at": score.submitted_at.isoformat(),
                "is_validated": score.is_validated
            }
            for score in top_scores
        ]
    }

@app.post("/admin/tournaments/{tournament_id}/finalize")
def admin_finalize_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Finalize tournament and generate signed payouts (ADMIN ONLY)"""
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament.is_finalized:
        raise HTTPException(status_code=400, detail="Tournament already finalized")
    
    top_scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.is_validated == True)\
        .order_by(TournamentScore.score.desc())\
        .limit(3)\
        .all()
    
    if len(top_scores) == 0:
        raise HTTPException(status_code=400, detail="No valid scores submitted")
    
    total_pool = tournament.total_pool
    
    if len(top_scores) == 1:
        prizes = {1: total_pool}
    elif len(top_scores) == 2:
        prizes = {1: total_pool * 0.70, 2: total_pool * 0.30}
    else:
        prizes = {1: total_pool * 0.50, 2: total_pool * 0.30, 3: total_pool * 0.20}
    
    payouts = []
    
    for rank, score_entry in enumerate(top_scores, 1):
        prize_amount = prizes.get(rank, 0)
        
        if prize_amount <= 0:
            continue
        
        nonce = secrets.randbits(64)
        
        payload_hash = hashlib.sha256(
            f"{score_entry.wallet_raw}{prize_amount}{nonce}{tournament.id}".encode()
        ).hexdigest()
        
        payouts.append({
            "rank": rank,
            "wallet": score_entry.wallet_raw,
            "score": score_entry.score,
            "prize_ton": prize_amount,
            "payload_hash": payload_hash,
            "nonce": nonce
        })
    
    tournament.status = TournamentStatus.COMPLETED
    tournament.is_finalized = True
    db.commit()
    
    return {
        "message": "Tournament finalized",
        "tournament_id": tournament.id,
        "total_pool": total_pool,
        "payouts": payouts
    }

@app.post("/admin/tournaments/{tournament_id}/delete")
def admin_delete_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Delete/cancel a tournament (ADMIN ONLY)"""
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    db.query(TournamentScore).filter(TournamentScore.tournament_id == tournament_id).delete()
    db.delete(tournament)
    db.commit()
    
    return {"message": "Tournament deleted", "tournament_id": tournament_id}

# ===== PUBLIC ENDPOINTS =====

@app.get("/tournaments")
def get_tournaments(
    status: Optional[str] = None,
    game_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all tournaments (PUBLIC)"""
    query = db.query(Tournament)
    
    if status:
        query = query.filter(Tournament.status == status)
    
    if game_type:
        query = query.filter(Tournament.game_type == game_type)
    
    tournaments = query.order_by(Tournament.created_at.desc()).all()
    
    return {
        "tournaments": [
            {
                "id": t.id,
                "contract_address": t.contract_address,
                "game_type": t.game_type,
                "entry_fee_ton": t.entry_fee_ton,
                "total_pool": t.total_pool,
                "start_time": t.start_time.isoformat(),
                "end_time": t.end_time.isoformat(),
                "status": t.status,
                "total_participants": t.total_participants,
                "max_participants": t.max_participants,
                "is_full": t.max_participants and t.total_participants >= t.max_participants
            }
            for t in tournaments
        ]
    }

@app.get("/tournaments/{tournament_id}")
def get_tournament_details(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    """Get tournament details with leaderboard (PUBLIC)"""
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.is_validated == True)\
        .order_by(TournamentScore.score.desc())\
        .limit(20)\
        .all()
    
    return {
        "tournament": {
            "id": tournament.id,
            "contract_address": tournament.contract_address,
            "game_type": tournament.game_type,
            "entry_fee_ton": tournament.entry_fee_ton,
            "total_pool": tournament.total_pool,
            "start_time": tournament.start_time.isoformat(),
            "end_time": tournament.end_time.isoformat(),
            "status": tournament.status,
            "total_participants": tournament.total_participants
        },
        "leaderboard": [
            {
                "rank": idx + 1,
                "wallet": score.wallet_raw[:6] + "..." + score.wallet_raw[-4:],
                "wallet_full": score.wallet_raw,
                "score": score.score,
                "submitted_at": score.submitted_at.isoformat()
            }
            for idx, score in enumerate(scores)
        ]
    }

@app.post("/tournaments/{tournament_id}/submit")
def submit_tournament_score(
    tournament_id: int,
    submission: ScoreSubmission,
    db: Session = Depends(get_db)
):
    """Submit a score to a tournament (PUBLIC)"""
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament.status != TournamentStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Tournament is not active")
    
    if datetime.utcnow() > tournament.end_time:
        raise HTTPException(status_code=400, detail="Tournament has ended")
    
    if submission.score <= 0 or submission.score > 999999:
        raise HTTPException(status_code=400, detail="Invalid score")
    
    existing = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.wallet_raw == submission.wallet)\
        .first()
    
    if existing:
        if submission.score > existing.score:
            existing.score = submission.score
            existing.game_data = submission.gameData
            existing.submitted_at = datetime.utcnow()
            db.commit()
            return {"message": "Score updated", "score": submission.score}
        else:
            return {"message": "Previous score was better", "score": existing.score}
    
    new_score = TournamentScore(
        tournament_id=tournament_id,
        wallet_raw=submission.wallet,
        score=submission.score,
        game_data=submission.gameData,
        is_validated=True
    )
    
    db.add(new_score)
    tournament.total_participants += 1
    db.commit()
    
    return {
        "message": "Score submitted successfully",
        "tournament_id": tournament_id,
        "score": submission.score
    }
'''
from schemas import WalletAuth, ScoreSubmission
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import func
from datetime import datetime, timedelta
from typing import Optional, List
from enum import Enum
import os
import secrets
import hashlib

# -------------------
# Настройка приложения
# -------------------
app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_SECRET = "your_secret_key_here"  # TODO: Move to .env
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

class TournamentStatus(str, Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    COMPLETED = "completed"

class Tournament(Base):
    __tablename__ = "tournaments"
    
    id = Column(Integer, primary_key=True, index=True)
    contract_address = Column(String, unique=True, index=True)
    game_type = Column(String, nullable=False)  # 'aim', 'color', 'reflex'
    
    # Entry & Prize
    entry_fee_ton = Column(Float, nullable=False)
    total_pool = Column(Float, default=0.0)
    initial_prize_pool = Column(Float, default=0.0)  # Seeded prize pool
    
    # Timing
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Limits
    max_participants = Column(Integer, nullable=True)  # NULL = unlimited
    total_participants = Column(Integer, default=0)
    
    # Status
    status = Column(String, default=TournamentStatus.ACTIVE)
    is_finalized = Column(Boolean, default=False)

class TournamentScore(Base):
    __tablename__ = "tournament_scores"
    
    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id", ondelete="CASCADE"))
    wallet_raw = Column(String, nullable=False)
    
    score = Column(Float, nullable=False)
    game_data = Column(JSON, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    
    # Anti-cheat
    is_validated = Column(Boolean, default=False)
    is_suspicious = Column(Boolean, default=False)

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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

#----!---!!!---!----
#----!---!!!---!----
#----!---!!!---!----

# ============= ADMIN MIDDLEWARE =============

def verify_admin(admin_secret: str = Header(None, alias="X-Admin-Secret")):
    """Verify admin authentication"""
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

# ============= ADMIN ENDPOINTS =============

@app.post("/admin/tournaments/create")
def admin_create_tournament(
    game_type: str,
    entry_fee: float,
    duration_hours: int,
    prize_pool: float = 0,
    max_participants: Optional[int] = None,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """
    Create a new tournament (ADMIN ONLY)
    """
    # Generate contract address (for now, simulated)
    # TODO: Deploy actual smart contract
    contract_address = f"EQ{secrets.token_hex(32)}"
    
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(hours=duration_hours)
    
    tournament = Tournament(
        contract_address=contract_address,
        game_type=game_type,
        entry_fee_ton=entry_fee,
        initial_prize_pool=prize_pool,
        total_pool=prize_pool,  # Start with seeded pool
        start_time=start_time,
        end_time=end_time,
        max_participants=max_participants,
        status=TournamentStatus.ACTIVE
    )
    
    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    
    return {
        "tournament_id": tournament.id,
        "contract_address": tournament.contract_address,
        "game_type": tournament.game_type,
        "entry_fee": tournament.entry_fee_ton,
        "end_time": tournament.end_time.isoformat(),
        "message": "Tournament created successfully"
    }

@app.get("/admin/tournaments/{tournament_id}/status")
def admin_get_tournament_status(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """
    Get detailed tournament status (ADMIN ONLY)
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top scores
    top_scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .order_by(TournamentScore.score.desc())\
        .limit(10)\
        .all()
    
    return {
        "tournament": {
            "id": tournament.id,
            "contract_address": tournament.contract_address,
            "game_type": tournament.game_type,
            "entry_fee_ton": tournament.entry_fee_ton,
            "total_pool": tournament.total_pool,
            "start_time": tournament.start_time.isoformat(),
            "end_time": tournament.end_time.isoformat(),
            "status": tournament.status,
            "total_participants": tournament.total_participants,
            "max_participants": tournament.max_participants
        },
        "top_scores": [
            {
                "wallet_raw": score.wallet_raw,
                "score": score.score,
                "submitted_at": score.submitted_at.isoformat(),
                "is_validated": score.is_validated
            }
            for score in top_scores
        ]
    }

@app.post("/admin/tournaments/{tournament_id}/finalize")
def admin_finalize_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """
    Finalize tournament and generate signed payouts (ADMIN ONLY)
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament.is_finalized:
        raise HTTPException(status_code=400, detail="Tournament already finalized")
    
    # Get top 3 scores
    top_scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.is_validated == True)\
        .order_by(TournamentScore.score.desc())\
        .limit(3)\
        .all()
    
    if len(top_scores) == 0:
        raise HTTPException(status_code=400, detail="No valid scores submitted")
    
    # Calculate prizes
    total_pool = tournament.total_pool
    
    # Prize distribution logic
    if len(top_scores) == 1:
        prizes = {1: total_pool}
    elif len(top_scores) == 2:
        prizes = {1: total_pool * 0.70, 2: total_pool * 0.30}
    else:
        prizes = {1: total_pool * 0.50, 2: total_pool * 0.30, 3: total_pool * 0.20}
    
    # Generate signed payouts
    payouts = []
    
    for rank, score_entry in enumerate(top_scores, 1):
        prize_amount = prizes.get(rank, 0)
        
        if prize_amount <= 0:
            continue
        
        # Create payload for smart contract
        # Format: winner_address | prize_amount | nonce | tournament_id
        nonce = secrets.randbits(64)
        
        payload_data = {
            "wallet": score_entry.wallet_raw,
            "prize_ton": prize_amount,
            "nonce": nonce,
            "tournament_id": tournament.id
        }
        
        # Sign with Ed25519 (TODO: implement proper signing)
        # For now, create a hash as placeholder
        payload_hash = hashlib.sha256(
            f"{score_entry.wallet_raw}{prize_amount}{nonce}{tournament.id}".encode()
        ).hexdigest()
        
        payouts.append({
            "rank": rank,
            "wallet": score_entry.wallet_raw,
            "score": score_entry.score,
            "prize_ton": prize_amount,
            "payload_hash": payload_hash,
            "nonce": nonce
        })
    
    # Update tournament status
    tournament.status = TournamentStatus.COMPLETED
    tournament.is_finalized = True
    db.commit()
    
    return {
        "message": "Tournament finalized",
        "tournament_id": tournament.id,
        "total_pool": total_pool,
        "payouts": payouts
    }

@app.post("/admin/tournaments/{tournament_id}/delete")
def admin_delete_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """
    Delete/cancel a tournament (ADMIN ONLY)
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Delete related scores
    db.query(TournamentScore).filter(TournamentScore.tournament_id == tournament_id).delete()
    
    # Delete tournament
    db.delete(tournament)
    db.commit()
    
    return {"message": "Tournament deleted", "tournament_id": tournament_id}

# ============= PUBLIC ENDPOINTS =============

@app.get("/tournaments")
def get_tournaments(
    status: Optional[str] = None,
    game_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get all tournaments (PUBLIC)
    """
    query = db.query(Tournament)
    
    if status:
        query = query.filter(Tournament.status == status)
    
    if game_type:
        query = query.filter(Tournament.game_type == game_type)
    
    tournaments = query.order_by(Tournament.created_at.desc()).all()
    
    return {
        "tournaments": [
            {
                "id": t.id,
                "contract_address": t.contract_address,
                "game_type": t.game_type,
                "entry_fee_ton": t.entry_fee_ton,
                "total_pool": t.total_pool,
                "start_time": t.start_time.isoformat(),
                "end_time": t.end_time.isoformat(),
                "status": t.status,
                "total_participants": t.total_participants,
                "max_participants": t.max_participants,
                "is_full": t.max_participants and t.total_participants >= t.max_participants
            }
            for t in tournaments
        ]
    }

@app.get("/tournaments/{tournament_id}")
def get_tournament_details(
    tournament_id: int,
    db: Session = Depends(get_db)
):
    """
    Get tournament details with leaderboard (PUBLIC)
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get leaderboard
    scores = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.is_validated == True)\
        .order_by(TournamentScore.score.desc())\
        .limit(20)\
        .all()
    
    return {
        "tournament": {
            "id": tournament.id,
            "contract_address": tournament.contract_address,
            "game_type": tournament.game_type,
            "entry_fee_ton": tournament.entry_fee_ton,
            "total_pool": tournament.total_pool,
            "start_time": tournament.start_time.isoformat(),
            "end_time": tournament.end_time.isoformat(),
            "status": tournament.status,
            "total_participants": tournament.total_participants
        },
        "leaderboard": [
            {
                "rank": idx + 1,
                "wallet": score.wallet_raw[:6] + "..." + score.wallet_raw[-4:],
                "wallet_full": score.wallet_raw,
                "score": score.score,
                "submitted_at": score.submitted_at.isoformat()
            }
            for idx, score in enumerate(scores)
        ]
    }

@app.post("/tournaments/{tournament_id}/submit")
def submit_tournament_score(
    tournament_id: int,
    submission: ScoreSubmission,
    db: Session = Depends(get_db)
):
    """
    Submit a score to a tournament (PUBLIC)
    """
    tournament = db.query(Tournament).filter(Tournament.id == tournament_id).first()
    
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament.status != TournamentStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Tournament is not active")
    
    # Check if tournament has ended
    if datetime.utcnow() > tournament.end_time:
        raise HTTPException(status_code=400, detail="Tournament has ended")
    
    # Basic validation (expand this!)
    if submission.score <= 0 or submission.score > 999999:
        raise HTTPException(status_code=400, detail="Invalid score")
    
    # Check if user already submitted
    existing = db.query(TournamentScore)\
        .filter(TournamentScore.tournament_id == tournament_id)\
        .filter(TournamentScore.wallet_raw == submission.wallet)\
        .first()
    
    if existing:
        # Update if new score is better
        if submission.score > existing.score:
            existing.score = submission.score
            existing.game_data = submission.gameData
            existing.submitted_at = datetime.utcnow()
            db.commit()
            return {"message": "Score updated", "score": submission.score}
        else:
            return {"message": "Previous score was better", "score": existing.score}
    
    # Add new score
    new_score = TournamentScore(
        tournament_id=tournament_id,
        wallet_raw=submission.wallet,
        score=submission.score,
        game_data=submission.gameData,
        is_validated=True  # TODO: Add proper validation
    )
    
    db.add(new_score)
    
    # Increment participants if first submission
    tournament.total_participants += 1
    
    db.commit()
    
    return {
        "message": "Score submitted successfully",
        "tournament_id": tournament_id,
        "score": submission.score
    }
'''
