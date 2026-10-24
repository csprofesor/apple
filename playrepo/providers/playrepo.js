const CATALOG_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/data/catalog.json";
const STREAMS_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

async function unpackLiveStreamUrl(rawUrl, referer) {
    if (!rawUrl || (rawUrl.includes(".m3u8") && !rawUrl.includes("/embed/"))) {
        return rawUrl;
    }

    try {
        const response = await fetch(rawUrl, {
            headers: {
                "User-Agent": UA,
                "Referer": referer || rawUrl
            }
        });

        if (!response.ok) return rawUrl;
        const html = await response.text();

        // 1. Vidmoly, Vidhide, FileLions, JWPlayer m3u8 regex
        const m3u8Match = html.match(/file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/src\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i);
        if (m3u8Match && m3u8Match[1]) {
            return m3u8Match[1];
        }

        // 2. VOE regex
        const voeMatch = html.match(/'hls'\s*:\s*['"](https?:\/\/[^'"]+)['"]/i) ||
                         html.match(/["']hls["']\s*:\s*["'](https?:\/\/[^"']+)["']/i);
        if (voeMatch && voeMatch[1]) {
            return voeMatch[1];
        }

        // 3. Ok.ru HLS Manifest
        const okMatch = html.match(/hlsManifestUrl["']?\s*:\s*["'](https?:\/\/[^"']+)["']/i);
        if (okMatch && okMatch[1]) {
            return okMatch[1].replace(/\\u0026/g, "&");
        }

        // 4. Direct mp4
        const mp4Match = html.match(/["'](https?:\/\/[^"']+\.mp4[^"']*)["']/i);
        if (mp4Match && mp4Match[1]) {
            return mp4Match[1];
        }
    } catch (e) {
        console.error("unpackLiveStreamUrl error:", e);
    }

    return rawUrl;
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
        console.error("PlayRepo getCatalog error:", e);
        return { metas: [] };
    }
}

async function getMeta(type, id) {
    try {
        const response = await fetch(CATALOG_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return { meta: null };

        const catalogData = await response.json();
        const metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

        const cleanId = id ? id.toString().replace(/^tmdb:/, "").replace(/^movie\//, "").replace(/^tv\//, "") : "";
        const found = metas.find(m => m.id === cleanId || m.id === `movie/${cleanId}` || m.id === `tmdb:${cleanId}`);

        if (found) {
            return {
                meta: {
                    id: found.id,
                    type: found.type || "movie",
                    name: found.name,
                    poster: found.poster,
                    description: found.description || "PlayRepo • 1080p Film Yayın Kaynağı",
                    genres: found.genres || ["Popüler"]
                }
            };
        }

        return {
            meta: {
                id: cleanId,
                type: type || "movie",
                name: `Film ${cleanId}`,
                poster: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                description: "PlayRepo • 1080p Film Yayın Kaynağı",
                genres: ["Popüler"]
            }
        };
    } catch (e) {
        console.error("PlayRepo getMeta error:", e);
        return { meta: null };
    }
}

async function getStreams(tmdbId, mediaType, season, episode) {
    try {
        const response = await fetch(STREAMS_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return [];

        const data = await response.json();
        const idStr = tmdbId ? tmdbId.toString() : "";
        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^movie\//, "").replace(/^tv\//, "");

        const keyMovie = `movie/${cleanId}`;
        const keyTv = `tv/${cleanId}/${season || 1}/${episode || 1}`;
        const keyTmdb = `tmdb:${cleanId}`;

        const itemStreams = data[keyTv] || data[keyMovie] || data[cleanId] || data[keyTmdb] || data[idStr] || [];

        const resolvedStreams = await Promise.all(itemStreams.map(async (s) => {
            const refererUrl = s.referer || s.url || "https://www.google.com";
            const directUrl = await unpackLiveStreamUrl(s.url, refererUrl);
            const isM3u8 = directUrl.toLowerCase().includes(".m3u8");

            return {
                name: s.name || "PlayRepo",
                title: s.title || `PlayRepo • ${s.quality || '1080p'}`,
                url: directUrl,
                quality: s.quality || 1080,
                provider: "playrepo-main",
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
        }));

        return resolvedStreams;
    } catch (e) {
        console.error("PlayRepo getStreams error:", e);
        return [];
    }
}

module.exports = {
    getCatalog: getCatalog,
    getMeta: getMeta,
    getStreams: getStreams
};
