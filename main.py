import os
import httpx
import random
import re
import asyncio
import json
import yt_dlp
from fastapi import FastAPI, Query, Header, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from collections import deque
from bs4 import BeautifulSoup
from yandex_music import Client
from async_lru import alru_cache

app = FastAPI(title="Ritm Smart Wave & Global Search API")

# ==========================================
# 0. CORS
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. CONFIG
# ==========================================
LASTFM_API_KEY = "f15f3ae666f3fc089b89a508a1607cf4"

PRIMARY_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not PRIMARY_GEMINI_KEY:
    print("WARNING: GEMINI_API_KEY is missing")

VK_FALLBACK_TOKEN = os.getenv("VK_TOKEN", "")

user_history = {}

http_client = httpx.AsyncClient(timeout=60.0)
ya_client = Client()

class Track(BaseModel):
    title: str
    artist: str
    coverUrl: str

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()


# ==========================================
# 2. GEMINI (СМАРТ ВОЛНА)
# ==========================================
async def fetch_gemini(prompt: str, model_name: str, api_key: str, timeout: float = 60.0) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response = await http_client.post(url, json=payload, timeout=timeout)

    if response.status_code != 200:
        print(f"GOOGLE ERROR CODE: {response.status_code}")
        print(f"GOOGLE ERROR TEXT: {response.text}")
        response.raise_for_status()

    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

async def get_smart_artists(
    liked_artists: list,
    skipped_artists: list,
    listened_artists: list,
    mood: str,
    language: str,
) -> list:
    print(f"AI liked artists: {liked_artists}")

    liked_context = ", ".join(liked_artists[:15]) if liked_artists else "Неизвестно"
    skipped_context = ", ".join(skipped_artists) if skipped_artists else "Нет"

    lang_rule = (
        f"СТРОГОЕ ПРАВИЛО: Выбирай артистов, поющих на языке: {language}."
        if language and language != "Любой"
        else ""
    )

    if not mood or mood.lower() == "любое":
        mood_rule = "ОПИРАЙСЯ ИСКЛЮЧИТЕЛЬНО НА ЖАНРЫ АРТИСТОВ ИЗ СПИСКА ЛАЙКОВ. Выдавай максимально похожих исполнителей."
    else:
        mood_rule = f"СТРОГОЕ ПРАВИЛО: Музыка должна быть в настроении '{mood}'. Адаптируй жанры из лайков под это настроение."

    prompt = f"""Ты — музыкальный рекомендательный ИИ.

ОСНОВА ДАННЫХ:
- ЛЮБИМЫЕ АРТИСТЫ ПОЛЬЗОВАТЕЛЯ: {liked_context}
- НЕДАВНО ПРОПУЩЕНЫ (ДИЗЛАЙКИ): {skipped_context}

ПРАВИЛА КОНТЕКСТА:
- {mood_rule}
- {lang_rule}

ТВОЯ ЗАДАЧА (ПРАВИЛО 80/20):
1. Изучи 'ЛЮБИМЫЕ АРТИСТЫ' и точно определи их жанр.
2. Выдай ровно 15 артистов.
3. 11 артистов должны быть МАКСИМАЛЬНО ПОХОЖИМИ по стилю и звучанию к любимым.
4. 4 артиста должны быть 'ОТКРЫТИЯМИ' (смежные жанры, свежие инди-артисты или тренды, которые удивят пользователя, но подходят под вайб).
5. ИСКЛЮЧИ артистов из списка пропущенных.
6. ОТВЕЧАЙ СТРОГО В ФОРМАТЕ JSON-МАССИВА СТРОК: ["Артист 1", "Артист 2", ...]. НИКАКОГО ТЕКСТА КРОМЕ JSON.
"""

    try:
        raw_text = await fetch_gemini(
            prompt,
            "gemini-3.1-flash-lite-preview",
            PRIMARY_GEMINI_KEY,
            timeout=60.0,
        )
        print(f"GEMINI success. Mood: {mood}, language: {language}")

        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if match:
            clean_text = match.group(0)
            print(f"SMART WAVE: {clean_text}")
            return json.loads(clean_text)
        raise ValueError("JSON array was not found in Gemini response")
    except Exception as e:
        import traceback
        print(f"GEMINI error: {repr(e)}")
        traceback.print_exc()
        print("Using fallback based on liked artists")
        if liked_artists:
            return random.sample(liked_artists, min(3, len(liked_artists)))
        return []


# ==========================================
# 3. LAST.FM CACHE (ДЛЯ ВОЛНЫ)
# ==========================================
@alru_cache(maxsize=500)
async def get_similar_artists(artist_name: str) -> list:
    if not artist_name or artist_name == "Неизвестно":
        return []
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=15"
    try:
        res = await http_client.get(url)
        return [artist["name"] for artist in res.json().get("similarartists", {}).get("artist", [])]
    except Exception:
        return []

@alru_cache(maxsize=1000)
async def get_top_tracks(artist_name: str, limit: int = 10) -> list:
    if not artist_name or artist_name == "Неизвестно":
        return []
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist_name}&api_key={LASTFM_API_KEY}&format=json&limit=20"
    try:
        res = await http_client.get(url)
        tracks = [
            Track(title=t["name"], artist=t["artist"]["name"], coverUrl="")
            for t in res.json().get("toptracks", {}).get("track", [])
        ]
        random.shuffle(tracks)
        return tracks[:limit]
    except Exception:
        return []

@alru_cache(maxsize=100)
async def get_tracks_by_tag(tag: str, limit: int = 15) -> list:
    url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={tag}&api_key={LASTFM_API_KEY}&format=json&limit=40"
    try:
        res = await http_client.get(url)
        tracks = [
            Track(title=t["name"], artist=t["artist"]["name"], coverUrl="")
            for t in res.json().get("tracks", {}).get("track", [])
        ]
        random.shuffle(tracks)
        return tracks[:limit]
    except Exception:
        return []


# ==========================================
# 4. SMART WAVE ENDPOINT
# ==========================================
@app.get("/api/wave/next")
async def generate_wave(
    user_id: str,
    liked: str = Query(""),
    skipped: str = Query(""),
    listened: str = Query(""),
    mood: str = Query("Любое"),
    language: str = Query("Любой"),
):
    if user_id not in user_history:
        user_history[user_id] = deque(maxlen=300)
    history = user_history[user_id]

    liked_list = [a.strip() for a in liked.split(",") if a.strip()]
    skipped_list = [a.strip() for a in skipped.split(",") if a.strip()]
    listened_list = [a.strip() for a in listened.split(",") if a.strip()]

    smart_artists = await get_smart_artists(liked_list, skipped_list, listened_list, mood, language)

    tasks = []
    for artist in smart_artists:
        if not any(skip.lower() in artist.lower() or artist.lower() in skip.lower() for skip in skipped_list):
            tasks.append(get_top_tracks(artist, limit=5))

    discovery_tag = "indie"
    if mood.lower() in ["грустное", "sad"]:
        discovery_tag = "melancholy"
    elif mood.lower() in ["бодрое", "веселое"]:
        discovery_tag = "upbeat"
    elif mood.lower() in ["релакс", "спокойное"]:
        discovery_tag = "lo-fi"

    tasks.append(get_tracks_by_tag(discovery_tag, limit=10))

    results = await asyncio.gather(*tasks)
    candidate_pool = []
    for res in results:
        candidate_pool.extend(res)

    random.shuffle(candidate_pool)

    wave_queue = []
    for track in candidate_pool:
        track_id = f"{track.artist}_{track.title}".lower()
        is_skipped_track = any(s.lower() in track.artist.lower() for s in skipped_list)

        if track_id not in history and not is_skipped_track:
            wave_queue.append(track)
            history.append(track_id)

        if len(wave_queue) >= 40:
            break

    return {"status": "success", "tracks": wave_queue}


# ==========================================
# 5. VK SEARCH (ИСПОЛЬЗУЕТСЯ В ГЛОБАЛЬНОМ ПОИСКЕ)
# ==========================================
def get_bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return VK_FALLBACK_TOKEN

def normalize_vk_track(item: dict) -> dict | None:
    stream_url = item.get("url") or item.get("streamUrl")
    if not stream_url or "audio_api_unavailable" in stream_url:
        return None

    thumb = item.get("thumb") or (item.get("album") or {}).get("thumb") or {}
    cover_url = (
        item.get("coverUrl")
        or thumb.get("photo_1200")
        or thumb.get("photo_600")
        or thumb.get("photo_300")
        or ""
    )

    return {
        "id": str(item.get("id") or f"{item.get('owner_id', 'vk')}_{item.get('title', '')}"),
        "title": item.get("title") or "Без названия",
        "artist": item.get("artist") or "VK",
        "coverUrl": cover_url.replace("http://", "https://"),
        "source": "VK",
        "streamUrl": stream_url,
        "isExplicit": bool(item.get("is_explicit")),
    }

async def fetch_vk_sync(q: str, count: int, offset: int, token: str):
    if not token:
        return []
    
    headers = {
        "User-Agent": "KateMobileAndroid/119.1 lite-482 (Android 11; SDK 30; arm64-v8a; Xiaomi Redmi; ru)",
        "Accept": "application/json",
    }
    params = {
        "q": q,
        "count": count,
        "offset": offset,
        "access_token": token,
        "v": "5.131",
    }
    try:
        response = await http_client.get("https://api.vk.com/method/audio.search", params=params, headers=headers)
        data = response.json()
        if data.get("error"):
            print(f"VK API Error: {data['error']}")
            return []
            
        items = []
        for item in data.get("response", {}).get("items", []):
            normalized = normalize_vk_track(item)
            if normalized:
                items.append(normalized)
        return items
    except Exception as e:
        print(f"VK request failed: {e}")
        return []


# ==========================================
# 6. YOUTUBE / SOUNDCLOUD SEARCH SYNC (БЕЗ ИЗВЛЕЧЕНИЯ STREAM URL)
# ==========================================
def normalize_search_text(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^\wа-яё]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()

def search_youtube_fast(query: str, limit: int = 20):
    ydl_opts = {
        "extract_flat": True, # ОЧЕНЬ ВАЖНО: Делает поиск мгновенным, не вытаскивает аудиопоток заранее
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            results = []
            for entry in info.get("entries", []):
                if not entry: continue
                duration = entry.get("duration")
                if duration and duration > 600: continue # Игнорируем миксы длиннее 10 минут
                
                video_id = entry.get("id")
                if not video_id: continue
                
                results.append({
                    "id": video_id,
                    "title": entry.get("title", "Без названия"),
                    "artist": entry.get("uploader", "YouTube"),
                    "coverUrl": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "source": "YOUTUBE",
                    "duration": duration,
                    "streamUrl": "", # Будет запрашиваться через JIT (Just-in-Time)
                })
            return results[:limit]
        except Exception as e:
            print(f"YouTube search error: {e}")
            return []

def search_soundcloud_fast(query: str, limit: int = 20):
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "scsearch",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
            results = []
            for entry in info.get("entries", []):
                if not entry: continue
                track_id = entry.get("id") or entry.get("url")
                
                # Ищем обложку в thumbnails
                thumbnails = entry.get("thumbnails", [])
                cover_url = thumbnails[-1].get("url", "") if thumbnails else ""

                results.append({
                    "id": str(track_id),
                    "title": entry.get("title", "Без названия"),
                    "artist": entry.get("uploader", "SoundCloud"),
                    "coverUrl": cover_url,
                    "source": "SOUNDCLOUD",
                    "duration": entry.get("duration", 0),
                    "streamUrl": "", # Будет запрашиваться через JIT
                })
            return results[:limit]
        except Exception as e:
            print(f"SoundCloud search error: {e}")
            return []


# ==========================================
# 7. PARALLEL GLOBAL SEARCH
# ==========================================
@app.get("/api/search/global")
@alru_cache(maxsize=500)
async def search_global(
    q: str,
    source: str = Query("YOUTUBE"),
    limit: int = Query(20),
    authorization: str | None = Header(default=None)
):
    if not q.strip():
        return {"status": "success", "items": []}

    token = get_bearer_token(authorization)

    # Параллельно запускаем все 3 парсера
    tasks = [
        asyncio.to_thread(search_youtube_fast, q, limit),
        asyncio.to_thread(search_soundcloud_fast, q, limit),
        fetch_vk_sync(q, limit, 0, token)
    ]

    # Если VK падает, yt-dlp всё равно вернет результаты
    results = await asyncio.gather(*tasks, return_exceptions=True)

    yt_tracks = results[0] if not isinstance(results[0], Exception) else []
    sc_tracks = results[1] if not isinstance(results[1], Exception) else []
    vk_tracks = results[2] if not isinstance(results[2], Exception) else []

    # Склеиваем с учетом приоритета (source)
    pref_source = source.upper()
    if pref_source == "VK":
        merged = vk_tracks + yt_tracks + sc_tracks
    elif pref_source == "SOUNDCLOUD":
        merged = sc_tracks + vk_tracks + yt_tracks
    else:
        merged = yt_tracks + vk_tracks + sc_tracks

    # Фильтруем дубликаты (оставляем первый встретившийся трек с таким названием и артистом)
    seen = set()
    unique_tracks = []
    for track in merged:
        key = f"{str(track.get('artist')).lower().strip()} {str(track.get('title')).lower().strip()}"
        if key not in seen:
            seen.add(key)
            unique_tracks.append(track)

    return {"status": "success", "items": unique_tracks[:limit]}


# ==========================================
# 8. JUST-IN-TIME STREAM RESOLVERS (ЗАЩИТА ОТ БИТЫХ ССЫЛОК)
# ==========================================
def extract_stream_url(url: str, source: str) -> str:
    # Игнорируем проверку IP, чтобы снизить шанс 403 Forbidden
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info.get("url", "")
        except Exception as e:
            print(f"JIT Stream Resolve Error [{source}]: {e}")
            return ""

@app.get("/api/stream/youtube")
async def stream_youtube(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    stream_url = await asyncio.to_thread(extract_stream_url, url, "YOUTUBE")
    if stream_url:
        return {"status": "success", "url": stream_url}
    raise HTTPException(status_code=404, detail="Не удалось получить поток YouTube (возможно видео заблокировано)")

@app.get("/api/stream/soundcloud")
async def stream_soundcloud(track_url: str):
    # track_url может быть ID или полной ссылкой. yt-dlp прожует оба варианта, если передать URL
    url = track_url if "soundcloud.com" in track_url else f"https://api.soundcloud.com/tracks/{track_url}"
    stream_url = await asyncio.to_thread(extract_stream_url, url, "SOUNDCLOUD")
    if stream_url:
        return {"status": "success", "url": stream_url}
    raise HTTPException(status_code=404, detail="Не удалось получить поток SoundCloud")


# ==========================================
# 9. PLAYLIST IMPORT
# ==========================================
def parse_yandex(url: str):
    tracks_list = []
    playlist_title = "Яндекс Плейлист"
    try:
        match_user = re.search(r"users/([^/]+)/playlists/(\d+)", url)
        match_album = re.search(r"album/(\d+)", url)
        match_artist = re.search(r"artist/(\d+)", url)

        if match_user:
            playlist = ya_client.users_playlists(int(match_user.group(2)), match_user.group(1))
            if playlist.title:
                playlist_title = playlist.title
            for track_short in playlist.fetch_tracks():
                if track_short.track:
                    artist = track_short.track.artists[0].name if track_short.track.artists else "Неизвестный"
                    tracks_list.append({"title": track_short.track.title, "artist": artist})
        elif match_album:
            album = ya_client.albums_with_tracks(int(match_album.group(1)))
            if album.title:
                playlist_title = album.title
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
    except Exception:
        pass
    return playlist_title, tracks_list

@app.get("/api/import")
async def import_playlist(url: str):
    tracks_list = []
    playlist_title = "Импортированный плейлист"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        if "music.yandex" in url:
            playlist_title, tracks_list = await asyncio.to_thread(parse_yandex, url)

            if not tracks_list:
                bot_headers = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"}
                response = await http_client.get(url, headers=bot_headers)
                soup = BeautifulSoup(response.text, "html.parser")

                title_tag = (
                    soup.find("h1", class_="page-playlist__title")
                    or soup.find("h1", class_="page-artist__title")
                    or soup.find("h1")
                )
                if title_tag:
                    playlist_title = title_tag.text.strip()

                for track_node in soup.find_all("div", class_="d-track"):
                    try:
                        title = track_node.find("div", class_="d-track__name").text.strip()
                        artist_node = track_node.find("span", class_="d-track__artists")
                        artist = artist_node.text.strip() if artist_node else "Неизвестно"
                        tracks_list.append({"title": title, "artist": artist})
                    except Exception:
                        continue

        elif "vk.com" in url or "vk.ru" in url:
            match = re.search(r"audio_playlist(-?\d+)_(\d+)", url) or re.search(r"playlist/(-?\d+)_(\d+)", url)
            if match:
                owner_id = match.group(1)
                album_id = match.group(2)
                access_key = ""
                key_match = re.search(r"access_key=([a-zA-Z0-9]+)", url) or re.search(r"_([a-zA-Z0-9]+)$", url)
                if key_match:
                    access_key = key_match.group(1)

                m_url = f"https://m.vk.com/audio?act=audio_playlist{owner_id}_{album_id}"
                if access_key:
                    m_url += f"&access_hash={access_key}"

                response = await http_client.get(m_url, headers=headers)
                soup = BeautifulSoup(response.text, "html.parser")

                title_tag = soup.find("div", class_="AudioPlaylistSnippet__title") or soup.find("h1") or soup.find("div", class_="op_header")
                if title_tag:
                    playlist_title = title_tag.text.strip()

                for item in soup.find_all("div", class_="audio_item"):
                    try:
                        title = item.find("span", class_="ai_title").text.strip()
                        artist = item.find("span", class_="ai_artist").text.strip()
                        tracks_list.append({"title": title, "artist": artist})
                    except Exception:
                        continue

        return {
            "status": "success",
            "playlist_title": playlist_title,
            "tracks": tracks_list,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "tracks": []}


# ==========================================
# 10. BACKGROUND VIDEO
# ==========================================
def get_direct_mp4_url(query: str):
    ydl_opts = {
        "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best",
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if "entries" in info and len(info["entries"]) > 0:
                return info["entries"][0].get("url")
        except Exception as e:
            print(f"Video load error: {e}")
    return ""

@app.get("/api/video/background")
async def get_video_background(artist: str, title: str):
    query = f"{artist} {title} official music video"
    url = await asyncio.to_thread(get_direct_mp4_url, query)
    if url:
        return {"status": "success", "url": url}
    return {"status": "error", "url": ""}

@app.get("/")
async def root():
    return {"status": "ok", "message": "API is running"}
