const CATALOG_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/catalog.json";
const STREAMS_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function unpackPackerJs(packed) {
    try {
        const match = packed.match(/eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d?\s*\)\s*\{.*?\}\s*\(\s*['"](.*?)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['"](.*?)['"]\.split\(\s*['"]\|['"]\s*\)/s);
        if (!match) return packed;
        const payload = match[1];
        const radix = parseInt(match[2], 10);
        const keywords = match[4].split("|");

        const chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
        function decodeBase(str, b) {
            let res = 0;
            for (let i = 0; i < str.length; i++) {
                const idx = chars.indexOf(str[i]);
                if (idx < 0 || idx >= b) return -1;
                res = res * b + idx;
            }
            return res;
        }

        return payload.replace(/\b\w+\b/g, (w) => {
            const idx = decodeBase(w, radix);
            return (idx >= 0 && idx < keywords.length && keywords[idx]) ? keywords[idx] : w;
        }).replace(/\\\//g, "/");
    } catch (_) {
        return packed;
    }
}

async function unpackLiveStreamUrl(rawUrl, referer) {
    if (!rawUrl || rawUrl === "about:blank" || rawUrl.startsWith("about:") || rawUrl.startsWith("javascript:")) {
        return "";
    }

    const lower = rawUrl.toLowerCase();

    // If direct playable stream already, return immediately
    if ((lower.includes(".m3u8") || lower.includes(".mp4")) && !lower.includes("/embed") && !lower.includes("/videoembed") && !lower.includes("/e/")) {
        return rawUrl;
    }

    // 1. Mail.ru unpacker (select 1080p)
    if (lower.includes("my.mail.ru/video/embed/") || lower.includes("my.mail.ru/+/video/meta/")) {
        try {
            const vid = rawUrl.split("/").pop().split("?")[0].replace(".html", "");
            const metaRes = await fetch(`https://my.mail.ru/+/video/meta/${vid}`, {
                headers: { "User-Agent": UA }
            });
            if (metaRes.ok) {
                const metaJson = await metaRes.json();
                if (metaJson && metaJson.videos && metaJson.videos.length > 0) {
                    const best = metaJson.videos.find(v => v.key === "1080p" || v.key === "720p") || metaJson.videos[0];
                    const vUrl = best.url || "";
                    if (vUrl) {
                        return vUrl.startsWith("//") ? `https:${vUrl}` : vUrl;
                    }
                }
            }
        } catch (e) {
            console.error("Mail.ru unpack error:", e);
        }
    }

    try {
        const response = await fetch(rawUrl, {
            headers: {
                "User-Agent": UA,
                "Referer": referer || rawUrl
            }
        });

        if (!response.ok) return "";
        let html = await response.text();

        // Pilavyer / DiziMom player (window.__PLAYER__)
        if (lower.includes("pilavyer") || lower.includes("/s.php") || html.includes("window.__PLAYER__")) {
            const playerMatch = html.match(/window\.__PLAYER__\s*=\s*(\{.*?\});/);
            if (playerMatch && playerMatch[1]) {
                try {
                    const playerData = JSON.parse(playerMatch[1]);
                    if (playerData && playerData.stream) {
                        return playerData.stream.replace(/\\\//g, "/");
                    }
                } catch (_) {}
            }
        }

        // Dean Edwards unpack
        if (html.includes("eval(function(p,a,c,k,e,d)")) {
            html += "\n" + unpackPackerJs(html);
        }

        // 2. Bitchute direct MP4
        if (lower.includes("bitchute.com")) {
            const bcMatch = html.match(/(https?:\/\/[^\s"'<>]+\.bitchute\.com\/[^\s"'<>]+\.mp4)/i);
            if (bcMatch && bcMatch[1]) return bcMatch[1];
        }

        // 3. Vidmoly, Vidhide, FileLions, StreamWish, JWPlayer m3u8 regex
        const m3u8Match = html.match(/file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/sources\s*:\s*\[\s*\{\s*file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/src\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i);
        if (m3u8Match && m3u8Match[1]) {
            return m3u8Match[1];
        }

        // 4. VOE regex
        const voeMatch = html.match(/'hls'\s*:\s*['"](https?:\/\/[^'"]+\.m3u8[^'"]*)["']/i) ||
                         html.match(/["']hls["']\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i);
        if (voeMatch && voeMatch[1]) {
            return voeMatch[1];
        }

        // 5. Ok.ru / Odnoklassniki HLS Manifest
        const okMatch = html.match(/hlsManifestUrl(?:\\?["']|&quot;)\s*:\s*(?:\\?["']|&quot;)(https?:[^"'\s&]+)/i);
        if (okMatch && okMatch[1]) {
            return okMatch[1].replace(/\\u0026/g, "&").replace(/&amp;/g, "&");
        }

        // 6. Streamtape robotlink
        if (lower.includes("streamtape") || lower.includes("streamta.pe")) {
            const linkMatch = html.match(/id=['"](?:robotlink|ideoolink)['"][^>]*>(.*?)</i);
            if (linkMatch && linkMatch[1] && linkMatch[1].startsWith("//")) {
                return "https:" + linkMatch[1];
            }
            const partMatch = html.match(/document\.getElementById\(['"](?:robotlink|ideoolink)['"]\)\.innerHTML\s*=\s*['"]([^'"]+)['"]\s*\+\s*\(['"]([^'"]+)['"]\)\.substring\((\d+)\)/i);
            if (partMatch) {
                const p1 = partMatch[1];
                const p2 = partMatch[2];
                const sub = parseInt(partMatch[3], 10) || 0;
                const combined = (sub < p2.length) ? p1 + p2.substring(sub) : p1 + p2;
                return combined.startsWith("//") ? `https:${combined}` : combined;
            }
        }

        // 7. Sibnet MP4
        if (lower.includes("sibnet.ru")) {
            const sibMatch = html.match(/src\s*:\s*["'](\/v\/[^"']+\.mp4)["']/i);
            if (sibMatch && sibMatch[1]) {
                return "https://video.sibnet.ru" + sibMatch[1];
            }
        }

        // 8. Mixdrop regex
        const mixMatch = html.match(/MDCore\.wurl\s*=\s*["']([^"']+)["']/i);
        if (mixMatch && mixMatch[1]) {
            const u = mixMatch[1];
            return u.startsWith("//") ? `https:${u}` : u;
        }

        // 9. Generic JSON source variable matching (JWPlayer, VideoJS, Plyr)
        const jsonMatch = html.match(/["']?(?:file|src|url|hls|stream)["']?\s*:\s*["'](https?:\/\/[^"']+\.(?:m3u8|mp4)[^"']*)["']/i);
        if (jsonMatch && jsonMatch[1]) {
            return jsonMatch[1];
        }

        // 10. Direct mp4 regex
        const mp4Match = html.match(/["'](https?:\/\/[^"']+\.mp4[^"']*)["']/i);
        if (mp4Match && mp4Match[1]) {
            return mp4Match[1];
        }

        // 11. Generic m3u8 regex
        const anyM3u8 = html.match(/https?:\/\/[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*/i);
        if (anyM3u8 && anyM3u8[0] && !anyM3u8[0].includes("/thumb") && !anyM3u8[0].includes("/preview")) {
            return anyM3u8[0];
        }
    } catch (e) {
        console.error("unpackLiveStreamUrl error:", e);
    }

    return "";
}

function matchesCatCheck(m, ...keywords) {
    if (!m) return false;
    const genres = (m.genres || []).map(g => (g || "").toString().toLowerCase());
    const desc = (m.description || "").toString().toLowerCase();
    const name = (m.name || "").toString().toLowerCase();
    const platform = (m.platform || "").toString().toLowerCase();

    return keywords.some(kw => {
        const k = kw.toLowerCase();
        if (platform.includes(k) || name.includes(k)) return true;
        if (genres.some(g => g.includes(k))) return true;
        // Avoid substring false positives for short words like "aile", "war", "star", "max" in description text
        if (["aile", "family", "war", "star", "max", "fx"].includes(k)) {
            return new RegExp(`\\b${k}\\b`, "i").test(desc);
        }
        return desc.includes(k);
    });
}

function enrichWithUpcomingEpisode(meta) {
    if (!meta) return meta;
    if (meta.type === "series" && Array.isArray(meta.videos) && meta.videos.length > 0) {
        const now = new Date();
        const futureVideos = meta.videos.filter(v => v.released && new Date(v.released) > now);
        if (futureVideos.length > 0) {
            futureVideos.sort((a, b) => new Date(a.released) - new Date(b.released));
            const nextEp = futureVideos[0];
            const d = new Date(nextEp.released);
            const dateStr = d.toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
            const sNum = nextEp.season || 1;
            const eNum = nextEp.episode || 1;
            const epTitle = (nextEp.title && nextEp.title !== `${sNum}. Sezon ${eNum}. Bölüm`) ? ` ("${nextEp.title}")` : "";
            const banner = `📅 YENİ BÖLÜM: Sezon ${sNum}, Bölüm ${eNum}${epTitle} • ${dateStr} tarihinde yayında!\n\n`;
            if (meta.description && !meta.description.includes("📅 YENİ BÖLÜM")) {
                meta.description = banner + meta.description;
            }
            if (!meta.behaviorHints) meta.behaviorHints = {};
            meta.behaviorHints.hasScheduledVideos = true;
        }
    }
    return meta;
}

function isYerliContent(m) {
    if (!m) return false;
    const genres = (m.genres || []).map(g => (g || "").toString().toLowerCase());
    const desc = (m.description || "").toString().toLowerCase();
    const name = (m.name || "").toString().toLowerCase();
    const platform = (m.platform || "").toString().toLowerCase();

    if (platform.includes("blutv") || platform.includes("gain") || platform.includes("exxen") || platform.includes("puhutv")) return true;
    if (genres.some(g => g === "yerli" || g.includes("yerli film") || g.includes("yerli dizi") || g.includes("türk filmi") || g.includes("türk dizisi"))) return true;
    const yerliTerms = ["blutv", "gain", "exxen", "yerli film", "yerli dizi", "yerli sinema", "türk filmi", "türk sineması", "türk dizisi", "türk yapımı"];
    return yerliTerms.some(term => desc.includes(term) || name.includes(term));
}

function getDisjointMetasForCatalog(allMetas, catalogId) {
    const allMovies = allMetas.filter(m => m.type !== "series");
    const allSeries = allMetas.filter(m => m.type === "series");

    if (catalogId === "playrepo-popular" || catalogId === "playrepo-catalog-movies") {
        return allMovies;
    }
    if (catalogId === "playrepo-series-popular") {
        return allSeries;
    }

    // 1. Yerli Filmler & Netflix Yerli Özel Koleksiyonları
    if (catalogId === "playrepo-blutv") {
        return allMovies.filter(m => isYerliContent(m));
    }
    if (catalogId === "playrepo-netflix-yerli") {
        return allMovies.filter(m => isYerliContent(m) && matchesCatCheck(m, "netflix"));
    }

    // 1b. Yerli Diziler & Netflix Yerli Dizi Özel Koleksiyonları
    if (catalogId === "playrepo-series-blutv") {
        return allSeries.filter(s => isYerliContent(s));
    }
    if (catalogId === "playrepo-series-netflix-yerli") {
        return allSeries.filter(s => isYerliContent(s) && matchesCatCheck(s, "netflix"));
    }

    const movieConfigs = [
        // 2. Yabancı Platform Koleksiyonları (Yerli yapımlarla karışmaz)
        { id: "playrepo-netflix", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "netflix") },
        { id: "playrepo-disney", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "disney", "pixar", "marvel") },
        { id: "playrepo-prime", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "prime video", "amazon prime", "amazon") },
        { id: "playrepo-apple", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "apple tv", "appletv", "apple original") },
        { id: "playrepo-hbo", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "hbo", "hbomax", "max original", "warner") },
        { id: "playrepo-hulu", check: (m) => !isYerliContent(m) && matchesCatCheck(m, "hulu") },
        { id: "playrepo-mubi", check: (m) => matchesCatCheck(m, "mubi", "arthouse", "festival") },
        // 3. Birleştirilmiş ve Derli Toplu Tür Koleksiyonları
        { id: "playrepo-bilimkurgu", check: (m) => matchesCatCheck(m, "bilim kurgu", "bilim-kurgu", "bilimkurgu", "sci-fi", "science fiction", "fantastik", "fantasy") },
        { id: "playrepo-aksiyon", check: (m) => matchesCatCheck(m, "aksiyon", "action", "macera", "adventure", "savaş", "savas", "war") },
        { id: "playrepo-korku", check: (m) => matchesCatCheck(m, "korku", "horror", "gerilim", "thriller", "gizem", "mystery") },
        { id: "playrepo-animasyon", check: (m) => matchesCatCheck(m, "animasyon", "animation", "anime", "çizgi", "aile", "family") },
        { id: "playrepo-komedi", check: (m) => matchesCatCheck(m, "komedi", "comedy", "mizah") },
        { id: "playrepo-suc", check: (m) => matchesCatCheck(m, "suç", "suc", "crime", "polisiye", "dedektif") },
        { id: "playrepo-dram", check: (m) => matchesCatCheck(m, "dram", "drama", "romantik", "romance", "aşk") },
        { id: "playrepo-belgesel", check: (m) => matchesCatCheck(m, "belgesel", "documentary", "tarih", "history", "biyografi", "biography") }
    ];

    const assignedMovieIds = new Set();
    for (const cat of movieConfigs) {
        const catList = [];
        for (const m of allMovies) {
            const mId = m.id;
            if (mId && !assignedMovieIds.has(mId) && cat.check(m)) {
                catList.push(m);
                assignedMovieIds.add(mId);
            }
        }
        if (cat.id === catalogId) {
            return catList;
        }
    }

    const seriesConfigs = [
        // 2. Yabancı Dizi Platform Koleksiyonları (Yerli dizilerle karışmaz)
        { id: "playrepo-series-netflix", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "netflix") },
        { id: "playrepo-series-disney", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "disney", "pixar", "marvel", "star wars") },
        { id: "playrepo-series-prime", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "prime video", "amazon prime", "amazon") },
        { id: "playrepo-series-apple", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "apple tv", "appletv", "apple original") },
        { id: "playrepo-series-hbo", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "hbo", "hbomax", "max original", "warner") },
        { id: "playrepo-series-hulu", check: (s) => !isYerliContent(s) && matchesCatCheck(s, "hulu", "fx") },
        // 3. Birleştirilmiş Dizi Tür Koleksiyonları
        { id: "playrepo-series-bilimkurgu", check: (s) => matchesCatCheck(s, "bilim kurgu", "bilim-kurgu", "sci-fi", "science fiction", "fantastik", "fantasy") },
        { id: "playrepo-series-aksiyon", check: (s) => matchesCatCheck(s, "aksiyon", "action", "macera", "adventure", "savaş", "savas") },
        { id: "playrepo-series-korku", check: (s) => matchesCatCheck(s, "korku", "horror", "gerilim", "thriller", "gizem", "mystery") },
        { id: "playrepo-series-animasyon", check: (s) => matchesCatCheck(s, "animasyon", "animation", "anime", "çizgi", "çizgi dizi") },
        { id: "playrepo-series-komedi", check: (s) => matchesCatCheck(s, "komedi", "comedy", "mizah", "sitcom") },
        { id: "playrepo-series-suc", check: (s) => matchesCatCheck(s, "suç", "suc", "crime", "polisiye", "dedektif") },
        { id: "playrepo-series-dram", check: (s) => matchesCatCheck(s, "dram", "drama", "romantik", "romance") },
        { id: "playrepo-series-belgesel", check: (s) => matchesCatCheck(s, "belgesel", "documentary", "tarih", "history", "biyografi", "doğa") }
    ];

    const assignedSeriesIds = new Set();
    for (const cat of seriesConfigs) {
        const catList = [];
        for (const s of allSeries) {
            const sId = s.id;
            if (sId && !assignedSeriesIds.has(sId) && cat.check(s)) {
                catList.push(s);
                assignedSeriesIds.add(sId);
            }
        }
        if (cat.id === catalogId) {
            return catList;
        }
    }

    return [];
}

async function getCatalog(type, id, extra) {
    try {
        // 1. Try static category catalog file first (supporting both movie and series directories)
        if (id && id.startsWith("playrepo-")) {
            try {
                const catFolder = (type === "series" || id.includes("series")) ? "series" : "movie";
                const catFileUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/catalog/${catFolder}/${id}.json?t=${Date.now()}`;
                const catRes = await fetch(catFileUrl, {
                    headers: {
                        "User-Agent": UA,
                        "Accept": "application/json",
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache"
                    }
                });
                if (catRes.ok) {
                    const catData = await catRes.json();
                    let metas = Array.isArray(catData) ? catData : (catData.metas || []);
                    metas.forEach(m => {
                        if (m.poster && m.poster.includes("@")) m.poster = m.poster.replace(/@/g, "%40");
                        if (m.background && m.background.includes("@")) m.background = m.background.replace(/@/g, "%40");
                    });
                    if (extra && extra.search) {
                        const query = extra.search.toLowerCase();
                        metas = metas.filter(m => m.name && m.name.toLowerCase().includes(query));
                    }
                    // Return catalog contents directly even if empty (e.g. after user cleared content)
                    return { metas: metas };
                }
            } catch (_) {}
        }

        const response = await fetch(`${CATALOG_URL}?t=${Date.now()}`, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache"
            }
        });

        if (!response.ok) return { metas: [] };

        const catalogData = await response.json();
        let allMetas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

        let metas = getDisjointMetasForCatalog(allMetas, id);

        if (extra && extra.genre) {
            const requestedGenre = extra.genre.toLowerCase();
            metas = metas.filter(m => {
                if (!m.genres) return true;
                return m.genres.some(g => g.toLowerCase().includes(requestedGenre) || requestedGenre.includes(g.toLowerCase()));
            });
        }

        if (extra && extra.search) {
            const query = extra.search.toLowerCase();
            metas = metas.filter(m => m.name && m.name.toLowerCase().includes(query));
        }

        metas.forEach(m => {
            if (m.poster && m.poster.includes("@")) m.poster = m.poster.replace(/@/g, "%40");
            if (m.background && m.background.includes("@")) m.background = m.background.replace(/@/g, "%40");
        });

        return { metas: metas };
    } catch (e) {
        console.error("Play getCatalog error:", e);
        return { metas: [] };
    }
}

async function getMeta(arg1, arg2) {
    try {
        let mediaType = "movie";
        let idStr = "";

        if (typeof arg1 === "object" && arg1 !== null) {
            idStr = (arg1.id || arg1.tmdbId || "").toString();
            mediaType = arg1.type || arg1.mediaType || "movie";
        } else if (arg1 === "movie" || arg1 === "series" || arg1 === "tv") {
            mediaType = arg1;
            idStr = arg2 ? arg2.toString() : "";
        } else {
            idStr = arg1 ? arg1.toString() : "";
            mediaType = arg2 || "movie";
        }

        if (!idStr) return { meta: null };

        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^tmdb-/, "").replace(/^movie\//, "").replace(/^tv\//, "").replace(/^playrepo:/, "").replace(/^playrepo-/, "");
        const safeId = idStr.replace(/:/g, "_");
        const hyphenId = idStr.replace(/:/g, "-").replace(/_/g, "-");

        // 1. Try static meta file published by APK first (checking all safe variations)
        const possibleStaticIds = [encodeURIComponent(idStr), encodeURIComponent(hyphenId), encodeURIComponent(safeId), encodeURIComponent(cleanId)];
        const targetTypes = mediaType === "series" ? ["series", "movie"] : ["movie", "series"];
        for (const t of targetTypes) {
            for (const pid of possibleStaticIds) {
                try {
                    const staticMetaUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/meta/${t}/${pid}.json?t=${Date.now()}`;
                    const smRes = await fetch(staticMetaUrl, {
                        headers: { "User-Agent": UA, "Accept": "application/json" }
                    });
                    if (smRes.ok) {
                        const smData = await smRes.json();
                        if (smData && smData.meta) {
                            if (smData.meta.poster && smData.meta.poster.includes("@")) smData.meta.poster = smData.meta.poster.replace(/@/g, "%40");
                            if (smData.meta.background && smData.meta.background.includes("@")) smData.meta.background = smData.meta.background.replace(/@/g, "%40");

                            // Dizilerde Cinemeta'dan tüm sezon ve bölümleri tam liste olarak al
                            if (smData.meta.type === "series" || t === "series") {
                                const targetImdb = (smData.meta.id && smData.meta.id.startsWith("tt")) ? smData.meta.id : (idStr.startsWith("tt") ? idStr : "");
                                if (targetImdb) {
                                    try {
                                        const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/series/${targetImdb}.json`, {
                                            headers: { "User-Agent": UA }
                                        });
                                        if (cmRes.ok) {
                                            const cmData = await cmRes.json();
                                            if (cmData && cmData.meta && Array.isArray(cmData.meta.videos) && cmData.meta.videos.length > 0) {
                                                const vMap = new Map();
                                                for (const cv of cmData.meta.videos) {
                                                    if (cv && cv.id) vMap.set(cv.id, cv);
                                                }
                                                if (Array.isArray(smData.meta.videos)) {
                                                    for (const sv of smData.meta.videos) {
                                                        if (sv && sv.id) {
                                                            const existing = vMap.get(sv.id) || {};
                                                            vMap.set(sv.id, { ...existing, ...sv });
                                                        }
                                                    }
                                                }
                                                smData.meta.videos = Array.from(vMap.values()).sort((a, b) => {
                                                    const sDiff = (a.season ?? 1) - (b.season ?? 1);
                                                    if (sDiff !== 0) return sDiff;
                                                    return (a.episode ?? a.number ?? 1) - (b.episode ?? b.number ?? 1);
                                                });
                                                if (cmData.meta.logo && !smData.meta.logo) smData.meta.logo = cmData.meta.logo;
                                                if (cmData.meta.trailers && (!smData.meta.trailers || smData.meta.trailers.length === 0)) smData.meta.trailers = cmData.meta.trailers;
                                                if (cmData.meta.trailer && !smData.meta.trailer) smData.meta.trailer = cmData.meta.trailer;
                                                if (cmData.meta.trailerStreams && (!smData.meta.trailerStreams || smData.meta.trailerStreams.length === 0)) smData.meta.trailerStreams = cmData.meta.trailerStreams;
                                            }
                                        }
                                    } catch (_) {}
                                }
                            }

                            return { meta: enrichWithUpcomingEpisode(smData.meta) };
                        }
                    }
                } catch (_) {}
            }
        }

        // 2. Search local catalog (Contains site's official Turkish description, genres, and poster)
        try {
            const response = await fetch(`${CATALOG_URL}?t=${Date.now()}`, {
                headers: {
                    "User-Agent": UA,
                    "Accept": "application/json"
                }
            });

            if (response.ok) {
                const catalogData = await response.json();
                const metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

                const normTarget = idStr.toLowerCase().replace(/[:_]/g, "-");
                const normClean = cleanId.toLowerCase().replace(/[:_]/g, "-");
                const found = metas.find(m => {
                    if (!m || !m.id) return false;
                    const normM = m.id.toLowerCase().replace(/[:_]/g, "-");
                    return normM === normTarget || normM === normClean || normM === `playrepo-${normClean}` || normTarget === `playrepo-${normM}` ||
                           m.id === idStr || m.id === hyphenId || m.id === safeId || m.id === cleanId;
                });

                if (found) {
                    const videoId = found.id || idStr;
                    const metaObj = {
                        id: videoId,
                        type: found.type || "movie",
                        name: found.name,
                        poster: found.poster || "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                        background: found.background || found.poster || "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                        logo: found.logo || undefined,
                        description: found.description || "Play • Film Yayın Kaynağı",
                        genres: found.genres || ["Popüler"],
                        releaseInfo: found.releaseInfo || "",
                        imdbRating: found.imdbRating || "",
                        runtime: found.runtime || "",
                        behaviorHints: {
                            defaultVideoId: videoId,
                            hasScheduledVideos: false
                        },
                        videos: [
                            {
                                id: videoId,
                                title: found.name,
                                released: found.releaseInfo ? `${found.releaseInfo}-01-01T00:00:00.000Z` : undefined
                            }
                        ]
                    };
                    if (found.trailers) metaObj.trailers = found.trailers;
                    if (found.trailerStreams) metaObj.trailerStreams = found.trailerStreams;
                    if (found.trailer) metaObj.trailer = found.trailer;

                    // Enrich with Cinemeta trailers/cast/director
                    const targetImdb = idStr.startsWith("tt") ? idStr : (found.imdb_id || found.imdbId || "");
                    let cmQueryId = targetImdb;
                    if (!cmQueryId && found.name) {
                        try {
                            const cType = (found.type === "series" || mediaType === "series") ? "series" : "movie";
                            const srRes = await fetch(`https://v3-cinemeta.strem.io/catalog/${cType}/top/search=${encodeURIComponent(found.name)}.json`, {
                                headers: { "User-Agent": UA }
                            });
                            if (srRes.ok) {
                                const srData = await srRes.json();
                                if (srData && srData.metas && srData.metas.length > 0) {
                                    cmQueryId = srData.metas[0].id;
                                }
                            }
                        } catch (_) {}
                    }

                    if (cmQueryId && cmQueryId.startsWith("tt")) {
                        try {
                            const cType = (found.type === "series" || mediaType === "series") ? "series" : "movie";
                            const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/${cType}/${cmQueryId}.json`, {
                                headers: { "User-Agent": UA }
                            });
                            if (cmRes.ok) {
                                const cmData = await cmRes.json();
                                if (cmData && cmData.meta) {
                                    if (cmData.meta.logo) metaObj.logo = cmData.meta.logo;
                                    if (cmData.meta.cast) metaObj.cast = cmData.meta.cast;
                                    if (cmData.meta.director) metaObj.director = cmData.meta.director;
                                    if (cmData.meta.trailers && cmData.meta.trailers.length > 0) {
                                        metaObj.trailers = cmData.meta.trailers;
                                        const firstTr = cmData.meta.trailers.find(t => t.type === "Trailer") || cmData.meta.trailers[0];
                                        if (firstTr && firstTr.source) {
                                            metaObj.trailer = `https://www.youtube.com/watch?v=${firstTr.source}`;
                                        }
                                    }
                                    if (cmData.meta.trailerStreams && cmData.meta.trailerStreams.length > 0) {
                                        metaObj.trailerStreams = cmData.meta.trailerStreams;
                                    } else if (metaObj.trailer) {
                                        const ytId = metaObj.trailer.split("v=")[1]?.split("&")[0];
                                        if (ytId) {
                                            metaObj.trailerStreams = [{ title: "Fragman", ytId: ytId }];
                                        }
                                    }
                                    if (cmData.meta.trailer) metaObj.trailer = cmData.meta.trailer;
                                    if (cType === "series" && Array.isArray(cmData.meta.videos) && cmData.meta.videos.length > 0) {
                                        const vMap = new Map();
                                        for (const cv of cmData.meta.videos) {
                                            if (cv && cv.id) vMap.set(cv.id, cv);
                                        }
                                        if (Array.isArray(metaObj.videos)) {
                                            for (const sv of metaObj.videos) {
                                                if (sv && sv.id) {
                                                    const existing = vMap.get(sv.id) || {};
                                                    vMap.set(sv.id, { ...existing, ...sv });
                                                }
                                            }
                                        }
                                        metaObj.videos = Array.from(vMap.values()).sort((a, b) => {
                                            const sDiff = (a.season ?? 1) - (b.season ?? 1);
                                            if (sDiff !== 0) return sDiff;
                                            return (a.episode ?? a.number ?? 1) - (b.episode ?? b.number ?? 1);
                                        });
                                    }
                                }
                            }
                        } catch (_) {}
                    }

                    return { meta: enrichWithUpcomingEpisode(metaObj) };
                }
            }
        } catch (_) {}

        // 3. Fallback: Cinemeta if not found in catalog yet
        if (idStr.startsWith("tt")) {
            try {
                const cType = mediaType === "series" ? "series" : "movie";
                const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/${cType}/${idStr}.json`, {
                    headers: { "User-Agent": UA }
                });
                if (cmRes.ok) {
                    const cmData = await cmRes.json();
                    if (cmData && cmData.meta) {
                        const m = cmData.meta;
                        if (!m.behaviorHints) m.behaviorHints = {};
                        m.behaviorHints.defaultVideoId = idStr;
                        m.behaviorHints.hasScheduledVideos = false;
                        if (!m.videos || m.videos.length === 0) {
                            m.videos = [
                                {
                                    id: idStr,
                                    title: m.name,
                                    released: m.releaseInfo ? `${m.releaseInfo}-01-01T00:00:00.000Z` : undefined
                                }
                            ];
                        }
                        return { meta: enrichWithUpcomingEpisode(m) };
                    }
                }
            } catch (_) { }
        }

        const fallbackId = idStr || cleanId;
        return {
            meta: {
                id: fallbackId,
                type: mediaType || "movie",
                name: `Film ${cleanId}`,
                poster: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                background: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                logo: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                description: "Play • Film Yayın Kaynağı",
                genres: ["Popüler"],
                behaviorHints: {
                    defaultVideoId: fallbackId,
                    hasScheduledVideos: false
                },
                videos: [
                    {
                        id: fallbackId,
                        title: `Film ${cleanId}`
                    }
                ]
            }
        };
    } catch (e) {
        console.error("Play getMeta error:", e);
        return { meta: null };
    }
}

async function resolveAndCapStreams(rawStreams) {
    if (!rawStreams || rawStreams.length === 0) return [];

    const resolvedPromises = rawStreams.map(async (s) => {
        if (!s || !s.url) return null;
        const lowUrl = s.url.toLowerCase();
        if (lowUrl.includes("youtube.com") || lowUrl.includes("youtu.be") || lowUrl.includes("googleads")) {
            return null;
        }
        const refererUrl = (s.behaviorHints && s.behaviorHints.proxyHeaders && s.behaviorHints.proxyHeaders.request && s.behaviorHints.proxyHeaders.request.Referer) || s.referer || s.url || "https://www.google.com";
        let directUrl = s.url;
        const lowerDirect = (directUrl || "").toLowerCase();

        // Only unpack if it's an embed page or expired OK.ru link that needs client-side extraction
        const isEmbedPage = lowerDirect.includes("/embed") || lowerDirect.includes("/videoembed") || lowerDirect.includes("/e/") || lowerDirect.includes("/s.php");
        const isExpiredCdn = lowerDirect.includes("srcip=") || lowerDirect.includes("okcdn.ru");
        const needsLiveUnpack = isEmbedPage || isExpiredCdn || (!lowerDirect.includes(".m3u8") && !lowerDirect.includes(".mp4") && !lowerDirect.includes("stream.php"));

        if (needsLiveUnpack) {
            const targetUrl = (refererUrl.includes("/embed") || refererUrl.includes("/videoembed") || refererUrl.includes("/e/") || refererUrl.includes("/s.php") || lowerDirect.includes("srcip=")) ? refererUrl : directUrl;
            const unpacked = await unpackLiveStreamUrl(targetUrl, refererUrl);
            if (unpacked) directUrl = unpacked;
        }

        if (!directUrl) return null;
        const finalLower = directUrl.toLowerCase();
        if (!finalLower.includes(".m3u8") && !finalLower.includes(".mp4") && !finalLower.includes("stream.php") && !finalLower.includes("/manifest")) {
            return null;
        }

        const isM3u8 = finalLower.includes(".m3u8") || finalLower.includes("stream.php") || finalLower.includes("/hls");
        let playUrl = directUrl;
        if (isM3u8 && !finalLower.includes(".m3u8")) {
            playUrl = playUrl.includes("?") ? `${playUrl}&ext=.m3u8` : `${playUrl}#.m3u8`;
        }

        return {
            rawTitle: s.title || s.name || "",
            url: playUrl,
            quality: s.quality || 1080,
            provider: "play",
            format: isM3u8 ? "m3u8" : "mp4",
            behaviorHints: {
                notSupported: false,
                proxyHeaders: {
                    request: {
                        "User-Agent": UA,
                        "Referer": refererUrl
                    }
                }
            },
            headers: {
                "User-Agent": UA,
                "Referer": refererUrl
            }
        };
    });

    const resolved = (await Promise.all(resolvedPromises)).filter(s => s && s.url && s.url.length > 5);

    // Deduplicate and cap: Max 2 Dublaj, Max 3 Altyazı
    const dubList = [];
    const subList = [];
    const seenUrls = new Set();

    // Sort HLS first for instant playback
    resolved.sort((a, b) => {
        const aHls = (a.format === "m3u8" || a.url.includes(".m3u8")) ? 1 : 0;
        const bHls = (b.format === "m3u8" || b.url.includes(".m3u8")) ? 1 : 0;
        return bHls - aHls;
    });

    for (const st of resolved) {
        if (seenUrls.has(st.url)) continue;
        seenUrls.add(st.url);

        const title = (st.rawTitle || "").toLowerCase();
        const hasSubs = st.subtitles && Array.isArray(st.subtitles) && st.subtitles.length > 0;

        if (title.includes("altyaz")) {
            if (subList.length < 2) subList.push(st);
        } else {
            if (dubList.length < 2) dubList.push(st);
            // Eğer akışta altyazı dosyası (subtitles) varsa ve ayrı bir altyazı akışı yoksa,
            // kullanıcıya hem dublaj hem de altyazılı seçenek olarak sun
            if (hasSubs && subList.length < 2) {
                const subClone = { ...st, rawTitle: "TR Altyazı", isSubClone: true };
                subList.push(subClone);
            }
        }
    }

    let dubC = 0, subC = 0;
    return [...dubList, ...subList].map(s => {
        const titleLower = (s.rawTitle || "").toLowerCase();
        let formattedTitle = "";
        let streamName = "";
        if (titleLower.includes("altyaz") || s.isSubClone) {
            subC++;
            streamName = "TR Altyazı";
            formattedTitle = `1080p • Sunucu ${subC}`;
        } else {
            dubC++;
            streamName = "TR Dublaj";
            formattedTitle = `1080p • Sunucu ${dubC}`;
        }

        const streamItem = {
            name: streamName,
            title: formattedTitle,
            url: s.url,
            quality: s.quality || 1080,
            provider: "play",
            format: s.format,
            behaviorHints: s.behaviorHints,
            headers: s.headers
        };
        if (s.subtitles && Array.isArray(s.subtitles) && s.subtitles.length > 0) {
            streamItem.subtitles = s.subtitles.map((sub, sIdx) => {
                let subUrl = sub.url || "";
                if (subUrl.includes("sub.php") && !subUrl.includes(".vtt")) {
                    subUrl = subUrl.includes("?") ? `${subUrl}&ext=.vtt` : `${subUrl}#.vtt`;
                }
                const rawLang = (sub.lang || sub.id || "").toLowerCase();
                const isTurkish = rawLang.includes("tr") || rawLang.includes("türk") || rawLang.includes("tur") || rawLang.includes("tam") || rawLang.includes("forced");
                return {
                    id: sub.id || `sub_${sIdx}`,
                    url: subUrl,
                    lang: isTurkish ? "tur" : (rawLang || "tur")
                };
            });
        }
        return streamItem;
    });
}

let cachedStreamsData = null;
let cachedStreamsTime = 0;

async function getStreams(arg1, arg2, arg3, arg4) {
    try {
        let idStr = "";
        let mediaType = "movie";
        let season = 1;
        let episode = 1;

        if (arg1 === "movie" || arg1 === "series" || arg1 === "tv") {
            mediaType = arg1;
            idStr = arg2 ? arg2.toString() : "";
            season = arg3 || 1;
            episode = arg4 || 1;
        } else {
            idStr = arg1 ? arg1.toString() : "";
            mediaType = arg2 || "movie";
            season = arg3 || 1;
            episode = arg4 || 1;
        }

        if (!idStr) return [];

        let baseId = idStr;
        if (idStr.includes(":")) {
            const parts = idStr.split(":");
            if (parts.length >= 3) {
                baseId = parts[0];
                season = parseInt(parts[1], 10) || season;
                episode = parseInt(parts[2], 10) || episode;
            }
        }

        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^tmdb-/, "").replace(/^movie\//, "").replace(/^tv\//, "").replace(/^playrepo:/, "").replace(/^playrepo-/, "");
        const cleanBaseId = baseId.replace(/^tmdb:/, "").replace(/^tmdb-/, "").replace(/^movie\//, "").replace(/^tv\//, "").replace(/^playrepo:/, "").replace(/^playrepo-/, "");
        const safeId = idStr.replace(/:/g, "_");
        const hyphenId = idStr.replace(/:/g, "-").replace(/_/g, "-");

        // Fast-path: Check cached or fetched streams.json directly (single request, <50ms)
        let allStreams = cachedStreamsData;
        if (!allStreams || (Date.now() - cachedStreamsTime > 60000)) {
            try {
                const response = await fetch(`${STREAMS_URL}?t=${Date.now()}`, {
                    headers: {
                        "User-Agent": UA,
                        "Accept": "application/json"
                    }
                });
                if (response.ok) {
                    allStreams = await response.json();
                    cachedStreamsData = allStreams;
                    cachedStreamsTime = Date.now();
                }
            } catch (_) {}
        }

        if (allStreams) {
            const isSeries = mediaType === "series" || idStr.includes(":") || (season != null && episode != null && (season > 1 || episode > 1));
            const targets = [
                idStr,
                hyphenId,
                safeId,
                cleanId,
                `playrepo-${cleanId}`,
                `movie/${cleanId}`,
                `tv/${cleanId}`,
                `tmdb:${cleanId}`,
                `playrepo:${cleanId}`
            ];

            if (isSeries && season != null && episode != null) {
                targets.unshift(
                    `${baseId}:${season}:${episode}`,
                    `${baseId}-${season}-${episode}`,
                    `${cleanBaseId}:${season}:${episode}`,
                    `${cleanBaseId}-${season}-${episode}`,
                    `playrepo-${cleanBaseId}:${season}:${episode}`,
                    `playrepo-${cleanBaseId}-${season}-${episode}`
                );
            }

            let itemStreams = null;
            for (const t of targets) {
                if (allStreams[t] && allStreams[t].length > 0) {
                    itemStreams = allStreams[t];
                    break;
                }
            }

            if (!itemStreams || itemStreams.length === 0) {
                // Sadece ve sadece TAM EŞLEŞME (Exact normalized match)
                for (const t of targets) {
                    const normTarget = t.toLowerCase().replace(/[:_]/g, "-");
                    for (const k of Object.keys(allStreams)) {
                        const normK = k.toLowerCase().replace(/[:_]/g, "-");
                        if (normK === normTarget) {
                            itemStreams = allStreams[k];
                            break;
                        }
                    }
                    if (itemStreams) break;
                }
            }

            if (itemStreams && itemStreams.length > 0) {
                return await resolveAndCapStreams(itemStreams);
            }
        }

        // Fallback: Check individual static stream file only if not found in streams.json
        const possibleStaticIds = [encodeURIComponent(idStr), encodeURIComponent(hyphenId), encodeURIComponent(safeId), encodeURIComponent(cleanId)];
        const folderType = (mediaType === "series" || idStr.includes(":")) ? "series" : "movie";
        for (const pid of possibleStaticIds) {
            try {
                const staticStreamUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/stream/${folderType}/${pid}.json?t=${Date.now()}`;
                const ssRes = await fetch(staticStreamUrl, {
                    headers: { "User-Agent": UA, "Accept": "application/json" }
                });
                if (ssRes.ok) {
                    const ssData = await ssRes.json();
                    if (ssData && ssData.streams && ssData.streams.length > 0) {
                        const capped = await resolveAndCapStreams(ssData.streams);
                        if (capped && capped.length > 0) {
                            return capped;
                        }
                    }
                }
            } catch (_) {}
        }

        return [];
    } catch (e) {
        console.error("Play getStreams error:", e);
        return [];
    }
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        getCatalog: getCatalog,
        catalog: getCatalog,
        getMeta: getMeta,
        meta: getMeta,
        getStreams: getStreams,
        stream: getStreams
    };
}
if (typeof window !== "undefined") {
    window.getStreams = getStreams;
    window.getCatalog = getCatalog;
    window.getMeta = getMeta;
}
if (typeof global !== "undefined") {
    global.getStreams = getStreams;
    global.getCatalog = getCatalog;
    global.getMeta = getMeta;
}
if (typeof globalThis !== "undefined") {
    globalThis.getStreams = getStreams;
    globalThis.getCatalog = getCatalog;
    globalThis.getMeta = getMeta;
}
