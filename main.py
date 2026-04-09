from fastapi import FastAPI, Query
from pydantic import BaseModel
import requests
import random
import re
from bs4 import BeautifulSoup
from yandex_music import Client

app = FastAPI(title="Ritm Wave & Import API")

LASTFM_API_KEY = "f15f3ae666f3fc089b89a508a1607cf4"
user_history = {}

class Track(BaseModel):
    title: str
    artist: str
    coverUrl: str

def get_similar_artists(artist_name: str) -> list:
    if not artist_name or artist_name == "Неизвестно": return []
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=15"
    try:
        response = requests.get(url).json()
        if "similarartists" in response and "artist" in response["similarartists"]:
            return [artist["name"] for artist in response["similarartists"]["artist"]]
    except:
        pass
    return []

def get_top_tracks(artist_name: str, limit: int = 5) -> list:
    if not artist_name or artist_name == "Неизвестно": return []
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

def get_tracks_by_tag(tag: str, limit: int = 10) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={tag}&api_key={LASTFM_API_KEY}&format=json&limit=40"
    try:
        response = requests.get(url).json()
        if "tracks" in response and "track" in response["tracks"]:
            tracks = []
            for track in response["tracks"]["track"]:
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

# НОВАЯ ФУНКЦИЯ ДЛЯ ЧИСТОГО ЛИСТА: Мировые хиты
def get_global_top_tracks(limit: int = 10) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json&limit=50"
    try:
        response = requests.get(url).json()
        if "tracks" in response and "track" in response["tracks"]:
            tracks = []
            for track in response["tracks"]["track"]:
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

@app.get("/api/wave/next")
async def generate_wave(
    user_id: str, 
    current_artist: str = Query(""), 
    mood: str = "Любое", 
    language: str = "Любой",
    limit: int = 5
):
    history = user_history.get(user_id, set())
    wave_queue = []
    
    # 1. ЖЕСТКИЕ ФИЛЬТРЫ НАСТРОЕНИЯ И ЯЗЫКА
    if mood != "Любое" or language != "Любой":
        tags_to_search = []
        
        if mood == "Бодрое": tags_to_search.extend(["workout", "energetic", "dance", "upbeat"])
        elif mood == "Грустное": tags_to_search.extend(["sad", "melancholic", "depressive"])
        elif mood == "Любовное": tags_to_search.extend(["love", "romantic", "soul"])
        elif mood == "Релакс": tags_to_search.extend(["chill", "lo-fi", "relax", "lounge"])
        elif mood == "Агрессивное": tags_to_search.extend(["aggressive", "hardcore", "phonk"])
        
        if language == "Русский": tags_to_search.extend(["russian", "russian rap", "russian pop"])
        elif language == "Иностранный": tags_to_search.extend(["english", "american"])
        
        if not tags_to_search:
            tags_to_search = ["hits"]

        chosen_tag = random.choice(tags_to_search)
        tag_tracks = get_tracks_by_tag(chosen_tag, limit=limit * 2)
        
        for track in tag_tracks:
            track_id = f"{track.artist}_{track.title}"
            if track_id not in history:
                wave_queue.append(track)
                history.add(track_id)
            if len(wave_queue) >= limit: break
                
        if len(wave_queue) >= limit - 2:
            if len(history) > 100: history.clear()
            user_history[user_id] = history
            return {"status": "success", "tracks": wave_queue}

    # 2. КЛАССИЧЕСКАЯ ВОЛНА (С ОБУЧЕНИЕМ С НУЛЯ)
    similar_artists = get_similar_artists(current_artist)
    attempts = 0
    
    while len(wave_queue) < limit and attempts < 20:
        attempts += 1
        dice = random.random()
        
        # Если артист неизвестен (чистый лист), сразу отдаем случайные популярные вещи
        if not current_artist or current_artist == "Неизвестно":
            dice = 0.9 
            
        if dice < 0.65 and similar_artists:
            chosen_artist = random.choice(similar_artists)
            candidate_tracks = get_top_tracks(chosen_artist)
        elif dice < 0.85 and current_artist and current_artist != "Неизвестно":
            candidate_tracks = get_top_tracks(current_artist)
        else:
            # УБРАН ХАРДКОД! Теперь мы берем либо мировые чарты, либо случайные глобальные жанры
            if random.random() < 0.5:
                wildcard_tag = random.choice(["pop", "electronic", "hip-hop", "rock", "indie", "dance", "rnb", "chillout"])
                candidate_tracks = get_tracks_by_tag(wildcard_tag, limit=10)
            else:
                candidate_tracks = get_global_top_tracks(limit=10)
            
        if not candidate_tracks: continue
            
        track = random.choice(candidate_tracks)
        track_id = f"{track.artist}_{track.title}"
        
        if track_id not in history:
            wave_queue.append(track)
            history.add(track_id)
            
    if len(history) > 100: history.clear()
    user_history[user_id] = history
    return {"status": "success", "tracks": wave_queue}


@app.get("/api/import")
async def import_playlist(url: str):
    tracks_list = []
    playlist_title = "Импортированный плейлист"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        if "music.yandex" in url:
            try:
                client = Client() 
                match_user = re.search(r'users/([^/]+)/playlists/(\d+)', url)
                match_album = re.search(r'album/(\d+)', url)
                match_artist = re.search(r'artist/(\d+)', url)

                if match_user:
                    playlist = client.users_playlists(int(match_user.group(2)), match_user.group(1))
                    playlist_title = playlist.title if playlist.title else "Яндекс Плейлист"
                    for track_short in playlist.fetch_tracks():
                        if track_short.track:
                            artist = track_short.track.artists[0].name if track_short.track.artists else "Неизвестный"
                            tracks_list.append({"title": track_short.track.title, "artist": artist})
                
                elif match_album:
                    album = client.albums_with_tracks(int(match_album.group(1)))
                    playlist_title = album.title if album.title else "Яндекс Альбом"
                    if album.volumes:
                        for volume in album.volumes:
                            for track in volume:
                                artist = track.artists[0].name if track.artists else "Неизвестный"
                                tracks_list.append({"title": track.title, "artist": artist})
                
                elif match_artist:
                    artist_id = int(match_artist.group(1))
                    artist_info = client.artists([artist_id])[0]
                    playlist_title = f"Топ: {artist_info.name}"
                    tracks = client.artists_tracks(artist_id).tracks
                    for track in tracks[:50]:
                        artist = track.artists[0].name if track.artists else artist_info.name
                        tracks_list.append({"title": track.title, "artist": artist})
            except Exception as e:
                pass

            if not tracks_list:
                bot_headers = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"}
                response = requests.get(url, headers=bot_headers)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                title_tag = soup.find('h1', class_='page-playlist__title') or soup.find('h1', class_='page-artist__title') or soup.find('h1')
                if title_tag: playlist_title = title_tag.text.strip()
                
                for track_node in soup.find_all('div', class_='d-track'):
                    try:
                        title = track_node.find('div', class_='d-track__name').text.strip()
                        artist_node = track_node.find('span', class_='d-track__artists')
                        artist = artist_node.text.strip() if artist_node else "Неизвестно"
                        tracks_list.append({"title": title, "artist": artist})
                    except: continue

        elif "vk.com" in url or "vk.ru" in url:
            match = re.search(r'audio_playlist(-?\d+)_(\d+)', url) or re.search(r'playlist/(-?\d+)_(\d+)', url)
            
            if match:
                owner_id = match.group(1)
                album_id = match.group(2)
                
                access_key = ""
                key_match = re.search(r'access_key=([a-zA-Z0-9]+)', url) or re.search(r'_([a-zA-Z0-9]+)$', url)
                if key_match:
                    access_key = key_match.group(1)
                
                if access_key:
                    m_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{album_id}&access_hash={access_key}"
                else:
                    m_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{album_id}"
                    
                response = requests.get(m_url, headers=headers)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                title_tag = soup.find('div', class_='AudioPlaylistSnippet__title') or soup.find('h1') or soup.find('div', class_='op_header')
                if title_tag: playlist_title = title_tag.text.strip()

                audio_items = soup.find_all('div', class_='audio_item')
                for item in audio_items:
                    try:
                        title = item.find('span', class_='ai_title').text.strip()
                        artist = item.find('span', class_='ai_artist').text.strip()
                        tracks_list.append({"title": title, "artist": artist})
                    except: continue

        return {
            "status": "success",
            "playlist_title": playlist_title,
            "tracks": tracks_list
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "tracks": []}
