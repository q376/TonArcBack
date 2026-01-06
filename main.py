from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import redis
import json
import os
import secrets
import hashlib
import time

# ===== APP SETUP =====
app = FastAPI(title="TonArcade API")

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://red-d5eck9khg0os73988ao0:6379")
ADMIN_SECRET = os.getenv("ADMIN_SECRET")

if not ADMIN_SECRET:
    raise ValueError("ADMIN_SECRET environment variable is required")

# Initialize Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Test connection
try:
    redis_client.ping()
    print("✅ Redis connected successfully")
except redis.ConnectionError:
    raise Exception("❌ Failed to connect to Redis")

# ===== PYDANTIC MODELS (Validation Only) =====

class WalletAuth(BaseModel):
    wallet_raw: str
    wallet_user_friendly: str

class TournamentCreateRequest(BaseModel):
    game_type: str
    entry_fee: float
    duration_hours: int
    prize_pool: float = 0
    max_participants: Optional[int] = None
    start_delay_hours: float = 0

class ScoreSubmission(BaseModel):
    wallet: str
    event_id: Optional[int] = None
    game: str
    score: float
    gameData: Dict[str, Any]
    timestamp: int

# ===== HELPER FUNCTIONS =====

def verify_admin(admin_secret: str = Header(None, alias="X-Admin-Secret")):
    if admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True

def get_timestamp():
    return int(time.time())

def format_user_data(user_dict):
    """Convert Redis hash to user response format"""
    if not user_dict:
        return None
    return {
        "wallet_raw": user_dict.get("wallet_raw"),
        "wallet_user_friendly": user_dict.get("wallet_friendly"),
        "created_at": datetime.fromtimestamp(int(user_dict.get("created_at", 0))).isoformat(),
        "total_earned": float(user_dict.get("total_earned", 0)),
        "tournaments_won": int(user_dict.get("tournaments_won", 0)),
        "games_played": int(user_dict.get("games_played", 0))
    }

def format_tournament_data(tournament_dict, tournament_id):
    """Convert Redis hash to tournament response format"""
    if not tournament_dict:
        return None
    
    # Get participant count from leaderboard
    participant_count = redis_client.zcard(f"tournament:{tournament_id}:leaderboard")
    
    return {
        "id": int(tournament_id),
        "contract_address": tournament_dict.get("contract_address"),
        "game_type": tournament_dict.get("game_type"),
        "entry_fee_ton": float(tournament_dict.get("entry_fee_ton", 0)),
        "total_pool": float(tournament_dict.get("total_pool", 0)),
        "start_time": datetime.fromtimestamp(int(tournament_dict.get("start_time", 0))).isoformat(),
        "end_time": datetime.fromtimestamp(int(tournament_dict.get("end_time", 0))).isoformat(),
        "status": tournament_dict.get("status"),
        "total_participants": participant_count,
        "max_participants": int(tournament_dict.get("max_participants")) if tournament_dict.get("max_participants") else None,
        "is_full": bool(tournament_dict.get("max_participants") and participant_count >= int(tournament_dict.get("max_participants")))
    }

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
        "message": "TonArcade API is running (Redis-powered)",
        "redis_connected": redis_client.ping(),
        "endpoints": {
            "auth": "/auth/wallet",
            "tournaments": "/tournaments",
            "admin": "/admin/tournaments/create"
        }
    }

# ===== AUTHENTICATION =====

@app.post("/auth/wallet")
def wallet_login(auth: WalletAuth):
    wallet_raw = auth.wallet_raw.strip()
    wallet_friendly = auth.wallet_user_friendly.strip()
    
    if not wallet_raw or not wallet_friendly:
        raise HTTPException(status_code=400, detail="Wallet addresses cannot be empty")
    
    # Check if user exists
    user_exists = redis_client.sismember("users:all", wallet_raw)
    
    if user_exists:
        # Existing user
        user_data = redis_client.hgetall(f"user:{wallet_raw}")
        return {
            "message": "Login successful",
            "user": format_user_data(user_data)
        }
    
    # New user - create
    redis_client.hset(f"user:{wallet_raw}", mapping={
        "wallet_raw": wallet_raw,
        "wallet_friendly": wallet_friendly,
        "created_at": get_timestamp(),
        "total_earned": 0.0,
        "tournaments_won": 0,
        "games_played": 0,
        "is_banned": "false"
    })
    
    redis_client.sadd("users:all", wallet_raw)
    
    user_data = redis_client.hgetall(f"user:{wallet_raw}")
    
    return {
        "message": "User registered",
        "user": format_user_data(user_data)
    }

# ===== ADMIN ENDPOINTS =====

@app.post("/admin/tournaments/create")
def admin_create_tournament(
    request: TournamentCreateRequest,
    _: bool = Depends(verify_admin)
):
    """Create a new tournament (ADMIN ONLY)"""
    
    # Generate tournament ID
    tournament_id = redis_client.incr("tournament:id_counter")
    
    # Generate contract address
    contract_address = f"EQ{secrets.token_hex(32)}"
    
    # Calculate timestamps
    start_time = get_timestamp() + int(request.start_delay_hours * 3600)
    end_time = start_time + (request.duration_hours * 3600)
    
    # Determine initial status
    status = "upcoming" if request.start_delay_hours > 0 else "active"
    
    # Store tournament
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee_ton": request.entry_fee,
        "total_pool": request.prize_pool,
        "initial_prize_pool": request.prize_pool,
        "start_time": start_time,
        "end_time": end_time,
        "created_at": get_timestamp(),
        "max_participants": request.max_participants or "",
        "status": status,
        "is_finalized": "false"
    })
    
    # Add to indexes
    redis_client.sadd("tournaments:all", tournament_id)
    redis_client.sadd(f"tournaments:{status}", tournament_id)
    redis_client.sadd(f"tournament:by_game:{request.game_type}", tournament_id)
    
    return {
        "tournament_id": tournament_id,
        "contract_address": contract_address,
        "game_type": request.game_type,
        "entry_fee": request.entry_fee,
        "end_time": datetime.fromtimestamp(end_time).isoformat(),
        "message": "Tournament created successfully"
    }

@app.get("/admin/tournaments/{tournament_id}/status")
def admin_get_tournament_status(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Get detailed tournament status (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 10 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 9,
        withscores=True
    )
    
    formatted_scores = []
    for wallet, score in top_scores:
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        formatted_scores.append({
            "wallet_raw": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None,
            "is_validated": score_meta.get("is_validated", "false") == "true" if score_meta else False
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "top_scores": formatted_scores
    }

@app.post("/admin/tournaments/{tournament_id}/finalize")
def admin_finalize_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Finalize tournament and generate signed payouts (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    if tournament_data.get("is_finalized") == "true":
        raise HTTPException(status_code=400, detail="Tournament already finalized")
    
    # Get top 3 scores
    top_scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 2,
        withscores=True
    )
    
    if len(top_scores) == 0:
        raise HTTPException(status_code=400, detail="No valid scores submitted")
    
    total_pool = float(tournament_data.get("total_pool", 0))
    
    # Calculate prize distribution
    if len(top_scores) == 1:
        prizes = {0: total_pool}
    elif len(top_scores) == 2:
        prizes = {0: total_pool * 0.70, 1: total_pool * 0.30}
    else:
        prizes = {0: total_pool * 0.50, 1: total_pool * 0.30, 2: total_pool * 0.20}
    
    payouts = []
    
    for rank, (wallet, score) in enumerate(top_scores):
        prize_amount = prizes.get(rank, 0)
        
        if prize_amount <= 0:
            continue
        
        nonce = secrets.randbits(64)
        
        payload_hash = hashlib.sha256(
            f"{wallet}{prize_amount}{nonce}{tournament_id}".encode()
        ).hexdigest()
        
        payouts.append({
            "rank": rank + 1,
            "wallet": wallet,
            "score": score,
            "prize_ton": prize_amount,
            "payload_hash": payload_hash,
            "nonce": nonce
        })
        
        # Update user stats
        redis_client.hincrbyfloat(f"user:{wallet}", "total_earned", prize_amount)
        if rank == 0:  # Winner
            redis_client.hincrby(f"user:{wallet}", "tournaments_won", 1)
    
    # Update tournament status
    redis_client.hset(f"tournament:{tournament_id}", mapping={
        "status": "completed",
        "is_finalized": "true"
    })
    
    # Move to completed index
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    redis_client.sadd("tournaments:completed", tournament_id)
    
    return {
        "message": "Tournament finalized",
        "tournament_id": tournament_id,
        "total_pool": total_pool,
        "payouts": payouts
    }

@app.post("/admin/tournaments/{tournament_id}/delete")
def admin_delete_tournament(
    tournament_id: int,
    _: bool = Depends(verify_admin)
):
    """Delete/cancel a tournament (ADMIN ONLY)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Delete tournament data
    redis_client.delete(f"tournament:{tournament_id}")
    redis_client.delete(f"tournament:{tournament_id}:leaderboard")
    redis_client.delete(f"tournament:{tournament_id}:entries")
    redis_client.delete(f"tournament:{tournament_id}:participants")
    
    # Delete all score metadata
    participants = redis_client.smembers(f"tournament:{tournament_id}:participants")
    for wallet in participants:
        redis_client.delete(f"score:{tournament_id}:{wallet}")
    
    # Remove from indexes
    redis_client.srem("tournaments:all", tournament_id)
    redis_client.srem("tournaments:active", tournament_id)
    redis_client.srem("tournaments:upcoming", tournament_id)
    redis_client.srem("tournaments:completed", tournament_id)
    redis_client.srem("tournaments:finalizing", tournament_id)
    
    game_type = tournament_data.get("game_type")
    if game_type:
        redis_client.srem(f"tournament:by_game:{game_type}", tournament_id)
    
    return {
        "message": "Tournament deleted",
        "tournament_id": tournament_id
    }

# ===== PUBLIC ENDPOINTS =====

@app.get("/tournaments")
def get_tournaments(
    status: Optional[str] = None,
    game_type: Optional[str] = None
):
    """Get all tournaments (PUBLIC)"""
    
    # Get tournament IDs based on filters
    if status:
        tournament_ids = redis_client.smembers(f"tournaments:{status}")
    else:
        tournament_ids = redis_client.smembers("tournaments:all")
    
    if game_type and tournament_ids:
        game_tournaments = redis_client.smembers(f"tournament:by_game:{game_type}")
        tournament_ids = tournament_ids.intersection(game_tournaments)
    
    # Fetch all tournament data using pipeline (efficient batch operation)
    if not tournament_ids:
        return {"tournaments": []}
    
    pipe = redis_client.pipeline()
    for tid in tournament_ids:
        pipe.hgetall(f"tournament:{tid}")
    
    tournaments_data = pipe.execute()
    
    # Format tournaments
    tournaments = []
    for tid, data in zip(tournament_ids, tournaments_data):
        if data:
            formatted = format_tournament_data(data, tid)
            if formatted:
                tournaments.append(formatted)
    
    # Sort by created_at (most recent first)
    tournaments.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    
    return {"tournaments": tournaments}

@app.get("/tournaments/{tournament_id}")
def get_tournament_details(tournament_id: int):
    """Get tournament details with leaderboard (PUBLIC)"""
    
    tournament_data = redis_client.hgetall(f"tournament:{tournament_id}")
    
    if not tournament_data:
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Get top 20 scores
    scores = redis_client.zrevrange(
        f"tournament:{tournament_id}:leaderboard",
        0, 19,
        withscores=True
    )
    
    leaderboard = []
    for rank, (wallet, score) in enumerate(scores, 1):
        score_meta = redis_client.hgetall(f"score:{tournament_id}:{wallet}")
        
        leaderboard.append({
            "rank": rank,
            "wallet": wallet[:6] + "..." + wallet[-4:],
            "wallet_full": wallet,
            "score": score,
            "submitted_at": datetime.fromtimestamp(int(score_meta.get("submitted_at", 0))).isoformat() if score_meta else None
        })
    
    return {
        "tournament": format_tournament_data(tournament_data, tournament_id),
        "leaderboard": leaderboard
    }

@app.post("/tournaments/{tournament_id}/submit")
def submit_tournament_score(
    tournament_id: int,
    submission: ScoreSubmission
):
    """Submit a score to a tournament (PUBLIC)"""
    

    
    # Check active tournaments
    active_ids = redis_client.smembers("tournaments:active")
    for tid in active_ids:
        tournament_data = redis_client.hgetall(f"tournament:{tid}")
        end_time = int(tournament_data.get("end_time", 0))
        
        if now >= end_time:
            # End tournament
            redis_client.hset(f"tournament:{tid}", "status", "finalizing")
            redis_client.srem("tournaments:active", tid)
            redis_client.sadd("tournaments:finalizing", tid)
            updated_count += 1
    
    return {
        "message": f"Updated {updated_count} tournaments",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

'''from fastapi import FastAPI, HTTPException, Depends, Header
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
    start_delay_hours: float = 0  # NEW: Hours until tournament starts

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
