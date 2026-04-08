from fastapi import FastAPI, Query
from pydantic import BaseModel
import requests
import random

app = FastAPI(title="Ritm Wave API")

# ТВОЙ КЛЮЧ LAST.FM
LASTFM_API_KEY = "f15f3ae666f3fc089b89a508a1607cf4"

# База данных истории (пока в памяти сервера)
user_history = {}

class Track(BaseModel):
    title: str
    artist: str
    coverUrl: str

def get_similar_artists(artist_name: str) -> list:
    """Спрашиваем Last.fm, кто похож на этого артиста"""
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=15"
    try:
        response = requests.get(url).json()
        if "similarartists" in response and "artist" in response["similarartists"]:
            return [artist["name"] for artist in response["similarartists"]["artist"]]
    except:
        pass
    return []

def get_top_tracks(artist_name: str, limit: int = 5) -> list:
    """Берем лучшие треки артиста"""
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=10"
    try:
        response = requests.get(url).json()
        if "toptracks" in response and "track" in response["toptracks"]:
            tracks = []
            for track in response["toptracks"]["track"]:
                tracks.append(Track(
                    title=track["name"],
                    artist=track["artist"]["name"],
                    coverUrl="" # Оставляем пустым, чтобы телефон сам скачал обложку из Apple Music!
                ))
            # Перемешиваем хиты, чтобы не всегда играла самая популярная песня
            random.shuffle(tracks)
            return tracks[:limit]
    except:
        pass
    return []

@app.get("/api/wave/next")
async def generate_wave(
    user_id: str, 
    current_artist: str = Query(..., description="Текущий артист"),
    limit: int = 5
):
    history = user_history.get(user_id, set())
    similar_artists = get_similar_artists(current_artist)
    
    wave_queue = []
    attempts = 0
    
    while len(wave_queue) < limit and attempts < 20:
        attempts += 1
        dice = random.random()
        
        # ЛОГИКА МИКСА
        if dice < 0.65 and similar_artists:
            # 65% - Похожий артист
            chosen_artist = random.choice(similar_artists)
            candidate_tracks = get_top_tracks(chosen_artist)
        elif dice < 0.85:
            # 20% - Тот же артист
            candidate_tracks = get_top_tracks(current_artist)
        else:
            # 15% - Рандомный популярный артист (шаг в сторону)
            wildcard = random.choice(["Miyagi & Эндшпиль", "Macan", "ANNA ASTI", "The Weeknd", "Скриптонит", "Instasamka"])
            candidate_tracks = get_top_tracks(wildcard)
            
        if not candidate_tracks:
            continue
            
        track = random.choice(candidate_tracks)
        track_id = f"{track.artist}_{track.title}"
        
        # Проверка на дубликаты
        if track_id not in history:
            wave_queue.append(track)
            history.add(track_id)
            
    # Если история переполнилась, чистим старое (чтобы память сервера не забилась)
    if len(history) > 100:
        history.clear()
        
    user_history[user_id] = history
    return {"status": "success", "tracks": wave_queue}
