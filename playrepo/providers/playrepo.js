const CATALOG_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/catalog.json";
const STREAMS_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

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
        const found = metas.find(m => m.id === cleanId || m.id === `movie/${cleanId}` || m.id.endsWith(cleanId));

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
                poster: "https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/logo.png",
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

        const itemStreams = data[keyTv] || data[keyMovie] || data[cleanId] || data[idStr] || [];

        return itemStreams.map((s) => ({
            name: s.name || "PlayRepo",
            title: s.title || `PlayRepo • ${s.quality || '1080p'}`,
            url: s.url,
            quality: s.quality || 1080,
            provider: "playrepo-main",
            format: s.format || "m3u8",
            headers: s.headers || {
                "User-Agent": UA,
                "Referer": s.referer || s.url
            }
        }));
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
