import os, json, logging, re, time
import azure.functions as func
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import requests

# --- lightweight mood logic ---
analyzer = SentimentIntensityAnalyzer()
KEYWORDS = {
    "focused":  {"focus","study","deadline","exam","code","debug","project","reading","research","assignment"},
    "energetic":{"gym","run","workout","dance","party","hype","power","pump"},
    "calm":     {"calm","relax","meditate","peace","yoga","sleep","breathe","chill"},
    "sad":      {"sad","tired","cry","lonely","depressed","down","blue"},
    "angry":    {"angry","frustrated","annoyed","irritated","mad","furious"},
    "romantic": {"love","romance","date","heart","crush"}
}
MOOD_QUERIES = {
    "happy":    ["good vibes","happy hits","feel good"],
    "focused":  ["deep focus","coding mode","instrumental focus"],
    "calm":     ["lofi beats","peaceful piano","ambient chill"],
    "energetic":["workout motivation","power workout","edm bangers"],
    "sad":      ["sad songs","rainy day","lofi sad"],
    "angry":    ["hard rock workout","aggressive metal","pump up"],
    "romantic": ["love pop","romantic","chill love"]
}

def detect_mood(text: str):
    t = text.lower()
    vs = analyzer.polarity_scores(t)             # {-1..1}
    compound = vs["compound"]
    words = re.findall(r"[a-z]+", t)
    wordset = set(words)
    counts = {mood: len(wordset & kws) for mood, kws in KEYWORDS.items()}

    if compound <= -0.35:
        primary = "sad" if counts.get("sad",0) >= counts.get("angry",0) else "angry"
    elif compound >= 0.35:
        primary = max(counts, key=counts.get) if max(counts.values() or [0])>0 else "happy"
    else:
        primary = "focused" if counts.get("focused",0)>0 else ("calm" if counts.get("calm",0)>0 else "happy")
    if counts.get("romantic",0)>0 and compound >= 0:
        primary = "romantic"
    return {"mood": primary, "compound": compound, "keyword_counts": counts}

def spotify_token():
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    csec = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("Missing SPOTIFY_CLIENT_ID/SECRET")
    r = requests.post("https://accounts.spotify.com/api/token",
                      data={"grant_type":"client_credentials"},
                      auth=(cid, csec), timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]

def search_playlists(token: str, queries, market="IN"):
    headers = {"Authorization": f"Bearer {token}"}
    out, seen = [], set()
    for q in queries:
        r = requests.get("https://api.spotify.com/v1/search",
                         params={"q": q, "type": "playlist", "limit": 5, "market": market},
                         headers=headers, timeout=10)
        if r.status_code != 200:
            continue
        for pl in r.json().get("playlists", {}).get("items", []):
            pid = pl.get("id")
            if pid in seen: 
                continue
            seen.add(pid)
            out.append({
                "name": pl.get("name"),
                "url": pl.get("external_urls", {}).get("spotify"),
                "image": (pl.get("images", [{}])[0] or {}).get("url"),
                "description": pl.get("description")
            })
    return out[:10]

def main(myblob: func.InputStream, outputBlob: func.Out[str]):
    logging.info(f"[BlobTrigger] {myblob.name} ({myblob.length} bytes)")
    text = myblob.read().decode("utf-8", errors="ignore")

    result = detect_mood(text)
    mood = result["mood"]
    try:
        token = spotify_token()
        recs = search_playlists(token, queries=MOOD_QUERIES.get(mood, MOOD_QUERIES["happy"]))
    except Exception as e:
        logging.exception("Spotify search failed")
        recs = []

    payload = {
        "input_blob": myblob.name,
        "mood": mood,
        "sentiment_compound": result["compound"],
        "keyword_counts": result["keyword_counts"],
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "recommendations": recs
    }
    outputBlob.set(json.dumps(payload, indent=2))
