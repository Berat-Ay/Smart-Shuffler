import os
import json
from flask import Flask, render_template, jsonify, request, redirect, session, url_for
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random
from dotenv import load_dotenv

try:
    from langdetect import detect
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default-fallback-key')
app.config['SESSION_COOKIE_NAME'] = 'spotify-login-session'

CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
REDIRECT_URI = os.getenv('SPOTIPY_REDIRECT_URI')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if GENAI_AVAILABLE and GEMINI_API_KEY and GEMINI_API_KEY != 'buraya_google_gemini_api_anahtarini_yapistiracaksiniz':
    genai.configure(api_key=GEMINI_API_KEY)

# Gerekli Kapsamlar (Kitaplık okuma, cihazları görme ve oynatmayı kontrol etme)
SCOPE = "user-library-read user-modify-playback-state user-read-playback-state"

def create_spotify_oauth():
    cache_handler = spotipy.cache_handler.FlaskSessionCacheHandler(session)
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_handler=cache_handler,
        show_dialog=True
    )

def detect_language_safe(text):
    if not LANGDETECT_AVAILABLE:
        return "unknown"
    try:
        lang = detect(text)
        return lang
    except:
        return "unknown"

def process_ai_sort(tracks, user_prompt):
    if not GENAI_AVAILABLE:
        raise Exception("Google Generative AI kütüphanesi eksik. Lütfen 'pip install google-generativeai' komutunu çalıştırın.")
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'buraya_google_gemini_api_anahtarini_yapistiracaksiniz':
        raise Exception("Gemini API anahtarı eksik. Lütfen .env dosyanıza geçerli bir GEMINI_API_KEY ekleyin.")
        
    # Eski modeller kullanımdan kaldırıldığı için mevcut en güncel modeli (gemini-flash-latest) kullanıyoruz.
    model = genai.GenerativeModel('gemini-flash-latest')
    
    # Şarkıları string olarak hazırla
    track_list_str = ""
    track_dict = {} 
    for item in tracks:
        track = item['track']
        tid = track['id']
        name = track['name']
        artist = track['artists'][0]['name'] if track['artists'] else "Unknown Artist"
        track_list_str += f"{tid} || {name} - {artist}\n"
        track_dict[tid] = track
        
    # Daha zekice bir Prompt Stratejisi:
    # Yapay zekadan koskoca listeyi aklında tutup sıralamasını istemek yerine,
    # her bir şarkıya "kullanıcının isteğine uygunluk puanı (score)" vermesini istiyoruz.
    prompt = f"""
Here is a list of {len(tracks)} songs in the format "ID || Song Name - Artist".
The user wants to sort these songs based on this request: "{user_prompt}"

Your task is to assign a sorting score (integer from 1 to 100) to each song based on the user's request.
- A LOWER score (e.g., 1, 2, 10) means the song should be played FIRST.
- A HIGHER score (e.g., 80, 90, 100) means the song should be played LATER.
For example, if the user asks for "Energetic songs first, then slow songs", assign 1 to highly energetic songs, 50 to medium, and 100 to slow/acoustic songs.
If the user asks for "Rock first, then Pop", assign 1 to Rock, 2 to Pop, and 100 to anything else.

You MUST return a valid JSON dictionary where the keys are the EXACT Track IDs and the values are the integer scores.
Do not skip any songs. You must output exactly {len(tracks)} key-value pairs.

Songs:
{track_list_str}
"""
    try:
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        # Markdown etiketlerini temizle
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        scores_dict = json.loads(response_text)
    except Exception as e:
        print(f"AI Parse/API Error: {str(e)}\nRaw Response: {response.text if 'response' in locals() else 'None'}")
        raise Exception(f"Yapay zeka hatası: {str(e)}")
        
    # Python tarafında puanlara göre şarkıları sıralıyoruz.
    # Eğer AI bir şarkıyı atlamışsa, en sona atıyoruz (puan = 999)
    sorted_items = sorted(
        track_dict.keys(), 
        key=lambda tid: int(scores_dict.get(tid, 999))
    )
    
    return sorted_items

def process_ai_discovery(sp, user_prompt):
    if not GENAI_AVAILABLE:
        raise Exception("Google Generative AI kütüphanesi eksik. Lütfen 'pip install google-generativeai' komutunu çalıştırın.")
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'buraya_google_gemini_api_anahtarini_yapistiracaksiniz':
        raise Exception("Gemini API anahtarı eksik. Lütfen .env dosyanıza geçerli bir GEMINI_API_KEY ekleyin.")
        
    if not user_prompt:
        return False, "Lütfen yapay zeka için bir şarkı ismi, tür veya duygu girin."
        
    model = genai.GenerativeModel('gemini-flash-latest')
    
    prompt = f"""
The user is asking for music recommendations based on this prompt: "{user_prompt}"

Your task is to recommend exactly 15 distinct songs that perfectly match the user's request. Do NOT just list songs the user might already know, try to find gems that fit the mood/genre/artists mentioned.
You MUST return ONLY a valid JSON array of objects. Each object must have "title" and "artist" keys.
Do not include any explanations, markdown formatting (like ```json), or extra text.

Example format:
[
  {{"title": "Bohemian Rhapsody", "artist": "Queen"}},
  {{"title": "Hotel California", "artist": "Eagles"}}
]
"""
    try:
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        recommended_songs = json.loads(response_text)
    except Exception as e:
        print(f"AI Parse/API Error: {str(e)}\nRaw Response: {response.text if 'response' in locals() else 'None'}")
        raise Exception(f"Yapay zeka öneri oluşturamadı: {str(e)}")

    final_uris = []
    found_count = 0
    for song in recommended_songs:
        # Daha esnek bir arama sorgusu: şarkı adı ve sanatçı
        query = f"track:{song['title']} artist:{song['artist']}"
        try:
            results = sp.search(q=query, type='track', limit=1)
            tracks = results['tracks']['items']
            if tracks:
                final_uris.append(tracks[0]['uri'])
                found_count += 1
            else:
                # Sadece şarkı adıyla tekrar deneyelim (bazen feat. isimleri vb bozabiliyor)
                fallback_query = song['title']
                results = sp.search(q=fallback_query, type='track', limit=1)
                tracks = results['tracks']['items']
                if tracks:
                    final_uris.append(tracks[0]['uri'])
                    found_count += 1
        except Exception as e:
            print(f"Spotify Search Error for {query}: {str(e)}")
            continue

    if not final_uris:
        return False, "Yapay zeka şarkılar önerdi ancak bu şarkılar Spotify'da bulunamadı."

    # 5. Aktif Cihazı Bul ve Çalmayı Başlat
    devices_data = sp.devices()
    devices = devices_data.get('devices', [])
    
    active_device_id = None
    for d in devices:
        if d['is_active']:
            active_device_id = d['id']
            break
            
    if not active_device_id and devices:
        active_device_id = devices[0]['id']
        
    if not active_device_id:
        return False, "Aktif bir Spotify cihazı bulunamadı. Önce telefon veya PC'de Spotify uygulamasını açıp bir şarkı oynatın."

    sp.start_playback(device_id=active_device_id, uris=final_uris)
        
    return True, f"Yapay zeka {found_count} yeni şarkı keşfetti ve çalmaya başladı!"


def process_smart_shuffle(sp, mode='artist', priority_lang='none', user_prompt=''):
    # 1. Beğenilen Şarkıları Çek 
    max_tracks_to_fetch = 250 if mode == 'ai_sort' else 500
    results = sp.current_user_saved_tracks(limit=50)
    tracks = results['items']
    
    fetched = len(tracks)
    while results['next'] and fetched < max_tracks_to_fetch:
        results = sp.next(results)
        tracks.extend(results['items'])
        fetched += len(results['items'])
        
    tracks = tracks[:max_tracks_to_fetch]
    
    if not tracks:
        return False, "Beğenilen şarkı bulunamadı."

    final_track_ids = []

    if mode == 'ai_sort':
        if not user_prompt:
            return False, "Yapay zeka için geçerli bir talep (prompt) girmediniz."
        final_track_ids = process_ai_sort(tracks, user_prompt)
        msg = f"Yapay zeka müziklerini '{user_prompt}' isteğine göre puanladı ve sıraladı!"
    else:
        # 2. Seçilen moda göre şarkıları grupla
        groups = {}
        for item in tracks:
            track = item['track']
            track_id = track['id']
            
            # Ana sanatçının adını al
            artist_name = 'Bilinmeyen Sanatçı'
            if track['artists'] and track['artists'][0]['name']:
                artist_name = track['artists'][0]['name']
                
            if mode == 'artist':
                group_key = artist_name
                
            elif mode == 'lang_artist':
                lang = detect_language_safe(track['name'])
                # Örn: "tr___Teoman" veya "en___Eminem"
                group_key = f"{lang}___{artist_name}"
                
            elif mode == 'decade':
                release_date = track['album']['release_date']
                try:
                    year = int(release_date[:4])
                    decade = (year // 10) * 10
                    group_key = f"{decade}'ler"
                except:
                    group_key = "Bilinmeyen Yıl"
            else:
                group_key = artist_name

            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(track_id)

        # 3. Her grubun içindeki şarkıları kendi arasında karıştır
        for key in groups:
            random.shuffle(groups[key])

        # 4. Grupların kendi aralarındaki sıralamasını belirle
        group_keys = list(groups.keys())
        
        if mode == 'lang_artist' and priority_lang != 'none':
            # priority_lang (örn: 'tr', 'en', 'ja') ile başlayanları ayır
            priority_keys = [k for k in group_keys if k.startswith(f"{priority_lang}___")]
            other_keys = [k for k in group_keys if not k.startswith(f"{priority_lang}___")]
            
            random.shuffle(priority_keys)
            random.shuffle(other_keys)
            group_keys = priority_keys + other_keys
            
            # Dil modunda blokları karıştırmak yeterli, sıralama öncelikli dile göre yapıldı.
            
        elif mode == 'decade':
            # Yılları kronolojik sıraya koy (Önce 70'ler, sonra 80'ler...)
            group_keys.sort()
        else:
            # Klasik artist modunda grupları rastgele sırala
            random.shuffle(group_keys)
        
        for key in group_keys:
            final_track_ids.extend(groups[key])
            
        if mode == 'decade':
            msg = "Zaman Yolculuğu (On yıllara göre) başlatıldı!"
        elif mode == 'lang_artist':
            msg = "Dil ve Sanatçıya göre karıştırma başlatıldı!"
        else:
            msg = "Sanatçıya göre karıştırma başlatıldı!"

    # Spotify URI formatına dönüştürme (spotify:track:ID)
    final_uris = [f"spotify:track:{tid}" for tid in final_track_ids]
    
    # 5. Aktif Cihazı Bul ve Çalmayı Başlat
    devices_data = sp.devices()
    devices = devices_data.get('devices', [])
    
    active_device_id = None
    for d in devices:
        if d['is_active']:
            active_device_id = d['id']
            break
            
    if not active_device_id and devices:
        active_device_id = devices[0]['id'] # Aktif yoksa ilk bulduğunu seç
        
    if not active_device_id:
        return False, "Aktif bir Spotify cihazı bulunamadı. Önce telefon veya PC'de Spotify uygulamasını açıp bir şarkı oynatın."

    # Spotify Web API start_playback parametresinde maksimum 100 URI kabul eder.
    sp.start_playback(device_id=active_device_id, uris=final_uris[:100])
        
    return True, msg

@app.route('/')
def index():
    # Token kontrolü / İlk Yetkilendirme URL üretimi
    sp_oauth = create_spotify_oauth()
    if not sp_oauth.validate_token(sp_oauth.cache_handler.get_cached_token()):
        auth_url = sp_oauth.get_authorize_url()
        return f'''
        <!DOCTYPE html>
        <html lang="tr">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Akıllı Karıştırıcı - Giriş</title>
            <style>
                * {{
                    box-sizing: border-box;
                }}
                body {{
                    background-color: #121212;
                    color: #FFFFFF;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                    text-align: center;
                    padding: 20px;
                }}
                .btn {{
                    background-color: #1DB954;
                    color: white;
                    text-decoration: none;
                    padding: 15px 40px;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 30px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.3);
                    display: inline-block;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <h2>Uygulamayı kullanmak için yetkilendirmeniz gerekiyor.</h2>
            <a href="{auth_url}" class="btn">Spotify ile Giriş Yap</a>
        </body>
        </html>
        '''
    return render_template('index.html')

@app.route('/callback')
def callback():
    sp_oauth = create_spotify_oauth()
    session.clear()
    code = request.args.get('code')
    
    if code:
        # Code'u kullanarak access token'ı alıyoruz
        try:
            sp_oauth.get_access_token(code)
            return redirect(url_for('index'))
        except Exception as e:
            return f"Yetkilendirme hatası: {str(e)}"
    
    return "Hata: Spotify'dan code dönmedi."

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/shuffle-and-play')
def shuffle_and_play():
    mode = request.args.get('mode', 'artist')
    priority_lang = request.args.get('priority_lang', 'none')
    user_prompt = request.args.get('prompt', '')
    
    if mode == 'lang_artist' and not LANGDETECT_AVAILABLE:
        return jsonify({
            'success': False, 
            'message': 'Dil tahmini için "langdetect" kütüphanesi eksik. Lütfen terminalde "pip install langdetect" komutunu çalıştırın.'
        })

    sp_oauth = create_spotify_oauth()
    token_info = sp_oauth.validate_token(sp_oauth.cache_handler.get_cached_token())
    if not token_info:
        return jsonify({
            'success': False, 
            'message': 'Oturum bulunamadı veya süresi doldu.', 
            'redirect': url_for('index')
        })
        
    sp = spotipy.Spotify(auth=token_info['access_token'])
    try:
        if mode == 'ai_discover':
            success, message = process_ai_discovery(sp, user_prompt=user_prompt)
        else:
            success, message = process_smart_shuffle(sp, mode=mode, priority_lang=priority_lang, user_prompt=user_prompt)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)