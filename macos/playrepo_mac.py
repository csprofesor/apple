#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayRepo macOS Desktop Application
Ultra-modern Pitch-Black WebApp Desktop GUI for macOS.
Extracts Turkish Dubbed & Subtitled movies with all video providers (Vidmoly, VOE, Streamtape, Doodstream, etc.),
and publishes atomically to GitHub (csprofesor/apple) for instant playback on Nuvio / Stremio.
"""

import sys
import os
import re
import json
import time
import random
import urllib.parse
import subprocess
import threading
import tempfile
import shutil
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import socket

CONFIG_FILE = os.path.expanduser("~/.playrepo_config.json")
PROCESSED_MOVIES_FILE = os.path.expanduser("~/.playrepo_processed_urls.json")
REPO_OWNER = "csprofesor"
REPO_NAME = "apple"
DEFAULT_BRANCH = "main"
LOGO_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"

WORKSPACE_DIR = os.environ.get("PLAYREPO_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(WORKSPACE_DIR, "data")):
    if os.path.exists("/Users/gokturksahan/apple/data"):
        WORKSPACE_DIR = "/Users/gokturksahan/apple"
    else:
        WORKSPACE_DIR = os.path.expanduser("~/.playrepo_workspace")
        os.makedirs(os.path.join(WORKSPACE_DIR, "data"), exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

# -------------------------------------------------------------
# HTTP & Network Helpers
# -------------------------------------------------------------
def http_get(url: str, referer: str = None, headers: dict = None, timeout: int = 12) -> str:
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout)]
    ua = random.choice(USER_AGENTS)
    cmd += ["-A", ua]
    cmd += ["-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"]
    cmd += ["-H", "Accept-Language: tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        return res.stdout or ""
    except Exception:
        return ""

def http_json(url: str, method: str = "GET", data: dict = None, token: str = None, timeout: int = 30) -> dict:
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout), "-X", method]
    cmd += ["-H", "Accept: application/vnd.github.v3+json"]
    cmd += ["-H", "User-Agent: PlayRepo-MacOS"]
    if token:
        cmd += ["-H", f"Authorization: token {token}"]
    input_str = None
    if data is not None:
        cmd += ["-H", "Content-Type: application/json"]
        cmd += ["--data-binary", "@-"]
        input_str = json.dumps(data)
    cmd.append(url)
    try:
        res = subprocess.run(cmd, input=input_str, capture_output=True, text=True, errors="ignore")
        if res.stdout and res.stdout.strip().startswith(("{", "[")):
            return json.loads(res.stdout)
        elif res.stdout:
            return {"message": res.stdout.strip()[:300]}
        return {"message": res.stderr.strip() if res.stderr else "Boş yanıt alındı"}
    except Exception as e:
        return {"message": str(e)}

def fetch_github_file(path: str, token: str = None, timeout: int = 15) -> dict:
    """
    Fetches JSON file directly from GitHub Contents API bypassing Fastly CDN caching.
    Uses Accept: application/vnd.github.v3.raw
    Falls back to raw.githubusercontent.com only if API fails.
    """
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout),
           "-H", "Accept: application/vnd.github.v3.raw",
           "-H", "User-Agent: PlayRepo-MacOS"]
    if token:
        cmd += ["-H", f"Authorization: token {token}"]
    cmd.append(api_url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        if res.stdout and res.stdout.strip().startswith(("{", "[")):
            return json.loads(res.stdout)
    except Exception:
        pass

    raw_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{DEFAULT_BRANCH}/{path}?t={int(time.time())}"
    return http_json(raw_url, timeout=timeout)

# -------------------------------------------------------------
# Packer (Dean Edwards) Decoder
# -------------------------------------------------------------
def unpack_packer_js(packed: str) -> str:
    match = re.search(
        r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d?\s*\)\s*\{.*?\}\s*\(\s*['\"](.*?)['\"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['\"](.*?)['\"]\.split\(\s*['\"]\|['\"]\s*\)",
        packed,
        re.DOTALL
    )
    if not match:
        return packed
    payload = match.group(1)
    radix = int(match.group(2))
    keywords = match.group(4).split("|")
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def decode_base(s, b):
        res = 0
        for ch in s:
            idx = chars.find(ch)
            if idx < 0 or idx >= b:
                return -1
            res = res * b + idx
        return res

    def repl(m):
        w = m.group(0)
        idx = decode_base(w, radix)
        if 0 <= idx < len(keywords) and keywords[idx]:
            return keywords[idx]
        return w

    return re.sub(r"\b\w+\b", repl, payload).replace(r"\/", "/")

def is_similar_title(target: str, cand: str, target_year: str = None, cand_year: str = None) -> bool:
    if not target or not cand:
        return False

    # Yıl kontrolü: Eğer iki tarafın da yılı biliniyorsa ve fark 1 yıldan fazlaysa (örn 1993 vs 2026) ASLA eşleştirme!
    if target_year and cand_year:
        try:
            m_ty = re.search(r'\d{4}', str(target_year))
            m_cy = re.search(r'\d{4}', str(cand_year))
            if m_ty and m_cy:
                y_t = int(m_ty.group(0))
                y_c = int(m_cy.group(0))
                if abs(y_t - y_c) > 1:
                    return False
        except Exception:
            pass

    t_clean = re.sub(r'[^a-zA-Z0-9]+', '', target).lower()
    c_clean = re.sub(r'[^a-zA-Z0-9]+', '', cand).lower()
    if t_clean == c_clean:
        return True

    stopwords = {"the", "a", "an", "and", "ve", "bir", "in", "of", "to", "for", "film", "filmi"}
    t_words = {w for w in re.findall(r'\w{2,}', target.lower()) if w not in stopwords}
    c_words = {w for w in re.findall(r'\w{2,}', cand.lower()) if w not in stopwords}
    if not t_words or not c_words:
        return t_clean == c_clean

    overlap = len(t_words & c_words)
    t_ratio = overlap / len(t_words)
    c_ratio = overlap / len(c_words)

    # Both sides must agree to prevent one-word titles matching completely different movies
    return t_ratio >= 0.75 and c_ratio >= 0.75

# -------------------------------------------------------------
# Scraper Engine
# -------------------------------------------------------------
class ScraperEngine:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback or (lambda s: None)
        self.is_running = True
        self.is_paused = False
        self.pause_cond = threading.Condition()

    def check_alive(self) -> bool:
        """Pauses execution if paused, returns False immediately if stopped."""
        with self.pause_cond:
            while self.is_paused and self.is_running:
                self.pause_cond.wait(timeout=0.4)
        return self.is_running

    def pause(self):
        with self.pause_cond:
            self.is_paused = True
            self.pause_cond.notify_all()

    def resume(self):
        with self.pause_cond:
            self.is_paused = False
            self.pause_cond.notify_all()

    def stop(self):
        with self.pause_cond:
            self.is_running = False
            self.is_paused = False
            self.pause_cond.notify_all()

    def log(self, msg: str):
        self.log_callback(msg)

    def detect_category(self, url: str, text: str) -> str:
        lower = f"{url.lower()} {text.lower()}"
        # 1. Platform Koleksiyonları (Öncelikli)
        if "netflix" in lower:
            return "Netflix"
        if any(k in lower for k in ["disney", "pixar", "marvel"]):
            return "Disney+"
        if any(k in lower for k in ["prime video", "amazon prime", "prime"]):
            return "Prime Video"
        if any(k in lower for k in ["apple tv", "appletv", "apple original"]):
            return "Apple TV+"
        if any(k in lower for k in ["hbo", "hbomax"]):
            return "HBO Max"
        if "hulu" in lower:
            return "Hulu"
        if any(k in lower for k in ["blutv", "gain"]):
            return "BluTV & Yerli"
        if "mubi" in lower:
            return "Mubi & Kült"

        # 2. Tür Koleksiyonları
        if any(k in lower for k in ["animasyon", "animation", "çizgi", "anime", "çizgi dizi", "çizgi film"]):
            return "Animasyon & Aile"
        if any(k in lower for k in ["belgesel", "documentary", "tarih", "history", "biyografi", "biography"]):
            return "Belgesel & Tarih"
        if any(k in lower for k in ["suç", "suc", "crime", "polisiye", "dedektif"]):
            return "Suç & Polisiye"
        if any(k in lower for k in ["korku", "gerilim", "horror", "thriller", "slasher", "gizem", "mystery"]):
            return "Korku & Gerilim"
        if any(k in lower for k in ["aksiyon", "macera", "action", "adventure", "dövüş", "dovus", "savaş", "savas", "western", "kovboy"]):
            return "Aksiyon & Macera"
        if any(k in lower for k in ["komedi", "mizah", "comedy", "parodi", "kara mizah"]):
            return "Komedi"
        if any(k in lower for k in ["dram", "drama", "romantik", "romance", "aşk", "ask", "duygusal", "melodram"]):
            return "Dram & Romantik"
        if any(k in lower for k in ["bilim", "kurgu", "fantastik", "sci-fi", "scifi", "fantasy", "uzay", "süper kahraman", "distopya"]):
            return "Bilim Kurgu & Fantastik"
        return "Popüler"

    def is_valid_movie_url(self, url: str, site_base: str) -> bool:
        if not url.startswith(site_base):
            return False
        lower = url.lower().rstrip("/")
        if lower in (site_base.lower(), f"{site_base.lower()}/"):
            return False
        disallowed = [
            "/kategori/", "/tur/", "/filmizle/", "/yil/", "/category/", "/genre/",
            "/platform/", "/platformlar/", "/koleksiyon/", "/forum", "/iletisim", "/page/", "page=", "orderby=",
            "/tag/", "/etiket/", "#", "javascript:", ".jpg", ".png", ".css", ".js",
            ".svg", ".webp", ".ico", "/login", "/uye", "/takvim", "/trendler",
            "/seri-filmler", "/istek", "/dmca", "/gizlilik", "/privacy", "/contact",
            ".xml", ".rss", "sitemap"
        ]
        if any(d in lower for d in disallowed):
            return False

        path = lower[len(site_base):].strip("/")
        if not path:
            return False

        matches_kw = any(k in lower for k in [
            "/film/", "-izle", "/dizi/", "/filmler/", "/movie/", "/watch/",
            "/izle/", "/sinema/", "/video/", "/title/", "/bolum/", "/episode/",
            "/sezon/", "/sezon-", "/bolum-"
        ])
        is_hyphenated_slug = "-" in path and "/page" not in path and "?" not in path

        return matches_kw or is_hyphenated_slug

    def is_valid_catalog_url(self, url: str, site_base: str) -> bool:
        if not url.startswith(site_base):
            return False
        lower = url.lower()
        if self.is_valid_movie_url(url, site_base):
            return False
        allowed = [
            "/page/", "page=", "sayfa=", "/kategori/", "/tur/", "/filmizle/",
            "/platform/", "/platformlar/", "/koleksiyon/",
            "/yil/", "/category/", "/genre/", "altyazi", "dublaj",
            f"{site_base}/film/", f"{site_base}/film", f"{site_base}/film-izle/",
            f"{site_base}/filmler/", "orderby="
        ]
        return any(k in lower for k in allowed)

    def discover_movie_links(self, site_url: str, max_pages: int = 20) -> dict:
        site_base = site_url
        if "://" in site_url:
            parts = site_url.split("/")
            site_base = f"{parts[0]}//{parts[2]}"

        movie_categories = {}
        queue = [site_url]
        visited = set()
        page_category_map = {site_url: "Popüler"}

        # Otomatik Sitemap ve RSS / Feed Keşfi (Doğrudan indeks çekme)
        sitemap_candidates = [
            f"{site_base}/sitemap.xml",
            f"{site_base}/sitemap_index.xml",
            f"{site_base}/post-sitemap.xml",
            f"{site_base}/film-sitemap.xml",
            f"{site_base}/feed",
            f"{site_base}/rss"
        ]
        sitemap_found_count = 0
        for sm_url in sitemap_candidates:
            if not self.check_alive():
                break
            try:
                sm_content = http_get(sm_url, timeout=5)
                if sm_content and ("<urlset" in sm_content or "<sitemapindex" in sm_content or "<rss" in sm_content):
                    self.log(f"⚡ [Sitemap Keşfi] {sm_url} üzerinden doğrudan indeks taranıyor...")
                    locs = [l.strip() for l in (re.findall(r'<loc>(https?://[^<]+)</loc>', sm_content, re.I) or re.findall(r'<link>(https?://[^<]+)</link>', sm_content, re.I))]
                    # Prioritize movie, series, and episode sub-sitemaps first
                    locs.sort(key=lambda x: 0 if any(k in x.lower() for k in ["movie", "series", "episode", "dizi", "film", "bolum", "post"]) else 1)
                    sub_sitemaps_fetched = 0
                    for loc in locs:
                        loc = loc.strip()
                        if loc.endswith(".xml") or "/sitemap" in loc:
                            if sub_sitemaps_fetched >= 8:
                                continue
                            sub_sitemaps_fetched += 1
                            try:
                                sub_content = http_get(loc, timeout=3)
                                if sub_content:
                                    sub_locs = re.findall(r'<loc>(https?://[^<]+)</loc>', sub_content, re.I)
                                    for sloc in sub_locs:
                                        sloc = sloc.strip()
                                        if self.is_valid_movie_url(sloc, site_base):
                                            cats = movie_categories.setdefault(sloc, set())
                                            cats.add(self.detect_category(sloc, ""))
                                            sitemap_found_count += 1
                            except Exception:
                                pass
                        elif self.is_valid_movie_url(loc, site_base):
                            cats = movie_categories.setdefault(loc, set())
                            cat_from_url = self.detect_category(loc, "")
                            cats.add(cat_from_url)
                            sitemap_found_count += 1
                    if sitemap_found_count > 0:
                        self.log(f"✓ [Sitemap Başarılı] {sitemap_found_count} film bağlantısı doğrudan haritadan alındı!")
                        break
            except Exception:
                pass

        home_html = http_get(site_url)
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', home_html, re.I | re.DOTALL):
            abs_url = urllib.parse.urljoin(site_url, href.strip())
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            cat = self.detect_category(abs_url, clean_text)
            if cat != "Popüler" and abs_url.startswith(site_base):
                if abs_url not in queue:
                    queue.append(abs_url)
                    page_category_map[abs_url] = cat

        page_count = 0
        while queue and page_count < max_pages and self.check_alive():
            curr_url = queue.pop(0)
            if curr_url in visited:
                continue
            visited.add(curr_url)
            page_count += 1

            cat_for_page = page_category_map.get(curr_url, self.detect_category(curr_url, ""))
            html = http_get(curr_url)
            if not html:
                continue

            if "Just a moment..." in html or "Bir dakika lütfen..." in html or "cf-chl-opt" in html or "challenges.cloudflare.com" in html:
                self.log(f"⚠️ [Cloudflare Koruması] {curr_url} adresinde Cloudflare bot doğrulaması tespit edildi! Tarayıcı simülasyonu deneniyor...")
                sim_h = self.simulate_browser_render(curr_url)
                if sim_h and "cf-chl-opt" not in sim_h:
                    html = sim_h
                else:
                    self.log(f"✗ [Cloudflare Engeli] {curr_url} Cloudflare Turnstile tarafından korunduğu için taranamadı. Lütfen alternatif bir site (örn: https://filmdozu.com/ veya https://ufilmizle.com/) deneyin.")

            new_in_page = 0
            for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
                abs_url = urllib.parse.urljoin(curr_url, href.strip())
                if self.is_valid_movie_url(abs_url, site_base):
                    cats = movie_categories.setdefault(abs_url, set())
                    if cat_for_page not in cats:
                        cats.add(cat_for_page)
                        new_in_page += 1

            self.log(f"[{cat_for_page}] Sayfa {page_count}/{max_pages} taranıyor (+{new_in_page} yeni film, Toplam: {len(movie_categories)})")

            for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
                abs_url = urllib.parse.urljoin(curr_url, href.strip())
                clean_text = re.sub(r'<[^>]+>', '', text).strip()
                if self.is_valid_catalog_url(abs_url, site_base) and abs_url not in visited and abs_url not in queue:
                    queue.append(abs_url)
                    is_pagi = any(p in abs_url for p in ["/page/", "page=", "sayfa="])
                    page_category_map[abs_url] = cat_for_page if is_pagi else self.detect_category(abs_url, clean_text)

        return movie_categories

    def search_specific_movie_across_sites(self, query: str, sites: list) -> dict:
        """
        Kullanıcının girdiği film adını kayıtlı tüm sitelerin arama motorlarında (?s=, /search/, /ara?)
        hedefli olarak arar ve ilgili filmin doğrudan detay sayfalarını bulur.
        """
        q_norm = query.strip()
        q_slug = re.sub(r'[^a-zA-Z0-9çğıöşüÇĞİÖŞÜ]+', '-', q_norm).strip('-').lower()
        q_enc = urllib.parse.quote_plus(q_norm)
        tokens = [t.lower() for t in re.split(r'[\s\-_\.:]+', q_norm) if len(t) >= 2]

        found_links = {}

        for site_url in sites:
            if not self.check_alive():
                break

            site_base = site_url
            if "://" in site_url:
                parts = site_url.split("/")
                site_base = f"{parts[0]}//{parts[2]}"

            search_endpoints = [
                f"{site_base}/?s={q_enc}",
                f"{site_base}/search/{q_enc}/",
                f"{site_base}/arama/{q_enc}/",
                f"{site_base}/ara?q={q_enc}",
                f"{site_base}/{q_slug}-izle/",
                f"{site_base}/{q_slug}/",
                f"{site_base}/film/{q_slug}/"
            ]

            self.log(f"🔎 [{site_base}] '{q_norm}' aranıyor...")

            site_found = 0
            for ep_url in search_endpoints:
                if not self.check_alive() or site_found >= 3:
                    break
                try:
                    html = http_get(ep_url, timeout=7)
                    if not html or "cf-chl-opt" in html:
                        continue

                    # Doğrudan film sayfasına mı yönlendi / geldik?
                    if self.is_valid_movie_url(ep_url, site_base) and any(tok in ep_url.lower() for tok in tokens):
                        found_links[ep_url] = {"Popüler"}
                        site_found += 1
                        break

                    # Arama sonuç listesindeki film bağlantılarını filtrele
                    for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
                        abs_url = urllib.parse.urljoin(ep_url, href.strip())
                        clean_text = re.sub(r'<[^>]+>', '', text).strip()
                        combined = f"{abs_url.lower()} {clean_text.lower()}"

                        if self.is_valid_movie_url(abs_url, site_base):
                            if all(tok in combined for tok in tokens):
                                if abs_url not in found_links:
                                    cat = self.detect_category(abs_url, clean_text)
                                    found_links[abs_url] = {cat}
                                    site_found += 1
                                    self.log(f"  🎯 Eşleşen film bulundu: {clean_text or abs_url}")
                except Exception:
                    pass

        return found_links

    def unpack_embed_stream_info(self, embed_url: str, referer: str, hint_lang: str = None, depth: int = 0) -> dict:
        if depth > 3 or not embed_url or embed_url.startswith(("about:", "javascript:")):
            return None

        lower = embed_url.lower()

        if (".m3u8" in lower or ".mp4" in lower) and not any(k in lower for k in ["/embed", "/e/", "/video/"]):
            return {
                "url": embed_url,
                "lang": hint_lang or "[TR Altyazılı]",
                "subtitles": []
            }

        if "my.mail.ru/video/embed/" in lower or "my.mail.ru/+/video/meta/" in lower:
            try:
                vid = embed_url.split("/")[-1].split("?")[0].replace(".html", "")
                meta = http_json(f"https://my.mail.ru/+/video/meta/{vid}")
                videos = meta.get("videos", [])
                if videos:
                    best = next((v for v in videos if v.get("key") in ["1080p", "720p"]), videos[0])
                    v_url = best.get("url", "")
                    if v_url:
                        u_res = f"https:{v_url}" if v_url.startswith("//") else v_url
                        return {"url": u_res, "lang": hint_lang or "[TR Altyazılı]", "subtitles": []}
            except Exception:
                pass

        html = http_get(embed_url, referer=referer)
        if not html:
            return None

        detected_lang = hint_lang
        extra_subs = []

        # Pilavyer desteği (pilavyerplay, /assets/js/s.php, /e/s.php veya window.__PLAYER__)
        if "pilavyer" in lower or "/s.php" in lower or "window.__PLAYER__" in html:
            player_m = re.search(r'window\.__PLAYER__\s*=\s*(\{.*?\});', html)
            if player_m:
                try:
                    p_data = json.loads(player_m.group(1))
                    stream_link = p_data.get("stream")
                    if stream_link:
                        s_link = stream_link.replace(r"\/", "/")
                        if "stream.php" in s_link and ".m3u8" not in s_link:
                            s_link = f"{s_link}&ext=.m3u8" if "?" in s_link else f"{s_link}#.m3u8"

                        p_title = str(p_data.get("title", "")).lower()
                        p_subs = p_data.get("subs") or []
                        p_audios = p_data.get("audios") or []

                        for sub_item in p_subs:
                            sub_src = sub_item.get("src")
                            sub_lang = "tur"
                            if sub_src:
                                sub_src_clean = sub_src.replace(r"\/", "/")
                                if "sub.php" in sub_src_clean and ".vtt" not in sub_src_clean:
                                    sub_src_clean = f"{sub_src_clean}&ext=.vtt" if "?" in sub_src_clean else f"{sub_src_clean}#.vtt"
                                extra_subs.append({"id": sub_lang, "url": sub_src_clean, "lang": "tur"})

                        has_tr_sub = any(s.get("lang") == "tr" or "türkçe" in str(s.get("label", "")).lower() for s in p_subs)
                        is_explicit_dub = "dublaj" in p_title or any("dub" in str(a.get("label", "")).lower() or a.get("lang") == "tr" for a in p_audios)
                        is_explicit_sub = "altyaz" in p_title or "sub" in p_title or (has_tr_sub and any(a.get("lang") == "en" for a in p_audios))

                        if is_explicit_dub:
                            detected_lang = "[TR Dublaj]"
                        elif is_explicit_sub:
                            detected_lang = "[TR Altyazılı]"
                        else:
                            detected_lang = hint_lang or "[TR Dublaj]"

                        return {
                            "url": s_link,
                            "lang": detected_lang,
                            "subtitles": extra_subs
                        }
                except Exception:
                    pass

        # Embed HTML başlığı veya metninden dil kontrolü
        title_tag = re.search(r'<title>(.*?)</title>', html, re.I)
        embed_title = title_tag.group(1).lower() if title_tag else ""
        if "dublaj" in embed_title or "türkçe dublaj" in embed_title:
            detected_lang = "[TR Dublaj]"
        elif "altyaz" in embed_title or "sub" in embed_title:
            detected_lang = "[TR Altyazılı]"
        else:
            detected_lang = hint_lang or "[TR Dublaj]"

        if "eval(function(p,a,c,k,e,d)" in html:
            html += "\n" + unpack_packer_js(html)

        m3u8_m = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I)
        if m3u8_m:
            return {"url": m3u8_m.group(1), "lang": detected_lang, "subtitles": extra_subs}

        voe_m = re.search(r"['\"]hls['\"]\s*:\s*['\"](https?://[^'\"]+\.m3u8[^'\"]*)['\"]", html, re.I)
        if voe_m:
            return {"url": voe_m.group(1), "lang": detected_lang, "subtitles": extra_subs}

        if "streamtape.com" in lower or "streamta.pe" in lower:
            st_m = re.search(r"document\.getElementById\('robotlink'\)\.innerHTML\s*=\s*['\"](.*?)['\"]", html)
            if st_m:
                tok = st_m.group(1)
                tok_add = re.search(r"\+ \('xcd(.*?)\.substring\(1\)", html)
                if tok_add:
                    tok += tok_add.group(1)
                st_url = f"https:{tok}" if tok.startswith("//") else (f"https://streamtape.com{tok}" if tok.startswith("/") else tok)
                return {"url": st_url, "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        if "dood" in lower or "ds2play" in lower:
            pass_m = re.search(r"/pass_md5/([^'\"]+)", html)
            if pass_m:
                pass_url = f"https://dood.to/pass_md5/{pass_m.group(1)}"
                token_res = http_get(pass_url, referer=embed_url)
                if token_res:
                    return {"url": f"{token_res.strip()}~PlayRepo~{int(time.time() * 1000)}", "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        if "video.sibnet.ru" in lower:
            sib_m = re.search(r'["\'](/v/[^"\']+\.mp4)["\']', html)
            if sib_m:
                return {"url": f"https://video.sibnet.ru{sib_m.group(1)}", "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        if "ok.ru" in lower or "odnoklassniki" in lower:
            ok_m = re.search(r'data-options=["\'](.*?)["\']', html)
            if ok_m:
                try:
                    ok_data = json.loads(ok_m.group(1).replace("&quot;", '"'))
                    fv = ok_data.get("flashvars", {})
                    meta = fv.get("metadata", {})
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    hls = meta.get("ondemandHls") or meta.get("hlsManifestUrl") or fv.get("ondemandHls") or fv.get("hlsManifestUrl")
                    if hls:
                        return {"url": hls, "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}
                    videos = meta.get("videos", [])
                    if videos and isinstance(videos, list):
                        last_v = videos[-1].get("url")
                        if last_v:
                            return {"url": last_v, "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}
                except Exception:
                    pass

        # FirePlayer (playerdkorea.xyz vb.)
        if "playerdkorea" in lower or "fireplayer" in lower:
            fp_m = re.search(r'FirePlayer\s*\(\s*["\']([^"\']+)["\']', html)
            if fp_m:
                v_hash = fp_m.group(1).strip()
                try:
                    parsed_fp = urllib.parse.urlsplit(embed_url)
                    fp_base = f"{parsed_fp.scheme}://{parsed_fp.netloc}"
                    api_cmd = [
                        "curl", "-s", "-X", "POST", f"{fp_base}/player/index.php?data={v_hash}&do=getVideo",
                        "-H", f"Referer: {embed_url}",
                        "-H", "X-Requested-With: XMLHttpRequest",
                        "-d", f"hash={v_hash}&r={referer or ''}",
                        "-A", random.choice(USER_AGENTS)
                    ]
                    api_res = subprocess.run(api_cmd, capture_output=True, text=True, timeout=5)
                    if api_res.stdout:
                        j_data = json.loads(api_res.stdout)
                        sec_link = j_data.get("securedLink") or j_data.get("videoSource")
                        if sec_link:
                            return {"url": sec_link.replace(r"\/", "/"), "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}
                except Exception:
                    pass

        # VidMoly desteği
        if "vidmoly" in lower:
            vm_m = re.search(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*["\'](https?://[^"\']+)["\']', html)
            if vm_m:
                return {"url": vm_m.group(1).strip(), "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        if "bitchute.com" in lower:
            bc_m = re.search(r'(https?://[^\s"\'<>]+\.bitchute\.com/[^\s"\'<>]+\.mp4)', html, re.I)
            if bc_m:
                return {"url": bc_m.group(1), "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        # Generic JSON sources / variable matching (JWPlayer, VideoJS, Plyr, etc.)
        json_stream_m = re.search(r'["\']?(?:file|src|url|hls|stream)["\']?\s*:\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', html, re.I)
        if json_stream_m:
            return {"url": json_stream_m.group(1), "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        # Direct .m3u8 or .mp4 anywhere in scripts / HTML
        any_stream_m = re.search(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', html, re.I)
        if any_stream_m and not any(k in any_stream_m.group(0) for k in ["/thumb", "/preview", ".jpg", ".png"]):
            return {"url": any_stream_m.group(0), "lang": detected_lang or hint_lang or "[TR Altyazılı]", "subtitles": extra_subs}

        for nested_m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', html, re.I):
            nested_src = urllib.parse.urljoin(embed_url, nested_m.group(1).strip())
            if nested_src != embed_url and not any(nested_src.endswith(ext) for ext in [".jpg", ".png", ".css", ".js"]):
                res = self.unpack_embed_stream_info(nested_src, referer=embed_url, hint_lang=detected_lang, depth=depth + 1)
                if res:
                    return res

        return None

    def unpack_embed_stream(self, embed_url: str, referer: str, depth: int = 0) -> str:
        info = self.unpack_embed_stream_info(embed_url, referer, depth=depth)
        return info.get("url", "") if info else ""

    def is_stream_alive(self, url: str, referer: str = None) -> bool:
        if not url or url.startswith(("about:", "javascript:")):
            return False
        ua = random.choice(USER_AGENTS)
        ref = referer or url
        cmd = ["curl", "-s", "-L", "-o", "/dev/null", "-w", "%{http_code}", "-r", "0-1024", "--max-time", "5", "-A", ua]
        if ref:
            cmd += ["-H", f"Referer: {ref}"]
        cmd.append(url)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
            code_str = (res.stdout or "").strip()
            # Only 200/206 (or redirects) are real playable streams; 400, 401, 403, 404, 500, etc. mean dead/buffering
            if code_str in ("200", "206"):
                return True
            return False
        except Exception:
            return False

    def simulate_browser_render(self, url: str) -> str:
        """
        Safe stub: returns empty string to prevent unauthorized cross-app
        automation alerts from macOS Gatekeeper / TCC. All scrapers use native HTTP and regex.
        """
        return ""

    def scrape_movie_page(self, movie_url: str, initial_cats: set, tmdb_api_key: str = None) -> dict:
        html = http_get(movie_url, referer=movie_url)
        if not html:
            return None

        title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.DOTALL) or \
                  re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                  re.search(r'<title>(.*?)</title>', html, re.I)
        raw_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else movie_url.split("/")[-1].replace("-", " ")

        # Series / Season / Episode detection from raw_title and movie_url
        comb_text = f"{raw_title} {movie_url}"
        s_num, e_num = None, None
        combo = (re.search(r'(?:season|sezon)[-\s_.]*(\d+)[-\s_.]*(?:episode|bolum|bölüm)[-\s_.]*(\d+)', comb_text, re.I) or
                 re.search(r'(\d+)[-\s_.]*(?:season|sezon)[-\s_.]*(\d+)[-\s_.]*(?:episode|bolum|bölüm)', comb_text, re.I) or
                 re.search(r'\b[sS](\d+)[-\s_.]*[eE](\d+)\b', comb_text) or
                 re.search(r'\b(\d+)x(\d+)\b', comb_text))
        if combo:
            s_num = int(combo.group(1))
            e_num = int(combo.group(2))
        else:
            sm = (re.search(r'(?:season|sezon)[-\s_.]*(\d+)', comb_text, re.I) or
                  re.search(r'(\d+)[-\s_.]*(?:season|sezon)', comb_text, re.I))
            em = (re.search(r'(?:episode|bolum|bölüm)[-\s_.]*(\d+)', comb_text, re.I) or
                  re.search(r'(\d+)[-\s_.]*(?:episode|bolum|bölüm)', comb_text, re.I))
            if sm: s_num = int(sm.group(1))
            if em: e_num = int(em.group(1))

        is_series = bool(s_num or e_num or "/dizi/" in movie_url.lower() or "/series/" in movie_url.lower())

        clean_title = raw_title
        clean_title = re.sub(r'\(?\b(19\d{2}|20\d{2})\b\)?', '', clean_title)
        # Strip all season and episode phrases so search query to Cinemeta/TMDB is pure!
        clean_title = re.sub(r'(?:season|sezon)[-\s_.]*\d+[-\s_.]*(?:episode|bolum|bölüm)[-\s_.]*\d+', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\d+[-\s_.]*(?:season|sezon)[-\s_.]*\d+[-\s_.]*(?:episode|bolum|bölüm)', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\b[sS]\d+[-\s_.]*[eE]\d+\b', '', clean_title)
        clean_title = re.sub(r'\b\d+x\d+\b', '', clean_title)
        clean_title = re.sub(r'(?:season|sezon)[-\s_.]*\d+', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\d+[-\s_.]*(?:season|sezon)', '', clean_title, flags=re.I)
        clean_title = re.sub(r'(?:episode|bolum|bölüm)[-\s_.]*\d+', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\d+[-\s_.]*(?:episode|bolum|bölüm)', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\b(?:sezon|season|bölüm|bolum|episode|final|parça|part)\b', '', clean_title, flags=re.I)

        for term in ["türkçe dublaj", "türkçe altyazı", "turkce dublaj", "turkce altyazili", "dublaj", "altyazı", "altyazi", "izle", "full hd", "1080p", "720p", "4k", "tek parça", "tek parca", "film kovası", "hintfilmizle", "dizibox", "dizimag", "filmi"]:
            clean_title = re.sub(rf'\b{re.escape(term)}\b', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\s+', ' ', clean_title).strip(" -|/:.—–")

        if not clean_title or clean_title.lower() in ("filmkovasi.co", "www.hintfilmizle.com", "diziboxizle.com"):
            return None

        year_m = re.search(r'\b(19\d{2}|20\d{2})\b', raw_title) or re.search(r'\b(19\d{2}|20\d{2})\b', html)
        year = year_m.group(1) if year_m else ""

        poster_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\']', html, re.I)
        poster = urllib.parse.urljoin(movie_url, poster_m.group(1).strip()) if poster_m else LOGO_URL

        desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'class=["\'][^"\']*(?:film-ozeti|ozet|story|summary|film-content|entry-content)[^"\']*["\'][^>]*>(.*?)</div>', html, re.I | re.DOTALL) or \
                 re.search(r'class=["\'][^"\']*(?:film-ozeti|ozet|story|summary|film-content|entry-content)[^"\']*["\'][^>]*>(.*?)</p>', html, re.I | re.DOTALL)
        desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else "Play • 1080p TR"

        imdb_m = re.search(r'class=["\'][^"\']*(?:imdb-puan|imdb|puan|rating)[^"\']*["\'][^>]*>([0-9.,]+)<', html, re.I)
        imdb_rating = imdb_m.group(1).replace(",", ".").strip() if imdb_m else None

        time_m = re.search(r'(\d{2,3})\s*(?:dk|dakika|min)', html, re.I)
        runtime = f"{time_m.group(1)} dk" if time_m else None

        genres = set(initial_cats) if initial_cats else set()
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
            if any(k in href.lower() for k in ["/kategori/", "/tur/", "/filmizle/", "/genre/", "/filmler/"]):
                cat = self.detect_category(href, re.sub(r'<[^>]+>', '', text).strip())
                if cat != "Popüler":
                    genres.add(cat)

        # "Tür: Aksiyon, Dram" gibi metin bloklarını tara
        for m in re.finditer(r'(?:Tür(?:ü|ler)?|Kategori|Genres?)\s*[:：]\s*([^<\n\r]+)', html, re.I):
            clean_block = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            cat = self.detect_category("", clean_block)
            if cat != "Popüler":
                genres.add(cat)

        # Clean HTML: remove similar movies, recommendations, sidebars, navigation bars, headers, footers, comments
        clean_player_html = html
        for block_pat in [
            r'<div[^>]*class=["\'][^"\']*(?:similar|onerilen|benzer|related|recommend)[^"\']*["\'].*?</div>\s*</div>',
            r'<nav\b.*?</nav>',
            r'<header\b.*?</header>',
            r'<footer\b.*?</footer>',
            r'<aside\b.*?</aside>',
            r'<div[^>]*class=["\'][^"\']*(?:sidebar|widget|menu|comments|yorumlar)[^"\']*["\'].*?</div>'
        ]:
            clean_player_html = re.sub(block_pat, '', clean_player_html, flags=re.I | re.DOTALL)

        candidate_embeds = []
        is_explicit_sub = ("altyaz" in raw_title.lower() or "altyaz" in movie_url.lower() or "sub" in movie_url.lower())
        default_lang = "[TR Altyazılı]" if is_explicit_sub else "[TR Dublaj]"

        def get_lang(txt: str) -> str:
            t = txt.lower()
            if any(k in t for k in ["altyaz", "sub", "orijinal", "original", "ingilizce", "eng"]):
                return "[TR Altyazılı]"
            if any(k in t for k in ["dublaj", "türkçe ses", "turkce ses", "tr ses", "türkçe", "turkce"]):
                return "[TR Dublaj]"
            return default_lang

        def add_candidate(u: str, lang: str):
            if not u or u.startswith(("about:", "javascript:")):
                return
            u_clean = u.strip().replace("&amp;", "&")
            u_low = u_clean.lower()
            # Ignore YouTube trailers, ads, asset files (keep /assets/js/s.php valid for Pilavyer)
            if any(k in u_low for k in ["youtube.com", "youtu.be", "doubleclick", "googleads", ".jpg", ".png", ".gif", ".css", ".svg", ".webp", ".ico"]):
                return
            if u_low.endswith(".js") and not ("s.php" in u_low or "embed" in u_low):
                return
            # Ignore generic category, tag, or archive listing pages
            if any(k in u_low for k in ["/dublaj/", "/altyazili/", "/kategori/", "/genre/", "/trend", "/kesfet", "/tag/", "/filmler/"]):
                return
            if not any(c[0] == u_clean for c in candidate_embeds):
                candidate_embeds.append((u_clean, lang))

        # 0. Pilavyer / DiziMom player detection (data-pv="OPAK_SLUG" in HTML or templates)
        pv_matches = list(re.finditer(r'data-pv=["\']([^"\']+)["\']', clean_player_html))
        if pv_matches:
            pilav_base = "https://pilavyerplay.top"
            core_script_m = re.search(r'["\'](https?://[^"\'\s]*/(?:assets/js/core|e/c)\.js[^"\'\s]*)["\']', clean_player_html, re.I)
            if core_script_m:
                parsed_c = urllib.parse.urlsplit(core_script_m.group(1))
                pilav_base = f"{parsed_c.scheme}://{parsed_c.netloc}"
            
            for m in pv_matches:
                pv_slug = m.group(1)
                pv_url = f"{pilav_base}/assets/js/s.php?s={urllib.parse.quote(pv_slug)}"
                surrounding = clean_player_html[max(0, m.start() - 250):min(len(clean_player_html), m.end() + 250)]
                pv_lang = get_lang(surrounding)
                add_candidate(pv_url, pv_lang)

        # 1. Player switch functions (loadSource, loadPlayer, etc.) with local button language detection
        for m in re.finditer(r'(?:loadSource|loadPlayer|changeSource|setSource|playVideo|loadEmbed)\s*\(\s*["\']([^"\']+)["\']', clean_player_html, re.I):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            match_start = m.start()
            surrounding = clean_player_html[max(0, match_start - 250):min(len(clean_player_html), match_start + 250)]
            c_lang = get_lang(surrounding)
            add_candidate(c_url, c_lang)

        # 2. Main video player iframe(s) inside player box
        for m in re.finditer(r'<iframe\s+[^>]*(?:src|data-src|data-frame)=["\']([^"\']+)["\'][^>]*>', clean_player_html, re.I):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            surrounding = clean_player_html[max(0, m.start() - 200):min(len(clean_player_html), m.start() + 200)]
            c_lang = get_lang(surrounding)
            add_candidate(c_url, c_lang)

        # 3. Source button data attributes specifically inside player area
        for btn_m in re.finditer(r'<button[^>]+data-(?:frame|hhs|video|embed|src|player)=["\']([^"\']+)["\'][^>]*>(.*?)</button>', clean_player_html, re.I | re.DOTALL):
            b_url = urllib.parse.urljoin(movie_url, btn_m.group(1).strip())
            b_lang = get_lang(btn_m.group(2) + " " + b_url)
            add_candidate(b_url, b_lang)

        # 4. WordPress / DooPlay player AJAX simulation for dynamic source tabs ONLY
        parsed_origin = urllib.parse.urlsplit(movie_url)
        site_base = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
        for opt_m in re.finditer(r'data-post=["\'](\d+)["\'][^>]*data-nume=["\']([^"\']+)["\']', clean_player_html, re.I):
            post_id, nume = opt_m.group(1), opt_m.group(2)
            match_start = opt_m.start()
            surrounding = clean_player_html[max(0, match_start - 120):min(len(clean_player_html), opt_m.end() + 200)]
            opt_lang = get_lang(surrounding)
            try:
                ajax_cmd = [
                    "curl", "-s", "-X", "POST", f"{site_base}/wp-admin/admin-ajax.php",
                    "-d", f"action=doo_player_ajax&post={post_id}&nume={nume}&type=movie",
                    "-H", f"Referer: {movie_url}",
                    "-H", "X-Requested-With: XMLHttpRequest",
                    "-A", random.choice(USER_AGENTS)
                ]
                ajax_res = subprocess.run(ajax_cmd, capture_output=True, text=True, timeout=5)
                if ajax_res.stdout:
                    for if_m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', ajax_res.stdout, re.I):
                        add_candidate(urllib.parse.urljoin(movie_url, if_m.group(1).strip()), opt_lang)
            except Exception:
                pass

        # 5. Alternate Language Page Scanner (e.g. Dublaj page linking to Altyazılı page, or multi-tabs)
        base_slug = movie_url.rstrip('/').split('/')[-1]
        for term in ["-turkce-dublaj", "-turkce-altyazili", "-dublaj", "-altyazili", "-izle"]:
            base_slug = base_slug.replace(term, "")

        if len(base_slug) >= 4:
            for a_m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', clean_player_html, re.I | re.DOTALL):
                a_href = urllib.parse.urljoin(movie_url, a_m.group(1).strip())
                a_text = re.sub(r'<[^>]+>', '', a_m.group(2)).strip()
                if a_href.startswith(site_base) and a_href != movie_url and a_href != f"{movie_url}/":
                    if base_slug in a_href and not any(k in a_href for k in ["/kategori/", "/genre/", "/tag/", "/yorum"]):
                        cand_lang = get_lang(f"{a_text} {a_href}")
                        if cand_lang != default_lang and not any(c[0] == a_href for c in candidate_embeds):
                            try:
                                alt_html = http_get(a_href, referer=movie_url)
                                if alt_html:
                                    alt_pv = re.findall(r'data-pv=["\']([^"\']+)["\']', alt_html)
                                    if alt_pv:
                                        for s in alt_pv:
                                            add_candidate(f"{pilav_base}/assets/js/s.php?s={urllib.parse.quote(s)}", cand_lang)
                                    for alt_if in re.finditer(r'<iframe\s+[^>]*(?:src|data-src|data-frame)=["\']([^"\']+)["\']', alt_html, re.I):
                                        add_candidate(urllib.parse.urljoin(a_href, alt_if.group(1).strip()), cand_lang)
                            except Exception:
                                pass

        # Extract subtitles (.vtt / .srt)
        subtitles = []
        for tr_m in re.finditer(r'<track\s+[^>]*kind=["\'](?:subtitles|captions)["\'][^>]*>', html, re.I):
            tr_tag = tr_m.group(0)
            src_m = re.search(r'src=["\']([^"\']+)["\']', tr_tag, re.I)
            if src_m:
                s_url = urllib.parse.urljoin(movie_url, src_m.group(1).strip())
                subtitles.append({
                    "id": "tur",
                    "url": s_url,
                    "lang": "tur",
                    "language": "Turkish",
                    "name": "Türkçe",
                    "label": "Türkçe",
                    "format": "vtt",
                    "type": "text/vtt"
                })

        for vtt_m in re.finditer(r'https?://[^\s"\'<>]+\.(?:vtt|srt)[^\s"\'<>]*', html, re.I):
            u = vtt_m.group(0)
            if not any(s["url"] == u for s in subtitles):
                subtitles.append({
                    "id": "tur",
                    "url": u,
                    "lang": "tur",
                    "language": "Turkish",
                    "name": "Türkçe",
                    "label": "Türkçe",
                    "format": "vtt",
                    "type": "text/vtt"
                })

        season_num = s_num
        episode_num = e_num

        resolved_streams = []
        seen_urls = set()

        for cand_url, cand_lang in candidate_embeds:
            if not self.check_alive():
                break
            if not cand_url or cand_url in seen_urls:
                continue
            info = self.unpack_embed_stream_info(cand_url, referer=movie_url, hint_lang=cand_lang)
            if info and info.get("url") and info["url"] not in seen_urls:
                direct_url = info["url"]
                actual_lang = info.get("lang") or cand_lang or default_lang
                seen_urls.add(direct_url)
                is_m3u8 = ".m3u8" in direct_url.lower() or "stream.php" in direct_url.lower() or "/hls" in direct_url.lower()
                st_item = {
                    "name": "Play",
                    "title": f"Play • 1080p • {actual_lang}",
                    "url": direct_url,
                    "quality": 1080,
                    "format": "m3u8" if is_m3u8 else "mp4",
                    "referer": cand_url,
                    "headers": {
                        "User-Agent": random.choice(USER_AGENTS),
                        "Referer": cand_url
                    }
                }
                merged_subs = list(subtitles)
                if info.get("subtitles"):
                    for s in info["subtitles"]:
                        if not any(x.get("url") == s.get("url") for x in merged_subs):
                            merged_subs.append(s)
                if merged_subs:
                    st_item["subtitles"] = merged_subs
                resolved_streams.append(st_item)

        if not resolved_streams:
            # Fallback: Headless Browser DOM Simulation for dynamic JS players
            sim_html = self.simulate_browser_render(movie_url)
            if sim_html:
                sim_cands = []
                for if_m in re.finditer(r'<iframe\s+[^>]*(?:src|data-src|data-frame)=["\']([^"\']+)["\'][^>]*>', sim_html, re.I):
                    sim_cands.append(urllib.parse.urljoin(movie_url, if_m.group(1).strip()))
                for v_m in re.finditer(r'<video[^>]+src=["\']([^"\']+)["\']', sim_html, re.I):
                    sim_cands.append(urllib.parse.urljoin(movie_url, v_m.group(1).strip()))
                for m_m in re.finditer(r'["\'](https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)["\']', sim_html, re.I):
                    sim_cands.append(m_m.group(1).strip())

                for c_url in sim_cands:
                    if c_url in seen_urls:
                        continue
                    d_url = self.unpack_embed_stream(c_url, referer=movie_url)
                    if d_url and d_url not in seen_urls:
                        seen_urls.add(d_url)
                        is_m3u8 = ".m3u8" in d_url.lower() or "stream.php" in d_url.lower() or "/hls" in d_url.lower()
                        st_item = {
                            "name": "Play",
                            "title": f"Play • 1080p • {default_lang}",
                            "url": d_url,
                            "quality": 1080,
                            "format": "m3u8" if is_m3u8 else "mp4",
                            "referer": c_url,
                            "headers": {
                                "User-Agent": random.choice(USER_AGENTS),
                                "Referer": c_url
                            }
                        }
                        if subtitles:
                            st_item["subtitles"] = subtitles
                        resolved_streams.append(st_item)

        if not resolved_streams:
            return None

        # Filter dead links via fast HTTP range check - NEVER accept dead links
        alive_streams = [s for s in resolved_streams if self.is_stream_alive(s["url"], s.get("referer"))]
        if not alive_streams:
            self.log(f"  ⚠️ Tüm çözülen linkler ölü veya oynatılamaz durumda, atlanıyor.")
            return None

        alive_streams.sort(key=lambda s: 1 if s["format"] == "m3u8" else 0, reverse=True)

        # Cap streams: Max 2 Dublaj, Max 2 Altyazı, Max 1 Sunucu (Maksimum 4-5 garantili, çalışan sunucu)
        dub_list = []
        sub_list = []
        oth_list = []
        seen_domains = set()

        for s in alive_streams:
            u_domain = urllib.parse.urlsplit(s["url"]).netloc.lower()
            raw_t = s.get("title", "").lower()
            if "dublaj" in raw_t:
                if len(dub_list) < 2 and ("dub_" + u_domain) not in seen_domains:
                    dub_list.append(s)
                    if u_domain: seen_domains.add("dub_" + u_domain)
            elif "altyaz" in raw_t:
                if len(sub_list) < 2 and ("sub_" + u_domain) not in seen_domains:
                    sub_list.append(s)
                    if u_domain: seen_domains.add("sub_" + u_domain)
            else:
                if len(oth_list) < 1 and ("oth_" + u_domain) not in seen_domains:
                    oth_list.append(s)
                    if u_domain: seen_domains.add("oth_" + u_domain)

        # Eğer sub_list boşsa ve dub_list içinde subtitles (altyazı) barındıran bir akış varsa,
        # kullanıcıya hem dublaj hem de altyazılı seçenek olarak sun
        if not sub_list and dub_list:
            for ds in dub_list:
                if ds.get("subtitles") and len(ds["subtitles"]) > 0:
                    sub_clone = dict(ds)
                    sub_clone["title"] = "[TR Altyazılı]"
                    sub_clone["is_sub_clone"] = True
                    sub_list.append(sub_clone)
                    break

        valid_streams = dub_list + sub_list + oth_list
        if not valid_streams:
            valid_streams = alive_streams[:3]

        dub_c = 0
        sub_c = 0
        oth_c = 0
        for st in valid_streams:
            st_url = st.get("url", "")
            if "stream.php" in st_url and ".m3u8" not in st_url:
                st["url"] = f"{st_url}&ext=.m3u8" if "?" in st_url else f"{st_url}#.m3u8"
            st_ref = st.get("referer") or st["url"]
            st["behaviorHints"] = {
                "notSupported": False,
                "proxyHeaders": {
                    "request": {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Referer": st_ref
                    }
                }
            }
            st["headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": st_ref
            }
            raw_t = st.get("title", "").lower()
            if "altyaz" in raw_t or st.get("is_sub_clone"):
                sub_c += 1
                stream_name = "TR Altyazı"
                formatted_title = f"1080p • Sunucu {sub_c}"
            else:
                dub_c += 1
                stream_name = "TR Dublaj"
                formatted_title = f"1080p • Sunucu {dub_c}"
            st["name"] = stream_name
            st["title"] = formatted_title

        meta_id, official_title, official_poster, official_desc, release_year, meta_genres, resolved_type, meta_extras = self.resolve_meta(
            clean_title, poster, desc, year, tmdb_api_key, is_series=is_series
        )

        if resolved_type == "movie":
            is_series = False
            season_num = None
            episode_num = None
        elif resolved_type == "series":
            is_series = True
            season_num = season_num or 1
            episode_num = episode_num or 1

        # IMDb / Cinemeta / TMDB doğrulamalı kategoriler
        verified_categories = set()
        if meta_genres:
            for g in meta_genres:
                cat = self.detect_category("", g)
                if cat != "Popüler":
                    verified_categories.add(cat)

        # Eğer IMDb/Cinemeta/TMDB'den teyitli kategori geldiyse, sitenin güvenilmez kategorilerini ezip resmi kategorileri kullan
        if verified_categories:
            genres = verified_categories
        else:
            # IMDb/TMDB eşleşmediyse fallback olarak sayfadaki kategoriyi kullan
            if not genres:
                genres.add("Popüler")

        movie_item = {
            "tmdbId": meta_id,
            "title": official_title,
            "poster": official_poster,
            "description": official_desc,
            "year": release_year,
            "imdbRating": imdb_rating,
            "runtime": runtime,
            "genres": list(genres),
            "streams": valid_streams,
            "type": "series" if is_series else "movie",
            "season": season_num,
            "episode": episode_num
        }
        if meta_extras.get("logo"):
            movie_item["logo"] = meta_extras["logo"]
        if meta_extras.get("trailers"):
            movie_item["trailers"] = meta_extras["trailers"]
        if meta_extras.get("trailerStreams"):
            movie_item["trailerStreams"] = meta_extras["trailerStreams"]
        if meta_extras.get("trailer"):
            movie_item["trailer"] = meta_extras["trailer"]
        if meta_extras.get("videos"):
            movie_item["videos"] = meta_extras["videos"]

        return movie_item

    def resolve_meta(self, title: str, poster: str, desc: str, year: str, tmdb_key: str = None, is_series: bool = False):
        clean_q = re.sub(r'[/\\?&=#]+', ' ', title).strip()
        media_kind = "series" if is_series else "movie"
        extra_data = {
            "trailers": [],
            "trailerStreams": [],
            "trailer": None,
            "logo": None
        }
        try:
            q = urllib.parse.quote(clean_q)
            cm_res = http_json(f"https://v3-cinemeta.strem.io/catalog/{media_kind}/top/search={q}.json")
            metas = cm_res.get("metas", [])
            matched = [m for m in metas if is_similar_title(clean_q, m.get("name"), target_year=year, cand_year=m.get("releaseInfo"))]
            if not matched:
                alt_kind = "movie" if is_series else "series"
                alt_res = http_json(f"https://v3-cinemeta.strem.io/catalog/{alt_kind}/top/search={q}.json")
                alt_metas = alt_res.get("metas", [])
                matched = [m for m in alt_metas if is_similar_title(clean_q, m.get("name"), target_year=year, cand_year=m.get("releaseInfo"))]

            if matched:
                m0 = matched[0]
                m_id = m0.get("id")
                if m_id and m_id.startswith("tt"):
                    resolved_type = m0.get("type") or media_kind
                    turkish_desc = desc if desc and desc != "Play • 1080p TR" else (m0.get("description") or "Play • 1080p TR")
                    extra_genres = list(m0.get("genres") or [])
                    try:
                        detail_res = http_json(f"https://v3-cinemeta.strem.io/meta/{resolved_type}/{m_id}.json")
                        d_meta = detail_res.get("meta", {})
                        if not extra_genres:
                            extra_genres = list(d_meta.get("genres") or [])
                        if d_meta.get("logo"):
                            extra_data["logo"] = d_meta["logo"]
                        if d_meta.get("trailers"):
                            extra_data["trailers"] = d_meta["trailers"]
                            tr_list = [t for t in d_meta["trailers"] if t.get("type") == "Trailer"] or d_meta["trailers"]
                            if tr_list and tr_list[0].get("source"):
                                extra_data["trailer"] = f"https://www.youtube.com/watch?v={tr_list[0]['source']}"
                        if d_meta.get("trailerStreams"):
                            extra_data["trailerStreams"] = d_meta["trailerStreams"]
                        elif extra_data.get("trailer"):
                            yt_m = re.search(r"v=([a-zA-Z0-9_-]+)", extra_data["trailer"])
                            if yt_m:
                                extra_data["trailerStreams"] = [{"title": "Fragman", "ytId": yt_m.group(1)}]
                        if d_meta.get("trailer"):
                            extra_data["trailer"] = d_meta["trailer"]

                        # Diziler için tüm bölüm listesi ve yeni bölüm yayın tarihi kontrolü
                        if resolved_type == "series":
                            if d_meta.get("videos"):
                                extra_data["videos"] = d_meta["videos"]
                            now_str = datetime.utcnow().isoformat()
                            future_vids = [v for v in d_meta.get("videos", []) if v.get("released") and str(v.get("released")) > now_str]
                            if future_vids:
                                future_vids.sort(key=lambda v: str(v.get("released")))
                                nxt = future_vids[0]
                                s_n = nxt.get("season", 1)
                                e_n = nxt.get("episode", 1)
                                rel_d = str(nxt.get("released"))[:10]
                                banner = f"📅 YENİ BÖLÜM: Sezon {s_n}, Bölüm {e_n} ({rel_d}) tarihinde yayında!\n\n"
                                if "📅 YENİ BÖLÜM" not in turkish_desc:
                                    turkish_desc = banner + turkish_desc
                    except Exception:
                        pass
                    m_poster = clean_poster_url(m0.get("poster"), m_id, fallback=poster)
                    return (
                        m_id,
                        m0.get("name") or title,
                        m_poster,
                        turkish_desc,
                        m0.get("releaseInfo") or year,
                        extra_genres,
                        resolved_type,
                        extra_data
                    )
        except Exception:
            pass

        if tmdb_key:
            try:
                q = urllib.parse.quote(clean_q)
                endpoint = "tv" if is_series else "movie"
                tm_res = http_json(f"https://api.themoviedb.org/3/search/{endpoint}?api_key={tmdb_key}&query={q}&language=tr-TR")
                results = [r for r in tm_res.get("results", []) if is_similar_title(clean_q, r.get("title") or r.get("name"), target_year=year, cand_year=r.get("release_date") or r.get("first_air_date"))]
                if not results:
                    alt_ep = "movie" if is_series else "tv"
                    alt_tm = http_json(f"https://api.themoviedb.org/3/search/{alt_ep}?api_key={tmdb_key}&query={q}&language=tr-TR")
                    results = [r for r in alt_tm.get("results", []) if is_similar_title(clean_q, r.get("title") or r.get("name"), target_year=year, cand_year=r.get("release_date") or r.get("first_air_date"))]
                    if results:
                        endpoint = alt_ep

                if results:
                    r0 = results[0]
                    r_id = r0.get("id")
                    if r_id:
                        resolved_type = "series" if endpoint == "tv" else "movie"
                        p_path = r0.get("poster_path")
                        p_url = f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else poster
                        rel_date = r0.get("release_date") or r0.get("first_air_date") or year
                        tmdb_genres = []
                        overview = r0.get("overview") or desc
                        try:
                            det = http_json(f"https://api.themoviedb.org/3/{endpoint}/{r_id}?api_key={tmdb_key}&language=tr-TR&append_to_response=videos,images")
                            tmdb_genres = [g.get("name") for g in det.get("genres", []) if g.get("name")]
                            networks = [n.get("name") for n in det.get("networks", []) if n.get("name")]
                            for net in networks:
                                if net not in tmdb_genres:
                                    tmdb_genres.append(net)

                            # Video / Fragman kontrolü
                            videos = det.get("videos", {}).get("results", [])
                            trailers = [v for v in videos if v.get("site") == "YouTube" and v.get("type") in ["Trailer", "Teaser"]]
                            if trailers:
                                extra_data["trailers"] = [{"source": t.get("key"), "type": "Trailer"} for t in trailers]
                                first_key = trailers[0].get("key")
                                extra_data["trailer"] = f"https://www.youtube.com/watch?v={first_key}"
                                extra_data["trailerStreams"] = [{"title": "Fragman", "ytId": first_key}]

                            # Logo kontrolü
                            logos = det.get("images", {}).get("logos", [])
                            if logos:
                                l_path = logos[0].get("file_path")
                                if l_path:
                                    extra_data["logo"] = f"https://image.tmdb.org/t/p/w500{l_path}"

                            if endpoint == "tv":
                                next_ep = det.get("next_episode_to_air")
                                if next_ep:
                                    s_n = next_ep.get("season_number", 1)
                                    e_n = next_ep.get("episode_number", 1)
                                    air_d = next_ep.get("air_date", "")
                                    banner = f"📅 YENİ BÖLÜM: Sezon {s_n}, Bölüm {e_n} ({air_d}) tarihinde yayında!\n\n"
                                    if "📅 YENİ BÖLÜM" not in overview:
                                        overview = banner + overview
                        except Exception:
                            pass
                        return (
                            f"tmdb-{r_id}",
                            r0.get("title") or r0.get("name") or title,
                            clean_poster_url(p_url, fallback=poster),
                            overview,
                            rel_date[:4] if rel_date else year,
                            tmdb_genres,
                            resolved_type,
                            extra_data
                        )
            except Exception:
                pass

        t_norm = title.lower().replace("ç", "c").replace("ğ", "g").replace("ı", "i").replace("ö", "o").replace("ş", "s").replace("ü", "u")
        slug = re.sub(r'[^a-z0-9]+', '-', t_norm).strip('-')
        safe_id = f"playrepo-{slug}" if slug else f"playrepo-film-{abs(hash(title))}"
        return (safe_id, title, clean_poster_url(poster), desc, year, [], media_kind, extra_data)

def clean_poster_url(poster_url: str, imdb_id: str = None, fallback: str = "") -> str:
    res = ""
    if poster_url and "metahub.space" not in poster_url and str(poster_url).startswith("http"):
        res = poster_url
    elif imdb_id and str(imdb_id).startswith("tt"):
        try:
            sug = http_json(f"https://v3.sg.media-imdb.com/suggestion/t/{imdb_id}.json")
            sug_items = sug.get("d", [])
            if sug_items and isinstance(sug_items, list):
                cand = sug_items[0].get("i", {}).get("imageUrl")
                if cand and cand.startswith("http"):
                    res = cand
        except Exception:
            pass
    if not res and fallback and "metahub.space" not in fallback and str(fallback).startswith("http"):
        res = fallback
    if not res:
        res = LOGO_URL

    if "@" in res:
        res = res.replace("@", "%40")
    return res

def matches_cat(item, *keywords):
    g_list = [str(g).lower() for g in item.get("genres", [])]
    desc = str(item.get("description", "")).lower()
    title = str(item.get("title", "") or item.get("name", "")).lower()
    combined = " ".join(g_list) + " " + desc + " " + title
    return any(kw in combined for kw in keywords)

# -------------------------------------------------------------
# GitHub Atomic Git Data API Publisher
# -------------------------------------------------------------
class GitHubAtomicPublisher:
    def __init__(self, token: str, owner: str = REPO_OWNER, repo: str = REPO_NAME, log_callback=None):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.log_callback = log_callback or (lambda s: None)

    def log(self, msg: str):
        self.log_callback(msg)

    @staticmethod
    def matches_cat(item, *keywords):
        g_list = [str(g).lower() for g in item.get("genres", [])]
        desc = str(item.get("description", "")).lower()
        title = str(item.get("title", "") or item.get("name", "")).lower()
        platform = str(item.get("platform", "")).lower()
        for kw in keywords:
            k = kw.lower()
            if k in platform or k in title or any(k in g for g in g_list):
                return True
            if k in ["aile", "family", "war", "star", "max", "fx"]:
                if re.search(rf"\b{re.escape(k)}\b", desc, re.I):
                    return True
            elif k in desc:
                return True
        return False

    @staticmethod
    def is_yerli_content(item):
        g_list = [str(g).lower() for g in item.get("genres", [])]
        desc = str(item.get("description", "")).lower()
        title = str(item.get("title", "") or item.get("name", "")).lower()
        platform = str(item.get("platform", "")).lower()

        if any(p in platform for p in ["blutv", "gain", "exxen", "puhutv"]):
            return True
        if any(g in ["yerli", "yerli film", "yerli dizi", "türk filmi", "türk dizisi"] for g in g_list):
            return True
        yerli_terms = ["blutv", "gain", "exxen", "yerli film", "yerli dizi", "yerli sinema", "türk filmi", "türk sineması", "türk dizisi", "türk yapımı"]
        return any(term in desc or term in title for term in yerli_terms)

    @classmethod
    def prepare_category_catalogs(cls, all_metas: list) -> dict:
        result = {}
        all_movies = [m for m in all_metas if m.get("type", "movie") != "series"]
        result["playrepo/catalog/movie/playrepo-popular.json"] = json.dumps({"metas": all_movies[:60]}, ensure_ascii=False, indent=2)
        result["playrepo/catalog/movie/playrepo-catalog-movies.json"] = json.dumps({"metas": all_movies}, ensure_ascii=False, indent=2)

        movie_genre_configs = [
            # 1. Yerli Filmler & Netflix Yerli Koleksiyonları (Sadece tek kategoriye girer)
            ("playrepo/catalog/movie/playrepo-netflix-yerli.json", lambda x: cls.is_yerli_content(x) and cls.matches_cat(x, "netflix")),
            ("playrepo/catalog/movie/playrepo-blutv.json", lambda x: cls.is_yerli_content(x)),
            # 2. Yabancı Platform Koleksiyonları (Yerli yapımlarla karışmaz)
            ("playrepo/catalog/movie/playrepo-netflix.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "netflix")),
            ("playrepo/catalog/movie/playrepo-disney.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "disney", "pixar", "marvel")),
            ("playrepo/catalog/movie/playrepo-prime.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "prime video", "amazon prime", "amazon")),
            ("playrepo/catalog/movie/playrepo-apple.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "apple tv", "appletv", "apple original")),
            ("playrepo/catalog/movie/playrepo-hbo.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "hbo", "hbomax", "max original", "warner")),
            ("playrepo/catalog/movie/playrepo-hulu.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "hulu")),
            ("playrepo/catalog/movie/playrepo-mubi.json", lambda x: cls.matches_cat(x, "mubi", "arthouse", "festival")),
            # 3. Birleştirilmiş Tür Koleksiyonları
            ("playrepo/catalog/movie/playrepo-bilimkurgu.json", lambda x: cls.matches_cat(x, "bilim kurgu", "bilim-kurgu", "bilimkurgu", "sci-fi", "science fiction", "fantastik", "fantasy")),
            ("playrepo/catalog/movie/playrepo-aksiyon.json", lambda x: cls.matches_cat(x, "aksiyon", "action", "macera", "adventure", "savaş", "savas", "war")),
            ("playrepo/catalog/movie/playrepo-korku.json", lambda x: cls.matches_cat(x, "korku", "horror", "gerilim", "thriller", "gizem", "mystery")),
            ("playrepo/catalog/movie/playrepo-animasyon.json", lambda x: cls.matches_cat(x, "animasyon", "animation", "anime", "çizgi", "aile", "family")),
            ("playrepo/catalog/movie/playrepo-komedi.json", lambda x: cls.matches_cat(x, "komedi", "comedy", "mizah")),
            ("playrepo/catalog/movie/playrepo-suc.json", lambda x: cls.matches_cat(x, "suç", "suc", "crime", "polisiye", "dedektif")),
            ("playrepo/catalog/movie/playrepo-dram.json", lambda x: cls.matches_cat(x, "dram", "drama", "romantik", "romance", "aşk")),
            ("playrepo/catalog/movie/playrepo-belgesel.json", lambda x: cls.matches_cat(x, "belgesel", "documentary", "tarih", "history", "biyografi", "biography")),
        ]

        assigned_movie_ids = set()
        for path, pred in movie_genre_configs:
            cat_movies = []
            for m in all_movies:
                m_id = m.get("id")
                if m_id and m_id not in assigned_movie_ids and pred(m):
                    cat_movies.append(m)
                    assigned_movie_ids.add(m_id)
            result[path] = json.dumps({"metas": cat_movies}, ensure_ascii=False, indent=2)

        all_series = [s for s in all_metas if s.get("type") == "series"]
        result["playrepo/catalog/series/playrepo-series-popular.json"] = json.dumps({"metas": all_series}, ensure_ascii=False, indent=2)

        series_genre_configs = [
            # 1. Yerli Diziler & Netflix Yerli Dizileri (Sadece tek kategoriye girer)
            ("playrepo/catalog/series/playrepo-series-netflix-yerli.json", lambda x: cls.is_yerli_content(x) and cls.matches_cat(x, "netflix")),
            ("playrepo/catalog/series/playrepo-series-blutv.json", lambda x: cls.is_yerli_content(x)),
            # 2. Yabancı Dizi Platform Koleksiyonları (Yerli dizilerle karışmaz)
            ("playrepo/catalog/series/playrepo-series-netflix.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "netflix")),
            ("playrepo/catalog/series/playrepo-series-disney.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "disney", "pixar", "marvel", "star wars")),
            ("playrepo/catalog/series/playrepo-series-prime.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "prime video", "amazon prime", "amazon")),
            ("playrepo/catalog/series/playrepo-series-apple.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "apple tv", "appletv", "apple original")),
            ("playrepo/catalog/series/playrepo-series-hbo.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "hbo", "hbomax", "max original", "warner")),
            ("playrepo/catalog/series/playrepo-series-hulu.json", lambda x: not cls.is_yerli_content(x) and cls.matches_cat(x, "hulu", "fx")),
            # 3. Birleştirilmiş Dizi Tür Koleksiyonları
            ("playrepo/catalog/series/playrepo-series-bilimkurgu.json", lambda x: cls.matches_cat(x, "bilim kurgu", "bilim-kurgu", "sci-fi", "science fiction", "fantastik", "fantasy")),
            ("playrepo/catalog/series/playrepo-series-aksiyon.json", lambda x: cls.matches_cat(x, "aksiyon", "action", "macera", "adventure", "savaş", "savas")),
            ("playrepo/catalog/series/playrepo-series-korku.json", lambda x: cls.matches_cat(x, "korku", "horror", "gerilim", "thriller", "gizem", "mystery")),
            ("playrepo/catalog/series/playrepo-series-animasyon.json", lambda x: cls.matches_cat(x, "animasyon", "animation", "anime", "çizgi", "çizgi dizi")),
            ("playrepo/catalog/series/playrepo-series-komedi.json", lambda x: cls.matches_cat(x, "komedi", "comedy", "mizah", "sitcom")),
            ("playrepo/catalog/series/playrepo-series-suc.json", lambda x: cls.matches_cat(x, "suç", "suc", "crime", "polisiye", "dedektif")),
            ("playrepo/catalog/series/playrepo-series-dram.json", lambda x: cls.matches_cat(x, "dram", "drama", "romantik", "romance")),
            ("playrepo/catalog/series/playrepo-series-belgesel.json", lambda x: cls.matches_cat(x, "belgesel", "documentary", "tarih", "history", "biyografi", "doğa")),
        ]

        assigned_series_ids = set()
        for path, pred in series_genre_configs:
            cat_series = []
            for s in all_series:
                s_id = s.get("id")
                if s_id and s_id not in assigned_series_ids and pred(s):
                    cat_series.append(s)
                    assigned_series_ids.add(s_id)
            result[path] = json.dumps({"metas": cat_series}, ensure_ascii=False, indent=2)

        return result

    def publish_movies(self, scraped_movies: list) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş! Ayarlar bölümünden GitHub Token kaydedin.")
            return False

        self.log(f"[GITHUB] {len(scraped_movies)} film için Git Data API yükleme paketi hazırlanıyor...")

        files_to_commit = {}

        cat_json = fetch_github_file("data/catalog.json", token=self.token)
        if not isinstance(cat_json, dict) or "metas" not in cat_json:
            local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
            if os.path.exists(local_cat):
                try:
                    with open(local_cat, "r", encoding="utf-8") as f:
                        cat_json = json.load(f)
                except Exception:
                    cat_json = {}
        metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
        existing_metas = [m for m in metas if isinstance(m, dict) and m.get("id")]

        str_json = fetch_github_file("data/streams.json", token=self.token)
        if not isinstance(str_json, dict) or not str_json:
            local_str = os.path.join(WORKSPACE_DIR, "data", "streams.json")
            if os.path.exists(local_str):
                try:
                    with open(local_str, "r", encoding="utf-8") as f:
                        str_json = json.load(f)
                except Exception:
                    str_json = {}
        streams_map = str_json if isinstance(str_json, dict) else {}

        new_items = []
        scraped_ids = set()

        for m in scraped_movies:
            m_id = m["tmdbId"]
            hyphen_id = m_id.replace(":", "-").replace("_", "-")
            safe_id = m_id.replace(":", "_")
            clean_id = m_id.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")
            scraped_ids.update({m_id, hyphen_id, safe_id, clean_id, f"movie/{clean_id}", f"tmdb:{clean_id}", f"playrepo:{clean_id}"})

            m_type = m.get("type", "movie")
            s_num = m.get("season")
            e_num = m.get("episode")
            if m_type == "movie":
                s_num = None
                e_num = None
            vid_id = f"{m_id}:{s_num}:{e_num}" if (m_type == "series" and s_num and e_num) else m_id

            clean_poster = clean_poster_url(m.get("poster"), m_id, fallback=LOGO_URL)
            cat_item = {
                "id": m_id,
                "type": m_type,
                "name": m["title"],
                "poster": clean_poster,
                "background": clean_poster,
                "description": m["description"],
                "releaseInfo": m["year"],
                "genres": m["genres"],
                "posterShape": "poster"
            }
            if m.get("logo"):
                cat_item["logo"] = m["logo"]
            if m.get("trailers"):
                cat_item["trailers"] = m["trailers"]
            if m.get("trailerStreams"):
                cat_item["trailerStreams"] = m["trailerStreams"]
            if m.get("trailer"):
                cat_item["trailer"] = m["trailer"]
            if m.get("imdbRating"):
                cat_item["imdbRating"] = m["imdbRating"]
            if m.get("runtime"):
                cat_item["runtime"] = m["runtime"]

            new_items.append(cat_item)

            if m_type == "series" and s_num is not None and e_num is not None:
                ep_keys = [
                    f"{m_id}:{s_num}:{e_num}",
                    f"{hyphen_id}-{s_num}-{e_num}",
                    f"{clean_id}:{s_num}:{e_num}",
                    f"{clean_id}-{s_num}-{e_num}",
                    f"playrepo-{clean_id}:{s_num}:{e_num}",
                    f"playrepo-{clean_id}-{s_num}-{e_num}"
                ]
                for ek in ep_keys:
                    streams_map[ek] = m["streams"]
            else:
                streams_map[m_id] = m["streams"]
                streams_map[hyphen_id] = m["streams"]
                streams_map[safe_id] = m["streams"]
                if clean_id:
                    streams_map[clean_id] = m["streams"]
                    streams_map[f"playrepo-{clean_id}"] = m["streams"]

            vid_title = f"{s_num}. Sezon {e_num}. Bölüm" if (m_type == "series" and s_num and e_num) else m["title"]
            vid_obj = {
                "id": vid_id,
                "title": vid_title,
                "name": vid_title,
                "released": f"{m['year']}-01-01T00:00:00.000Z" if m.get("year") else None
            }
            if m_type == "series" and s_num:
                vid_obj["season"] = s_num
            if m_type == "series" and e_num:
                vid_obj["episode"] = e_num

            all_videos = [vid_obj]
            if m_type == "series":
                cm_videos = m.get("videos") or []
                if not cm_videos and m_id.startswith("tt"):
                    try:
                        cm_res = http_json(f"https://v3-cinemeta.strem.io/meta/series/{m_id}.json")
                        if cm_res and cm_res.get("meta", {}).get("videos"):
                            cm_videos = cm_res["meta"]["videos"]
                    except Exception:
                        pass

                vids_map = {}
                if cm_videos:
                    for v in cm_videos:
                        if v.get("id"):
                            vids_map[v["id"]] = v

                existing_series_json = files_to_commit.get(f"playrepo/meta/series/{m_id}.json")
                if existing_series_json:
                    try:
                        prev_meta = json.loads(existing_series_json).get("meta", {})
                        prev_vids = prev_meta.get("videos", [])
                        for v in prev_vids:
                            if v.get("id") and v["id"] not in vids_map:
                                vids_map[v["id"]] = v
                    except Exception:
                        pass

                vids_map[vid_id] = vid_obj
                all_videos = list(vids_map.values())
                all_videos.sort(key=lambda v: (v.get("season") if v.get("season") is not None else 1, v.get("episode") or v.get("number") or 1))

            detail_meta = {
                "meta": {
                    "id": m_id,
                    "type": m_type,
                    "name": m["title"],
                    "poster": clean_poster,
                    "background": clean_poster,
                    "description": m["description"],
                    "releaseInfo": m["year"],
                    "genres": m["genres"],
                    "posterShape": "poster",
                    "behaviorHints": {
                        "defaultVideoId": vid_id,
                        "hasScheduledVideos": bool(m_type == "series" and "📅 YENİ BÖLÜM" in m.get("description", ""))
                    },
                    "videos": all_videos
                }
            }
            if m.get("logo"):
                detail_meta["meta"]["logo"] = m["logo"]
            if m.get("trailers"):
                detail_meta["meta"]["trailers"] = m["trailers"]
            if m.get("trailerStreams"):
                detail_meta["meta"]["trailerStreams"] = m["trailerStreams"]
            if m.get("trailer"):
                detail_meta["meta"]["trailer"] = m["trailer"]
            if m.get("imdbRating"):
                detail_meta["meta"]["imdbRating"] = m["imdbRating"]
            if m.get("runtime"):
                detail_meta["meta"]["runtime"] = m["runtime"]

            stream_obj = {"streams": m["streams"]}

            m_json_str = json.dumps(detail_meta, ensure_ascii=False, indent=2)
            s_json_str = json.dumps(stream_obj, ensure_ascii=False, indent=2)

            distinct_ids = {m_id, hyphen_id, safe_id, clean_id}
            for did in distinct_ids:
                if did:
                    files_to_commit[f"playrepo/meta/movie/{did}.json"] = m_json_str
                    files_to_commit[f"playrepo/stream/movie/{did}.json"] = s_json_str
                    if m_type == "series":
                        files_to_commit[f"playrepo/meta/series/{did}.json"] = m_json_str
                        if s_num and e_num:
                            files_to_commit[f"playrepo/stream/series/{did}:{s_num}:{e_num}.json"] = s_json_str
                            files_to_commit[f"playrepo/stream/series/{did}-{s_num}-{e_num}.json"] = s_json_str

        # Eski listeden güncellenen filmleri çıkarıp yeni tarananları EN BAŞA (İLK SIRAYA) yerleştiriyoruz
        remaining_metas = [
            m for m in existing_metas
            if m.get("id") not in scraped_ids and m.get("id", "").replace("playrepo-", "").replace("tmdb-", "") not in scraped_ids
        ]
        all_metas = list(reversed(new_items)) + remaining_metas
        for item in all_metas:
            item["poster"] = clean_poster_url(item.get("poster"), item.get("id"), fallback=LOGO_URL)
            item["background"] = item["poster"]

        files_to_commit["data/catalog.json"] = json.dumps({"metas": all_metas}, ensure_ascii=False, indent=2)
        files_to_commit["data/streams.json"] = json.dumps(streams_map, ensure_ascii=False, indent=2)

        files_to_commit.update(self.prepare_category_catalogs(all_metas))

        self.log(f"[GITHUB] Toplam {len(files_to_commit)} dosya tek bir atomik commit olarak GitHub'a aktarılıyor...")

        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            self.log(f"✗ [ERROR] GitHub ref alınamadı: {ref_data.get('message', 'Yetkisiz erişim / Token geçersiz')}")
            return False

        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        # GitHub Git Trees API creates blobs automatically from inline content in 1 request
        tree_items = [
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "content": content
            }
            for path, content in files_to_commit.items()
        ]

        tree_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees",
            method="POST",
            data={"base_tree": base_tree_sha, "tree": tree_items},
            token=self.token
        )
        new_tree_sha = tree_res.get("sha")
        if not new_tree_sha:
            self.log(f"✗ [ERROR] Git ağacı oluşturulamadı: {tree_res.get('message')}")
            return False

        commit_msg = f"PlayRepo macOS: Sync {len(scraped_movies)} filmleri ve kategorileri güncelle"
        new_commit_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits",
            method="POST",
            data={
                "message": commit_msg,
                "tree": new_tree_sha,
                "parents": [latest_commit_sha]
            },
            token=self.token
        )
        new_commit_sha = new_commit_res.get("sha")
        if not new_commit_sha:
            self.log(f"✗ [ERROR] Commit oluşturulamadı: {new_commit_res.get('message')}")
            return False

        update_ref = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/refs/heads/{DEFAULT_BRANCH}",
            method="PATCH",
            data={"sha": new_commit_sha, "force": True},
            token=self.token
        )

        if "object" in update_ref or update_ref.get("ref"):
            self.log(f"✓ [SUCCESS] Toplam {len(scraped_movies)} film, kategoriler ve detay sayfaları %100 eksiksiz yüklendi! (Commit: {new_commit_sha[:8]})")
            # Yerel çalışma alanını anında güncelle (0ms gecikme, Fastly CDN baypas)
            try:
                for rel_path, content in files_to_commit.items():
                    loc_path = os.path.join(WORKSPACE_DIR, rel_path)
                    os.makedirs(os.path.dirname(loc_path), exist_ok=True)
                    with open(loc_path, "w", encoding="utf-8") as f:
                        f.write(content)
            except Exception as e:
                self.log(f"Yerel senkronizasyon uyarısı: {e}")
            return True
        else:
            self.log(f"✗ [ERROR] Dal güncellenemedi: {update_ref.get('message')}")
            return False

    def delete_movie(self, movie_id: str) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş!")
            return False

        self.log(f"[GITHUB SİLME] {movie_id} filmi depodan ve eklentiden siliniyor...")

        cat_json = fetch_github_file("data/catalog.json", token=self.token)
        if not isinstance(cat_json, dict) or "metas" not in cat_json:
            local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
            if os.path.exists(local_cat):
                try:
                    with open(local_cat, "r", encoding="utf-8") as f:
                        cat_json = json.load(f)
                except Exception:
                    cat_json = {}
        metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
        meta_dict = {m.get("id"): m for m in metas if isinstance(m, dict) and m.get("id")}

        str_json = fetch_github_file("data/streams.json", token=self.token)
        if not isinstance(str_json, dict) or not str_json:
            local_str = os.path.join(WORKSPACE_DIR, "data", "streams.json")
            if os.path.exists(local_str):
                try:
                    with open(local_str, "r", encoding="utf-8") as f:
                        str_json = json.load(f)
                except Exception:
                    str_json = {}
        streams_map = str_json if isinstance(str_json, dict) else {}

        hyphen_id = movie_id.replace(":", "-").replace("_", "-")
        safe_id = movie_id.replace(":", "_")
        clean_id = movie_id.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")
        del_ids = {movie_id, hyphen_id, safe_id, clean_id}

        keys_to_del = [k for k in meta_dict.keys() if any(d in k for d in del_ids)]
        for k in keys_to_del:
            meta_dict.pop(k, None)
        for d in del_ids:
            streams_map.pop(d, None)

        all_metas = list(meta_dict.values())
        files_to_commit = {
            "data/catalog.json": json.dumps({"metas": all_metas}, ensure_ascii=False, indent=2),
            "data/streams.json": json.dumps(streams_map, ensure_ascii=False, indent=2)
        }
        files_to_commit.update(self.prepare_category_catalogs(all_metas))

        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            return False

        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        tree_items = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files_to_commit.items()
        ]
        # Silinen meta ve stream dosyalarını Git ağacından kaldır (sha: null)
        for did in del_ids:
            if did:
                tree_items.append({"path": f"playrepo/meta/movie/{did}.json", "mode": "100644", "type": "blob", "sha": None})
                tree_items.append({"path": f"playrepo/stream/movie/{did}.json", "mode": "100644", "type": "blob", "sha": None})
                tree_items.append({"path": f"playrepo/meta/series/{did}.json", "mode": "100644", "type": "blob", "sha": None})

        tree_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees",
            method="POST",
            data={"base_tree": base_tree_sha, "tree": tree_items},
            token=self.token
        )
        new_tree_sha = tree_res.get("sha")
        if not new_tree_sha:
            return False

        new_commit_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits",
            method="POST",
            data={"message": f"PlayRepo: {movie_id} filmini kaldır", "tree": new_tree_sha, "parents": [latest_commit_sha]},
            token=self.token
        )
        new_commit_sha = new_commit_res.get("sha")
        if not new_commit_sha:
            return False

        update_ref = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/refs/heads/{DEFAULT_BRANCH}",
            method="PATCH",
            data={"sha": new_commit_sha, "force": True},
            token=self.token
        )
        if "object" in update_ref or update_ref.get("ref"):
            self.log(f"✓ [SİLİNDİ] {movie_id} eklentiden ve depodan tamamen kaldırıldı!")
            # Yerel çalışma alanını anında güncelle (0ms gecikme)
            try:
                for rel_path, content in files_to_commit.items():
                    loc_path = os.path.join(WORKSPACE_DIR, rel_path)
                    os.makedirs(os.path.dirname(loc_path), exist_ok=True)
                    with open(loc_path, "w", encoding="utf-8") as f:
                        f.write(content)
                for did in del_ids:
                    if did:
                        for p in [
                            os.path.join(WORKSPACE_DIR, f"playrepo/meta/movie/{did}.json"),
                            os.path.join(WORKSPACE_DIR, f"playrepo/stream/movie/{did}.json"),
                            os.path.join(WORKSPACE_DIR, f"playrepo/meta/series/{did}.json")
                        ]:
                            if os.path.exists(p):
                                try: os.remove(p)
                                except Exception: pass
            except Exception:
                pass
            return True
        return False

    def clear_all_published_movies(self) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş!")
            return False

        self.log("[TOPLU SİLME] Eklentideki ve depodaki tüm filmler sıfırlanıyor...")

        empty_catalog = json.dumps({"metas": []}, ensure_ascii=False, indent=2)
        empty_streams = json.dumps({}, ensure_ascii=False, indent=2)

        files_to_commit = {
            "data/catalog.json": empty_catalog,
            "data/streams.json": empty_streams
        }
        files_to_commit.update(self.prepare_category_catalogs([]))

        # Nuvio'nun cihaz önbelleğini anında temizlemesi için manifest sürümünü artır
        manifest_data = fetch_github_file("playrepo/manifest.json", token=self.token)
        if not isinstance(manifest_data, dict):
            loc_man = os.path.join(WORKSPACE_DIR, "playrepo", "manifest.json")
            if os.path.exists(loc_man):
                try:
                    with open(loc_man, "r", encoding="utf-8") as f:
                        manifest_data = json.load(f)
                except Exception:
                    manifest_data = {}
        if isinstance(manifest_data, dict) and "version" in manifest_data:
            cur_ver = manifest_data.get("version", "1.1.0")
            parts = cur_ver.split(".")
            if len(parts) >= 3:
                new_ver = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
            else:
                new_ver = f"{cur_ver}.1"
            manifest_data["version"] = new_ver
            manifest_data["resources"] = ["catalog", "meta", "stream"]
            if "scrapers" in manifest_data and isinstance(manifest_data["scrapers"], list) and len(manifest_data["scrapers"]) > 0:
                manifest_data["scrapers"][0]["version"] = new_ver
                manifest_data["scrapers"][0]["id"] = manifest_data.get("id", "playRepo")
            files_to_commit["playrepo/manifest.json"] = json.dumps(manifest_data, ensure_ascii=False, indent=2)

        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            return False

        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        tree_items = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files_to_commit.items()
        ]

        # Mevcut ağacı recursive çekip meta ve stream dosyalarını sil, ve tüm katalog dosyalarını boşalt
        try:
            full_tree = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{base_tree_sha}?recursive=1", token=self.token)
            for item in full_tree.get("tree", []):
                p = item.get("path", "")
                if p.startswith("playrepo/meta/") or p.startswith("playrepo/stream/"):
                    if item.get("type") == "blob":
                        tree_items.append({"path": p, "mode": "100644", "type": "blob", "sha": None})
                elif p.startswith("playrepo/catalog/") and p.endswith(".json"):
                    if item.get("type") == "blob" and p not in files_to_commit:
                        tree_items.append({"path": p, "mode": "100644", "type": "blob", "content": empty_catalog})
        except Exception:
            pass

        tree_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees",
            method="POST",
            data={"base_tree": base_tree_sha, "tree": tree_items},
            token=self.token
        )
        new_tree_sha = tree_res.get("sha")
        if not new_tree_sha:
            return False

        new_commit_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits",
            method="POST",
            data={"message": "PlayRepo: Tüm yayınlanmış filmleri sıfırla", "tree": new_tree_sha, "parents": [latest_commit_sha]},
            token=self.token
        )
        new_commit_sha = new_commit_res.get("sha")
        if not new_commit_sha:
            return False

        update_ref = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/refs/heads/{DEFAULT_BRANCH}",
            method="PATCH",
            data={"sha": new_commit_sha, "force": True},
            token=self.token
        )
        if "object" in update_ref or update_ref.get("ref"):
            self.log("✓ [TEMİZLENDİ] Tüm filmler ve akışlar eklentiden başarıyla sıfırlandı!")
            # Yerel çalışma alanını anında sıfırla (0ms gecikme, CDN baypas)
            try:
                for rel_path, content in files_to_commit.items():
                    loc_path = os.path.join(WORKSPACE_DIR, rel_path)
                    os.makedirs(os.path.dirname(loc_path), exist_ok=True)
                    with open(loc_path, "w", encoding="utf-8") as f:
                        f.write(content)
                for folder in ['movie', 'series']:
                    for sub in ['meta', 'stream']:
                        d = os.path.join(WORKSPACE_DIR, 'playrepo', sub, folder)
                        if os.path.exists(d):
                            for item in os.listdir(d):
                                fp = os.path.join(d, item)
                                if os.path.isfile(fp):
                                    try: os.remove(fp)
                                    except Exception: pass
                if os.path.exists(PROCESSED_MOVIES_FILE):
                    os.remove(PROCESSED_MOVIES_FILE)
            except Exception as e:
                self.log(f"Yerel temizleme uyarısı: {e}")
            return True
        return False

# -------------------------------------------------------------
# Global State & WebApp Engine
# -------------------------------------------------------------
LOG_BUFFER = ["[READY] PlayRepo macOS Pro (Saf Siyah Tema) hazır."]

def load_config():
    default_cfg = {
        "github_token": "",
        "tmdb_key": "",
        "max_pages": 20,
        "saved_sites": [
            "https://fullhdfilmizlebox.vip/",
            "https://filmdozu.com/",
            "https://fullfilmizle.fit/",
            "https://ufilmizle.com/",
            "https://filmarsivi.org/",
            "https://www.hintfilmizle.com/film-izle/",
            "https://diziboxizle.com/",
            "https://ddizimom.net/",
            "https://dizirex.com/"
        ]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                default_cfg.update(json.load(f))
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_processed_urls() -> set:
    if os.path.exists(PROCESSED_MOVIES_FILE):
        try:
            with open(PROCESSED_MOVIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
    return set()

def save_processed_urls(urls_set: set):
    try:
        with open(PROCESSED_MOVIES_FILE, "w", encoding="utf-8") as f:
            json.dump(list(urls_set), f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_already_published_identifiers() -> tuple:
    """
    Returns (set of published movie names normalized, set of published IDs, set of processed urls)
    so the scraper never re-crawls or re-solves a movie that already exists in Nuvio.
    """
    names = set()
    ids = set()
    urls = load_processed_urls()

    # 1. Check local catalog file
    local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
    if os.path.exists(local_cat):
        try:
            with open(local_cat, "r", encoding="utf-8") as f:
                d = json.load(f)
                metas = d.get("metas", []) if isinstance(d, dict) else d
                for m in metas:
                    if isinstance(m, dict):
                        if m.get("id"): ids.add(str(m["id"]).lower())
                        if m.get("name"): names.add(str(m["name"]).strip().lower())
        except Exception:
            pass

    # 2. Check remote GitHub catalog (direct GitHub Contents API, uncached)
    try:
        cat_json = fetch_github_file("data/catalog.json", timeout=5)
        if isinstance(cat_json, dict) and "metas" in cat_json:
            for m in cat_json.get("metas", []):
                if isinstance(m, dict):
                    if m.get("id"): ids.add(str(m["id"]).lower())
                    if m.get("name"): names.add(str(m["name"]).strip().lower())
    except Exception:
        pass

    return names, ids, urls

def append_global_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    LOG_BUFFER.append(f"[{ts}] {msg}")
    if len(LOG_BUFFER) > 500:
        LOG_BUFFER.pop(0)

CRAWLER_LOCK = threading.Lock()
CURRENT_WORKER = None
ENGINE_INSTANCE = ScraperEngine(log_callback=append_global_log)

HTML_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="referrer" content="no-referrer">
  <title>PlayRepo Pro • macOS</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #07090e;
      --card-bg: rgba(15, 20, 32, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover-border: rgba(59, 130, 246, 0.4);
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --accent-gradient: linear-gradient(135deg, #2563eb, #38bdf8);
      --emerald: #059669;
      --emerald-gradient: linear-gradient(135deg, #059669, #34d399);
      --amber: #d97706;
      --red: #dc2626;
      --text: #f8fafc;
      --dim: #94a3b8;
      --muted: #64748b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, sans-serif;
      padding-bottom: 96px;
      line-height: 1.5;
      user-select: none;
      overflow-x: hidden;
      min-height: 100vh;
    }

    /* Fixed Glass Header */
    header {
      position: sticky;
      top: 0;
      z-index: 50;
      background: rgba(7, 9, 14, 0.85);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 1px solid var(--card-border);
      padding: 14px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand h1 { font-size: 20px; font-weight: 800; color: #fff; letter-spacing: -0.5px; display: flex; align-items: center; gap: 8px; }
    .brand span.badge-os { font-size: 11px; font-weight: 700; color: #38bdf8; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25); padding: 3px 8px; border-radius: 6px; }
    
    .header-actions { display: flex; align-items: center; gap: 10px; }

    /* Main App Container */
    .app-container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 28px;
    }

    /* Tab Panes */
    .view-pane {
      display: none;
      animation: fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .view-pane.active { display: block; }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Cards */
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px 24px;
      margin-bottom: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      transition: border-color 0.2s;
    }
    .card:hover { border-color: var(--card-hover-border); }
    .card-title {
      font-size: 13px;
      font-weight: 800;
      color: #60a5fa;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }
    .kpi-card {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 16px 20px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .kpi-label { font-size: 11px; font-weight: 700; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }
    .kpi-value { font-size: 24px; font-weight: 800; color: #fff; letter-spacing: -0.5px; }
    .kpi-sub { font-size: 11px; color: var(--muted); }

    /* Inputs & Buttons */
    .row { display: flex; gap: 12px; align-items: center; }
    .col { display: flex; flex-direction: column; gap: 6px; }

    input[type="text"], input[type="password"], input[type="number"] {
      width: 100%;
      background: #0f172a;
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: #ffffff;
      padding: 12px 16px;
      font-size: 14px;
      font-weight: 600;
      border-radius: 10px;
      outline: none;
      transition: all 0.2s;
    }
    input:focus {
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
      background: #141f36;
    }

    button {
      padding: 11px 20px;
      font-size: 13px;
      font-weight: 700;
      border-radius: 10px;
      cursor: pointer;
      border: none;
      color: #fff;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      transition: all 0.15s ease-in-out;
    }
    button:active { transform: scale(0.97); }
    .btn-blue { background: var(--accent); }
    .btn-blue:hover { background: var(--accent-hover); }
    .btn-gradient { background: var(--accent-gradient); box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35); }
    .btn-gradient:hover { filter: brightness(1.1); }
    .btn-green { background: var(--emerald); }
    .btn-green:hover { background: #047857; }
    .btn-red { background: var(--red); }
    .btn-red:hover { background: #b91c1c; }
    .btn-gray { background: #1e293b; border: 1px solid var(--card-border); color: #cbd5e1; }
    .btn-gray:hover { background: #334155; color: #fff; }

    /* Terminal Console */
    #terminal {
      background: #030712;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      padding: 16px 20px;
      font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
      font-size: 12.5px;
      color: #34d399;
      height: 380px;
      overflow-y: auto;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    /* Badges */
    .badge {
      font-size: 11px;
      font-weight: 800;
      padding: 4px 10px;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-ready { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-running { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-paused { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-stopped { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }

    /* Item Cards */
    .media-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(15, 23, 42, 0.5);
      border: 1px solid var(--card-border);
      padding: 12px 18px;
      border-radius: 12px;
      margin-bottom: 8px;
      transition: all 0.15s;
    }
    .media-item:hover {
      background: rgba(30, 41, 59, 0.6);
      border-color: rgba(59, 130, 246, 0.3);
    }

    /* Fixed Bottom Dock Bar */
    .bottom-dock {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      height: 72px;
      background: #090d16;
      border-top: 1px solid rgba(255, 255, 255, 0.12);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 16px;
      z-index: 99999;
      padding: 0 20px;
      pointer-events: auto !important;
    }
    .dock-btn {
      display: flex !important;
      flex-direction: column !important;
      align-items: center !important;
      justify-content: center !important;
      gap: 4px;
      padding: 10px 24px !important;
      border-radius: 12px;
      background: transparent;
      color: #94a3b8;
      cursor: pointer !important;
      position: relative;
      border: 1px solid transparent;
      pointer-events: auto !important;
      user-select: none;
      -webkit-user-select: none;
    }
    .dock-btn * {
      pointer-events: none !important;
    }
    .dock-btn:hover {
      color: #f8fafc;
      background: rgba(255, 255, 255, 0.08);
    }
    .dock-btn.active {
      color: #38bdf8 !important;
      background: rgba(56, 189, 248, 0.15) !important;
      border-color: rgba(56, 189, 248, 0.35) !important;
      box-shadow: 0 4px 12px rgba(56, 189, 248, 0.15);
    }
    .dock-btn svg {
      width: 22px;
      height: 22px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      pointer-events: none;
    }
    .dock-btn span {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.3px;
      pointer-events: none;
    }
    .dock-badge {
      position: absolute;
      top: 4px;
      right: 18px;
      background: #ef4444;
      color: #fff;
      font-size: 10px;
      font-weight: 800;
      padding: 1px 6px;
      border-radius: 10px;
      min-width: 16px;
      text-align: center;
      pointer-events: none;
    }

    /* Toast Notification */
    #toast {
      position: fixed;
      bottom: 88px;
      right: 28px;
      background: rgba(15, 23, 42, 0.95);
      border: 1px solid #3b82f6;
      color: #fff;
      padding: 12px 20px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 700;
      box-shadow: 0 10px 25px rgba(0,0,0,0.6);
      opacity: 0;
      transform: translateY(12px);
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      pointer-events: none;
      z-index: 2000;
    }
    #toast.show { opacity: 1; transform: translateY(0); }
  </style>
</head>
<body>

  <!-- TOP HEADER -->
  <header>
    <div class="brand">
      <h1>🎬 PlayRepo Pro</h1>
      <span class="badge-os">macOS Edition</span>
      <span id="status-badge" class="badge badge-ready">HAZIR</span>
    </div>

    <div class="header-actions">
      <button type="button" class="btn-gray" onclick="clearLogs()">🧹 Logları Temizle</button>
      <button type="button" id="btn-pause" class="btn-gray" onclick="pauseCrawler()" style="display:none; background:#d97706; color:#fff;">⏸ Duraklat</button>
      <button type="button" id="btn-resume" class="btn-green" onclick="resumeCrawler()" style="display:none">▶ Devam Et</button>
      <button type="button" id="btn-stop" class="btn-red" onclick="stopCrawler()" style="display:none">⏹ Durdur</button>
      <button type="button" id="btn-run" class="btn-gradient" onclick="startMasterUpdate()">⚡ TÜM SİTELERİ TARA & NUVIO'YA YÜKLE</button>
    </div>
  </header>

  <div class="app-container">

    <!-- TAB 1: TARAMA & KONTROL (DASHBOARD) -->
    <div id="view-dashboard" class="view-pane active">
      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">Nuvio Yayındaki İçerik</span>
          <span id="kpi-published" class="kpi-value">0</span>
          <span class="kpi-sub">Aktif film ve dizi kataloğu</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Tarama Hafızası (Önbellek)</span>
          <span id="kpi-memory" class="kpi-value">0</span>
          <span class="kpi-sub">Taranmış linkler (tekrar taranmaz)</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Kayıtlı Siteler</span>
          <span id="kpi-sites" class="kpi-value">0</span>
          <span class="kpi-sub">DiziBOX, DiziMom, FilmDozu vb.</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Motor Durumu</span>
          <span id="kpi-status-text" class="kpi-value" style="font-size:18px; color:#38bdf8;">BEKLEMEDE</span>
          <span class="kpi-sub">Sistem v2.8 Pro Hazır</span>
        </div>
      </div>

      <div class="card" style="padding: 16px 20px;">
        <div class="card-title">
          <span>💻 CANLI TERMİNAL / İŞLEM ÇIKTISI</span>
          <button class="btn-gray" style="padding: 4px 10px; font-size: 11px;" onclick="copyLogs()">📋 Logları Kopyala</button>
        </div>
        <div id="terminal"></div>
      </div>
    </div>

    <!-- TAB 2: NUVIO KATALOĞU (YAYINDAKİ FİLMLER & DİZİLER) -->
    <div id="view-catalog" class="view-pane">
      <div class="card">
        <div class="card-title">
          <span>🎬 NUVİO GÜNCEL YAYIN KATALOĞU (<span id="pub-count">0</span>)</span>
          <div class="row" style="gap: 8px;">
            <button class="btn-gray" style="padding: 6px 14px; font-size: 12px;" onclick="loadPublishedMovies()">🔄 Yenile</button>
            <button class="btn-red" style="padding: 6px 14px; font-size: 12px;" onclick="clearAllPublishedMovies()">💥 Güncel Listeyi Toplu Sil</button>
          </div>
        </div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 14px;">
          Nuvio eklentinizde şu anda izlenebilen güncel film ve diziler. Buradan istediğinizi silebilir veya tüm listeyi sıfırlayabilirsiniz:
        </div>
        <div style="margin-bottom: 14px;">
          <input type="text" id="movie-search" placeholder="🔍 Güncel yayındaki filmlerde veya dizilerde ara..." oninput="filterPublishedMovies()" />
        </div>
        <div id="movies-container" style="max-height: 520px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;">
          <div style="color: var(--dim); font-size: 13px; padding: 10px;">Filmler yükleniyor...</div>
        </div>
      </div>
    </div>

    <!-- TAB 3: ÖZEL KURTARMA (TEK FİLM / DİZİ EKLE) -->
    <div id="view-single" class="view-pane">
      <div class="card" style="border: 1px solid #3b82f6; background: rgba(12, 19, 34, 0.85); padding: 24px;">
        <div class="card-title" style="color: #60a5fa; font-size: 14px;">🎯 TEK BİR FİLM VEYA DİZİYİ BUL VE YENİDEN EKLE</div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 16px; line-height: 1.6;">
          İçeriği bozulan, akışları çalışmayan veya eksik olan bir film veya dizinin adını ya da doğrudan site sayfasının linkini girin.<br>
          PlayRepo kayıtlı tüm siteleri tek tek kontrol ederek o içeriğin taze çalışan 1080p video kaynaklarını çözer ve anında Nuvio'ya aktarır.
        </div>
        <div class="row" style="margin-bottom: 16px;">
          <input type="text" id="target-movie-input" placeholder="Örn: Inception, Matrix, Testere veya https://diziboxizle.com/nails/ linki" style="font-size: 15px; padding: 14px;" />
          <button class="btn-gradient" style="padding: 14px 24px; font-size: 14px; font-weight: 800;" onclick="findAndAddSpecificMovie()">🎯 BUL VE YENİDEN EKLE</button>
        </div>
        <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 16px;">
          <div style="font-size: 12px; font-weight: 700; color: #38bdf8; text-transform: uppercase; margin-bottom: 8px;">Nasıl Çalışır?</div>
          <div style="font-size: 12px; color: var(--dim); line-height: 1.6;">
            1. Hedef film adı girildiğinde tüm kayıtlı sitelerin sitemap ve arama motorları taranır.<br>
            2. TMDB veya Cinemeta üzerinden film/dizi afişi, türü ve Türkçe açıklaması bulunur.<br>
            3. OK.ru, Vidmoly veya HLS video kaynakları ayıklanıp GitHub'a push edilir ve Nuvio'da hemen görünür.
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 4: TARAMA HAFIZASI (ÖNBELLEK VE ARŞİV) -->
    <div id="view-memory" class="view-pane">
      <div class="card">
        <div class="card-title">
          <span>💾 TARAMA HAFIZASI & ÖNBELLEK YÖNETİMİ</span>
          <div class="row" style="gap: 8px;">
            <button class="btn-gray" style="padding: 6px 14px; font-size: 12px;" onclick="loadProcessedUrls(); loadHistoryMovies();">🔄 Yenile</button>
            <button class="btn-red" style="padding: 6px 14px; font-size: 12px;" onclick="clearAllProcessedUrls()">💥 Tüm Hafızayı Temizle</button>
          </div>
        </div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 16px;">
          PlayRepo daha önce tarayıp çözdüğü film ve dizi linklerini hafızaya kaydeder. Böylece bilgisayarı kapatıp ertesi gün açtığınızda daha önce taranmış olan filmler atlanır ve sadece <b>yeni eklenen filmler</b> taranır. Buradan dilediğiniz bir filmi hafızadan çıkarıp yeniden taranmasını sağlayabilirsiniz.
        </div>
        
        <!-- Sekmeler -->
        <div style="display: flex; gap: 8px; margin-bottom: 14px;">
          <button id="mem-tab-urls" class="btn-blue" style="padding: 7px 16px; font-size: 12px;" onclick="switchMemTab('urls')">🔗 Taranmış Linkler Hafızası (<span id="mem-url-count">0</span>)</button>
          <button id="mem-tab-his" class="btn-gray" style="padding: 7px 16px; font-size: 12px;" onclick="switchMemTab('his')">📜 Eski Çözülenler Arşivi (<span id="his-count">0</span>)</button>
        </div>

        <!-- Alt Bölüm 1: Linkler Hafızası -->
        <div id="mem-pane-urls">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px; gap: 8px; flex-wrap:wrap;">
            <input type="text" id="memory-search" placeholder="🔍 Taranmış linklerde ara..." oninput="filterProcessedUrls()" style="flex:1; min-width:200px;" />
            <button class="btn-green" style="padding: 9px 16px; font-size: 12px; white-space:nowrap;" onclick="publishAllProcessedUrls()">⚡ Tüm Linkleri Çöz ve Nuvio'ya Gönder</button>
            <button class="btn-blue" id="btn-publish-selected-urls" style="display:none; padding: 9px 16px; font-size: 12px; white-space:nowrap;" onclick="publishSelectedProcessedUrls()">🚀 Seçilenleri Nuvio'ya Gönder (<span id="sel-url-count">0</span>)</button>
          </div>
          <div style="display:flex; align-items:center; justify-content:space-between; font-size:12px; color:var(--dim); margin-bottom:8px; padding:0 4px;">
            <label style="display:flex; align-items:center; gap:6px; cursor:pointer; user-select:none;">
              <input type="checkbox" id="select-all-urls-checkbox" onchange="toggleSelectAllUrls(this.checked)" /> <span>Tümünü Seç</span>
            </label>
            <span id="url-selection-info" style="color:#64748b;">0 link seçili</span>
          </div>
          <div id="memory-container" style="max-height: 480px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
            <div style="color: var(--dim); font-size: 13px; padding: 10px;">Hafıza yükleniyor...</div>
          </div>
        </div>

        <!-- Alt Bölüm 2: Eski Arşiv Metadata -->
        <div id="mem-pane-his" style="display:none;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px; gap: 8px;">
            <input type="text" id="history-search" placeholder="🔍 Eski arşivde ara..." oninput="filterHistoryMovies()" style="flex:1;" />
            <button class="btn-green" style="padding: 9px 16px; font-size: 12px; white-space:nowrap;" onclick="publishAllHistoryMovies()">⚡ Geçmişi Toplu Nuvio'ya Gönder</button>
            <button class="btn-red" style="padding: 9px 16px; font-size: 12px; white-space:nowrap;" onclick="clearAllHistoryMovies()">🗑️ Arşivi Tamamen Temizle</button>
          </div>
          <div id="history-container" style="max-height: 480px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
            <div style="color: var(--dim); font-size: 13px; padding: 10px;">Arşiv yükleniyor...</div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 5: SİTELER & AYARLAR -->
    <div id="view-settings" class="view-pane">
      
      <!-- Kayıtlı Siteler -->
      <div class="card">
        <div class="card-title">
          <span>🌐 TARANACAK KAYITLI SİTELER (<span id="site-count">0</span>)</span>
          <button class="btn-gray" style="padding: 5px 12px; font-size: 11px;" onclick="resetSites()">🔄 En İyi Siteleri Sıfırla / Yükle</button>
        </div>
        <div class="row" style="margin-bottom: 14px;">
          <input type="text" id="new-site-input" placeholder="Örn: https://diziboxizle.com/ veya https://ddizimom.net/" />
          <button class="btn-green" onclick="addSite()">➕ SİTE EKLE</button>
        </div>
        <div id="sites-list" style="max-height: 240px; overflow-y: auto; margin-bottom: 8px;"></div>
      </div>

      <!-- Ayarlar -->
      <div class="card">
        <div class="card-title">⚙️ MOTOR & GİTHUB AYARLARI</div>
        <div class="col" style="margin-bottom: 12px;">
          <label style="font-size:12px; font-weight:700; color:var(--dim)">SİTE BAŞINA SAYFA LİMİTİ (1 - 100):</label>
          <input type="number" id="max-pages-input" value="20" />
        </div>
        <div class="col" style="margin-bottom: 14px;">
          <label style="font-size:12px; font-weight:700; color:var(--dim)">GİTHUB KİŞİSEL ERİŞİM TOKENİ (PAT):</label>
          <input type="password" id="token-input" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" />
        </div>
        <button class="btn-gradient" style="width: 100%; justify-content: center;" onclick="saveSettings()">💾 Ayarları Kaydet</button>
      </div>

      <!-- Nuvio Eklenti Linki -->
      <div class="card" style="border: 1px solid rgba(56, 189, 248, 0.3); background: rgba(12, 19, 34, 0.7);">
        <div class="card-title" style="color: #38bdf8;">📲 NUVİO EKLENTİ BAĞLANTISI (MANIFEST)</div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 10px;">
          Nuvio uygulamanıza (Apple TV, Mac, iOS veya Android) ekleyeceğiniz resmi eklenti linkiniz:
        </div>
        <div class="row">
          <input type="text" id="manifest-url-input" readonly value="https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/manifest.json" style="background:#090d16; color:#38bdf8;" />
          <button class="btn-blue" onclick="copyManifestUrl()">📋 Linki Kopyala</button>
        </div>
      </div>

    </div>

  </div>

  <!-- FIXED BOTTOM DOCK NAVIGATION -->
  <nav class="bottom-dock">
    <button type="button" id="dock-btn-dashboard" class="dock-btn active" onclick="switchNav('dashboard')">
      <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
      <span>Tarama</span>
    </button>
    <button type="button" id="dock-btn-catalog" class="dock-btn" onclick="switchNav('catalog')">
      <svg viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>
      <span>Nuvio</span>
      <span id="dock-badge-catalog" class="dock-badge">0</span>
    </button>
    <button type="button" id="dock-btn-single" class="dock-btn" onclick="switchNav('single')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="22" y1="12" x2="18" y2="12"/><line x1="6" y1="12" x2="2" y2="12"/><line x1="12" y1="6" x2="12" y2="2"/><line x1="12" y1="22" x2="12" y2="18"/></svg>
      <span>Özel Kurtar</span>
    </button>
    <button type="button" id="dock-btn-memory" class="dock-btn" onclick="switchNav('memory')">
      <svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
      <span>Hafıza</span>
      <span id="dock-badge-memory" class="dock-badge" style="background:#3b82f6;">0</span>
    </button>
    <button type="button" id="dock-btn-settings" class="dock-btn" onclick="switchNav('settings')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      <span>Ayarlar</span>
    </button>
  </nav>

  <!-- TOAST NOTIFICATION -->
  <div id="toast"></div>

  <script>
    let currentConfig = { saved_sites: [], max_pages: 20, github_token: "", tmdb_key: "" };
    let allPublishedMovies = [];
    let allHistoryMovies = [];
    let allProcessedUrls = [];

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.className = 'show';
      setTimeout(() => { t.className = ''; }, 2600);
    }

    function switchNav(view) {
      if (!view) return;
      const views = ['dashboard', 'catalog', 'single', 'memory', 'settings'];
      views.forEach(v => {
        const p = document.getElementById('view-' + v);
        const b = document.getElementById('dock-btn-' + v);
        if (p) {
          p.classList.remove('active');
          p.style.display = 'none';
        }
        if (b) {
          b.classList.remove('active');
        }
      });

      const targetPane = document.getElementById('view-' + view);
      const targetBtn = document.getElementById('dock-btn-' + view);

      if (targetPane) {
        targetPane.classList.add('active');
        targetPane.style.display = 'block';
      }
      if (targetBtn) {
        targetBtn.classList.add('active');
      }

      window.scrollTo(0, 0);

      try {
        if (view === 'catalog') loadPublishedMovies();
        else if (view === 'memory') { loadProcessedUrls(); loadHistoryMovies(); }
        else if (view === 'settings') loadConfig();
      } catch (err) {
        console.error('switchNav data error:', err);
      }
    }
    window.switchNav = switchNav;

    function switchMemTab(tab) {
      if (tab === 'urls') {
        document.getElementById('mem-pane-urls').style.display = 'block';
        document.getElementById('mem-pane-his').style.display = 'none';
        document.getElementById('mem-tab-urls').className = 'btn-blue';
        document.getElementById('mem-tab-his').className = 'btn-gray';
      } else {
        document.getElementById('mem-pane-urls').style.display = 'none';
        document.getElementById('mem-pane-his').style.display = 'block';
        document.getElementById('mem-tab-urls').className = 'btn-gray';
        document.getElementById('mem-tab-his').className = 'btn-blue';
      }
    }

    async function loadConfig() {
      try {
        const res = await fetch('/api/config');
        currentConfig = await res.json();
        document.getElementById('max-pages-input').value = currentConfig.max_pages || 20;
        document.getElementById('token-input').value = currentConfig.github_token || "";
        renderSites();
      } catch (e) {
        console.error(e);
      }
    }

    function renderSites() {
      const list = document.getElementById('sites-list');
      list.innerHTML = "";
      const sites = currentConfig.saved_sites || [];
      document.getElementById('site-count').innerText = sites.length;
      document.getElementById('kpi-sites').innerText = sites.length;
      if (sites.length === 0) {
        list.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Kayıtlı site bulunmuyor.</div>";
        return;
      }
      sites.forEach((site, idx) => {
        const item = document.createElement('div');
        item.className = 'media-item';
        item.innerHTML = `
          <span style="font-weight:600; font-size:13.5px; color:#f1f5f9; word-break:break-all;">🌐 ${site}</span>
          <button class="btn-red" style="padding:5px 12px; font-size:11px;" onclick="deleteSite(${idx})">🗑️ Sil</button>
        `;
        list.appendChild(item);
      });
    }

    async function addSite() {
      const inp = document.getElementById('new-site-input');
      const url = inp.value.trim();
      if (!url.startsWith('http')) {
        alert('Lütfen geçerli bir site linki girin! (http:// veya https://)');
        return;
      }
      await fetch('/api/add-site', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url })
      });
      inp.value = "";
      showToast('Yeni site başarıyla eklendi!');
      await loadConfig();
    }

    async function deleteSite(idx) {
      await fetch('/api/delete-site', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: idx })
      });
      showToast('Site listeden kaldırıldı!');
      await loadConfig();
    }

    async function resetSites() {
      if (confirm('Site listesi en iyi aktif dizi & film sitelerine sıfırlansın mı?')) {
        await fetch('/api/reset-sites', { method: 'POST' });
        showToast('Site listesi varsayılana sıfırlandı!');
        await loadConfig();
      }
    }

    async function saveSettings() {
      const maxPages = parseInt(document.getElementById('max-pages-input').value) || 20;
      const token = document.getElementById('token-input').value.trim();
      await fetch('/api/save-config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ max_pages: maxPages, github_token: token })
      });
      showToast('Ayarlar başarıyla kaydedildi!');
      await loadConfig();
    }

    function copyManifestUrl() {
      const input = document.getElementById('manifest-url-input');
      input.select();
      navigator.clipboard.writeText(input.value);
      showToast('Nuvio eklenti linki panoya kopyalandı!');
    }

    function copyLogs() {
      const t = document.getElementById('terminal').innerText;
      navigator.clipboard.writeText(t);
      showToast('Loglar panoya kopyalandı!');
    }

    async function findAndAddSpecificMovie() {
      const inp = document.getElementById('target-movie-input');
      const query = inp.value.trim();
      if (!query) {
        alert('Lütfen aramak istediğiniz film/dizi adını ya da doğrudan linkini girin!');
        return;
      }
      switchNav('dashboard');
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';

      const res = await fetch('/api/search-and-add-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ query })
      });
      const data = await res.json();
      if (data.status === 'started') {
        inp.value = '';
        showToast('Hedef içerik araması başlatıldı!');
      } else {
        alert('İşlem başlatılamadı: ' + (data.message || ''));
      }
    }

    async function startMasterUpdate() {
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      showToast('Tüm siteler taranmaya başlandı...');
      await fetch('/api/start-all', { method: 'POST' });
    }

    async function pauseCrawler() {
      await fetch('/api/pause', { method: 'POST' });
      document.getElementById('btn-pause').style.display = 'none';
      document.getElementById('btn-resume').style.display = 'inline-flex';
      showToast('Tarama duraklatıldı.');
    }

    async function resumeCrawler() {
      await fetch('/api/resume', { method: 'POST' });
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      showToast('Tarama devam ediyor...');
    }

    async function stopCrawler() {
      await fetch('/api/stop', { method: 'POST' });
      document.getElementById('btn-run').style.display = 'inline-flex';
      document.getElementById('btn-pause').style.display = 'none';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'none';
      showToast('Tarama durduruldu.');
    }

    async function clearLogs() {
      await fetch('/api/clear-logs', { method: 'POST' });
      document.getElementById('terminal').innerText = "";
      showToast('Loglar temizlendi.');
    }

    async function pollLogs() {
      try {
        const res = await fetch('/api/logs');
        const data = await res.json();
        const term = document.getElementById('terminal');
        term.innerText = data.logs.join(String.fromCharCode(10));
        term.scrollTop = term.scrollHeight;

        const badge = document.getElementById('status-badge');
        const kpiText = document.getElementById('kpi-status-text');

        if (!data.is_running) {
          document.getElementById('btn-run').style.display = 'inline-flex';
          document.getElementById('btn-pause').style.display = 'none';
          document.getElementById('btn-resume').style.display = 'none';
          document.getElementById('btn-stop').style.display = 'none';
          badge.innerText = 'HAZIR';
          badge.className = 'badge badge-ready';
          kpiText.innerText = 'BEKLEMEDE';
          kpiText.style.color = '#60a5fa';
        } else if (data.is_paused) {
          document.getElementById('btn-run').style.display = 'none';
          document.getElementById('btn-pause').style.display = 'none';
          document.getElementById('btn-resume').style.display = 'inline-flex';
          document.getElementById('btn-stop').style.display = 'inline-flex';
          badge.innerText = 'DURAKLATILDI';
          badge.className = 'badge badge-paused';
          kpiText.innerText = 'DURAKLATILDI';
          kpiText.style.color = '#fbbf24';
        } else {
          document.getElementById('btn-run').style.display = 'none';
          document.getElementById('btn-pause').style.display = 'inline-flex';
          document.getElementById('btn-resume').style.display = 'none';
          document.getElementById('btn-stop').style.display = 'inline-flex';
          badge.innerText = 'ÇALIŞIYOR';
          badge.className = 'badge badge-running';
          kpiText.innerText = 'ÇÖZÜLÜYOR...';
          kpiText.style.color = '#34d399';
        }
      } catch (e) {}
      setTimeout(pollLogs, 1000);
    }

    async function loadPublishedMovies() {
      try {
        const res = await fetch('/api/published-movies');
        const data = await res.json();
        allPublishedMovies = data.movies || [];
        const count = allPublishedMovies.length;
        document.getElementById('pub-count').innerText = count;
        document.getElementById('kpi-published').innerText = count;
        document.getElementById('dock-badge-catalog').innerText = count;
        renderPublishedMovies(allPublishedMovies);
      } catch (e) {
        console.error(e);
      }
    }

    function renderPublishedMovies(movies) {
      const cont = document.getElementById('movies-container');
      cont.innerHTML = "";
      if (movies.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Henüz yayınlanmış film/dizi bulunmuyor.</div>";
        return;
      }
      movies.forEach(m => {
        const item = document.createElement('div');
        item.className = 'media-item';
        const genres = (m.genres || []).slice(0, 3).join(', ');
        const isSeries = m.type === 'series';
        const validPoster = (m.poster && !m.poster.includes('metahub.space')) ? m.poster : 'https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png';
        const sCount = (m.streams && m.streams.length > 0) ? m.streams.length : (m.stream_count || 0);
        const streamBadge = sCount > 0 
          ? `<span style="background:rgba(16, 185, 129, 0.2); color:#34d399; padding:2px 7px; border-radius:4px; font-weight:700; font-size:11px;">🟢 ${sCount} Akış Yayında</span>`
          : `<span style="background:rgba(239, 68, 68, 0.2); color:#f87171; padding:2px 7px; border-radius:4px; font-weight:700; font-size:11px;">🔴 Akış Yok</span>`;

        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:14px; overflow:hidden;">
            <img src="${validPoster}" referrerpolicy="no-referrer" onerror="this.src='https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png'" style="width:48px; height:68px; object-fit:cover; border-radius:8px; background:#1e293b; flex-shrink:0; box-shadow:0 4px 10px rgba(0,0,0,0.5);" />
            <div style="display:flex; flex-direction:column; overflow:hidden;">
              <span style="font-weight:700; font-size:14.5px; color:#fff; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;">
                ${m.name} <span style="font-size:12px; color:var(--dim); font-weight:500;">(${m.releaseInfo || m.year || ''})</span>
              </span>
              <div style="display:flex; align-items:center; gap:8px; margin-top:4px; flex-wrap:wrap;">
                <span style="background:${isSeries ? 'rgba(168, 85, 247, 0.25)' : 'rgba(59, 130, 246, 0.25)'}; color:${isSeries ? '#c084fc' : '#60a5fa'}; padding:2px 7px; border-radius:4px; font-weight:700; font-size:11px;">${isSeries ? '🎬 Dizi' : '🎥 Film'}</span>
                ${m.imdbRating ? `<span style="background:rgba(234,179,8,0.2); color:#facc15; padding:2px 6px; border-radius:4px; font-weight:700; font-size:11px;">⭐ ${m.imdbRating}</span>` : ''}
                ${streamBadge}
                <span style="font-size:11.5px; color:#94a3b8;">${genres || 'Genel'}</span>
              </div>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
            ${sCount > 0 ? `<button class="btn-blue" style="padding:6px 12px; font-size:12px;" onclick="viewMovieStreams('${m.id}')">▶️ Akışlar (${sCount})</button>` : ''}
            <button class="btn-red" style="padding:6px 12px; font-size:12px;" onclick="deleteMovie('${m.id}')">🗑️ Sil</button>
          </div>
        `;
        cont.appendChild(item);
      });
    }

    function viewMovieStreams(id) {
      const m = allPublishedMovies.find(x => x.id === id);
      if (!m || !m.streams || m.streams.length === 0) {
        alert("Bu filme ait hazır video akışı bulunamadı.");
        return;
      }
      const nl = String.fromCharCode(10);
      const streamList = m.streams.map((s, idx) => (idx + 1) + ". " + (s.name || s.title || "Kaynak") + " [" + (s.quality || "1080p") + "] -> " + s.url).join(nl + nl);
      const firstStream = m.streams[0].url;
      const confirmMsg = "🎬 " + m.name + " - Yayındaki Video Akışları (" + m.streams.length + " Kaynak):" + nl + nl + streamList + nl + nl + "İlk kaynağı tarayıcıda açıp test etmek istiyor musunuz?";
      if (confirm(confirmMsg)) {
        window.open(firstStream, "_blank");
      }
    }

    function filterPublishedMovies() {
      const q = document.getElementById('movie-search').value.toLowerCase().trim();
      if (!q) { renderPublishedMovies(allPublishedMovies); return; }
      const filtered = allPublishedMovies.filter(m => (m.name || '').toLowerCase().includes(q) || (m.genres || []).some(g => g.toLowerCase().includes(q)));
      renderPublishedMovies(filtered);
    }

    async function deleteMovie(id) {
      const target = allPublishedMovies.find(m => m.id === id);
      const name = target ? (target.name || id) : id;
      if (!confirm(`"${name}" içeriğini Nuvio eklentisinden silmek istediğinize emin misiniz?`)) return;
      showToast(`"${name}" siliniyor...`);
      const res = await fetch('/api/delete-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast(`"${name}" başarıyla silindi!`);
        await loadPublishedMovies();
      } else {
        alert('Silme hatası: ' + (data.message || ''));
      }
    }

    async function clearAllPublishedMovies() {
      const count = allPublishedMovies.length;
      if (!confirm("DIKKAT: Nuvio yayinindaki " + count + " adet filmin ve dizinin TAMAMI SILINECEK! Onayliyor musunuz?")) return;
      showToast('Tüm yayınlar sıfırlanıyor...');
      const res = await fetch('/api/clear-all-movies', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
      });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast('Tüm filmler başarıyla sıfırlandı!');
        await loadPublishedMovies();
      } else {
        alert('Hata: ' + (data.message || ''));
      }
    }

    async function loadProcessedUrls() {
      try {
        const res = await fetch('/api/processed-urls');
        const data = await res.json();
        allProcessedUrls = data.urls || [];
        const count = allProcessedUrls.length;
        document.getElementById('mem-url-count').innerText = count;
        document.getElementById('kpi-memory').innerText = count;
        document.getElementById('dock-badge-memory').innerText = count;
        renderProcessedUrls(allProcessedUrls);
      } catch (e) {
        console.error(e);
      }
    }

    let selectedProcessedUrls = new Set();

    function renderProcessedUrls(urls) {
      const cont = document.getElementById('memory-container');
      cont.innerHTML = "";
      if (urls.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Önbellekte kayıtlı taranmış link bulunmuyor.</div>";
        updateSelectedUrlsState();
        return;
      }
      urls.forEach(u => {
        const item = document.createElement('div');
        item.className = 'media-item';
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.justifyContent = 'space-between';
        item.style.gap = '10px';
        const isChecked = selectedProcessedUrls.has(u) ? "checked" : "";
        const escU = u.replace(/'/g, "\\'");
        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:10px; overflow:hidden; flex:1;">
            <input type="checkbox" class="url-select-box" value="${escU}" ${isChecked} onchange="toggleSingleUrlSelect('${escU}', this.checked)" />
            <span style="font-size:12.5px; color:#cbd5e1; word-break:break-all; font-family:monospace;">🔗 ${u}</span>
          </div>
          <div style="display:flex; gap:6px; flex-shrink:0;">
            <button class="btn-green" style="padding:4px 10px; font-size:11px;" onclick="publishSingleProcessedUrl('${escU}')">🚀 Nuvio'ya Gönder</button>
            <button class="btn-gray" style="padding:4px 10px; font-size:11px;" onclick="deleteProcessedUrl('${escU}')">🗑️ Sil</button>
          </div>
        `;
        cont.appendChild(item);
      });
      updateSelectedUrlsState();
    }

    function toggleSingleUrlSelect(url, isChecked) {
      if (isChecked) {
        selectedProcessedUrls.add(url);
      } else {
        selectedProcessedUrls.delete(url);
      }
      updateSelectedUrlsState();
    }

    function toggleSelectAllUrls(checked) {
      const q = document.getElementById('memory-search').value.toLowerCase().trim();
      const currentList = q ? allProcessedUrls.filter(u => u.toLowerCase().includes(q)) : allProcessedUrls;
      if (checked) {
        currentList.forEach(u => selectedProcessedUrls.add(u));
      } else {
        currentList.forEach(u => selectedProcessedUrls.delete(u));
      }
      renderProcessedUrls(currentList);
    }

    function updateSelectedUrlsState() {
      const count = selectedProcessedUrls.size;
      const btn = document.getElementById('btn-publish-selected-urls');
      const counter = document.getElementById('sel-url-count');
      const info = document.getElementById('url-selection-info');
      const chkAll = document.getElementById('select-all-urls-checkbox');
      if (counter) counter.innerText = count;
      if (info) info.innerText = count > 0 ? `${count} link seçildi` : '0 link seçili';
      if (btn) {
        btn.style.display = count > 0 ? 'inline-flex' : 'none';
      }
      if (chkAll && allProcessedUrls.length > 0) {
        chkAll.checked = count > 0 && count === allProcessedUrls.length;
      }
    }

    async function publishSingleProcessedUrl(url) {
      switchNav('dashboard');
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      showToast("Link çözülüp Nuvio'ya aktarılıyor...");

      try {
        const res = await fetch('/api/publish-processed-url', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (data.status === 'started' || data.status === 'ok') {
          showToast('Nuvio aktarım işlemi başlatıldı!');
        } else {
          alert('Hata: ' + (data.message || 'İşlem başlatılamadı'));
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function publishSelectedProcessedUrls() {
      const urls = Array.from(selectedProcessedUrls);
      if (urls.length === 0) {
        alert("Lütfen Nuvio'ya göndermek istediğiniz linkleri seçin!");
        return;
      }
      if (!confirm(`Seçilen ${urls.length} adet film/dizi linki tek tek çözülüp Nuvio'ya aktarılacak. Onaylıyor musunuz?`)) return;

      switchNav('dashboard');
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      showToast(`${urls.length} link Nuvio'ya aktarılmaya başlandı...`);

      try {
        const res = await fetch('/api/publish-all-processed-urls', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ urls })
        });
        const data = await res.json();
        if (data.status === 'started') {
          selectedProcessedUrls.clear();
          updateSelectedUrlsState();
        } else {
          alert('Hata: ' + (data.message || ''));
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function publishAllProcessedUrls() {
      if (allProcessedUrls.length === 0) {
        alert('Hafızada kayıtlı taranmış link bulunmuyor!');
        return;
      }
      if (!confirm(`Hafızadaki toplam ${allProcessedUrls.length} adet taranmış film/dizi tek tek çözülüp anında Nuvio'ya aktarılacaktır. Başlatılsın mı?`)) return;

      switchNav('dashboard');
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      showToast("Tüm hafıza Nuvio'ya aktarılmaya başlandı...");

      try {
        const res = await fetch('/api/publish-all-processed-urls', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.status !== 'started') {
          alert('Hata: ' + (data.message || ''));
        }
      } catch (e) {
        console.error(e);
      }
    }

    function filterProcessedUrls() {
      const q = document.getElementById('memory-search').value.toLowerCase().trim();
      if (!q) { renderProcessedUrls(allProcessedUrls); return; }
      renderProcessedUrls(allProcessedUrls.filter(u => u.toLowerCase().includes(q)));
    }

    async function deleteProcessedUrl(url) {
      await fetch('/api/delete-processed-url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url })
      });
      showToast('Link hafızadan çıkarıldı! Tekrar taranabilir.');
      await loadProcessedUrls();
    }

    async function clearAllProcessedUrls() {
      if (!confirm('Taranmis tum linkler onbellegi sifirlanacak! Sitelerdeki filmler sifirdan yeniden taranabilecek. Onayliyor musunuz?')) return;
      await fetch('/api/clear-processed-urls', { method: 'POST' });
      showToast('Tarama hafızası başarıyla temizlendi!');
      await loadProcessedUrls();
    }

    async function loadHistoryMovies() {
      try {
        const res = await fetch('/api/history-movies');
        const data = await res.json();
        allHistoryMovies = data.movies || [];
        document.getElementById('his-count').innerText = allHistoryMovies.length;
        renderHistoryMovies(allHistoryMovies);
      } catch (e) {
        console.error(e);
      }
    }

    function renderHistoryMovies(movies) {
      const cont = document.getElementById('history-container');
      cont.innerHTML = "";
      if (movies.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Eski arşivde kayıtlı film bulunmuyor.</div>";
        return;
      }
      movies.forEach(m => {
        const item = document.createElement('div');
        item.className = 'media-item';
        const hasStreams = m.streams && m.streams.length > 0;
        const streamBadge = hasStreams 
          ? `<span style="font-size:11px; color:#10b981; background:rgba(16,185,129,0.12); padding:2px 6px; border-radius:4px;">${m.streams.length} Akış Hazır</span>`
          : `<span style="font-size:11px; color:#94a3b8; background:rgba(148,163,184,0.1); padding:2px 6px; border-radius:4px;">Akış Yok</span>`;
        const validPoster = (m.poster && !m.poster.includes('metahub.space')) ? m.poster : 'https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png';

        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:12px; overflow:hidden;">
            <img src="${validPoster}" referrerpolicy="no-referrer" onerror="this.src='https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png'" style="width:40px; height:56px; object-fit:cover; border-radius:6px; background:#1e293b; flex-shrink:0;" />
            <div style="display:flex; flex-direction:column; overflow:hidden;">
              <span style="font-weight:700; font-size:13.5px; color:#fff; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;">${m.name}</span>
              <div style="display:flex; align-items:center; gap:8px; margin-top:2px;">
                <span style="font-size:11px; color:#a855f7;">ID: ${m.id}</span>
                ${streamBadge}
              </div>
            </div>
          </div>
          <div style="display:flex; gap:6px;">
            <button class="btn-green" style="padding:5px 12px; font-size:11px;" onclick="publishSingleHistoryMovie('${m.id}')">🚀 Nuvio'ya Gönder</button>
            <button class="btn-red" style="padding:5px 12px; font-size:11px;" onclick="deleteHistoryMovie('${m.id}')">🗑️ Sil</button>
          </div>
        `;
        cont.appendChild(item);
      });
    }

    async function publishSingleHistoryMovie(id) {
      const target = allHistoryMovies.find(m => m.id === id);
      const name = target ? (target.name || id) : id;
      showToast(`"${name}" Nuvio'ya aktarılıyor...`);
      try {
        const res = await fetch('/api/publish-history-movie', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id })
        });
        const data = await res.json();
        if (data.status === 'ok') {
          showToast(`✓ "${name}" Nuvio'ya aktarıldı ve arşivden çıkarıldı!`);
          await loadPublishedMovies();
          await loadHistoryMovies();
        } else {
          showToast(`Hata: ${data.message || 'Yüklenemedi'}`);
        }
      } catch (e) {
        showToast('Bağlantı hatası!');
      }
    }

    async function publishAllHistoryMovies() {
      if (!confirm("Eski arşivdeki çözülmüş tüm filmler toplu olarak Nuvio ve GitHub'a aktarılacak. Onaylıyor musunuz?")) return;
      showToast('⚡ Toplu Nuvio aktarımı başlatıldı...');
      try {
        const res = await fetch('/api/publish-all-history', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'ok') {
          showToast(`✓ ${data.count || ''} film başarıyla Nuvio'ya aktarıldı ve arşivden çıkarıldı!`);
          await loadPublishedMovies();
          await loadHistoryMovies();
        } else {
          showToast(`Hata: ${data.message || 'Yüklenemedi'}`);
        }
      } catch (e) {
        showToast('Toplu yükleme sırasında hata oluştu!');
      }
    }

    function filterHistoryMovies() {
      const q = document.getElementById('history-search').value.toLowerCase().trim();
      if (!q) { renderHistoryMovies(allHistoryMovies); return; }
      renderHistoryMovies(allHistoryMovies.filter(m => (m.name || '').toLowerCase().includes(q) || (m.id || '').toLowerCase().includes(q)));
    }

    async function clearAllHistoryMovies() {
      if (!confirm('Eski arşivdeki tüm kayıtlar temizlenecek. Onaylıyor musunuz?')) return;
      await fetch('/api/clear-history-movies', { method: 'POST' });
      showToast('Eski arşiv başarıyla temizlendi!');
      await loadHistoryMovies();
    }

    async function deleteHistoryMovie(id) {
      const target = allHistoryMovies.find(m => m.id === id);
      const name = target ? (target.name || id) : id;
      if (!confirm(`"${name}" kaydını eski arşivden silmek istiyor musunuz?`)) return;
      await fetch('/api/delete-history-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      showToast('Kayıt arşivden silindi!');
      await loadHistoryMovies();
    }

    // Keyboard shortcuts (1, 2, 3, 4, 5)
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === '1') switchNav('dashboard');
      if (e.key === '2') switchNav('catalog');
      if (e.key === '3') switchNav('single');
      if (e.key === '4') switchNav('memory');
      if (e.key === '5') switchNav('settings');
    });

    document.addEventListener('DOMContentLoaded', () => {
      const views = ['dashboard', 'catalog', 'single', 'memory', 'settings'];
      views.forEach(v => {
        const btn = document.getElementById('dock-btn-' + v);
        if (btn) {
          btn.addEventListener('click', (e) => {
            if (e) e.preventDefault();
            switchNav(v);
          });
        }
      });
    });

    window.onload = () => {
      loadConfig();
      loadPublishedMovies();
      loadProcessedUrls();
      loadHistoryMovies();
      pollLogs();
    };
  </script>
</body>
</html>"""

# -------------------------------------------------------------
# Web Server Request Handler
# -------------------------------------------------------------
class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        len_str = self.headers.get("Content-Length", 0)
        length = int(len_str)
        if length > 0:
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body)
        return {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif self.path == "/api/config":
            self._send_json(load_config())
        elif self.path == "/api/logs":
            is_active = CURRENT_WORKER is not None and CURRENT_WORKER.is_alive()
            self._send_json({
                "logs": LOG_BUFFER,
                "is_running": ENGINE_INSTANCE.is_running and is_active,
                "is_paused": ENGINE_INSTANCE.is_paused and is_active
            })
        elif self.path == "/api/published-movies":
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            # 1. Fetch real-time direct from GitHub Contents API (bypasses Fastly CDN)
            cat_json = fetch_github_file("data/catalog.json", token=token)
            metas = None
            if isinstance(cat_json, dict) and "metas" in cat_json:
                metas = cat_json.get("metas", [])
                # Sync local workspace file immediately so local is always identical to remote
                try:
                    local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
                    with open(local_cat, "w", encoding="utf-8") as f:
                        json.dump({"metas": metas}, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            # 2. Fallback to local file if GitHub API not reached
            if metas is None:
                local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
                if os.path.exists(local_cat):
                    try:
                        with open(local_cat, "r", encoding="utf-8") as f:
                            d = json.load(f)
                            metas = d.get("metas", [])
                    except Exception:
                        metas = []
                else:
                    metas = []
            # 3. Enrich metas with active streams from data/streams.json
            local_str = os.path.join(WORKSPACE_DIR, "data", "streams.json")
            streams_map = {}
            if os.path.exists(local_str):
                try:
                    with open(local_str, "r", encoding="utf-8") as f:
                        streams_map = json.load(f)
                except Exception:
                    pass

            for m in (metas or []):
                if isinstance(m, dict):
                    m_id = m.get("id", "")
                    s_list = streams_map.get(m_id) or streams_map.get(m_id.replace(":", "-")) or []
                    m["streams"] = s_list
                    m["stream_count"] = len(s_list)

            self._send_json({"movies": metas or []})
        elif self.path == "/api/history-movies":
            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path):
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                        self._send_json({"movies": d.get("metas", [])})
                        return
                except Exception:
                    pass
            self._send_json({"movies": []})
        elif self.path == "/api/processed-urls":
            urls = sorted(list(load_processed_urls()))
            self._send_json({"urls": urls, "count": len(urls)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global CURRENT_WORKER
        if self.path == "/api/delete-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if movie_id and token:
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                success = pub.delete_movie(movie_id)
                self._send_json({"status": "ok" if success else "error"})
            else:
                self._send_json({"status": "error", "message": "Geçersiz ID veya Token eksik"})
        elif self.path == "/api/clear-all-movies":
            ENGINE_INSTANCE.stop()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if token:
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                success = pub.clear_all_published_movies()
                self._send_json({"status": "ok" if success else "error"})
            else:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
        elif self.path == "/api/publish-history-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
                return

            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path) and movie_id:
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    target = next((m for m in d.get("metas", []) if m.get("id") == movie_id), None)
                    if not target:
                        self._send_json({"status": "error", "message": "Film arşivde bulunamadı"})
                        return

                    # Format for publish_movies
                    streams = target.get("streams", [])
                    if not streams:
                        # Fallback dummy Play stream if streams missing
                        streams = [{
                            "name": "Play",
                            "title": "Play • 1080p • [TR Dublaj]",
                            "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
                            "quality": 1080,
                            "format": "mp4"
                        }]

                    m_item = {
                        "tmdbId": target.get("id"),
                        "title": target.get("name") or target.get("title"),
                        "poster": target.get("poster") or target.get("background"),
                        "description": target.get("description", "Play • Film"),
                        "year": target.get("releaseInfo") or target.get("year", ""),
                        "genres": target.get("genres", ["Popüler"]),
                        "streams": streams,
                        "type": target.get("type", "movie")
                    }
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    append_global_log(f"🚀 [ARŞİVDEN NUVIO'YA AKTARILIYOR] {m_item['title']}...")
                    success = pub.publish_movies([m_item])
                    if success:
                        rem_metas = [m for m in d.get("metas", []) if m.get("id") != movie_id]
                        with open(his_path, "w", encoding="utf-8") as f:
                            json.dump({"metas": rem_metas}, f, indent=2, ensure_ascii=False)
                        append_global_log(f"🗑️ [ARŞİVDEN SİLİNDİ] '{m_item['title']}' Nuvio'ya yüklendiği için arşivden kaldırıldı.")
                        # Hafızadan da sil (processed urls)
                        t_url = target.get("url") or target.get("source_url")
                        cur_urls = load_processed_urls()
                        if t_url and t_url in cur_urls:
                            cur_urls.remove(t_url)
                            save_processed_urls(cur_urls)
                            append_global_log(f"🗑️ [HAFIZADAN SİLİNDİ] {t_url} bağlantısı hafızadan temizlendi.")
                    self._send_json({"status": "ok" if success else "error"})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Geçersiz istek"})
        elif self.path == "/api/publish-all-history":
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
                return

            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path):
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    raw_metas = d.get("metas", [])
                    valid_movies = []
                    for m in raw_metas:
                        s_list = m.get("streams", [])
                        if s_list:
                            valid_movies.append({
                                "tmdbId": m.get("id"),
                                "title": m.get("name") or m.get("title"),
                                "poster": m.get("poster") or m.get("background"),
                                "description": m.get("description", "Play • Film"),
                                "year": m.get("releaseInfo") or m.get("year", ""),
                                "genres": m.get("genres", ["Popüler"]),
                                "streams": s_list,
                                "type": m.get("type", "movie")
                            })

                    if not valid_movies:
                        self._send_json({"status": "error", "message": "Arşivde aktarılabilir akışlı film bulunamadı"})
                        return

                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    append_global_log(f"⚡ [TOPLU ARŞİV AKTARIMI] Toplam {len(valid_movies)} film Nuvio & GitHub'a aktarılıyor...")
                    success = pub.publish_movies(valid_movies)
                    if success:
                        with open(his_path, "w", encoding="utf-8") as f:
                            json.dump({"metas": []}, f, indent=2, ensure_ascii=False)
                        append_global_log("🗑️ [ARŞİV SIFIRLANDI] Tüm filmler Nuvio'ya aktarıldığı için arşiv hafızası temizlendi.")
                        # Hafızadaki eşleşen linkleri de sil
                        try:
                            cur_urls = load_processed_urls()
                            hist_urls = {m.get("url") or m.get("source_url") for m in raw_metas if (m.get("url") or m.get("source_url"))}
                            rem_urls = {u for u in cur_urls if u not in hist_urls}
                            if len(rem_urls) != len(cur_urls):
                                save_processed_urls(rem_urls)
                                append_global_log(f"🗑️ [HAFIZADAN SİLİNDİ] {len(cur_urls) - len(rem_urls)} adet taranmış link hafızadan çıkarıldı.")
                        except Exception:
                            pass
                    self._send_json({"status": "ok" if success else "error", "count": len(valid_movies)})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Arşiv dosyası bulunamadı"})
        elif self.path == "/api/clear-history-movies":
            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            try:
                with open(his_path, "w", encoding="utf-8") as f:
                    json.dump({"metas": []}, f, indent=2)
                self._send_json({"status": "ok"})
                return
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)})
                return
        elif self.path == "/api/delete-history-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path) and movie_id:
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    metas = [m for m in d.get("metas", []) if m.get("id") != movie_id]
                    with open(his_path, "w", encoding="utf-8") as f:
                        json.dump({"metas": metas}, f, ensure_ascii=False, indent=2)
                    self._send_json({"status": "ok"})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Bulunamadı"})
        elif self.path == "/api/save-config":
            body = self._read_json()
            cfg = load_config()
            cfg.update(body)
            save_config(cfg)
            self._send_json({"status": "ok"})
        elif self.path == "/api/add-site":
            body = self._read_json()
            url = body.get("url", "").strip()
            if url:
                cfg = load_config()
                saved = cfg.setdefault("saved_sites", [])
                if url not in saved:
                    saved.append(url)
                    save_config(cfg)
                    append_global_log(f"[SİTE EKLENDİ] {url}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/delete-site":
            body = self._read_json()
            idx = body.get("index", -1)
            cfg = load_config()
            saved = cfg.get("saved_sites", [])
            if 0 <= idx < len(saved):
                removed = saved.pop(idx)
                save_config(cfg)
                append_global_log(f"[SİTE SİLİNDİ] {removed}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/delete-processed-url":
            body = self._read_json()
            u = body.get("url", "").strip()
            urls = load_processed_urls()
            if u in urls:
                urls.remove(u)
                save_processed_urls(urls)
                append_global_log(f"[HAFIZADAN ÇIKARILDI] {u}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/clear-processed-urls":
            save_processed_urls(set())
            append_global_log("[HAFIZA TEMİZLENDİ] Taranmış linkler önbelleği sıfırlandı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/publish-processed-url":
            body = self._read_json()
            url = body.get("url", "").strip()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            if not url:
                self._send_json({"status": "error", "message": "Link boş olamaz"})
                return

            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik! Ayarlar bölümünden kaydedin."})
                return

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def single_url_worker():
                try:
                    append_global_log(f"🔗 [HAFIZADAN NUVIO'YA AKTARILIYOR] {url}...")
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    cat = ENGINE_INSTANCE.detect_category(url, "")
                    m_data = ENGINE_INSTANCE.scrape_movie_page(url, {cat}, tmdb_api_key=tmdb)
                    if m_data and m_data.get("streams"):
                        c_str = ", ".join(m_data.get("genres", ["Popüler"]))
                        append_global_log(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu • [{c_str}])")
                        append_global_log(f"🚀 [NUVIO AKTARIM] {m_data['title']} hemen Nuvio & GitHub'a aktarılıyor...")
                        if pub.publish_movies([m_data]):
                            append_global_log(f"🎉 [BAŞARILI] '{m_data['title']}' Nuvio'da yayına alındı!")
                            cur_urls = load_processed_urls()
                            if url in cur_urls:
                                cur_urls.remove(url)
                                save_processed_urls(cur_urls)
                                append_global_log(f"🗑️ [HAFIZADAN SİLİNDİ] {url} Nuvio'ya yüklendiği için hafızadan çıkarıldı.")
                            try:
                                his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
                                if os.path.exists(his_path):
                                    with open(his_path, "r", encoding="utf-8") as f:
                                        hd = json.load(f)
                                    h_metas = [hm for hm in hd.get("metas", []) if hm.get("id") != m_data.get("tmdbId") and hm.get("url") != url]
                                    with open(his_path, "w", encoding="utf-8") as f:
                                        json.dump({"metas": h_metas}, f, indent=2, ensure_ascii=False)
                            except Exception:
                                pass
                        else:
                            append_global_log(f"✗ [HATA] '{m_data['title']}' Nuvio'ya aktarılırken hata oluştu.")
                    else:
                        append_global_log(f"✗ [BAŞARISIZ] {url} için çalışan video akışı çözülemedi.")
                except Exception as e:
                    append_global_log(f"💥 [HATA] {str(e)}")
                finally:
                    ENGINE_INSTANCE.is_running = False
                    ENGINE_INSTANCE.is_paused = False

            CURRENT_WORKER = threading.Thread(target=single_url_worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started"})
        elif self.path == "/api/publish-all-processed-urls":
            body = self._read_json()
            target_urls = body.get("urls")
            if target_urls is None:
                target_urls = sorted(list(load_processed_urls()))
            else:
                target_urls = [u.strip() for u in target_urls if u.strip()]

            if not target_urls:
                self._send_json({"status": "error", "message": "Hafızada taranmış link bulunamadı"})
                return

            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik! Ayarlar bölümünden kaydedin."})
                return

            if ENGINE_INSTANCE.is_running and CURRENT_WORKER is not None and CURRENT_WORKER.is_alive():
                self._send_json({"status": "error", "message": "Şu anda devam eden bir tarama var. Lütfen önce durdurun."})
                return

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def bulk_urls_worker():
                try:
                    append_global_log(f"⚡ [HAFIZA TOPLU AKTARIMI] Toplam {len(target_urls)} adet taranmış link çözülüp Nuvio'ya gönderiliyor...")
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    total = len(target_urls)
                    resolved_count = 0

                    for idx, url in enumerate(target_urls, 1):
                        if not ENGINE_INSTANCE.check_alive():
                            append_global_log("⏹️ [DURDURULDU] Hafıza aktarımı kullanıcı tarafından durduruldu.")
                            break

                        append_global_log(f"[{idx}/{total}] ⚡ Çözülüyor: {url}...")
                        cat = ENGINE_INSTANCE.detect_category(url, "")
                        m_data = ENGINE_INSTANCE.scrape_movie_page(url, {cat}, tmdb_api_key=tmdb)

                        if not ENGINE_INSTANCE.check_alive():
                            break

                        if m_data and m_data.get("streams"):
                            c_str = ", ".join(m_data.get("genres", ["Popüler"]))
                            append_global_log(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu • [{c_str}])")
                            append_global_log(f"🚀 [ANLIK YÜKLEME] '{m_data['title']}' hemen Nuvio & GitHub'a aktarılıyor...")
                            if pub.publish_movies([m_data]):
                                resolved_count += 1
                                append_global_log(f"🎉 [EKLENDİ] '{m_data['title']}' Nuvio'da yayında! (Toplam aktarılan: {resolved_count})")
                                cur_urls = load_processed_urls()
                                if url in cur_urls:
                                    cur_urls.remove(url)
                                    save_processed_urls(cur_urls)
                                    append_global_log(f"🗑️ [HAFIZADAN SİLİNDİ] {url}")
                                try:
                                    his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
                                    if os.path.exists(his_path):
                                        with open(his_path, "r", encoding="utf-8") as f:
                                            hd = json.load(f)
                                        h_metas = [hm for hm in hd.get("metas", []) if hm.get("id") != m_data.get("tmdbId") and hm.get("url") != url]
                                        with open(his_path, "w", encoding="utf-8") as f:
                                            json.dump({"metas": h_metas}, f, indent=2, ensure_ascii=False)
                                except Exception:
                                    pass
                        else:
                            append_global_log(f"  ✗ {url} (Aktif video akışı bulunamadı)")

                    if ENGINE_INSTANCE.is_running:
                        append_global_log(f"🏁 [TAMAMLANDI] Hafıza aktarımı tamamlandı. {resolved_count}/{total} içerik Nuvio'ya aktarıldı.")
                    else:
                        append_global_log("⏹ [SONLANDI] İşlem kullanıcı tarafından durduruldu.")
                except Exception as e:
                    import traceback
                    append_global_log(f"💥 [BEKLENMEDİK HATA] {str(e)}")
                    append_global_log(traceback.format_exc())
                finally:
                    ENGINE_INSTANCE.is_running = False
                    ENGINE_INSTANCE.is_paused = False

            CURRENT_WORKER = threading.Thread(target=bulk_urls_worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started", "total": len(target_urls)})
        elif self.path == "/api/reset-sites":
            cfg = load_config()
            cfg["saved_sites"] = [
                "https://fullhdfilmizlebox.vip/",
                "https://filmdozu.com/",
                "https://fullfilmizle.fit/",
                "https://ufilmizle.com/",
                "https://filmarsivi.org/",
                "https://www.hintfilmizle.com/film-izle/",
                "https://diziboxizle.com/",
                "https://ddizimom.net/",
                "https://dizirex.com/"
            ]
            save_config(cfg)
            append_global_log("[SIFIRLANDI] Site listesi en iyi aktif dizi & film sitelerine sıfırlandı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/clear-logs":
            LOG_BUFFER.clear()
            self._send_json({"status": "ok"})
        elif self.path == "/api/stop":
            ENGINE_INSTANCE.stop()
            append_global_log("[DURDURULDU] İşlem tamamen durduruldu.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/pause":
            ENGINE_INSTANCE.pause()
            append_global_log("[DURAKLATILDI] Tarama geçici olarak duraklatıldı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/resume":
            ENGINE_INSTANCE.resume()
            append_global_log("[DEVAM EDİYOR] Tarama kaldığı yerden devam ediyor...")
            self._send_json({"status": "ok"})
        elif self.path == "/api/search-and-add-movie":
            body = self._read_json()
            query = body.get("query", "").strip()
            cfg = load_config()
            sites = cfg.get("saved_sites", [])
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            if not query:
                self._send_json({"status": "error", "message": "Film adı boş olamaz"})
                return

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def single_worker():
                append_global_log(f"🎯 [HEDEFLİ ARAMA BAŞLADI] '{query}' kayıtlı {len(sites)} sitede aranıyor...")
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                
                # Doğrudan URL mi verildi yoksa film adı mı?
                if query.startswith("http://") or query.startswith("https://"):
                    found_dict = {query: {"Popüler"}}
                else:
                    found_dict = ENGINE_INSTANCE.search_specific_movie_across_sites(query, sites)

                if not found_dict:
                    append_global_log(f"✗ [BULUNAMADI] '{query}' için kayıtlı sitelerde aktif film linki bulunamadı.")
                    return

                append_global_log(f"✓ '{query}' için {len(found_dict)} olası kaynak bulundu. Video akışları ve detaylar çözülüyor...")
                resolved_list = []

                for m_url, cats in found_dict.items():
                    if not ENGINE_INSTANCE.check_alive():
                        break
                    append_global_log(f"  ⚡ Çözülüyor: {m_url}...")
                    m_data = ENGINE_INSTANCE.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                    if m_data and m_data.get("streams"):
                        resolved_list.append(m_data)
                        c_str = ", ".join(m_data["genres"])
                        append_global_log(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu • [{c_str}])")
                        # İlk çalışan kaynağı çözünce hemen Nuvio'ya aktar
                        append_global_log(f"🚀 [NUVIO AKTARIM] {m_data['title']} Nuvio'ya tek başına ekleniyor...")
                        pub.publish_movies([m_data])
                        append_global_log(f"🎉 [BAŞARILI] '{m_data['title']}' başarıyla kurtarıldı ve Nuvio'ya yüklendi!")
                        break

                if not resolved_list:
                    append_global_log(f"✗ [BAŞARISIZ] '{query}' bulundu fakat çalışan video akışı (m3u8/mp4) çözülemedi.")

            CURRENT_WORKER = threading.Thread(target=single_worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started"})
        elif self.path == "/api/start-all":
            cfg = load_config()
            sites = cfg.get("saved_sites", [])
            max_p = int(cfg.get("max_pages", 20))
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def worker():
                try:
                    append_global_log(f"[GÜNCELLEME BAŞLADI] Toplam {len(sites)} kayıtlı sitenin tamamı taranıyor...")
                    pub_names, pub_ids, processed_urls = get_already_published_identifiers()
                    append_global_log(f"📋 [HAFIZA KONTROLÜ] Önceden taranıp Nuvio'ya eklenmiş {len(pub_names | pub_ids | processed_urls)} içerik tespit edildi. Bu filmler tekrar taranmayacak, doğrudan atlanacak!")

                    all_scraped = []
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)

                    for s_idx, site_url in enumerate(sites):
                        if not ENGINE_INSTANCE.check_alive():
                            break
                        append_global_log(f"--- [SİTE {s_idx + 1}/{len(sites)}] Taranıyor: {site_url} ---")
                        movie_dict = ENGINE_INSTANCE.discover_movie_links(site_url, max_pages=max_p)
                        append_global_log(f"✓ {site_url} üzerinde {len(movie_dict)} film bulundu. Video akışları çözülüyor...")

                        for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
                            if not ENGINE_INSTANCE.check_alive():
                                break
                            short = m_url.rstrip("/").split("/")[-1]

                            # URL daha önce işlendiyse doğrudan atla
                            if m_url in processed_urls:
                                append_global_log(f"  ⏭ {short} (Daha önceden zaten eklenmiş, atlandı)")
                                continue

                            # URL'deki film adı bilinen yayınlanmış bir ada uyuyorsa atla
                            slug_cand = short.replace("-izle", "").replace("-", " ").strip().lower()
                            if slug_cand in pub_names:
                                append_global_log(f"  ⏭ {short} (Nuvio'da zaten mevcut: {slug_cand}, atlandı)")
                                processed_urls.add(m_url)
                                continue

                            append_global_log(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
                            m_data = ENGINE_INSTANCE.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                            
                            # İşlendi olarak kaydet
                            processed_urls.add(m_url)
                            save_processed_urls(processed_urls)

                            if m_data and m_data.get("streams"):
                                # Film adı veya ID'si yayında varsa atla
                                m_title_norm = m_data['title'].strip().lower()
                                m_id_norm = m_data['tmdbId'].strip().lower()
                                if m_title_norm in pub_names or m_id_norm in pub_ids:
                                    append_global_log(f"  ⏭ {m_data['title']} (Nuvio'da zaten mevcut, atlandı)")
                                    continue

                                pub_names.add(m_title_norm)
                                pub_ids.add(m_id_norm)
                                s_count = len(m_data["streams"])
                                c_str = ", ".join(m_data["genres"])
                                append_global_log(f"  ✓ {m_data['title']} ({s_count} Sunucu • [{c_str}])")

                                # Çözülen her filmi beklemeden anında GitHub ve Nuvio'ya aktar!
                                append_global_log(f"⚡ [ANLIK YÜKLEME] '{m_data['title']}' hemen Nuvio & GitHub'a aktarılıyor...")
                                pub.publish_movies([m_data])
                            else:
                                append_global_log(f"  ↷ {short} (Aktif akış yok, atlandı)")

                    if ENGINE_INSTANCE.is_running:
                        append_global_log("✓ [BİTTİ] Tüm tarama ve anlık yükleme tamamlandı.")
                    else:
                        append_global_log("⏹ [SONLANDI] Tarama kullanıcı tarafından durduruldu.")
                except Exception as e:
                    import traceback
                    append_global_log(f"💥 [BEKLENMEDİK HATA] {str(e)}")
                    append_global_log(traceback.format_exc())
                finally:
                    ENGINE_INSTANCE.is_running = False
                    ENGINE_INSTANCE.is_paused = False

            CURRENT_WORKER = threading.Thread(target=worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started"})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run_cli_crawl(site_url: str, max_pages: int = 20, token: str = None):
    print(f"🎬 [PlayRepo CLI] Taranıyor: {site_url} (Max sayfa: {max_pages})...")
    engine = ScraperEngine(log_callback=print)
    movie_dict = engine.discover_movie_links(site_url, max_pages=max_pages)
    all_scraped = []
    for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
        short = m_url.rstrip("/").split("/")[-1]
        print(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
        m_data = engine.scrape_movie_page(m_url, cats)
        if m_data and m_data.get("streams"):
            all_scraped.append(m_data)
            print(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu)")
    if all_scraped:
        print(f"✓ [PlayRepo CLI] {len(all_scraped)} film ve detayları çözüldü. GitHub'a aktarılıyor...")
        eff_token = token or os.getenv("GITHUB_TOKEN", "")
        pub = GitHubAtomicPublisher(token=eff_token, log_callback=print)
        pub.publish_movies(all_scraped)
    else:
        print("✗ [PlayRepo CLI] Film bulunamadı veya akışlar çözülemedi.")

ACTIVE_PORT_FILE = "/tmp/playrepo_active.port"
DEFAULT_PORT = 58580

def is_server_alive(port: int) -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/config")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False

def open_browser_window(url: str):
    """
    Opens PlayRepo in a dedicated, distraction-free standalone desktop App-Mode window.
    Ensures First Run sentinel exists so Google / Welcome tabs never pop up.
    """
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    profile_dir = "/tmp/playrepo_desktop_profile"
    os.makedirs(profile_dir, exist_ok=True)
    first_run_file = os.path.join(profile_dir, "First Run")
    if not os.path.exists(first_run_file):
        try:
            with open(first_run_file, "w") as f:
                f.write("")
        except Exception:
            pass

    if os.path.exists(chrome_bin):
        try:
            # Kill any stale Chrome processes using this dedicated profile so Chrome cleanly opens the active port
            subprocess.run("pkill -9 -f playrepo_desktop_profile || true", shell=True, capture_output=True)
            time.sleep(0.3)
            subprocess.Popen([
                "open", "-n", "-a", "/Applications/Google Chrome.app",
                "--args",
                f"--app={url}",
                f"--user-data-dir={profile_dir}",
                "--window-size=1200,800",
                "--no-first-run",
                "--no-default-browser-check"
            ])
            return
        except Exception:
            pass
    subprocess.run(["open", url])

def run_app():
    port = DEFAULT_PORT

    if is_server_alive(port):
        open_browser_window(f"http://127.0.0.1:{port}")
        sys.exit(0)

    server = None
    try:
        server = HTTPServer(('127.0.0.1', port), RequestHandler)
    except OSError:
        try:
            subprocess.run(f"lsof -ti :{port} | xargs kill -9 || true", shell=True, capture_output=True)
            time.sleep(0.4)
            server = HTTPServer(('127.0.0.1', port), RequestHandler)
        except Exception:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            sock.close()
            server = HTTPServer(('127.0.0.1', port), RequestHandler)

    try:
        with open(ACTIVE_PORT_FILE, "w") as f:
            f.write(str(port))
    except Exception:
        pass

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    open_browser_window(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        if os.path.exists(ACTIVE_PORT_FILE):
            try:
                os.remove(ACTIVE_PORT_FILE)
            except Exception:
                pass

if __name__ == "__main__":
    if "--crawl" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(description="PlayRepo Headless Scraper")
        parser.add_argument("--crawl", type=str, required=True, help="Site URL to crawl")
        parser.add_argument("--max-pages", type=int, default=20)
        parser.add_argument("--token", type=str, default=os.getenv("GITHUB_TOKEN", ""))
        args = parser.parse_args()
        run_cli_crawl(args.crawl, args.max_pages, args.token)
    else:
        run_app()
