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

        // Filter by genre
        if (extra && extra.genre) {
            const requestedGenre = extra.genre.toLowerCase();
            metas = metas.filter(m => {
                if (!m.genres) return true;
                return m.genres.some(g => g.toLowerCase().includes(requestedGenre) || requestedGenre.includes(g.toLowerCase()));
            });
        }

        // Filter by search query
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

        const keyWithPrefix = mediaType === "tv"
            ? `tv/${tmdbId}/${season || 1}/${episode || 1}`
            : `movie/${tmdbId}`;

        const itemStreams = data[keyWithPrefix] || data[tmdbId?.toString()] || [];

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
    getStreams: getStreams
};
