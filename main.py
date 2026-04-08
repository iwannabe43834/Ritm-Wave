from fastapi import FastAPI, Query
from pydantic import BaseModel
import requests
import random
import re
from bs4 import BeautifulSoup
from yandex_music import Client

app = FastAPI(title="Ritm Wave & Import API")

# ТВОЙ КЛЮЧ LAST.FM
LASTFM_API_KEY = "f15f3ae666f3fc089b89a508a1607cf4"

# База данных истории
user_history = {}

class Track(BaseModel):
    title: str
    artist: str
    coverUrl: str

def get_similar_artists(artist_name: str) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=15"
    try:
        response = requests.get(url).json()
        if "similarartists" in response and "artist" in response["similarartists"]:
            return [artist["name"] for artist in response["similarartists"]["artist"]]
    except:
        pass
    return []

def get_top_tracks(artist_name: str, limit: int = 5) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=10"
    try:
        response = requests.get(url).json()
        if "toptracks" in response and "track" in response["toptracks"]:
            tracks = []
            for track in response["toptracks"]["track"]:
                tracks.append(Track(
                    title=track["name"],
                    artist=track["artist"]["name"],
                    coverUrl=""
                ))
            random.shuffle(tracks)
            return tracks[:limit]
    except:
        pass
    return []

# ==========================================
# ЭНДПОИНТ 1: МОЯ ВОЛНА
# ==========================================
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
        
        if dice < 0.65 and similar_artists:
            chosen_artist = random.choice(similar_artists)
            candidate_tracks = get_top_tracks(chosen_artist)
        elif dice < 0.85:
            candidate_tracks = get_top_tracks(current_artist)
        else:
            wildcard = random.choice(["Miyagi & Эндшпиль", "Macan", "ANNA ASTI", "The Weeknd", "Скриптонит", "Instasamka"])
            candidate_tracks = get_top_tracks(wildcard)
            
        if not candidate_tracks:
            continue
            
        track = random.choice(candidate_tracks)
        track_id = f"{track.artist}_{track.title}"
        
        if track_id not in history:
            wave_queue.append(track)
            history.add(track_id)
            
    if len(history) > 100:
        history.clear()
        
    user_history[user_id] = history
    return {"status": "success", "tracks": wave_queue}

# ==========================================
# ЭНДПОИНТ 2: ИМПОРТ ПЛЕЙЛИСТОВ (ЯНДЕКС И ВК)
# ==========================================
@app.get("/api/import")
async def import_playlist(url: str):
    tracks_list = []
    playlist_title = "Импортированный плейлист"

    try:
        # 1. ПАРСЕР ЯНДЕКС МУЗЫКИ
        # ИСПРАВЛЕНИЕ 1: Теперь ищет "music.yandex", игнорируя домен (.ru, .kz, .by)
        if "music.yandex" in url:
            client = Client() 
            
            match_user = re.search(r'users/([^/]+)/playlists/(\d+)', url)
            match_album = re.search(r'album/(\d+)', url)

            if match_user:
                user_id = match_user.group(1)
                kind = match_user.group(2)
                playlist = client.users_playlists(kind, user_id)
                playlist_title = playlist.title if playlist.title else "Яндекс Плейлист"
                
                # ИСПРАВЛЕНИЕ 2: Загружаем все треки разом (оптом), чтобы сервер не падал
                full_tracks = playlist.fetch_tracks()
                for track_short in full_tracks:
                    track = track_short.track
                    if track:
                        artist_name = track.artists[0].name if track.artists else "Неизвестный"
                        tracks_list.append({"title": track.title, "artist": artist_name})
                    
            elif match_album:
                album_id = match_album.group(1)
                album = client.albums_with_tracks(album_id)
                playlist_title = album.title if album.title else "Яндекс Альбом"
                
                if album.volumes:
                    for volume in album.volumes:
                        for track in volume:
                            artist_name = track.artists[0].name if track.artists else "Неизвестный"
                            tracks_list.append({"title": track.title, "artist": artist_name})

        # 2. ПАРСЕР ВКОНТАКТЕ
        elif "vk.com" in url:
            # ИСПРАВЛЕНИЕ 3: Переделываем мобильные ссылки m.vk.com в обычные
            url = url.replace("m.vk.com", "vk.com")
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
            }
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            title_tag = soup.find('h1', class_='AudioPlaylistSnippet__title')
            if title_tag:
                playlist_title = title_tag.text.strip()

            audio_rows = soup.find_all('div', class_='audio_row')
            for row in audio_rows:
                try:
                    title = row.find('span', class_='audio_row__title_inner').text.strip()
                    artist = row.find('div', class_='audio_row__performers').text.strip()
                    tracks_list.append({"title": title, "artist": artist})
                except AttributeError:
                    continue

        return {
            "status": "success",
            "playlist_title": playlist_title,
            "tracks": tracks_list
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "tracks": []}
