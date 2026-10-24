const CATALOG_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/catalog.json";
const STREAMS_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

async function unpackLiveStreamUrl(rawUrl, referer) {
    if (!rawUrl || rawUrl === "about:blank" || rawUrl.startsWith("about:") || rawUrl.startsWith("javascript:")) {
        return "";
    }

    const lower = rawUrl.toLowerCase();

    // If direct playable stream already, return immediately
    if ((lower.includes(".m3u8") || lower.includes(".mp4")) && !lower.includes("/embed") && !lower.includes("/videoembed")) {
        return rawUrl;
    }

    // 1. Mail.ru unpacker
    if (lower.includes("my.mail.ru/video/embed/") || lower.includes("my.mail.ru/+/video/meta/")) {
        try {
            const vid = rawUrl.split("/").pop().split("?")[0].replace(".html", "");
            const metaRes = await fetch(`https://my.mail.ru/+/video/meta/${vid}`, {
                headers: { "User-Agent": UA }
            });
            if (metaRes.ok) {
                const metaJson = await metaRes.json();
                if (metaJson && metaJson.videos && metaJson.videos.length > 0) {
                    const best = metaJson.videos[metaJson.videos.length - 1];
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
        const html = await response.text();

        // 2. Bitchute direct MP4
        if (lower.includes("bitchute.com")) {
            const bcMatch = html.match(/(https?:\/\/[^\s"'<>]+\.bitchute\.com\/[^\s"'<>]+\.mp4)/i);
            if (bcMatch && bcMatch[1]) return bcMatch[1];
        }

        // 3. Vidmoly, Vidhide, FileLions, JWPlayer m3u8 regex
        const m3u8Match = html.match(/file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/sources\s*:\s*\[\s*\{\s*file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/src\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i);
        if (m3u8Match && m3u8Match[1]) {
            return m3u8Match[1];
        }

        // 4. VOE regex
        const voeMatch = html.match(/'hls'\s*:\s*['"](https?:\/\/[^'"]+)['"]/i) ||
                         html.match(/["']hls["']\s*:\s*["'](https?:\/\/[^"']+)["']/i);
        if (voeMatch && voeMatch[1]) {
            return voeMatch[1];
        }

        // 5. Ok.ru HLS Manifest
        const okMatch = html.match(/hlsManifestUrl["']?\s*:\s*["'](https?:\/\/[^"']+)["']/i);
        if (okMatch && okMatch[1]) {
            return okMatch[1].replace(/\\u0026/g, "&");
        }

        // 6. Mixdrop regex
        const mixMatch = html.match(/MDCore\.wurl\s*=\s*["']([^"']+)["']/i);
        if (mixMatch && mixMatch[1]) {
            const u = mixMatch[1];
            return u.startsWith("//") ? `https:${u}` : u;
        }

        // 7. Direct mp4 regex
        const mp4Match = html.match(/["'](https?:\/\/[^"']+\.mp4[^"']*)["']/i);
        if (mp4Match && mp4Match[1]) {
            return mp4Match[1];
        }
    } catch (e) {
        console.error("unpackLiveStreamUrl error:", e);
    }

    return "";
}

async function getCatalog(type, id, extra) {
    try {
        const response = await fetch(CATALOG_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return { metas: [] };

        const catalogData = await response.json();
        let metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

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

        return { metas: metas };
    } catch (e) {
        console.error("Play getCatalog error:", e);
        return { metas: [] };
    }
}

async function getMeta(type, id) {
    try {
        const idStr = id ? id.toString() : "";
        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^movie\//, "").replace(/^tv\//, "");

        // 1. If it's an IMDb ID (tt...), fetch rich meta from Cinemeta
        if (idStr.startsWith("tt")) {
            try {
                const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/movie/${idStr}.json`, {
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
                        return { meta: m };
                    }
                }
            } catch (_) { }
        }

        // 2. Fetch local catalog
        const response = await fetch(CATALOG_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return { meta: null };

        const catalogData = await response.json();
        const metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

        const found = metas.find(m => m.id === idStr || m.id === cleanId || m.id === `movie/${cleanId}` || m.id === `tmdb:${cleanId}`);

        if (found) {
            const videoId = found.id || idStr;
            return {
                meta: {
                    id: videoId,
                    type: found.type || "movie",
                    name: found.name,
                    poster: found.poster,
                    background: found.poster,
                    description: found.description || "Play • Film Yayın Kaynağı",
                    genres: found.genres || ["Popüler"],
                    releaseInfo: found.releaseInfo || "",
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
                }
            };
        }

        const fallbackId = idStr || cleanId;
        return {
            meta: {
                id: fallbackId,
                type: type || "movie",
                name: `Film ${cleanId}`,
                poster: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                background: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
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

async function getStreams(arg1, arg2, arg3, arg4) {
    try {
        let idStr = "";
        let mediaType = "movie";
        let season = 1;
        let episode = 1;

        // Support both (type, id) and (id, type) and (tmdbId, mediaType, season, episode)
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

        const response = await fetch(STREAMS_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return [];

        const data = await response.json();
        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^movie\//, "").replace(/^tv\//, "");

        const keyMovie = `movie/${cleanId}`;
        const keyTv = `tv/${cleanId}/${season}/${episode}`;
        const keyTmdb = `tmdb:${cleanId}`;

        // Match keys
        let itemStreams = data[idStr] || data[keyMovie] || data[cleanId] || data[keyTmdb] || data[keyTv] || [];

        // Case-insensitive search fallback
        if (!itemStreams || itemStreams.length === 0) {
            const lowerId = idStr.toLowerCase();
            const lowerClean = cleanId.toLowerCase();
            for (const k of Object.keys(data)) {
                const lk = k.toLowerCase();
                if (lk === lowerId || lk === lowerClean || lk === `movie/${lowerClean}` || lk === `tmdb:${lowerClean}`) {
                    itemStreams = data[k];
                    break;
                }
            }
        }

        if (!itemStreams || itemStreams.length === 0) return [];

        const resolvedPromises = itemStreams.map(async (s) => {
            const refererUrl = s.referer || s.url || "https://www.google.com";
            const directUrl = await unpackLiveStreamUrl(s.url, refererUrl);

            if (!directUrl) return null;
            const lowerDirect = directUrl.toLowerCase();

            // ONLY return playable media streams
            if (!lowerDirect.includes(".m3u8") && !lowerDirect.includes(".mp4") && !lowerDirect.includes("/manifest")) {
                return null;
            }

            const isM3u8 = lowerDirect.includes(".m3u8");
            const rawTitle = s.title || `Play • ${s.quality || '1080p'}`;
            const maskedTitle = rawTitle.replace(/^PlayRepo/i, "Play");

            return {
                name: "Play",
                title: maskedTitle,
                url: directUrl,
                quality: s.quality || 1080,
                provider: "play",
                format: isM3u8 ? "m3u8" : "mp4",
                behaviorHints: {
                    notSupported: false,
                    proxyHeaders: {
                        "request": {
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

        const resolvedStreams = await Promise.all(resolvedPromises);
        return resolvedStreams.filter(s => s !== null && s.url && s.url.length > 5);
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
