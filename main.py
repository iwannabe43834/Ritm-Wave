from fastapi import FastAPI, Query
from pydantic import BaseModel
import httpx
import random
import re
import asyncio
import json
from collections import deque
from bs4 import BeautifulSoup
from yandex_music import Client
from async_lru import alru_cache

app = FastAPI(title="Ritm Smart Wave & Import API")

# ==========================================
# 1. КОНФИГУРАЦИЯ API И КЛИЕНТОВ
# ==========================================
LASTFM_API_KEY = "f15f3ae666f3fc089b89a508a1607cf4"

# Твой основной ключ Gemini (Pro-версия)
PRIMARY_GEMINI_KEY = "AIzaSyAVOf9OORCld7hFZddyFFfqQjJL95yQkew"

# Очередь истории (запоминает 200 последних треков на юзера)
user_history = {}

# Единый асинхронный HTTP-клиент для всего (Last.fm, Gemini, VK)
http_client = httpx.AsyncClient(timeout=10.0)
ya_client = Client()

class Track(BaseModel):
    title: str
    artist: str
    coverUrl: str

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()

# ==========================================
# 2. ИИ-АНАЛИТИКА (ТОЛЬКО GEMINI PRO)
# ==========================================
async def fetch_gemini(prompt: str, model_name: str, api_key: str, timeout: float = 7.0) -> str:
    """Прямой асинхронный REST-запрос к API Google Gemini"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    response = await http_client.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    
    data = response.json()
    return data['candidates'][0]['content']['parts'][0]['text']

async def get_smart_artists(recent_tracks: list, mood: str, language: str, disliked_artists: list) -> list:
    """Универсальная функция нейросети: понимает вайб, язык, настроение и вырезает дизлайки"""
    
    prompt = "Ты лучший в мире музыкальный критик и рекомендательный алгоритм.\n"
    
    if recent_tracks:
        prompt += f"Пользователь сейчас слушает этот вайб: {', '.join(recent_tracks)}.\n"
    
    if mood != "Любое" or language != "Любой":
        prompt += f"Пожелания пользователя на эту сессию -> Настроение: {mood}. Язык исполнения: {language}.\n"
        
    prompt += "Выдай список из 6 неочевидных, но идеально подходящих артистов, которые соответствуют этим условиям.\n"
    
    if disliked_artists:
        prompt += f"КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО предлагать этих артистов или очень похожих на них: {', '.join(disliked_artists)}.\n"
        
    prompt += 'ОТВЕЧАЙ СТРОГО В ФОРМАТЕ JSON-МАССИВА СТРОК: ["Артист 1", "Артист 2"]. Не пиши лишний текст.'
    
    try:
        # Используем только основную модель (Pro)
        raw_text = await fetch_gemini(prompt, "gemini-1.5-pro", PRIMARY_GEMINI_KEY, timeout=8.0)
        print("⚡ Успешно отработала модель GEMINI PRO")
        
        clean_text = raw_text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        print(f"⚠️ Ошибка GEMINI PRO ({e}). Временно переключаюсь на стандартные алгоритмы Last.fm.")
        return []

# ==========================================
# 3. КЭШИРОВАННЫЕ ЗАПРОСЫ К LAST.FM
# ==========================================
@alru_cache(maxsize=500)
async def get_similar_artists(artist_name: str) -> list:
    if not artist_name or artist_name == "Неизвестно": return []
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=15"
    try:
        res = await http_client.get(url)
        return [artist["name"] for artist in res.json().get("similarartists", {}).get("artist", [])]
    except: return []

@alru_cache(maxsize=1000)
async def get_top_tracks(artist_name: str, limit: int = 10) -> list:
    if not artist_name or artist_name == "Неизвестно": return []
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=20"
    try:
        res = await http_client.get(url)
        tracks = [Track(title=t["name"], artist=t["artist"]["name"], coverUrl="") 
                  for t in res.json().get("toptracks", {}).get("track", [])]
        random.shuffle(tracks)
        return tracks[:limit]
    except: return []

@alru_cache(maxsize=100)
async def get_tracks_by_tag(tag: str, limit: int = 15) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={tag}&api_key={LASTFM_API_KEY}&format=json&limit=40"
    try:
        res = await http_client.get(url)
        tracks = [Track(title=t["name"], artist=t["artist"]["name"], coverUrl="") 
                  for t in res.json().get("tracks", {}).get("track", [])]
        random.shuffle(tracks)
        return tracks[:limit]
    except: return []

@alru_cache(maxsize=50)
async def get_global_top_tracks(limit: int = 15) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json&limit=50"
    try:
        res = await http_client.get(url)
        tracks = [Track(title=t["name"], artist=t["artist"]["name"], coverUrl="") 
                  for t in res.json().get("tracks", {}).get("track", [])]
        random.shuffle(tracks)
        return tracks[:limit]
    except: return []

# ==========================================
# 4. ГЛАВНЫЙ ЭНДПОИНТ: СМАРТ-ВОЛНА
# ==========================================
@app.get("/api/wave/next")
async def generate_wave(
    user_id: str, 
    current_artist: str = Query(""), 
    mood: str = "Любое", 
    language: str = "Любой",
    disliked: str = Query(""), 
    limit: int = 10
):
    if user_id not in user_history:
        user_history[user_id] = deque(maxlen=200)
    history = user_history[user_id]
    
    # Парсим дизлайки в список
    disliked_list = [a.strip().lower() for a in disliked.split(",") if a.strip()]
    
    def is_artist_disliked(artist_name):
        """Проверяет, не находится ли артист в черном списке"""
        name_lower = artist_name.lower()
        return any(d in name_lower or name_lower in d for d in disliked_list)

    wave_queue = []
    candidate_pool = []
    tasks = []

    recent_clean = [t.replace("_", " ") for t in list(history)[-10:]] if len(history) >= 2 else []
    
    # 1. ЗАПРАШИВАЕМ ИИ (Для фильтров или продолжения вайба)
    if mood != "Любое" or language != "Любой" or recent_clean:
        smart_artists = await get_smart_artists(recent_clean, mood, language, disliked_list)
        for artist in smart_artists:
            if not is_artist_disliked(artist):
                tasks.append(get_top_tracks(artist, limit=5))
    
    # 2. ЕСЛИ ИИ НЕ СПРАВИЛСЯ ИЛИ ЗАПРОС ПУСТОЙ (Классические алгоритмы)
    if not tasks:
        if current_artist and current_artist != "Неизвестно" and not is_artist_disliked(current_artist):
            tasks.append(get_top_tracks(current_artist, limit=10))
            similar_artists = await get_similar_artists(current_artist)
            safe_similars = [a for a in similar_artists if not is_artist_disliked(a)]
            
            if safe_similars:
                chosen_similars = random.sample(safe_similars, min(4, len(safe_similars)))
                for art in chosen_similars:
                    tasks.append(get_top_tracks(art, limit=5))
        else:
            # Фоллбек: если язык выбран Русский, ищем русский поп/хиты
            fallback_tag = "russian" if language == "Русский" else "pop"
            tasks.append(get_tracks_by_tag(fallback_tag, limit=15))

    # Выполняем все запросы к Last.fm одновременно
    results = await asyncio.gather(*tasks)
    for res in results:
        candidate_pool.extend(res)

    random.shuffle(candidate_pool)
    
    # Финальная сборка очереди (с двойной проверкой дизлайков и истории)
    for track in candidate_pool:
        if is_artist_disliked(track.artist):
            continue
            
        track_id = f"{track.artist}_{track.title}".lower()
        if track_id not in history:
            wave_queue.append(track)
            history.append(track_id)
            
        if len(wave_queue) >= limit: 
            break
            
    return {"status": "success", "tracks": wave_queue}

# ==========================================
# 5. ИМПОРТ ПЛЕЙЛИСТОВ (БЕЗ БЛОКИРОВОК)
# ==========================================
def parse_yandex(url: str):
    tracks_list = []
    playlist_title = "Яндекс Плейлист"
    try:
        match_user = re.search(r'users/([^/]+)/playlists/(\d+)', url)
        match_album = re.search(r'album/(\d+)', url)
        match_artist = re.search(r'artist/(\d+)', url)

        if match_user:
            playlist = ya_client.users_playlists(int(match_user.group(2)), match_user.group(1))
            if playlist.title: playlist_title = playlist.title
            for track_short in playlist.fetch_tracks():
                if track_short.track:
                    artist = track_short.track.artists[0].name if track_short.track.artists else "Неизвестный"
                    tracks_list.append({"title": track_short.track.title, "artist": artist})
        elif match_album:
            album = ya_client.albums_with_tracks(int(match_album.group(1)))
            if album.title: playlist_title = album.title
            if album.volumes:
                for volume in album.volumes:
                    for track in volume:
                        artist = track.artists[0].name if track.artists else "Неизвестный"
                        tracks_list.append({"title": track.title, "artist": artist})
        elif match_artist:
            artist_id = int(match_artist.group(1))
            artist_info = ya_client.artists([artist_id])[0]
            playlist_title = f"Топ: {artist_info.name}"
            tracks = ya_client.artists_tracks(artist_id).tracks
            for track in tracks[:50]:
                artist = track.artists[0].name if track.artists else artist_info.name
                tracks_list.append({"title": track.title, "artist": artist})
    except:
        pass
    return playlist_title, tracks_list

@app.get("/api/import")
async def import_playlist(url: str):
    tracks_list = []
    playlist_title = "Импортированный плейлист"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        if "music.yandex" in url:
            playlist_title, tracks_list = await asyncio.to_thread(parse_yandex, url)

            if not tracks_list:
                bot_headers = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"}
                response = await http_client.get(url, headers=bot_headers)
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
                
                m_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{album_id}"
                if access_key: m_url += f"&access_hash={access_key}"
                    
                response = await http_client.get(m_url, headers=headers)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                title_tag = soup.find('div', class_='AudioPlaylistSnippet__title') or soup.find('h1') or soup.find('div', class_='op_header')
                if title_tag: playlist_title = title_tag.text.strip()

                for item in soup.find_all('div', class_='audio_item'):
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
