"""Adventure Mode endpoints for level progress and goals."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)
adventure_router = APIRouter(prefix="/api", tags=["adventure"])


# ====================== Models ======================

class LevelGoal(BaseModel):
    id: str
    type: str = Field(..., description="Goal type: hit-pegs, survive-time, reach-score, collect-items, no-drain")
    target: int
    current: int = 0
    description: str


class LevelStory(BaseModel):
    intro: str
    complete: str
    videoUrl: Optional[str] = None


class LevelRewards(BaseModel):
    scoreMultiplier: float = 1.0
    unlockMap: Optional[str] = None


class AdventureLevel(BaseModel):
    id: str
    name: str
    mapType: str
    goals: List[LevelGoal]
    story: LevelStory
    rewards: LevelRewards


class AdventureProgress(BaseModel):
    currentLevel: str = "level-1-neon"
    currentMap: str = "neon-helix"
    completedLevels: List[str] = []
    unlockedMaps: List[str] = ["neon-helix"]
    totalScore: int = 0
    bestScores: Dict[str, int] = {}
    lastPlayed: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProgressResponse(BaseModel):
    success: bool
    progress: AdventureProgress


class GoalUpdate(BaseModel):
    goalType: str
    value: int


# ====================== Level Definitions ======================

ADVENTURE_LEVELS: List[AdventureLevel] = [
    AdventureLevel(
        id="level-1-neon",
        name="Neon Awakening",
        mapType="neon-helix",
        goals=[
            LevelGoal(id="g1-1", type="hit-pegs", target=30, description="Hit 30 pegs"),
            LevelGoal(id="g1-2", type="reach-score", target=5000, description="Score 5,000 points"),
        ],
        story=LevelStory(
            intro="The Nexus awakens...",
            complete="First light achieved. The Cascade awaits.",
            videoUrl="/videos/story/level1-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=1.1, unlockMap="cyber-core")
    ),
    AdventureLevel(
        id="level-2-cyber",
        name="Cyber Infiltration",
        mapType="cyber-core",
        goals=[
            LevelGoal(id="g2-1", type="survive-time", target=60, description="Survive 60 seconds"),
            LevelGoal(id="g2-2", type="hit-pegs", target=50, description="Hit 50 pegs"),
            LevelGoal(id="g2-3", type="reach-score", target=10000, description="Score 10,000 points"),
        ],
        story=LevelStory(
            intro="Breaching the firewall...",
            complete="Access granted. Quantum pathways opening.",
            videoUrl="/videos/story/level2-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=1.2, unlockMap="quantum-grid")
    ),
    AdventureLevel(
        id="level-3-quantum",
        name="Quantum Entanglement",
        mapType="quantum-grid",
        goals=[
            LevelGoal(id="g3-1", type="collect-items", target=10, description="Collect 10 quantum orbs"),
            LevelGoal(id="g3-2", type="reach-score", target=15000, description="Score 15,000 points"),
        ],
        story=LevelStory(
            intro="Particles align...",
            complete="Entanglement stable. New pathways discovered.",
            videoUrl="/videos/story/level3-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=1.3, unlockMap="singularity-well")
    ),
    AdventureLevel(
        id="level-4-singularity",
        name="Event Horizon",
        mapType="singularity-well",
        goals=[
            LevelGoal(id="g4-1", type="no-drain", target=1, description="Complete without draining"),
            LevelGoal(id="g4-2", type="reach-score", target=25000, description="Score 25,000 points"),
        ],
        story=LevelStory(
            intro="Approaching the void...",
            complete="Singularity breached. Gravity bends to your will.",
            videoUrl="/videos/story/level4-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=1.5, unlockMap="glitch-spire")
    ),
    AdventureLevel(
        id="level-5-glitch",
        name="System Corruption",
        mapType="glitch-spire",
        goals=[
            LevelGoal(id="g5-1", type="survive-time", target=120, description="Survive 120 seconds"),
            LevelGoal(id="g5-2", type="hit-pegs", target=100, description="Hit 100 pegs"),
            LevelGoal(id="g5-3", type="reach-score", target=50000, description="Score 50,000 points"),
        ],
        story=LevelStory(
            intro="System instability detected...",
            complete="Corruption contained. The Matrix awaits.",
            videoUrl="/videos/story/level5-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=2.0, unlockMap="matrix-core")
    ),
    AdventureLevel(
        id="level-6-matrix",
        name="Digital Rain",
        mapType="matrix-core",
        goals=[
            LevelGoal(id="g6-1", type="collect-items", target=20, description="Collect 20 data shards"),
            LevelGoal(id="g6-2", type="reach-score", target=75000, description="Score 75,000 points"),
        ],
        story=LevelStory(
            intro="Entering the construct...",
            complete="Reality is code. The Void calls.",
            videoUrl="/videos/story/level6-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=2.5, unlockMap="cyan-void")
    ),
    AdventureLevel(
        id="level-7-cyan",
        name="Void Tranquility",
        mapType="cyan-void",
        goals=[
            LevelGoal(id="g7-1", type="survive-time", target=180, description="Survive 3 minutes"),
            LevelGoal(id="g7-2", type="no-drain", target=1, description="Perfect run - no drains"),
            LevelGoal(id="g7-3", type="reach-score", target=100000, description="Score 100,000 points"),
        ],
        story=LevelStory(
            intro="Silence in the deep...",
            complete="Tranquility achieved. The final dream approaches.",
            videoUrl="/videos/story/level7-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=3.0, unlockMap="magenta-dream")
    ),
    AdventureLevel(
        id="level-8-magenta",
        name="Final Dream",
        mapType="magenta-dream",
        goals=[
            LevelGoal(id="g8-1", type="reach-score", target=150000, description="Score 150,000 points"),
            LevelGoal(id="g8-2", type="hit-pegs", target=200, description="Hit 200 pegs"),
            LevelGoal(id="g8-3", type="no-drain", target=1, description="Master run - no drains"),
        ],
        story=LevelStory(
            intro="The ultimate cascade...",
            complete="Nexus Cascade mastered. You are the legend.",
            videoUrl="/videos/story/level8-complete.mp4"
        ),
        rewards=LevelRewards(scoreMultiplier=5.0)
    ),
]


# ====================== Helpers ======================

def _get_adventure_dir() -> Path:
    """Get the adventure storage directory."""
    base = Path(settings.files_dir)
    adventure_dir = base / "adventure"
    adventure_dir.mkdir(parents=True, exist_ok=True)
    return adventure_dir


def _get_progress_file(user_id: str = "default") -> Path:
    """Get the progress file for a user."""
    return _get_adventure_dir() / f"{user_id}_progress.json"


def _load_progress(user_id: str = "default") -> dict:
    """Load progress from storage."""
    progress_file = _get_progress_file(user_id)
    
    if not progress_file.exists():
        # Return default progress
        return AdventureProgress().dict()
    
    try:
        with open(progress_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load progress: {e}")
        return AdventureProgress().dict()


def _save_progress(user_id: str, progress: dict) -> None:
    """Save progress to storage."""
    progress_file = _get_progress_file(user_id)
    progress["lastPlayed"] = datetime.now(timezone.utc).isoformat()
    
    with open(progress_file, "w") as f:
        json.dump(progress, f, indent=2)


def _get_level_by_id(level_id: str) -> Optional[AdventureLevel]:
    """Get a level by its ID."""
    for level in ADVENTURE_LEVELS:
        if level.id == level_id:
            return level
    return None


def _get_level_by_map(map_type: str) -> Optional[AdventureLevel]:
    """Get a level by its map type."""
    for level in ADVENTURE_LEVELS:
        if level.mapType == map_type:
            return level
    return None


# ====================== Endpoints ======================

@adventure_router.get("/adventure/progress", response_model=ProgressResponse)
async def get_progress(
    user_id: str = Query("default", description="User ID for progress tracking")
):
    """Get the current adventure progress for a user."""
    progress = _load_progress(user_id)
    
    return ProgressResponse(
        success=True,
        progress=AdventureProgress(**progress)
    )


@adventure_router.post("/adventure/progress", response_model=ProgressResponse)
async def save_progress(
    progress: AdventureProgress,
    user_id: str = Query("default", description="User ID for progress tracking")
):
    """Save adventure progress for a user."""
    try:
        _save_progress(user_id, progress.dict())
        logger.info(f"Progress saved for user {user_id}: level {progress.currentLevel}")
        
        return ProgressResponse(
            success=True,
            progress=progress
        )
    except Exception as e:
        logger.error(f"Failed to save progress: {e}")
        raise HTTPException(status_code=500, detail="Failed to save progress")


@adventure_router.get("/adventure/levels")
async def get_levels():
    """Get all adventure level definitions."""
    return {
        "levels": [level.dict() for level in ADVENTURE_LEVELS],
        "count": len(ADVENTURE_LEVELS)
    }


@adventure_router.get("/adventure/level/{level_id}")
async def get_level(level_id: str):
    """Get a specific level definition."""
    level = _get_level_by_id(level_id)
    if not level:
        raise HTTPException(status_code=404, detail=f"Level {level_id} not found")
    return level.dict()


@adventure_router.get("/adventure/level-by-map/{map_type}")
async def get_level_by_map(map_type: str):
    """Get level definition for a specific map."""
    level = _get_level_by_map(map_type)
    if not level:
        raise HTTPException(status_code=404, detail=f"No level found for map {map_type}")
    return level.dict()


@adventure_router.post("/adventure/complete-level/{level_id}")
async def complete_level(
    level_id: str,
    score: int = Query(0, ge=0, description="Final score for the level"),
    user_id: str = Query("default", description="User ID")
):
    """Mark a level as complete and unlock rewards."""
    level = _get_level_by_id(level_id)
    if not level:
        raise HTTPException(status_code=404, detail=f"Level {level_id} not found")
    
    progress = _load_progress(user_id)
    
    # Add to completed levels if not already there
    if level_id not in progress["completedLevels"]:
        progress["completedLevels"].append(level_id)
    
    # Update best score for this level
    current_best = progress["bestScores"].get(level_id, 0)
    if score > current_best:
        progress["bestScores"][level_id] = score
    
    # Unlock next map if specified
    if level.rewards.unlockMap:
        if level.rewards.unlockMap not in progress["unlockedMaps"]:
            progress["unlockedMaps"].append(level.rewards.unlockMap)
            logger.info(f"Unlocked map {level.rewards.unlockMap} for user {user_id}")
    
    # Update current level to next if available
    current_idx = next((i for i, l in enumerate(ADVENTURE_LEVELS) if l.id == level_id), -1)
    if current_idx >= 0 and current_idx < len(ADVENTURE_LEVELS) - 1:
        next_level = ADVENTURE_LEVELS[current_idx + 1]
        progress["currentLevel"] = next_level.id
        progress["currentMap"] = next_level.mapType
    
    # Add to total score
    progress["totalScore"] += int(score * level.rewards.scoreMultiplier)
    
    _save_progress(user_id, progress)
    
    return {
        "success": True,
        "levelId": level_id,
        "score": score,
        "multiplier": level.rewards.scoreMultiplier,
        "unlockedMap": level.rewards.unlockMap,
        "progress": progress
    }


@adventure_router.post("/adventure/reset")
async def reset_progress(
    user_id: str = Query("default", description="User ID to reset")
):
    """Reset all adventure progress for a user."""
    default_progress = AdventureProgress().dict()
    _save_progress(user_id, default_progress)
    logger.info(f"Progress reset for user {user_id}")
    
    return {
        "success": True,
        "message": "Adventure progress reset",
        "progress": default_progress
    }
